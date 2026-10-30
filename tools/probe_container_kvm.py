# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Probe only KVM API access in a restricted helper, never boot a guest."""
import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

IMAGE = 'ghcr.io/archledger/plasma-fusion-kwin-builder@sha256:4ea17e89d08d6d921e50cb2dabc3011a7061b3cfd1a2b22b026ce8842d0042eb'
CONFIG = '5e5eb48e65c6e9cf3738963de73a09cb6acad10401362b95cee3dc25a6958aa5'
LABEL = 'org.archledger.kvm-probe'
CHILD = Path(__file__).with_name('kvm_api_probe.py').read_text(encoding='utf-8')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def create_args(name, token, gid):
    return ['create', '--name=' + name, '--label=' + LABEL + '=' + token,
            '--rm', '--pull=never', '--network=none', '--cap-drop=ALL',
            '--security-opt=no-new-privileges', '--security-opt=apparmor=docker-default',
            '--read-only', '--cgroupns=private',
            '--pids-limit=64', '--memory=128m', '--memory-swap=128m', '--cpus=1',
            '--user=1000:1000', '--group-add=' + str(gid),
            '--device=/dev/kvm:/dev/kvm:rw', '--entrypoint=/usr/bin/python3',
            IMAGE, '-I', '-B', '-c', CHILD]


def owned_identity(info, name, token):
    require(isinstance(info, dict), 'malformed container metadata')
    require(isinstance(info.get('Config'), dict) and isinstance(info['Config'].get('Labels'), dict),
            'malformed container identity')
    require(info['Name'] == '/' + name, 'container name changed')
    require(info['Config'].get('Labels', {}).get(LABEL) == token, 'container ownership mismatch')
    require(re.fullmatch(r'[0-9a-f]{64}', info['Id']), 'invalid container identity')
    return info['Id']


def validate_container(info, name, token):
    cid = owned_identity(info, name, token)
    host = info['HostConfig']
    require(info['Image'].removeprefix('sha256:') == CONFIG, 'wrong image config')
    require(info['Config']['User'] == '1000:1000', 'wrong user')
    require(not info['Mounts'] and not host.get('Binds'), 'unexpected mount')
    require(not host['Privileged'] and host['ReadonlyRootfs'], 'unsafe root filesystem or privilege')
    require(host['NetworkMode'] == 'none', 'network is not disabled')
    require(host.get('PidMode') != 'host' and host.get('IpcMode') == 'private' and
            host.get('CgroupnsMode') == 'private' and host.get('UTSMode') != 'host', 'shared host namespace')
    require(host['Devices'] == [{'PathOnHost': '/dev/kvm', 'PathInContainer': '/dev/kvm',
                                 'CgroupPermissions': 'rw'}], 'unexpected device access')
    require(not host.get('DeviceRequests') and not host.get('CapAdd'), 'unexpected extra access')
    require('ALL' in [cap.upper() for cap in host['CapDrop']], 'capabilities not dropped')
    require(any(value in {'no-new-privileges', 'no-new-privileges:true'}
                for value in host['SecurityOpt']), 'no-new-privileges missing')
    require('apparmor=docker-default' in host['SecurityOpt'], 'AppArmor profile not requested')
    require(set(host['SecurityOpt']) <= {'no-new-privileges', 'no-new-privileges:true',
                                         'apparmor=docker-default'}, 'unexpected security override')
    require(info.get('AppArmorProfile') == 'docker-default', 'default AppArmor confinement absent')
    require(host['AutoRemove'] and host['RestartPolicy']['Name'] == 'no', 'unsafe lifecycle')
    require(host['Memory'] == 128 * 1024**2 and host['MemorySwap'] == 128 * 1024**2 and
            host['PidsLimit'] == 64 and host['NanoCpus'] == 10**9, 'resource limits changed')
    return {'id': cid, 'user': info['Config']['User'], 'devices': host['Devices'],
            'mounts': info['Mounts'], 'network': host['NetworkMode'],
            'read_only': host['ReadonlyRootfs'], 'apparmor': info['AppArmorProfile']}


