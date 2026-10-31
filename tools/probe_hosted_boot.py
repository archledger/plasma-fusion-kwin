# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Boot one pinned public guest using an owned, isolated Docker volume."""
import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import time
import uuid
from pathlib import Path

from ci.hypervisor.boot_guest import (
    GUEST_SHA256,
    parse_guest_result,
    qemu_command,
    read_bounded,
)
from tools.probe_container_kvm import device_snapshot, require

LABEL = 'org.archledger.guest-boot'
RUN_SECONDS = 720
CLEANUP_SECONDS = 90
BOOT_SECONDS = 390
GUEST_BYTES = 583729152


def owned_container(info, name, token):
    require(info['Name'] == '/' + name and info['Config']['Labels'].get(LABEL) == token,
            'container ownership mismatch')
    require(re.fullmatch(r'[0-9a-f]{64}', info['Id']), 'invalid container ID')
    return info['Id']


def validate_volume(info, name, token):
    require(info['Name'] == name and info['Labels'].get(LABEL) == token, 'volume ownership mismatch')
    require(info['Driver'] == 'local' and info['Scope'] == 'local' and not info.get('Options'),
            'volume has unexpected backing or driver options')


def create_args(name, token, volume, image, gid):
    return ['create', '--name=' + name, '--label=' + LABEL + '=' + token, '--rm', '--pull=never',
            '--network=none', '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
            '--security-opt=apparmor=docker-default', '--cgroupns=private', '--user=1000:1000',
            '--group-add=' + str(gid), '--device=/dev/kvm:/dev/kvm:rw', '--pids-limit=256',
            '--memory=8g', '--memory-swap=8g', '--cpus=2',
            '--mount=type=volume,source=' + volume + ',destination=/work', image, '--probe']


def validate_runtime(info, name, token, volume, image, config, gid):
    cid = owned_container(info, name, token)
    host = info['HostConfig']
    require(info['Image'] == config and info['Config']['Image'] == image, 'unexpected image')
    require(info['Config']['User'] == '1000:1000', 'unexpected user')
    mounts = info['Mounts']
    require(len(mounts) == 1 and mounts[0]['Type'] == 'volume' and mounts[0]['Name'] == volume and
            mounts[0]['Destination'] == '/work' and mounts[0]['RW'] is True and
            mounts[0]['Driver'] == 'local' and not host.get('Binds'), 'unexpected mounts')
    require(host['Privileged'] is False and host['ReadonlyRootfs'] is True, 'unsafe privilege or root')
    require(host['NetworkMode'] == 'none' and host['IpcMode'] == 'private' and
            host['CgroupnsMode'] == 'private' and not host.get('PidMode') and
            not host.get('UTSMode'), 'unsafe namespace')
    require(host['Devices'] == [{'PathOnHost': '/dev/kvm', 'PathInContainer': '/dev/kvm',
                                 'CgroupPermissions': 'rw'}] and not host.get('DeviceRequests') and
            not host.get('DeviceCgroupRules'), 'unexpected device access')
    require(host['GroupAdd'] == [str(gid)], 'unexpected supplementary groups')
    require(not host.get('CapAdd') and [cap.upper() for cap in host['CapDrop']] == ['ALL'],
            'capabilities are not fully dropped')
    options = set(host['SecurityOpt'])
    require('apparmor=docker-default' in options and
            options & {'no-new-privileges', 'no-new-privileges:true'} and
            options <= {'apparmor=docker-default', 'no-new-privileges', 'no-new-privileges:true'} and
            info['AppArmorProfile'] == 'docker-default', 'confinement differs')
    require(host['AutoRemove'] is True and host['RestartPolicy']['Name'] == 'no', 'unsafe lifecycle')
    require(host['Memory'] == 8 * 1024**3 and host['MemorySwap'] == 8 * 1024**3 and
            host['PidsLimit'] == 256 and host['NanoCpus'] == 2 * 10**9, 'resource limits differ')
    return {'id': cid, 'user': info['Config']['User'], 'image_id': info['Image'],
            'mounts': mounts, 'host_config': host, 'apparmor': info['AppArmorProfile']}


def validate_boot_report(report):
    require(isinstance(report, dict) and report.get('success') is True and report.get('uid') == 1000,
            'boot controller did not pass')
    require(report.get('apparmor') == 'docker-default (enforce)' and report.get('qemu_exit') == 0 and
            report.get('vm_attempted') is True and report.get('guest_base_sha256') == GUEST_SHA256 and
            report.get('command') == qemu_command(Path('/work')), 'boot evidence differs')
    guest = report['guest']
    nonce = guest['nonce']
    require(isinstance(nonce, str) and re.fullmatch(r'[0-9a-f]{32}', nonce), 'invalid guest nonce')
    parse_guest_result('PF_BOOT_BEGIN ' + nonce + '\n' + json.dumps(guest) + '\nPF_BOOT_END ' + nonce, nonce)


