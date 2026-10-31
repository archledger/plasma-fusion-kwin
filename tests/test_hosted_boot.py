# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
import copy
import hashlib
import io
import json
import subprocess
import tarfile
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from ci.hypervisor.boot_guest import GUEST_SHA256, qemu_command
from tools import probe_hosted_boot
from tools.probe_hosted_boot import (
    LABEL,
    create_args,
    validate_runtime,
    validate_volume,
)


class HostedBootTest(unittest.TestCase):
    def setUp(self):
        self.image = 'ghcr.io/archledger/plasma-fusion-kwin-hypervisor@sha256:' + 'a' * 64
        self.config = 'sha256:' + 'b' * 64
        self.name, self.token, self.volume = 'boot-test', 'owner-token', 'boot-volume'
        self.info = {
            'Id': 'c' * 64, 'Name': '/' + self.name, 'Image': self.config,
            'Config': {'User': '1000:1000', 'Image': self.image, 'Labels': {LABEL: self.token}},
            'AppArmorProfile': 'docker-default',
            'Mounts': [{'Type': 'volume', 'Name': self.volume, 'Driver': 'local',
                        'Destination': '/work', 'RW': True}],
            'HostConfig': {
                'Privileged': False, 'ReadonlyRootfs': True, 'NetworkMode': 'none',
                'Binds': None, 'Devices': [{'PathOnHost': '/dev/kvm', 'PathInContainer': '/dev/kvm',
                                          'CgroupPermissions': 'rw'}],
                'DeviceRequests': None, 'DeviceCgroupRules': None,
                'CapDrop': ['ALL'], 'CapAdd': [], 'GroupAdd': ['993'],
                'SecurityOpt': ['no-new-privileges', 'apparmor=docker-default'],
                'PidMode': '', 'IpcMode': 'private', 'CgroupnsMode': 'private', 'UTSMode': '',
                'AutoRemove': True, 'Memory': 8 * 1024**3, 'MemorySwap': 8 * 1024**3,
                'PidsLimit': 256, 'NanoCpus': 2 * 10**9, 'RestartPolicy': {'Name': 'no'},
            },
        }

    def validate(self, info):
        return validate_runtime(info, self.name, self.token, self.volume, self.image, self.config, 993)

    def test_exact_boot_configuration(self):
        self.assertEqual(self.validate(self.info)['user'], '1000:1000')
        command = create_args(self.name, self.token, self.volume, self.image, 993)
        self.assertIn('--device=/dev/kvm:/dev/kvm:rw', command)
        self.assertIn('--read-only', command)
        self.assertEqual(command[-2:], [self.image, '--probe'])
        self.assertFalse(any(part.startswith(('--privileged', '--env', '--volume=')) for part in command))

    def test_forbidden_privileges_resources_and_namespaces(self):
        changes = [('Privileged', True), ('ReadonlyRootfs', False), ('NetworkMode', 'host'),
                   ('PidMode', 'host'), ('PidMode', 'container:foreign'),
                   ('IpcMode', 'host'), ('UTSMode', 'host'),
                   ('CgroupnsMode', 'host'), ('CapAdd', ['SYS_ADMIN']),
                   ('DeviceCgroupRules', ['a *:* rwm']), ('GroupAdd', ['0']),
                   ('AutoRemove', False), ('Memory', 0), ('PidsLimit', 0),
                   ('SecurityOpt', ['no-new-privileges', 'apparmor=docker-default', 'seccomp=unconfined'])]
        for key, value in changes:
            info = copy.deepcopy(self.info)
            info['HostConfig'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.validate(info)

    def test_extra_or_host_backed_mount_is_rejected(self):
        for mount in [{'Type': 'bind', 'Source': '/home', 'Destination': '/work', 'RW': True},
                      {'Type': 'volume', 'Name': 'foreign', 'Driver': 'local', 'Destination': '/work', 'RW': True}]:
            info = copy.deepcopy(self.info)
            info['Mounts'] = [mount]
            with self.assertRaises(ValueError):
                self.validate(info)
        info = copy.deepcopy(self.info)
        info['Mounts'] *= 2
        with self.assertRaises(ValueError):
            self.validate(info)

    def test_only_owned_unconfigured_local_volume_is_accepted(self):
        volume = {'Name': self.volume, 'Driver': 'local', 'Options': None,
                  'Scope': 'local', 'Labels': {LABEL: self.token}}
        validate_volume(volume, self.volume, self.token)
        for key, value in [('Driver', 'nfs'), ('Options', {'type': 'none', 'device': '/home', 'o': 'bind'}),
                           ('Labels', {LABEL: 'foreign'}), ('Name', 'foreign')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_volume(volume | {key: value}, self.volume, self.token)

    def test_docker_lowercase_volume_absence_is_recognized(self):
        result = subprocess.CompletedProcess(['docker', 'volume', 'inspect', 'owned'], 1, '',
                                             'Error response from daemon: get owned: no such volume')
        self.assertTrue(probe_hosted_boot.absent(result, 'volume'))
        result.stderr = 'Cannot connect to the Docker daemon'
        self.assertFalse(probe_hosted_boot.absent(result, 'volume'))

    def test_input_archive_has_only_owned_directory_and_readonly_image(self):
        content = b'test image payload'
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / 'base'
            base.write_bytes(content)
            archive = io.BytesIO()
            with patch.object(probe_hosted_boot, 'GUEST_BYTES', len(content)), \
                    patch.object(probe_hosted_boot, 'GUEST_SHA256', hashlib.sha256(content).hexdigest()):
                probe_hosted_boot.stage_image(base, archive)
            with tarfile.open(fileobj=archive, mode='r:') as tar:
                entries = tar.getmembers()
                self.assertEqual([entry.name for entry in entries], ['.', 'base.qcow2'])
                self.assertEqual([(entry.uid, entry.gid, entry.mode) for entry in entries],
                                 [(1000, 1000, 0o700), (1000, 1000, 0o400)])
                self.assertTrue(entries[0].isdir())
                self.assertTrue(entries[1].isreg())
                with tar.extractfile(entries[1]) as image:
                    self.assertEqual(image.read(), content)
            link = Path(directory) / 'link'
            link.symlink_to(base)
            with self.assertRaises(OSError):
                probe_hosted_boot.stage_image(link, io.BytesIO())

    def simulate(self, failure=None):
        token = 'd' * 32
        name, volume = 'pf-boot-' + token, 'pf-boot-data-' + token
        info = copy.deepcopy(self.info)
        info.update(Name='/' + name)
        info['Config']['Labels'] = {LABEL: token}
        info['Mounts'][0]['Name'] = volume
        volume_info = {'Name': volume, 'Driver': 'local', 'Scope': 'local', 'Options': None,
                       'Labels': {LABEL: token}}
        state = {'container': False, 'volume': False, 'commands': [], 'clock': 100.0}
        if failure == 'unsafe':
            info['HostConfig']['Privileged'] = True

        def engine(command, **kwargs):
            args = command[1:]
            state['commands'].append(args)
            self.assertEqual(set(kwargs['env']), {'PATH', 'LANG', 'HOME', 'DOCKER_CONFIG'})
            code, stdout, stderr = 0, '', ''
            if args[0] == 'info':
                stdout = json.dumps(['name=apparmor', 'name=seccomp,profile=builtin'])
            elif args[:2] == ['image', 'inspect']:
                stdout = self.config
            elif args[:2] == ['volume', 'create']:
                state['volume'] = True
            elif args[0] == 'create':
                state['container'] = True
                stdout = info['Id']
            elif args[0] == 'inspect':
                if state['container']:
                    stdout = json.dumps([info])
                else:
                    code, stderr = 1, 'No such object: ' + name
            elif args[:2] == ['volume', 'inspect']:
                if state['volume']:
                    stdout = json.dumps([volume_info])
                else:
                    code, stderr = 1, 'No such volume: ' + volume
            elif args[0] == 'cp':
                self.assertEqual(args, ['cp', '--archive', '-', info['Id'] + ':/work'])
                self.assertEqual(kwargs['stdin'].read(), b'fake verified input')
                if failure == 'budget':
                    state['clock'] += 241
            elif args[0] == 'start':
                if failure == 'timeout':
                    raise subprocess.TimeoutExpired(command, 390)
                receipt = {'success': True, 'uid': 1000, 'apparmor': 'docker-default (enforce)',
                           'qemu_exit': 0, 'vm_attempted': True, 'guest_base_sha256': GUEST_SHA256,
                           'command': qemu_command(Path('/work')),
                           'guest': {'nonce': 'e' * 32, 'uid': 0, 'success': True,
                                     'kernel': '6.19.10-300.fc44.x86_64', 'vgem_available': True,
                                     'vgem_sha256': 'bc6aa5f12b7fb27d4c2184cffba85b989d9e1a385bb6b42c7cc831e427f65ff8'}}
                kwargs['stdout'].write(json.dumps(receipt))
                state['container'] = failure == 'delayed-remove'
            elif args[0] == 'rm':
                self.assertEqual(args[-1], info['Id'])
                state['container'] = False
            elif args[:2] == ['volume', 'rm']:
                if failure == 'volume-cleanup':
                    code, stderr = 1, 'volume is still in use'
                else:
                    state['volume'] = False
            elif args[0] != 'pull':
                self.fail('unexpected engine command: ' + repr(args))
            return subprocess.CompletedProcess(command, code, stdout, stderr)

        snapshot = {'uid': 0, 'gid': 993, 'mode': 0o660, 'major': 10, 'minor': 232}
        after = snapshot | {'mode': 0o666} if failure == 'metadata' else snapshot

        def stage(base, archive):
            archive.write(b'fake verified input')
            archive.seek(0)

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(probe_hosted_boot.uuid, 'uuid4', return_value=types.SimpleNamespace(hex=token)), \
                patch.object(probe_hosted_boot, 'device_snapshot', side_effect=[snapshot, after]), \
                patch.object(probe_hosted_boot, 'stage_image', side_effect=stage), \
                patch.object(probe_hosted_boot.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=10*1024**3)), \
                patch.object(probe_hosted_boot.shutil, 'which', return_value='/usr/bin/docker'), \
                patch.object(probe_hosted_boot.time, 'monotonic', side_effect=lambda: state['clock']), \
                patch.object(probe_hosted_boot.subprocess, 'run', side_effect=engine):
            report = probe_hosted_boot.run_probe(Path(directory)/'base', self.image, self.config, Path(directory))
        return report, state

    def test_success_requires_container_and_volume_cleanup(self):
        report, state = self.simulate()
        self.assertTrue(report['success'])
        self.assertTrue(report['cleanup_confirmed'])
        self.assertFalse(state['container'] or state['volume'])

    def test_unsafe_configuration_is_never_started(self):
        report, state = self.simulate('unsafe')
        self.assertFalse(report['success'])
        self.assertFalse(any(command[0] == 'start' for command in state['commands']))
        self.assertFalse(state['container'] or state['volume'])

    def test_timeout_removes_container_and_volume(self):
        report, state = self.simulate('timeout')
        self.assertFalse(report['success'])
        self.assertTrue(report['cleanup_confirmed'])
        self.assertFalse(state['container'] or state['volume'])

    def test_delayed_autoremove_is_explicitly_cleaned(self):
        report, state = self.simulate('delayed-remove')
        self.assertTrue(report['success'])
        self.assertTrue(any(command[0] == 'rm' for command in state['commands']))
        self.assertFalse(state['container'] or state['volume'])

    def test_insufficient_remaining_budget_never_starts_guest(self):
        report, state = self.simulate('budget')
        self.assertFalse(report['success'])
        self.assertFalse(any(command[0] == 'start' for command in state['commands']))
        self.assertTrue(report['cleanup_confirmed'])
        self.assertFalse(state['container'] or state['volume'])

    def test_volume_cleanup_failure_prevents_success(self):
        report, state = self.simulate('volume-cleanup')
        self.assertFalse(report['success'])
        self.assertFalse(report['cleanup_confirmed'])
        self.assertTrue(state['volume'])
        self.assertEqual(report['volume_remove_error'], 'volume is still in use')

    def test_device_metadata_change_prevents_success(self):
        report, _ = self.simulate('metadata')
        self.assertFalse(report['success'])
        self.assertFalse(report['device_metadata_unchanged'])


if __name__ == '__main__':
    unittest.main()
