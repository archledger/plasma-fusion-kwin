# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run one pinned, offline boot probe inside the hypervisor container."""
import argparse
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

GUEST_SHA256 = '28680fe5b371a5a82ebf43a31926e086a168e59949d03969c5093e7071f90b7f'
GUEST_KERNEL = '6.19.10-300.fc44.x86_64'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_image_info(info):
    require(isinstance(info, dict), 'invalid image metadata')
    require(info.get('format') == 'qcow2', 'guest must be qcow2')
    require(info.get('virtual-size') == 5368709120, 'unexpected base disk size')
    require(not info.get('backing-filename'), 'base image must not have a backing file')


def qemu_command(root):
    return ['qemu-system-x86_64', '-no-user-config', '-nodefaults',
            '-machine', 'pc-q35-10.2', '-accel', 'kvm', '-cpu', 'host', '-smp', '2', '-m', '6144',
            '-display', 'none', '-vga', 'none', '-nic', 'none', '-monitor', 'none', '-no-reboot',
            '-bios', '/usr/share/seabios/bios-256k.bin',
            '-serial', 'file:' + str(root / 'serial.log'),
            '-drive', 'if=none,id=osdisk,format=qcow2,file=' + str(root / 'overlay.qcow2'),
            '-device', 'virtio-blk-pci,drive=osdisk,bootindex=1',
            '-device', 'ich9-ahci,id=seed-ahci',
            '-drive', 'if=none,id=seed,format=raw,readonly=on,file=' + str(root / 'seed.iso'),
            '-device', 'ide-cd,drive=seed,bus=seed-ahci.0',
            '-sandbox', 'on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny']


def parse_guest_result(serial, nonce):
    begin, end = 'PF_BOOT_BEGIN ' + nonce, 'PF_BOOT_END ' + nonce
    lines = serial.splitlines()
    require(lines.count(begin) == 1 and lines.count(end) == 1, 'guest result markers missing or repeated')
    first, last = lines.index(begin), lines.index(end)
    require(last == first + 2, 'guest result framing is invalid')
    body = lines[first + 1]
    value = json.loads(body)
    require(isinstance(value, dict), 'guest result is not an object')
    require(value.get('nonce') == nonce and value.get('success') is True and
            type(value.get('uid')) is int and value['uid'] == 0 and 'error' not in value,
            'guest did not report success')
    require(value.get('kernel') == GUEST_KERNEL and value.get('vgem_available') is True,
            'guest kernel or vgem does not match')
    require(value.get('vgem_sha256') == 'bc6aa5f12b7fb27d4c2184cffba85b989d9e1a385bb6b42c7cc831e427f65ff8',
            'guest module identity does not match')
    return value


def validate_confinement(status, profile):
    require(re.search(r'^NoNewPrivs:\s+1$', status, re.MULTILINE), 'no-new-privileges is absent')
    require(re.search(r'^CapEff:\s+0+$', status, re.MULTILINE), 'capabilities not fully dropped')
    require(re.search(r'^Seccomp:\s+2$', status, re.MULTILINE), 'seccomp is absent')
    require(profile == 'docker-default (enforce)', 'expected enforcing AppArmor profile is absent')


def seed_userdata(nonce):
    require(re.fullmatch(r'[0-9a-f]{32}', nonce), 'invalid boot nonce')
    payload = Path(__file__).with_name('guest_probe.py').read_text()
    return ('#cloud-config\nssh_pwauth: false\ndisable_root: true\nwrite_files:\n'
            '  - path: /etc/pf-boot-nonce\n    content: ' + json.dumps(nonce) + '\n'
            '  - path: /usr/local/sbin/pf-boot-probe.py\n    permissions: \'0755\'\n    content: |\n'
            + ''.join('      ' + line + '\n' for line in payload.splitlines())
            + 'runcmd:\n  - [python3, /usr/local/sbin/pf-boot-probe.py]\n')