def stage_image(base, archive):
    fd = os.open(base, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as image:
        info = os.fstat(image.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_size == GUEST_BYTES, 'unexpected base image file')
        require(hashlib.file_digest(image, 'sha256').hexdigest() == GUEST_SHA256, 'base image digest differs')
        image.seek(0)
        with tarfile.open(fileobj=archive, mode='w') as tar:
            root = tarfile.TarInfo('.')
            root.type, root.mode, root.uid, root.gid = tarfile.DIRTYPE, 0o700, 1000, 1000
            tar.addfile(root)
            member = tarfile.TarInfo('base.qcow2')
            member.size, member.mode, member.uid, member.gid = info.st_size, 0o400, 1000, 1000
            tar.addfile(member, image)
    archive.seek(0)


def absent(result, kind):
    return result.returncode != 0 and ('no such ' + kind) in result.stderr.casefold()


def run_probe(base, image, config, directory):
    report = {'success': False, 'image': image, 'expected_image_id': config,
              'scope': 'one offline public guest boot; no KWin build or tests',
              'budget_seconds': RUN_SECONDS, 'cleanup_reserve_seconds': CLEANUP_SECONDS}
    deadline = time.monotonic() + RUN_SECONDS
    token = uuid.uuid4().hex
    name, volume = 'pf-boot-' + token, 'pf-boot-data-' + token
    container_attempted = volume_attempted = False
    env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'LANG': 'C.UTF-8',
           'HOME': str(directory), 'DOCKER_CONFIG': str(directory)}
    (directory / 'config.json').write_text('{"auths":{}}\n')
    executable = shutil.which('docker') or 'docker'

    def call(args, timeout=30, cleanup=False, **kwargs):
        remaining = deadline - time.monotonic() - (0 if cleanup else CLEANUP_SECONDS)
        require(remaining > 0, 'operation deadline exhausted')
        return subprocess.run([executable, *args], env=env, text=True, timeout=min(timeout, remaining),
                              capture_output=True, check=False, **kwargs)

    def checked(args, timeout=30, **kwargs):
        result = call(args, timeout, **kwargs)
        require(result.returncode == 0, (result.stderr or result.stdout)[:4000])
        return result.stdout

    try:
        require(re.fullmatch(r'ghcr\.io/archledger/plasma-fusion-kwin-hypervisor@sha256:[0-9a-f]{64}', image)
                and re.fullmatch(r'sha256:[0-9a-f]{64}', config), 'image must be digest-pinned')
        before = device_snapshot()
        report['host_device_before'] = before
        security = json.loads(checked(['info', '--format={{json .SecurityOptions}}']))
        require('name=apparmor' in security and 'name=seccomp,profile=builtin' in security,
                'Docker default confinement is unavailable')
        report['docker_security'] = security
        require(shutil.disk_usage(directory).free >= 8 * 1024**3, 'insufficient temporary storage')
        with tempfile.TemporaryFile(dir=directory) as archive:
            stage_image(base, archive)
            checked(['pull', '--quiet', image], 300)
            require(checked(['image', 'inspect', '--format={{.Id}}', image]).strip() == config,
                    'pulled image config differs')
            volume_attempted = True
            checked(['volume', 'create', '--driver=local', '--label=' + LABEL + '=' + token, volume])
            validate_volume(json.loads(checked(['volume', 'inspect', volume]))[0], volume, token)
            container_attempted = True
            cid = checked(create_args(name, token, volume, image, before['gid'])).strip()
            info = json.loads(checked(['inspect', cid]))[0]
            report['container'] = validate_runtime(info, name, token, volume, image, config, before['gid'])
            checked(['cp', '--archive', '-', cid + ':/work'], 120, stdin=archive)
        require(deadline - time.monotonic() >= BOOT_SECONDS + CLEANUP_SECONDS,
                'insufficient remaining budget to boot and clean up')
        with (directory / 'controller.json').open('x') as output:
            result = subprocess.run([executable, 'start', '--attach', cid], env=env, text=True,
                                    stdout=output, stderr=subprocess.PIPE, timeout=BOOT_SECONDS, check=False)
        report['controller_exit'] = result.returncode
        report['controller_error'] = result.stderr[:4000]
        report['boot'] = json.loads(read_bounded(directory / 'controller.json', 16 * 1024**2))
        require(result.returncode == 0, 'boot controller failed')
        validate_boot_report(report['boot'])
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
                        owned = owned_container(info, name, token)
                        removed = call(['rm', '--force', owned], cleanup=True)
                    else:
                        validate_volume(info, volume, token)
                        removed = call(['volume', 'rm', volume], cleanup=True)
                    report[kind + '_remove_exit'] = removed.returncode
                    report[kind + '_remove_error'] = removed.stderr[:2000]
                    lookup = call(lookup_args, cleanup=True)
                gone = absent(lookup, 'object') or absent(lookup, kind)
                report[kind + '_removed'] = gone
                report[kind + '_cleanup_check_exit'] = lookup.returncode
                report[kind + '_cleanup_check_error'] = lookup.stderr[:2000]
                clean = clean and gone
            except (OSError, ValueError, KeyError, TypeError, IndexError, subprocess.SubprocessError) as error:
                report[kind + '_cleanup_error'] = str(error)[:1000]
                clean = False
        report['cleanup_confirmed'] = clean
        try:
            report['host_device_after'] = device_snapshot()
            report['device_metadata_unchanged'] = report['host_device_after'] == report.get('host_device_before')
        except (OSError, ValueError):
            report['device_metadata_unchanged'] = False
        report['success'] = report['success'] and clean and report['device_metadata_unchanged']
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--image', required=True)
    parser.add_argument('--image-id', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    with args.output.open('x') as output:
        with tempfile.TemporaryDirectory(prefix='pf-boot-', dir=args.output.parent) as directory:
            report = run_probe(args.base, args.image, args.image_id, Path(directory))
        json.dump(report, output, indent=2)
        output.write('\n')
    print(json.dumps(report, indent=2))
    return 0 if report['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
