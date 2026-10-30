# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci.native.controller import native_command, seed_data
from ci.native.guest import ready_device, recheck_device, verify_container, verify_oci


class NativeControllerTest(unittest.TestCase):
    def test_qemu_only_adds_readonly_input_medium(self):
        command = native_command(Path('/work'))
        self.assertEqual(command[command.index('-accel') + 1], 'kvm')
        self.assertEqual(command[command.index('-nic') + 1], 'none')
        self.assertIn('if=none,id=nativeinput,format=raw,readonly=on,file=/work/native-input.iso', command)
        self.assertFalse(any('virtfs' in word or 'vfio' in word or 'tcg' in word for word in command))

    def test_seed_binds_manifest_before_executing_payload(self):
        text = seed_data('a' * 32, 'native', 'b' * 64)
        self.assertLess(text.index('b' * 64), text.index('python3 -m'))
        self.assertLess(text.index('trap '), text.index('mkdir '))
        self.assertIn('sha256sum --check SHA256SUMS', text)
        for nonce, mode, digest in [('bad;', 'native', 'b' * 64), ('a' * 32, 'native;bad', 'b' * 64),
                                    ('a' * 32, 'native', 'b' * 63)]:
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                seed_data(nonce, mode, digest)

    def test_inner_container_forbids_host_mounts_and_privilege(self):
        node = '/dev/dri/card0'
        info = {'Config': {'User': '1000:1000', 'WorkingDir': '/tmp'}, 'Mounts': [],
                'HostConfig': {'Privileged': False, 'NetworkMode': 'none', 'IpcMode': 'private',
                               'PidMode': 'private', 'UTSMode': 'private', 'CapAdd': [],
                               'SecurityOpt': ['no-new-privileges'], 'GroupAdd': ['39'],
                               # Podman 5.8.1's actual retained inspection leaves this field empty.
                               'Devices': [{'PathOnHost': node, 'PathInContainer': node, 'CgroupPermissions': ''}],
                               'Memory': 5 * 1024**3, 'MemorySwap': 5 * 1024**3, 'CpuPeriod': 100000,
                               'CpuQuota': 200000, 'PidsLimit': 1024}}
        verify_container(info, node, 39)
        for key, value in [('Privileged', True), ('NetworkMode', 'host'), ('Memory', 0), ('CapAdd', ['SYS_ADMIN'])]:
            bad = info | {'HostConfig': info['HostConfig'] | {key: value}}
            with self.subTest(key=key), self.assertRaises(ValueError):
                verify_container(bad, node, 39)
        with self.assertRaises(ValueError):
            verify_container(info | {'Mounts': [{'Source': '/home'}]}, node, 39)

    def test_oci_device_policy_requires_exact_rw_node(self):
        node, identity = '/dev/dri/card0', (226, 0, 0, 39, 0o660)
        spec = {'process': {'user': {'uid': 1000, 'gid': 1000, 'additionalGids': [1000, 39]},
                            'noNewPrivileges': True, 'capabilities': {},
                            'args': ['/usr/bin/python3', '-m', 'ci.native.workload', 'native']},
                'linux': {'seccomp': {'defaultAction': 'SCMP_ACT_ERRNO'},
                          'namespaces': [{'type': name} for name in ['pid', 'network', 'ipc', 'uts', 'mount', 'cgroup']],
                          'devices': [{'path': node, 'type': 'c', 'major': 226, 'minor': 0}],
                          'resources': {'devices': [{'allow': True, 'type': 'c', 'major': 226,
                                                    'minor': 0, 'access': 'rw'}]}}}
        verify_oci(spec, node, identity, 'native')
        spec['linux']['resources']['devices'][0]['access'] = 'rwm'
        with self.assertRaises(ValueError):
            verify_oci(spec, node, identity, 'native')
        spec['linux']['resources']['devices'][0]['access'] = 'rw'
        spec['linux']['resources']['devices'].append(
            {'allow': True, 'type': 'c', 'major': 226, 'minor': 0, 'access': 'rwm'})
        with self.assertRaises(ValueError):
            verify_oci(spec, node, identity, 'native')

    def test_udev_settles_before_group_is_read_and_identity_is_rechecked(self):
        class Node:
            name = 'card0'

            def __truediv__(self, part):
                return Path('/sys/devices/platform/vgem')

        class Root:
            def glob(self, pattern):
                return [Node()]

        state = {'identity': (226, 0, 0, 0, 0o600)}

        def settle(command, timeout):
            self.assertEqual(command, ['udevadm', 'settle', '--timeout=10'])
            state['identity'] = (226, 0, 0, 39, 0o660)

        with patch('ci.native.guest.Path', return_value=Root()), \
                patch('ci.native.guest.grp.getgrnam', return_value=type('Group', (), {'gr_gid': 39})()), \
                patch('ci.native.guest.device_identity', side_effect=lambda node: state['identity']):
            node, identity = ready_device(settle)
            self.assertEqual(identity[3], 39)
            state['identity'] = (226, 0, 0, 40, 0o660)
            with self.assertRaises(ValueError):
                recheck_device(node, identity)

    def test_bootstrap_failures_power_off_without_unverified_python(self):
        data = seed_data('a' * 32, 'native', 'b' * 64)
        script = '\n'.join(line[6:] for line in data.split('content: |\n', 1)[1].split('runcmd:', 1)[0].splitlines())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tools = root / 'bin'
            tools.mkdir()
            for tool, result in [('mount', 'MOUNT_RESULT'), ('sha256sum', 'CHECK_RESULT'),
                                 ('python3', 'PYTHON_RESULT'), ('systemctl', 'SHUTDOWN_RESULT')]:
                path = tools / tool
                path.write_text('#!/bin/sh\necho ' + tool + ' >> "$EVENTS"\nexit "${' + result + ':-0}"\n')
                path.chmod(0o755)
            script = script.replace('/mnt/native-input', str(root / 'input')).replace('/dev/ttyS0', str(root / 'serial'))
            for fail in ['MOUNT_RESULT', 'CHECK_RESULT', 'PYTHON_RESULT']:
                events = root / 'events'
                events.unlink(missing_ok=True)
                env = dict(os.environ, PATH=str(tools) + ':' + os.environ['PATH'], EVENTS=str(events),
                           MOUNT_RESULT='0', CHECK_RESULT='0', PYTHON_RESULT='0', SHUTDOWN_RESULT='0')
                env[fail] = '1'
                run = subprocess.run(['bash', '-c', script], env=env, capture_output=True,
                                     text=True, timeout=10, check=False)
                self.assertNotEqual(run.returncode, 0)
                calls = events.read_text().splitlines()
                self.assertIn('systemctl', calls)
                if fail != 'PYTHON_RESULT':
                    self.assertNotIn('python3', calls)


if __name__ == '__main__':
    unittest.main()
