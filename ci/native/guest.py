# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Trusted guest-only setup: isolate the builder and export bounded text."""
import argparse
import grp
import hashlib
import json
import os
import re
import stat
import subprocess
import time
from pathlib import Path

from ci.native.transport import MAX_FILE_BYTES, encode, gather
from tools.native_inputs import BUILDER_CONFIG, verify_image_directory
from tools.patchset import require

KERNEL = '6.19.10-300.fc44.x86_64'
VGEM = 'bc6aa5f12b7fb27d4c2184cffba85b989d9e1a385bb6b42c7cc831e427f65ff8'


def verify_container(info, node, gid):
    host, config = info['HostConfig'], info['Config']
    require(config['User'] == '1000:1000' and config['WorkingDir'] == '/tmp', 'unexpected builder identity')
    require(not info['Mounts'] and not host['Privileged'], 'unexpected builder mount or privilege')
    require(host['NetworkMode'] == 'none' and host['IpcMode'] == 'private' and
            host['PidMode'] == 'private' and host['UTSMode'] == 'private', 'unexpected builder namespace')
    require(not host.get('CapAdd') and 'no-new-privileges' in host['SecurityOpt'], 'unsafe builder capabilities')
    devices = host['Devices']
    # Podman 5.8.1 inspection leaves CgroupPermissions empty. The actual access
    # policy is checked separately in the generated OCI specification.
    require(len(devices) == 1 and devices[0]['PathOnHost'] == node and
            devices[0]['PathInContainer'] == node and devices[0]['CgroupPermissions'] in {'', 'rw'},
            'unexpected builder device mapping')
    require(host['GroupAdd'] == [str(gid)], 'unexpected builder group')
    require(host['Memory'] == 5 * 1024**3 and host['MemorySwap'] == 5 * 1024**3 and
            host['CpuPeriod'] == 100000 and host['CpuQuota'] == 200000 and host['PidsLimit'] == 1024,
            'unexpected builder resource limits')


def device_identity(node):
    value = os.stat(node)
    require(stat.S_ISCHR(value.st_mode), 'guest DRM is not a character device')
    return (os.major(value.st_rdev), os.minor(value.st_rdev), value.st_uid,
            value.st_gid, stat.S_IMODE(value.st_mode))


def ready_device(checked):
    checked(['udevadm', 'settle', '--timeout=10'], 15)
    nodes = [entry for entry in Path('/sys/class/drm').glob('card[0-9]*')
             if re.fullmatch(r'card[0-9]+', entry.name) and
             re.search(r'/vgem(?:\.[^/]*)?$', str((entry / 'device').resolve()))]
    require(len(nodes) == 1, 'expected exactly one guest vgem node')
    node = '/dev/dri/' + nodes[0].name
    identity = device_identity(node)
    require(identity[0] == 226 and identity[2:] == (0, grp.getgrnam('video').gr_gid, 0o660),
            'guest DRM permissions are not settled')
    return node, identity


def recheck_device(node, expected):
    require(device_identity(node) == expected, 'guest DRM identity changed before start')


def verify_oci(spec, node, identity, mode):
    process, linux = spec['process'], spec['linux']
    require(process['user']['uid'] == 1000 and process['user']['gid'] == 1000 and
            identity[3] in process['user'].get('additionalGids', []) and
            set(process['user']['additionalGids']) <= {1000, identity[3]}, 'unexpected OCI user/groups')
    capabilities = process.get('capabilities', {})
    require(process.get('noNewPrivileges') is True and isinstance(capabilities, dict) and
            all(not value for value in capabilities.values()), 'unsafe OCI privileges')
    require(process['args'] == ['/usr/bin/python3', '-m', 'ci.native.workload', mode], 'unexpected OCI command')
    require(linux.get('seccomp', {}).get('defaultAction') == 'SCMP_ACT_ERRNO', 'missing OCI seccomp filter')
    namespaces = {entry['type']: entry for entry in linux['namespaces']}
    require(all(kind in namespaces and not namespaces[kind].get('path')
                for kind in ['pid', 'network', 'ipc', 'uts', 'mount', 'cgroup']), 'shared OCI namespace')
    devices = linux.get('devices', [])
    require(len(devices) == 1 and devices[0]['path'] == node and devices[0]['type'] == 'c' and
            (devices[0]['major'], devices[0]['minor']) == identity[:2], 'unexpected OCI device')
    rules = linux['resources']['devices']
    require(isinstance(rules, list), 'missing OCI device policy')
    require(any(rule.get('allow') is True and rule.get('type') == 'c' and
                (rule.get('major'), rule.get('minor')) == identity[:2] and rule.get('access') == 'rw'
                for rule in rules), 'OCI DRM access is not read/write only')
    standard = {(1, 3), (1, 5), (1, 7), (1, 8), (1, 9), (5, 0), (5, 1), (5, 2), (136, None)}
    for rule in rules:
        if rule.get('allow') and (rule.get('major'), rule.get('minor')) == identity[:2]:
            require(rule.get('type') == 'c' and rule.get('access') == 'rw', 'excess OCI DRM permission')
        if rule.get('allow') and set(rule.get('access', '')) & {'r', 'w'}:
            require(rule.get('type') == 'c' and (rule.get('major'), rule.get('minor')) in
                    standard | {identity[:2]}, 'unexpected OCI device permission')


