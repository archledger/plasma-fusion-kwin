# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Container payload: query only KVM_GET_API_VERSION, never create a VM."""
import fcntl
import json
import os
import stat


def inspect_kvm():
    result = {'uid': os.geteuid(), 'groups': os.getgroups(), 'vm_created': False}
    try:
        if result['uid'] != 1000:
            raise RuntimeError('expected unprivileged UID1000')
        fd = os.open('/dev/kvm', os.O_RDWR | os.O_CLOEXEC)
        try:
            value = os.fstat(fd)
            if not stat.S_ISCHR(value.st_mode):
                raise RuntimeError('not a character device')
            result['device'] = {'major': os.major(value.st_rdev), 'minor': os.minor(value.st_rdev)}
            result['api_version'] = fcntl.ioctl(fd, 0xAE00, 0)
        finally:
            os.close(fd)
    except (OSError, RuntimeError) as error:
        result['error'] = str(error)
    return result


if __name__ == '__main__':
    result = inspect_kvm()
    print(json.dumps(result))
    raise SystemExit(0 if result.get('api_version') == 12 and 'error' not in result else 1)
