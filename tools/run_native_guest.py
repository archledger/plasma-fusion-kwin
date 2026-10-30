# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run the trusted native qualification in a disposable, constrained guest."""
import argparse
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import tarfile
import tempfile
import time
import uuid
from pathlib import Path, PurePosixPath

from ci.hypervisor.boot_guest import GUEST_SHA256, read_bounded
from ci.native.controller import native_command
from ci.native.transport import decode
from ci.native.verify import verify_envelope
from tools.native_inputs import regular_digest, verify_image_directory
from tools.patchset import load_manifest, require
from tools.probe_container_kvm import device_snapshot
from tools.probe_hosted_boot import (
    LABEL,
    absent,
    create_args,
    owned_container,
    stage_image,
    validate_runtime,
    validate_volume,
)

IMAGE = 'ghcr.io/archledger/plasma-fusion-kwin-hypervisor@sha256:279f86086562a41840b597358c3bc302836e0dd03c627d2e22cc14dded4cab95'
CONFIG = 'sha256:0cb5e9e562afc5c68917647681fd88e9d6bc84d01287599cf33f6ee3b8525cbc'
BOOTSTRAP = 'import runpy,sys;sys.path.insert(0,"/work/native-input");runpy.run_module("ci.native.controller",run_name="__main__")'
PAYLOAD = (
    'ci/__init__.py', 'ci/native/__init__.py', 'ci/hypervisor/boot_guest.py',
    'ci/native/controller.py', 'ci/native/guest.py', 'ci/native/workload.py',
    'ci/native/transport.py', 'ci/native/results.py', 'ci/native/runtime_assets.py',
    'ci/native/render_probe.c', 'tools/__init__.py', 'tools/native_inputs.py', 'tools/patchset.py',
)
RUNTIME_HOST_FIELDS = (
    'Privileged', 'ReadonlyRootfs', 'NetworkMode', 'Binds', 'IpcMode', 'CgroupnsMode',
    'PidMode', 'UTSMode', 'Devices', 'DeviceRequests', 'DeviceCgroupRules', 'GroupAdd',
    'CapAdd', 'CapDrop', 'SecurityOpt', 'AutoRemove', 'RestartPolicy', 'Memory',
    'MemorySwap', 'PidsLimit', 'NanoCpus',
)


def runtime_evidence(info):
    """Retain enforced runtime fields, never container environment or unrelated labels."""
    config = info['Config']
    return {
        'Id': info['Id'], 'Name': info['Name'], 'Image': info['Image'],
        'Config': {**{key: config[key] for key in ['Image', 'User', 'Entrypoint', 'Cmd']},
                   'Labels': {LABEL: config['Labels'].get(LABEL)}},
        'Mounts': [{key: mount[key] for key in ['Type', 'Name', 'Destination', 'RW', 'Driver']}
                   for mount in info['Mounts']],
        'HostConfig': {key: info['HostConfig'].get(key) for key in RUNTIME_HOST_FIELDS},
        'AppArmorProfile': info['AppArmorProfile'],
    }


def container_args(name, token, volume, gid, mode):
    command = create_args(name, token, volume, IMAGE, gid)[:-2]
    return command + ['--entrypoint=/usr/bin/python3', IMAGE, '-I', '-B', '-c', BOOTSTRAP, mode]


def verify_controller(controller, mode, manifest_sha):
    require(controller.get('input_manifest_sha256') == manifest_sha and controller.get('mode') == mode,
            'executed input or mode differs')
    require(type(controller.get('uid')) is int and controller['uid'] == 1000 and
            controller.get('apparmor') == 'docker-default (enforce)', 'controller confinement differs')
    require(controller.get('command') == native_command(Path('/work')) and
            controller.get('base_sha256') == GUEST_SHA256 and
            type(controller.get('qemu_exit')) is int and controller['qemu_exit'] == 0,
            'guest execution evidence differs')
    require(controller.get('success') is True, 'native controller did not pass')