def read_bounded(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        require(stat.S_ISREG(os.fstat(fd).st_mode), 'log is not regular')
        data = bytearray()
        while len(data) <= limit:
            chunk = os.read(fd, min(65536, limit + 1 - len(data)))
            if not chunk:
                return bytes(data).decode(errors='replace')
            data.extend(chunk)
        raise ValueError('log exceeds bound')
    finally:
        os.close(fd)


def boot(root):
    result = {'started': datetime.now(timezone.utc).isoformat(), 'success': False,
              'scope': 'one offline guest boot; no KWin build or tests', 'uid': os.geteuid()}
    try:
        require(os.geteuid() == 1000, 'hypervisor must run as UID1000')
        status = Path('/proc/self/status').read_text()
        profile = Path('/proc/self/attr/current').read_text().strip('\n\0')
        validate_confinement(status, profile)
        result['apparmor'] = profile
        fd = os.open('/dev/kvm', os.O_RDWR | os.O_CLOEXEC)
        try:
            require(fcntl.ioctl(fd, 0xAE00, 0) == 12, 'KVM API mismatch')
        finally:
            os.close(fd)
        base = root / 'base.qcow2'
        require(base.is_file() and not base.is_symlink(), 'missing regular guest image')
        with base.open('rb') as image:
            require(hashlib.file_digest(image, 'sha256').hexdigest() == GUEST_SHA256,
                    'guest image digest mismatch')
        info = json.loads(subprocess.check_output(['qemu-img', 'info', '--output=json', str(base)],
                                                text=True, timeout=15))
        validate_image_info(info)
        for name in ['overlay.qcow2', 'seed.iso', 'seed', 'serial.log', 'qemu.log']:
            require(not (root / name).exists(), 'workspace is not fresh: ' + name)
        subprocess.run(['qemu-img', 'create', '-f', 'qcow2', '-F', 'qcow2', '-b', str(base),
                        str(root / 'overlay.qcow2'), '32G'], check=True, capture_output=True, timeout=30)
        nonce = uuid.uuid4().hex
        seed = root / 'seed'
        seed.mkdir()
        (seed / 'meta-data').write_text('instance-id: pf-boot-' + nonce + '\nlocal-hostname: pf-boot\n')
        (seed / 'network-config').write_text('version: 2\nethernets: {}\n')
        (seed / 'user-data').write_text(seed_userdata(nonce))
        subprocess.run(['xorriso', '-as', 'mkisofs', '-volid', 'cidata', '-joliet', '-rock',
                        '-output', str(root / 'seed.iso'), str(seed)], check=True, capture_output=True, timeout=30)
        command = qemu_command(root)
        result['command'] = command
        result['guest_base_sha256'] = GUEST_SHA256
        result['vm_attempted'] = True
        with (root / 'qemu.log').open('xb') as log:
            run = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=300, check=False)
        result['qemu_exit'] = run.returncode
        result['qemu_log'] = read_bounded(root / 'qemu.log', 1024 * 1024)
        require(run.returncode == 0, 'QEMU did not exit cleanly')
        serial = read_bounded(root / 'serial.log', 1024 * 1024)
        result['guest'] = parse_guest_result(serial, nonce)
        result['success'] = True
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        result['error'] = str(error)[:2000]
        for key, name in [('qemu_log', 'qemu.log'), ('serial_tail', 'serial.log')]:
            try:
                content = read_bounded(root / name, 1024 * 1024)
                result[key] = content[-16000:]
                if name == 'serial.log':
                    result['serial_log'] = content
            except (OSError, ValueError):
                pass
    result['finished'] = datetime.now(timezone.utc).isoformat()
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe', action='store_true', help='explicitly run the bounded guest boot')
    args = parser.parse_args()
    if not args.probe:
        parser.print_help()
        raise SystemExit(0)
    report = boot(Path('/work'))
    print(json.dumps(report))
    raise SystemExit(0 if report['success'] else 1)