def parse_result(text):
    value = json.loads(text)
    require(isinstance(value, dict) and value.get('uid') == 1000 and value.get('api_version') == 12
            and value.get('vm_created') is False and 'error' not in value, 'KVM helper did not pass')
    return value


def device_snapshot():
    value = os.lstat('/dev/kvm')
    require(stat.S_ISCHR(value.st_mode), 'KVM path is not a direct character device')
    return {'uid': value.st_uid, 'gid': value.st_gid, 'mode': stat.S_IMODE(value.st_mode),
            'major': os.major(value.st_rdev), 'minor': os.minor(value.st_rdev)}


def run_probe(directory):
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'image': IMAGE,
              'scope': 'container KVM API access only; no VM or vCPU created',
              'payload_sha256': hashlib.sha256(CHILD.encode()).hexdigest(),
              'success': False, 'cleanup_confirmed': True}
    token = uuid.uuid4().hex
    name = 'pf-kvm-probe-' + token
    attempted = False
    env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'LANG': 'C.UTF-8',
           'HOME': str(directory), 'DOCKER_CONFIG': str(directory)}
    (directory / 'config.json').write_text('{"auths": {}}\n')
    executable = shutil.which('docker') or 'docker'

    def call(args, timeout=20):
        return subprocess.run([executable, *args], env=env, capture_output=True, text=True,
                              timeout=timeout, check=False)

    def checked(args, timeout=20):
        result = call(args, timeout)
        require(result.returncode == 0, (result.stderr or result.stdout)[:4096])
        return result.stdout

    try:
        before = device_snapshot()
        report['host_device_before'] = before
        report['docker_server'] = checked(['version', '--format={{.Server.Version}}']).strip()
        security = json.loads(checked(['info', '--format={{json .SecurityOptions}}']))
        require(isinstance(security, list) and 'name=apparmor' in security and
                'name=seccomp,profile=builtin' in security,
                'Docker AppArmor/default-seccomp support unavailable')
        report['docker_security'] = security
        checked(['pull', '--quiet', IMAGE], 300)
        attempted = True
        cid = checked(create_args(name, token, before['gid'])).strip()
        info = json.loads(checked(['inspect', cid]))[0]
        report['container'] = validate_container(info, name, token)
        result = call(['start', '--attach', cid], 30)
        report['helper_exit'] = result.returncode
        report['helper_output'] = result.stdout[:4096]
        report['helper_error'] = result.stderr[:4096]
        require(result.returncode == 0, 'helper returned failure')
        report['result'] = parse_result(result.stdout)
        require(report['result']['device'] == {'major': before['major'], 'minor': before['minor']},
                'mapped device identity changed')
        report['success'] = True
    except (OSError, ValueError, KeyError, IndexError, TypeError, subprocess.TimeoutExpired) as error:
        report['error'] = str(error)[:4096]
    finally:
        if attempted:
            try:
                lookup = call(['inspect', name])
                if lookup.returncode == 0:
                    info = json.loads(lookup.stdout)[0]
                    removed = call(['rm', '--force', owned_identity(info, name, token)])
                    report['cleanup_remove_exit'] = removed.returncode
                    lookup = call(['inspect', name])
                report['cleanup_confirmed'] = lookup.returncode != 0 and any(
                    text in lookup.stderr for text in ['No such object', 'No such container'])
                if not report['cleanup_confirmed']:
                    report['cleanup_error'] = lookup.stderr[:4096]
            except (OSError, ValueError, KeyError, IndexError, TypeError, subprocess.TimeoutExpired) as error:
                report['cleanup_confirmed'] = False
                report['cleanup_error'] = str(error)[:4096]
        try:
            after = device_snapshot()
            report['host_device_after'] = after
            report['device_metadata_unchanged'] = after == report.get('host_device_before')
        except (OSError, ValueError):
            report['device_metadata_unchanged'] = False
        report['success'] = report['success'] and report['cleanup_confirmed'] and report['device_metadata_unchanged']
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    with args.output.open('x') as output:
        with tempfile.TemporaryDirectory(prefix='kvm-probe-', dir=args.output.parent) as directory:
            report = run_probe(Path(directory))
        json.dump(report, output, indent=2)
        output.write('\n')
    print(json.dumps(report, indent=2))
    return 0 if report['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