def output_tail(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return ''
    with os.fdopen(fd, 'rb') as output:
        info = os.fstat(output.fileno())
        require(stat.S_ISREG(info.st_mode), 'controller output is not regular')
        output.seek(max(0, info.st_size - 65536))
        return output.read(65536).decode(errors='replace')


def pack_inputs(repo, prepared, base, archive):
    inputs = json.loads((prepared / 'inputs.json').read_text())
    manifest = load_manifest(repo)
    require(inputs['source']['tree'] == manifest.expected_tree, 'prepared source tree differs')
    digest, size = regular_digest(prepared / 'patched-source.tar', 128 * 1024**2)
    require(digest == inputs['source']['sha256'] and size == inputs['source']['bytes'], 'prepared source differs')
    image = verify_image_directory(prepared / 'builder-image')
    require(image == inputs['builder'], 'prepared builder identity differs')
    packages = sorted(line for line in (repo / 'ci/builder/packages.lock').read_text().splitlines()
                      if line and not line.startswith('#'))
    stage_image(base, archive)
    sums, directories = [], set()
    with tarfile.open(fileobj=archive, mode='a') as tar:
        def directory(path):
            for parent in reversed([path, *path.parents]):
                name = parent.as_posix()
                if name == '.' or name in directories:
                    continue
                item = tarfile.TarInfo(name)
                item.type, item.mode, item.uid, item.gid = tarfile.DIRTYPE, 0o755, 1000, 1000
                tar.addfile(item)
                directories.add(name)

        def add(name, source=None, data=None):
            target = PurePosixPath('native-input') / name
            directory(target.parent)
            if source is not None:
                digest, size = regular_digest(source, 2 * 1024**3)
                stream = source.open('rb')
            else:
                digest, size = hashlib.sha256(data).hexdigest(), len(data)
                stream = io.BytesIO(data)
            item = tarfile.TarInfo(target.as_posix())
            item.size, item.mode, item.uid, item.gid = size, 0o444, 1000, 1000
            with stream:
                tar.addfile(item, stream)
            if name != 'SHA256SUMS':
                sums.append(digest + '  ' + name + '\n')

        for name in PAYLOAD:
            add(name, source=repo / name)
        for name in image['files']:
            add('builder-image/' + name, source=prepared / 'builder-image' / name)
        for name in ['patched-source.tar', 'inputs.json']:
            add(name, source=prepared / name)
        add('builder-packages.txt', data=('\n'.join(packages) + '\n').encode())
        checksums = ''.join(sums).encode()
        add('SHA256SUMS', data=checksums)
    archive.seek(0)
    return inputs, packages, hashlib.sha256(checksums).hexdigest()


def run_guest(repo, prepared, base, output, mode, budget):
    require(mode in {'render', 'native'} and 0 < budget <= 4200, 'invalid native execution budget')
    reserve, allowance = (120, 3750) if mode == 'native' else (90, 750)
    deadline = time.monotonic() + budget
    report = {'success': False, 'mode': mode, 'image': IMAGE, 'image_id': CONFIG,
              'budget_seconds': budget, 'cleanup_reserve_seconds': reserve}
    token = uuid.uuid4().hex
    name, volume = 'pf-native-' + token, 'pf-native-data-' + token
    volume_attempted = container_attempted = False
    with tempfile.TemporaryDirectory(prefix='native-driver-', dir=output) as temporary:
        temporary = Path(temporary)
        (temporary / 'config.json').write_text('{"auths":{}}\n')
        env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'LANG': 'C.UTF-8',
               'HOME': str(temporary), 'DOCKER_CONFIG': str(temporary)}

        def call(args, timeout=30, cleanup=False, **kwargs):
            remaining = deadline - time.monotonic() - (0 if cleanup else reserve)
            require(remaining > 0, 'native execution deadline exhausted')
            return subprocess.run(['docker', *args], env=env, text=True, capture_output=True,
                                  timeout=min(timeout, remaining), check=False, **kwargs)

        def checked(args, timeout=30, **kwargs):
            result = call(args, timeout, **kwargs)
            require(result.returncode == 0, (result.stderr or result.stdout)[:4000])
            return result.stdout

        try:
            before = device_snapshot()
            report['host_device_before'] = before
            security = json.loads(checked(['info', '--format={{json .SecurityOptions}}']))
            require('name=apparmor' in security and 'name=seccomp,profile=builtin' in security,
                    'Docker confinement unavailable')
            report['engine_security_options'] = security
            require(shutil.disk_usage(output).free >= 12 * 1024**3, 'insufficient temporary storage')
            with tempfile.TemporaryFile(dir=temporary) as archive:
                inputs, packages, manifest_sha = pack_inputs(repo, prepared, base, archive)
                report['input_manifest_sha256'] = manifest_sha
                report['source'] = inputs['source']
                checked(['pull', '--quiet', IMAGE], 180)
                require(checked(['image', 'inspect', '--format={{.Id}}', IMAGE]).strip() == CONFIG,
                        'hypervisor image configuration differs')
                volume_attempted = True
                checked(['volume', 'create', '--driver=local', '--label=' + LABEL + '=' + token, volume])
                volume_info = json.loads(checked(['volume', 'inspect', volume]))[0]
                validate_volume(volume_info, volume, token)
                report['volume'] = {**{key: volume_info[key] for key in ['Name', 'Driver', 'Scope']},
                                    'Options': volume_info.get('Options'),
                                    'Labels': {LABEL: volume_info['Labels'].get(LABEL)}}
                command = container_args(name, token, volume, before['gid'], mode)
                container_attempted = True
                cid = checked(command).strip()
                info = json.loads(checked(['inspect', cid]))[0]
                report['runtime'] = runtime_evidence(info)
                report['container'] = validate_runtime(report['runtime'], name, token, volume,
                                                       IMAGE, CONFIG, before['gid'])
                require(info['Config']['Entrypoint'] == ['/usr/bin/python3'] and
                        info['Config']['Cmd'] == ['-I', '-B', '-c', BOOTSTRAP, mode], 'unexpected controller command')
                checked(['cp', '--archive', '-', cid + ':/work'], 180, stdin=archive)
            require(deadline - time.monotonic() >= allowance + reserve, 'insufficient time for guest and cleanup')
            try:
                with (temporary / 'controller.json').open('x') as log:
                    result = subprocess.run(['docker', 'start', '--attach', cid], env=env, text=True,
                                            stdout=log, stderr=subprocess.PIPE, timeout=allowance, check=False)
                report['controller_stderr'] = result.stderr[-8192:]
            except subprocess.TimeoutExpired as error:
                stderr = error.stderr or ''
                report['controller_stderr'] = (stderr.decode(errors='replace')
                                               if isinstance(stderr, bytes) else stderr)[-8192:]
                raise
            finally:
                report['controller_output_tail'] = output_tail(temporary / 'controller.json')
            report['controller_exit'] = result.returncode
            report['controller'] = json.loads(read_bounded(temporary / 'controller.json', 64 * 1024**2))
            controller = report['controller']
            require(controller.get('input_manifest_sha256') == manifest_sha, 'executed input manifest differs')
            if 'wire' in controller:
                envelope = decode(controller['wire'], controller['nonce'])
                artifacts = output / 'artifacts'
                artifacts.mkdir()
                for filename, content in envelope['files'].items():
                    (artifacts / filename).write_text(content)
                report['guest_metadata'] = envelope['metadata']
                report['verification'] = verify_envelope(envelope, mode, inputs, packages)
            verify_controller(controller, mode, manifest_sha)
            require(result.returncode == 0 and report.get('verification') is not None, 'native guest did not qualify')
            report['success'] = True
        except (OSError, ValueError, KeyError, TypeError, IndexError, subprocess.SubprocessError) as error:
            report['error'] = str(error)[:4000]
        finally:
            clean = True
            for attempted, kind, target in [(container_attempted, 'container', name),
                                             (volume_attempted, 'volume', volume)]:
                if not attempted:
                    continue
                try:
                    lookup_args = ['inspect', target] if kind == 'container' else ['volume', 'inspect', target]
                    lookup = call(lookup_args, cleanup=True)
                    if lookup.returncode == 0:
                        info = json.loads(lookup.stdout)[0]
                        if kind == 'container':
                            removed = call(['rm', '--force', owned_container(info, name, token)], cleanup=True)
                        else:
                            validate_volume(info, volume, token)
                            removed = call(['volume', 'rm', volume], cleanup=True)
                        report[kind + '_remove_exit'] = removed.returncode
                        report[kind + '_remove_error'] = removed.stderr[:2000]
                        lookup = call(lookup_args, cleanup=True)
                    gone = absent(lookup, kind) or absent(lookup, 'object')
                    report[kind + '_removed'] = gone
                    report[kind + '_cleanup_error'] = lookup.stderr[:2000]
                    clean = clean and gone
                except (OSError, ValueError, KeyError, TypeError, IndexError, subprocess.SubprocessError) as error:
                    report[kind + '_cleanup_error'] = str(error)[:2000]
                    clean = False
            report['cleanup_confirmed'] = clean
            try:
                report['host_device_after'] = device_snapshot()
                report['device_metadata_unchanged'] = report['host_device_after'] == report.get('host_device_before')
            except (OSError, ValueError):
                report['device_metadata_unchanged'] = False
            report['success'] = report['success'] and clean and report['device_metadata_unchanged']
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--mode', choices=['render', 'native'], required=True)
    parser.add_argument('--budget', type=int, required=True)
    args = parser.parse_args()
    args.output.mkdir(mode=0o700)
    report = run_guest(Path(__file__).resolve().parents[1], args.inputs, args.base,
                       args.output, args.mode, args.budget)
    (args.output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'success': report['success'], 'mode': report['mode'], 'error': report.get('error')}))
    raise SystemExit(0 if report['success'] else 1)
