# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Trusted QEMU controller for the dedicated native-qualification branch."""
import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from ci.hypervisor.boot_guest import (
    GUEST_SHA256,
    qemu_command,
    read_bounded,
    require,
    validate_confinement,
    validate_image_info,
)
from ci.native.transport import MAX_ENCODED_BYTES, decode
from tools.patchset import object_pairs


def validate_invocation(value):
    require(type(value) is dict and set(value) == {'nonce', 'qualification_id'}, 'invalid invocation fields')
    for field, length in [('nonce', 32), ('qualification_id', 64)]:
        item = value[field]
        if field == 'qualification_id' and item is None:
            continue
        require(type(item) is str and re.fullmatch(r'[0-9a-f]{' + str(length) + '}', item)
                and item != '0' * length, 'invalid invocation identity')
    return value


def load_invocation(root):
    return validate_invocation(json.loads(read_bounded(root / 'native-input/invocation.json', 1024),
                                          object_pairs_hook=object_pairs))


def native_command(root):
    return qemu_command(root) + [
        '-drive', 'if=none,id=nativeinput,format=raw,readonly=on,file=' + str(root / 'native-input.iso'),
        '-device', 'ide-cd,drive=nativeinput,bus=seed-ahci.1',
    ]


def seed_data(nonce, mode, manifest_sha):
    require(len(nonce) == 32 and all(c in '0123456789abcdef' for c in nonce), 'invalid nonce')
    require(mode in {'render', 'native'} and len(manifest_sha) == 64 and
            all(c in '0123456789abcdef' for c in manifest_sha), 'invalid bootstrap input')
    script = ('#!/bin/bash\nset -euo pipefail\n'
              'trap \'status=$?; set +e; if [[ $status != 0 ]]; then '
              'printf "\\nPF_NATIVE_BOOTSTRAP_FAILED status=%s\\n" "$status" >/dev/ttyS0; fi; '
              'systemctl poweroff\' EXIT\n'
              'mkdir -p /mnt/native-input\nmount -o ro /dev/disk/by-label/PF_NATIVE /mnt/native-input\ncd /mnt/native-input\n'
              f'echo "{manifest_sha}  SHA256SUMS" | sha256sum --check\nsha256sum --check SHA256SUMS\n'
              f'python3 -m ci.native.guest {nonce} {mode}\n')
    return ('#cloud-config\nssh_pwauth: false\ndisable_root: true\nwrite_files:\n'
            '  - path: /usr/local/sbin/pf-native\n    permissions: \'0755\'\n    content: |\n' +
            ''.join('      ' + line + '\n' for line in script.splitlines()) +
            'runcmd:\n  - [bash, /usr/local/sbin/pf-native]\n')


def main(mode, root=Path('/work')):
    report = {'success': False, 'mode': mode, 'uid': os.geteuid()}
    try:
        require(os.geteuid() == 1000, 'controller must run as UID1000')
        invocation = load_invocation(root)
        nonce = invocation['nonce']
        report.update(invocation)
        profile = Path('/proc/self/attr/current').read_text().strip('\n\0')
        validate_confinement(Path('/proc/self/status').read_text(), profile)
        report['apparmor'] = profile
        fd = os.open('/dev/kvm', os.O_RDWR | os.O_CLOEXEC)
        try:
            require(fcntl.ioctl(fd, 0xAE00, 0) == 12, 'KVM API mismatch')
        finally:
            os.close(fd)
        with (root / 'base.qcow2').open('rb') as image:
            require(hashlib.file_digest(image, 'sha256').hexdigest() == GUEST_SHA256, 'base image differs')
        info = json.loads(subprocess.check_output(['qemu-img', 'info', '--output=json', str(root / 'base.qcow2')],
                                                text=True, timeout=15))
        validate_image_info(info)
        for name in ['overlay.qcow2', 'seed', 'seed.iso', 'native-input.iso', 'serial.log', 'qemu.log']:
            require(not (root / name).exists(), 'workspace is not fresh')
        subprocess.run(['qemu-img', 'create', '-f', 'qcow2', '-F', 'qcow2', '-b', str(root / 'base.qcow2'),
                        str(root / 'overlay.qcow2'), '32G'], check=True, capture_output=True, timeout=30)
        inputs = root / 'native-input'
        manifest_sha = hashlib.sha256((inputs / 'SHA256SUMS').read_bytes()).hexdigest()
        report['input_manifest_sha256'] = manifest_sha
        seed = root / 'seed'
        seed.mkdir()
        (seed / 'meta-data').write_text('instance-id: pf-native-' + nonce + '\nlocal-hostname: pf-native\n')
        (seed / 'network-config').write_text('version: 2\nethernets: {}\n')
        (seed / 'user-data').write_text(seed_data(nonce, mode, manifest_sha))
        for directory, label, output in [(seed, 'cidata', root / 'seed.iso'),
                                         (inputs, 'PF_NATIVE', root / 'native-input.iso')]:
            subprocess.run(['xorriso', '-as', 'mkisofs', '-volid', label, '-joliet', '-rock',
                            '-output', str(output), str(directory)], check=True, capture_output=True, timeout=120)
        command = native_command(root)
        report['command'] = command
        report['base_sha256'] = GUEST_SHA256
        with (root / 'qemu.log').open('xb') as log:
            process = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                                     timeout=3600 if mode == 'native' else 600, check=False)
        report['qemu_exit'] = process.returncode
        serial = read_bounded(root / 'serial.log', MAX_ENCODED_BYTES + 8 * 1024**2)
        envelope = decode(serial, nonce)
        lines = serial.splitlines()
        first = lines.index('PF_NATIVE_BEGIN ' + nonce)
        last = lines.index('PF_NATIVE_END ' + nonce)
        report['wire'] = '\n'.join(lines[first:last + 1]) + '\n'
        require(process.returncode == 0 and envelope['metadata'].get('status') == '0', 'native guest failed')
        report['success'] = True
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        report['error'] = str(error)[:4000]
        try:
            report['serial_tail'] = read_bounded(root / 'serial.log', MAX_ENCODED_BYTES + 8 * 1024**2)[-64000:]
        except (OSError, ValueError):
            pass
    try:
        report['qemu_log'] = read_bounded(root / 'qemu.log', 1024**2)
    except (OSError, ValueError):
        pass
    print(json.dumps(report))
    return 0 if report['success'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['render', 'native'])
    args = parser.parse_args()
    raise SystemExit(main(args.mode))
