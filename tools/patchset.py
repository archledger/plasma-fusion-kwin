# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Validate a patch set, or apply it only in a disposable upstream checkout."""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

UPSTREAM = 'https://invent.kde.org/plasma/kwin.git'
MAX_PATCH_BYTES = 4 * 1024 * 1024


class PatchsetError(ValueError):
    """The patch set cannot support a successful validation result."""


@dataclass(frozen=True)
class Patch:
    identifier: str
    filename: str
    digest: str
    paths: tuple[str, ...]
    content: bytes


@dataclass(frozen=True)
class Patchset:
    release: str
    commit: str
    expected_tree: str
    patches: tuple[Patch, ...]


def require(condition, message):
    if not condition:
        raise PatchsetError(message)


def object_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f'duplicate JSON key: {key}')
        result[key] = value
    return result


def keys(value, expected, label):
    require(type(value) is dict and set(value) == set(expected), f'invalid {label} fields')


def digest(value, size, label):
    require(type(value) is str and re.fullmatch(f'[0-9a-f]{{{size}}}', value),
            f'invalid {label}')


def relative_path(value):
    require(type(value) is str and re.fullmatch(r'[A-Za-z0-9_./-]+', value), 'invalid path')
    path = PurePosixPath(value)
    require(path.parts and not path.is_absolute() and path.as_posix() == value
            and not any(part in ('.', '..', '.git') for part in path.parts), 'unsafe path')
    return value


def load_manifest(root):
    root = Path(root).resolve()
    try:
        manifest_path = root / 'patchset.json'
        require(not manifest_path.is_symlink(), 'manifest must not be a symlink')
        require(manifest_path.stat().st_size <= 1024 * 1024, 'manifest is too large')
        raw = manifest_path.read_bytes()
        require(len(raw) <= 1024 * 1024, 'manifest is too large')
        data = json.loads(raw, object_pairs_hook=object_pairs)
        keys(data, ['schema_version', 'upstream', 'expected_tree', 'patches'], 'manifest')
        require(type(data['schema_version']) is int and data['schema_version'] == 1,
                'unsupported schema version')
        upstream = data['upstream']
        keys(upstream, ['repository', 'release', 'commit'], 'upstream')
        require(upstream['repository'] == UPSTREAM, 'unexpected upstream repository')
        require(type(upstream['release']) is str
                and re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+', upstream['release']),
                'invalid release reference')
        digest(upstream['commit'], 40, 'upstream commit')
        digest(data['expected_tree'], 40, 'expected tree')
        require(type(data['patches']) is list and data['patches'], 'empty or invalid patch list')
        patches, identifiers, filenames, digests = [], set(), set(), set()
        for entry in data['patches']:
            keys(entry, ['id', 'file', 'sha256', 'paths', 'provenance'], 'patch')
            identifier = entry['id']
            require(type(identifier) is str and re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', identifier),
                    'invalid patch identifier')
            filename = relative_path(entry['file'])
            require(filename.startswith('patches/') and filename.endswith('.patch'),
                    'patch must be below patches/ and end with .patch')
            require(identifier not in identifiers and filename not in filenames,
                    'duplicate patch identity or file')
            identifiers.add(identifier)
            filenames.add(filename)
            digest(entry['sha256'], 64, 'patch SHA256')
            require(entry['sha256'] not in digests, 'duplicate patch content')
            digests.add(entry['sha256'])
            require(type(entry['paths']) is list and entry['paths'], 'invalid patch target list')
            paths = tuple(relative_path(path) for path in entry['paths'])
            require(len(set(paths)) == len(paths), 'duplicate patch target')
            provenance = entry['provenance']
            keys(provenance, ['base_repository', 'qualification_commit',
                             'qualification_commit_published', 'public_submission_commit',
                             'upstream_merge_request'], 'provenance')
            require(provenance['base_repository'] == UPSTREAM, 'unexpected provenance repository')
            digest(provenance['qualification_commit'], 40, 'qualification commit')
            require(type(provenance['qualification_commit_published']) is bool,
                    'invalid qualification publication flag')
            require(type(provenance['public_submission_commit']) is str and re.fullmatch(
                r'https://invent\.kde\.org/[A-Za-z0-9_/-]+/-/commit/[0-9a-f]{40}',
                provenance['public_submission_commit']), 'invalid public submission reference')
            require(type(provenance['upstream_merge_request']) is str and re.fullmatch(
                r'https://invent\.kde\.org/plasma/kwin/-/merge_requests/[1-9][0-9]*',
                provenance['upstream_merge_request']), 'invalid upstream merge request')
            path = root
            for part in PurePosixPath(filename).parts:
                path = path / part
                require(not path.is_symlink(), 'patch path must not contain symlinks')
            require(path.resolve().is_relative_to(root) and path.is_file(), 'missing patch file')
            require(path.stat().st_size <= MAX_PATCH_BYTES, 'patch is too large')
            content = path.read_bytes()
            require(0 < len(content) <= MAX_PATCH_BYTES, 'empty or oversized patch')
            require(hashlib.sha256(content).hexdigest() == entry['sha256'], 'patch digest mismatch')
            patches.append(Patch(identifier, filename, entry['sha256'], paths, content))
        return Patchset(upstream['release'], upstream['commit'], data['expected_tree'], tuple(patches))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PatchsetError(f'cannot read patch set: {error}') from error


