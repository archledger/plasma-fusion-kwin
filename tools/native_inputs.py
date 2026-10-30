# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Prepare pinned public inputs for the offline native guest."""
import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path

from tools.patchset import (
    apply_patchset,
    fetch_source,
    git,
    load_manifest,
    object_pairs,
    require,
)

BUILDER_DIGEST = '4ea17e89d08d6d921e50cb2dabc3011a7061b3cfd1a2b22b026ce8842d0042eb'
BUILDER_CONFIG = '5e5eb48e65c6e9cf3738963de73a09cb6acad10401362b95cee3dc25a6958aa5'
BUILDER_REFERENCE = 'ghcr.io/archledger/plasma-fusion-kwin-builder@sha256:' + BUILDER_DIGEST


def regular_digest(path, limit):
    require(not path.is_symlink(), 'symlink input is forbidden')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as file:
        info = os.fstat(file.fileno())
        require(stat.S_ISREG(info.st_mode) and 0 < info.st_size <= limit, 'invalid input file size/type')
        digest = hashlib.file_digest(file, 'sha256').hexdigest()
    return digest, info.st_size


def read_json(path):
    regular_digest(path, 1024 * 1024)
    return json.loads(path.read_text(), object_pairs_hook=object_pairs)


def verify_image_directory(directory):
    directory = Path(directory).absolute()
    require(directory.resolve() == directory and directory.is_dir(), 'invalid image directory')
    manifest_file = directory / 'manifest.json'
    digest, _ = regular_digest(manifest_file, 1024 * 1024)
    require(digest == BUILDER_DIGEST, 'builder manifest digest differs')
    manifest = read_json(manifest_file)
    require(isinstance(manifest, dict) and type(manifest.get('schemaVersion')) is int and
            manifest['schemaVersion'] == 2, 'invalid image manifest')
    layers = manifest.get('layers')
    require(isinstance(layers, list) and 0 < len(layers) <= 32, 'invalid image layers')
    descriptors = [manifest.get('config'), *layers]
    names = {'manifest.json', 'version'}
    total = 0
    for descriptor in descriptors:
        require(isinstance(descriptor, dict), 'invalid blob descriptor')
        digest = descriptor.get('digest')
        size = descriptor.get('size')
        require(isinstance(digest, str) and re.fullmatch(r'sha256:[0-9a-f]{64}', digest),
                'invalid blob digest')
        require(type(size) is int and 0 < size <= 2 * 1024**3, 'invalid blob size')
        name = digest.removeprefix('sha256:')
        actual, actual_size = regular_digest(directory / name, 2 * 1024**3)
        require(actual == name and actual_size == size, 'builder blob differs')
        if name not in names:
            total += actual_size
            names.add(name)
        require(total <= 2 * 1024**3, 'builder input exceeds size budget')
    require(manifest['config']['digest'] == 'sha256:' + BUILDER_CONFIG, 'builder config identity differs')
    config = read_json(directory / BUILDER_CONFIG)
    require(isinstance(config, dict) and isinstance(config.get('config'), dict), 'invalid image config')
    require(config.get('architecture') == 'amd64' and config.get('os') == 'linux' and
            config.get('config', {}).get('User') == 'builder', 'unexpected builder platform or user')
    regular_digest(directory / 'version', 128)
    require((directory / 'version').read_text() == 'Directory Transport Version: 1.1\n',
            'unexpected image transport format')
    require({path.name for path in directory.iterdir()} == names, 'unexpected image directory entry')
    return {'manifest': BUILDER_DIGEST, 'config': BUILDER_CONFIG, 'bytes': total,
            'files': sorted(names), 'reference': BUILDER_REFERENCE}


def archive_tree(source, expected_tree, output):
    require(re.fullmatch(r'[0-9a-f]{40}', expected_tree), 'invalid expected source tree')
    require(not Path(output).exists(), 'source archive destination exists')
    actual = git(source, 'write-tree').decode().strip()
    require(actual == expected_tree, 'source index tree differs')
    git(source, 'diff', '--quiet')
    # HEAD remains unpatched. Archive the verified index tree with fixed times.
    git(source, 'archive', '--format=tar', '--mtime=@0', '--output=' + str(Path(output).absolute()), expected_tree)
    digest, size = regular_digest(Path(output), 128 * 1024**2)
    return {'tree': expected_tree, 'sha256': digest, 'bytes': size}


def prepare(root, output):
    output.mkdir(mode=0o700)
    manifest = load_manifest(root)
    with tempfile.TemporaryDirectory(prefix='native-source-', dir=output) as directory:
        source = Path(directory)
        fetch_source(source, manifest)
        patch_result = apply_patchset(source, manifest)
        archive = archive_tree(source, manifest.expected_tree, output / 'patched-source.tar')
    with tempfile.TemporaryDirectory(prefix='native-auth-', dir=output) as directory:
        auth = Path(directory) / 'auth.json'
        auth.write_text('{"auths":{}}\n')
        env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': directory, 'LANG': 'C.UTF-8'}
        subprocess.run(['skopeo', 'copy', '--authfile', str(auth), '--src-no-creds', '--preserve-digests',
                        'docker://' + BUILDER_REFERENCE, 'dir:' + str(output / 'builder-image')],
                       env=env, check=True, timeout=300)
    image = verify_image_directory(output / 'builder-image')
    result = {'source': archive, 'patchset': patch_result, 'builder': image}
    (output / 'inputs.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.root, args.output), indent=2))