def main(nonce, mode):
    require(re.fullmatch(r'[0-9a-f]{32}', nonce), 'invalid guest nonce')
    root = Path('/var/tmp/pf-native-' + nonce)
    root.mkdir(mode=0o700)
    stage = root / 'guest-stage.log'
    artifact_root = root / 'artifacts'
    metadata = {'mode': mode, 'status': '1', 'kernel': os.uname().release, 'container_removed': 'true'}
    cid, copied, oci_proof, container_proof = None, False, None, None
    deadline = time.monotonic() + (3550 if mode == 'native' else 540)
    reserve = 150 if mode == 'native' else 120
    with stage.open('x') as log:
        def checked(command, timeout=60, cleanup=False):
            remaining = deadline - time.monotonic() - (0 if cleanup else reserve)
            require(remaining > 0, 'guest deadline exhausted')
            result = subprocess.run(command, capture_output=True, text=True, timeout=min(timeout, remaining), check=False)
            log.write(result.stdout + result.stderr)
            log.flush()
            require(result.returncode == 0, 'guest command failed: ' + command[0])
            return result.stdout

        try:
            checked(['systemctl', 'stop', 'serial-getty@ttyS0.service'])
            require(os.geteuid() == 0 and metadata['kernel'] == KERNEL, 'unexpected guest identity')
            require(set(os.listdir('/sys/class/net')) == {'lo'}, 'guest has a network interface')
            module = Path(checked(['modinfo', '-F', 'filename', 'vgem']).strip())
            require(hashlib.sha256(module.read_bytes()).hexdigest() == VGEM, 'guest module identity differs')
            metadata['vgem_sha256'] = VGEM
            checked(['modprobe', 'vgem'])
            node, identity = ready_device(checked)
            gid = identity[3]
            metadata.update(drm_node=node, drm_major=str(identity[0]), drm_minor=str(identity[1]), drm_gid=str(gid))
            source = Path('/mnt/native-input')
            image = verify_image_directory(source / 'builder-image')
            metadata.update(builder_config=image['config'], builder_manifest=image['manifest'])
            imported = checked(['podman', 'pull', '--quiet', 'dir:' + str(source / 'builder-image')], 120).strip()
            require(imported.removeprefix('sha256:') == BUILDER_CONFIG, 'imported image identity differs')
            inventory = checked(['podman', 'run', '--rm', '--network=none', '--cap-drop=all',
                                 '--security-opt=no-new-privileges', '--entrypoint=/usr/bin/rpm', imported,
                                 '-qa', '--qf', '%{NAME}\t%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n'])
            packages = sorted(line.split('\t')[1] for line in inventory.splitlines()
                              if line.split('\t')[0] != 'gpg-pubkey')
            require(packages == (source / 'builder-packages.txt').read_text().splitlines(),
                    'builder RPM inventory differs')
            cid = checked(['podman', 'create', '--pull=never', '--network=none', '--cap-drop=all',
                           '--security-opt=no-new-privileges', '--user=1000:1000', '--workdir=/tmp',
                           '--ipc=private', '--pid=private', '--uts=private', '--cgroupns=private',
                           '--group-add=' + str(gid), '--device=' + node + ':' + node + ':rw',
                           '--memory=5g', '--memory-swap=5g', '--cpus=2', '--pids-limit=1024',
                           '--env=EXPECTED_DRM_NODE=' + node, '--entrypoint=/usr/bin/python3', imported,
                           '-m', 'ci.native.workload', mode]).strip()
            require(re.fullmatch(r'[0-9a-f]{64}', cid), 'invalid builder container ID')
            info = json.loads(checked(['podman', 'inspect', cid]))[0]
            container_proof = {'Config': {key: info['Config'].get(key) for key in ['User', 'WorkingDir']},
                               'HostConfig': info['HostConfig'], 'Mounts': info['Mounts']}
            verify_container(info, node, gid)
            for name in ['ci', 'patched-source.tar', 'inputs.json']:
                checked(['podman', 'cp', str(source / name), cid + ':/tmp/'], 120)
            checked(['podman', 'cp', str(source / 'ci/native/render_probe.c'), cid + ':/tmp/render_probe.c'])
            checked(['podman', 'init', cid])
            info = json.loads(checked(['podman', 'inspect', cid]))[0]
            config_path = Path(info['OCIConfigPath'])
            require(config_path.name == 'config.json' and cid in config_path.parts,
                    'unexpected OCI configuration path')
            spec = json.loads(config_path.read_text())
            oci_proof = {'process': {key: spec['process'].get(key) for key in
                                    ['user', 'args', 'capabilities', 'noNewPrivileges']},
                         'linux': {key: spec['linux'].get(key) for key in
                                   ['devices', 'resources', 'namespaces', 'seccomp']}}
            verify_oci(spec, node, identity, mode)
            recheck_device(node, identity)
            allowance = 3100 if mode == 'native' else 240
            require(deadline - time.monotonic() >= allowance + reserve, 'insufficient guest cleanup budget')
            process = subprocess.run(['podman', 'start', '--attach', cid], stdout=log, stderr=subprocess.STDOUT,
                                     timeout=allowance, check=False)
            metadata['status'] = str(process.returncode)
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
            metadata['error'] = str(error)[:4000]
            metadata['status'] = '1'
        finally:
            if cid:
                try:
                    checked(['podman', 'stop', '--time=10', cid], 30, cleanup=True)
                    checked(['podman', 'cp', cid + ':/tmp/results', str(artifact_root)], 60, cleanup=True)
                    copied = True
                except (OSError, ValueError, subprocess.SubprocessError) as error:
                    metadata['error'] = str(error)[:4000]
                    metadata['status'] = '1'
                try:
                    checked(['podman', 'rm', '--force', cid], 30, cleanup=True)
                    remaining = deadline - time.monotonic()
                    require(remaining > 0, 'guest cleanup deadline exhausted')
                    absent = subprocess.run(['podman', 'container', 'exists', cid], capture_output=True,
                                            timeout=min(20, remaining), check=False)
                    require(absent.returncode == 1, 'builder removal not confirmed')
                except (OSError, ValueError, subprocess.SubprocessError) as error:
                    metadata.update(status='1', container_removed='false', error=str(error)[:4000])
    try:
        files = gather(artifact_root) if copied else {}
        data = stage.read_bytes()
        require(len(data) <= MAX_FILE_BYTES, 'guest stage log exceeds bound')
        files['guest-stage.log'] = data.decode('utf-8')
        if oci_proof is not None:
            files['builder-oci.json'] = json.dumps(oci_proof)
        if container_proof is not None:
            files['builder-container.json'] = json.dumps(container_proof)
        files['collector-status.txt'] = 'ok\n' if copied else 'failed\n'
    except (OSError, ValueError) as error:
        metadata.update(status='1', error=str(error)[:4000])
        files = {'collector-status.txt': 'failed\n'}
    try:
        with Path('/dev/ttyS0').open('w') as serial:
            serial.write(encode(nonce, files, metadata))
    finally:
        subprocess.run(['systemctl', 'poweroff'], check=False, timeout=10)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('nonce')
    parser.add_argument('mode', choices=['render', 'native'])
    args = parser.parse_args()
    main(args.nonce, args.mode)