def git(source, *arguments, content=None):
    # This repository is disposable. Do not inherit external Git directories,
    # credential helpers, template hooks or URL rewrites from the caller.
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT='0')
    command = ['git', '-c', 'protocol.file.allow=never', *arguments]
    try:
        result = subprocess.run(command, cwd=source, env=env, input=content,
                                capture_output=True, timeout=300, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PatchsetError(f'git {arguments[0]} could not complete: {error}') from error
    require(result.returncode == 0,
            f'git {arguments[0]} failed: {result.stderr.decode(errors="replace").strip()}')
    return result.stdout


def fetch_source(destination, manifest):
    git(destination, '-c', 'init.templateDir=', 'init', '-q')
    git(destination, 'config', 'core.autocrlf', 'false')
    git(destination, 'fetch', '--no-tags', '--depth=1', UPSTREAM, manifest.commit)
    git(destination, 'checkout', '--detach', 'FETCH_HEAD')


def apply_patchset(source, manifest):
    """Apply only to a trusted, fresh, full disposable checkout; no receipt on failure.

    Existing repository-local configuration and index flags are trusted here.
    The CLI never accepts an existing checkout: it initializes its own instead.
    """
    source = Path(source).resolve()
    top = git(source, 'rev-parse', '--show-toplevel').decode().strip()
    require(Path(top).resolve() == source, 'source is not a repository root')
    require(git(source, 'rev-parse', 'HEAD').decode().strip() == manifest.commit,
            'source commit mismatch')
    require(not git(source, 'status', '--porcelain=v1', '--untracked-files=all', '--ignored'),
            'source checkout is not clean')
    expected_paths = set()
    for patch in manifest.patches:
        records = git(source, 'apply', '--numstat', '-z', '-', content=patch.content).split(b'\0')
        paths = []
        for record in records:
            if not record:
                continue
            fields = record.split(b'\t', 2)
            require(len(fields) == 3 and all(value.isdigit() for value in fields[:2]),
                    'unsupported binary or rename patch')
            try:
                paths.append(relative_path(fields[2].decode()))
            except UnicodeError as error:
                raise PatchsetError('invalid patch target encoding') from error
        require(len(paths) == len(patch.paths) and set(paths) == set(patch.paths),
                f'patch target scope mismatch: {patch.identifier}')
        git(source, 'apply', '--check', '--index', '--whitespace=error-all', '-',
            content=patch.content)
        git(source, 'apply', '--index', '--whitespace=error-all', '-', content=patch.content)
        expected_paths.update(patch.paths)
    changed = git(source, 'diff', '--cached', '--name-only', '-z', 'HEAD').decode().split('\0')
    require(set(filter(None, changed)) == expected_paths, 'result path set mismatch')
    git(source, 'diff', '--quiet')
    for path in expected_paths:
        mode = git(source, 'ls-files', '--stage', '--', path).split(b' ', 1)[0]
        require(mode in (b'100644', b'100755'), 'unsupported result file mode')
    tree = git(source, 'write-tree').decode().strip()
    require(tree == manifest.expected_tree, 'result tree mismatch')
    return {
        'upstream': {'repository': UPSTREAM, 'release': manifest.release, 'commit': manifest.commit},
        'patches': [{'id': patch.identifier, 'file': patch.filename, 'sha256': patch.digest}
                    for patch in manifest.patches],
        'result_tree': tree,
        'scope': 'patch application only; no build or installation',
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['validate', 'check'])
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    try:
        manifest = load_manifest(args.root)
        if args.command == 'check':
            with tempfile.TemporaryDirectory(prefix='fusion-kwin-check-') as directory:
                source = Path(directory)
                fetch_source(source, manifest)
                result = apply_patchset(source, manifest)
        else:
            result = {'commit': manifest.commit, 'patches': len(manifest.patches),
                      'scope': 'manifest and local patch digests only'}
        print(json.dumps(result, indent=2))
        return 0
    except PatchsetError as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
