# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Read-only prerequisites probe; never creates a VM or installs host software."""
import argparse
import fcntl
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REQUIRED_TOOLS = ('qemu-system-x86_64', 'qemu-img', 'virsh', 'virt-xml-validate', 'xorriso', 'podman')
KVM_GET_API_VERSION = 0xAE00  # Linux UAPI: _IO(KVMIO, 0x00), KVMIO = 0xAE.


def blockers(snapshot):
    result = []
    if snapshot['architecture'] != 'x86_64':
        result.append('x86_64 is required')
    if snapshot['kvm'].get('api_version') != 12:
        result.append('KVM API 12 is not accessible')
    for name in REQUIRED_TOOLS:
        if not snapshot['tools'].get(name):
            result.append('missing tool: ' + name)
    if not snapshot['python_libvirt']:
        result.append('Python libvirt bindings are unavailable')
    if not snapshot['q35_10_2']:
        result.append('verified pc-q35-10.2 machine is unavailable')
    if snapshot['memory_total_bytes'] < 8 * 1024**3:
        result.append('less than 8 GiB total memory')
    if snapshot['free_disk_bytes'] < 12 * 1024**3:
        result.append('less than 12 GiB free workspace storage')
    return result


def probe_libvirt():
    try:
        result = subprocess.run([sys.executable, '-I', '-c', 'import libvirt; print(libvirt.getVersion())'],
                                capture_output=True, text=True, timeout=10, check=False)
        return {'usable': result.returncode == 0, 'version': result.stdout.strip()[:1024],
                'error': result.stderr.strip()[:1024] if result.returncode else None}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {'usable': False, 'error': str(error)[:1024]}


def probe_qemu(path):
    try:
        version = subprocess.run([path, '--version'], capture_output=True, text=True,
                                 timeout=10, check=False)
        if version.returncode != 0:
            return {'q35_10_2': False, 'error': version.stderr.strip()[:1024]}
        result = subprocess.run([path, '-machine', 'help'], capture_output=True, text=True,
                                timeout=10, check=False)
        return {'version': version.stdout.splitlines()[0] if version.stdout else None,
                'q35_10_2': result.returncode == 0 and bool(re.search(
                    r'^pc-q35-10\.2\s', result.stdout, re.MULTILINE)),
                'error': result.stderr.strip()[:1024] if result.returncode else None}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {'q35_10_2': False, 'error': str(error)[:1024]}


def collect():
    tools = {name: shutil.which(name) for name in REQUIRED_TOOLS}
    kvm = {}
    try:
        fd = os.open('/dev/kvm', os.O_RDWR | os.O_CLOEXEC)
        try:
            kvm['api_version'] = fcntl.ioctl(fd, KVM_GET_API_VERSION, 0)
        finally:
            os.close(fd)
    except OSError as error:
        kvm = {'errno': error.errno, 'error': os.strerror(error.errno)}
    qemu = probe_qemu(tools['qemu-system-x86_64']) if tools['qemu-system-x86_64'] else {'q35_10_2': False}
    libvirt = probe_libvirt()
    memory = re.search(r'^MemTotal:\s+(\d+)\s+kB$', Path('/proc/meminfo').read_text(), re.MULTILINE)
    return {
        'checked_at': datetime.now(timezone.utc).isoformat(),
        'scope': 'read-only prerequisites; no VM, native tests or installation',
        'architecture': platform.machine(), 'kernel': platform.release(),
        'runner_image': {name: os.environ.get(name) for name in ['ImageOS', 'ImageVersion', 'RUNNER_OS', 'RUNNER_ARCH']},
        'effective_uid': os.geteuid(), 'kvm': kvm, 'tools': tools,
        'qemu': qemu, 'q35_10_2': qemu['q35_10_2'],
        'python_libvirt': libvirt['usable'], 'libvirt': libvirt,
        'memory_total_bytes': int(memory.group(1)) * 1024 if memory else 0,
        'free_disk_bytes': shutil.disk_usage(Path.cwd()).free,
        'cpu_count': os.cpu_count(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    snapshot = collect()
    snapshot['blockers'] = blockers(snapshot)
    snapshot['local_recipe_prerequisites_ready'] = not snapshot['blockers']
    with args.output.open('x') as output:
        json.dump(snapshot, output, indent=2)
        output.write('\n')
    print(json.dumps(snapshot, indent=2))
    return 1 if snapshot['blockers'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
