# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Trusted boot-only payload; it does not install packages or run KWin."""
import hashlib
import json
import os
import subprocess
from pathlib import Path


def main():
    nonce = Path('/etc/pf-boot-nonce').read_text().strip()
    result = {'nonce': nonce, 'success': False, 'uid': os.geteuid()}
    try:
        subprocess.run(['systemctl', 'stop', 'serial-getty@ttyS0.service'], check=True)
        result['kernel'] = os.uname().release
        if result['kernel'] != '6.19.10-300.fc44.x86_64':
            raise ValueError('unexpected guest kernel')
        if set(os.listdir('/sys/class/net')) != {'lo'}:
            raise ValueError('unexpected guest network interface')
        module = subprocess.check_output(['modinfo', '-F', 'filename', 'vgem'], text=True).strip()
        digest = hashlib.sha256(Path(module).read_bytes()).hexdigest()
        if digest != 'bc6aa5f12b7fb27d4c2184cffba85b989d9e1a385bb6b42c7cc831e427f65ff8':
            raise ValueError('unexpected vgem module')
        result.update(vgem_available=True, vgem_sha256=digest, success=True)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        result['error'] = str(error)[:1000]
    try:
        with Path('/dev/ttyS0').open('w') as output:
            output.write('PF_BOOT_BEGIN ' + nonce + '\n')
            output.write(json.dumps(result) + '\n')
            output.write('PF_BOOT_END ' + nonce + '\n')
    finally:
        subprocess.run(['systemctl', 'poweroff'], check=False)


if __name__ == '__main__':
    main()
