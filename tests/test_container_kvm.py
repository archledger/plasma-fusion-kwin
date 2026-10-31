# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
import copy
import json
import os
import stat
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import kvm_api_probe, probe_container_kvm
from tools.probe_container_kvm import (
    IMAGE,
    LABEL,
    create_args,
    owned_identity,
    parse_result,
    validate_container,
)


class ContainerKvmTest(unittest.TestCase):
    def setUp(self):
        self.name, self.token = 'pf-kvm-test', 'unique-owner'
        self.info = {
            'Id': 'a' * 64, 'Name': '/' + self.name, 'Image': 'sha256:5e5eb48e65c6e9cf3738963de73a09cb6acad10401362b95cee3dc25a6958aa5',
            'Config': {'User': '1000:1000', 'Labels': {LABEL: self.token}},
            'AppArmorProfile': 'docker-default', 'Mounts': [],
            'HostConfig': {
                'Privileged': False, 'ReadonlyRootfs': True, 'NetworkMode': 'none',
                'Binds': None, 'Devices': [{'PathOnHost': '/dev/kvm', 'PathInContainer': '/dev/kvm', 'CgroupPermissions': 'rw'}],
                'DeviceRequests': None, 'CapDrop': ['ALL'], 'CapAdd': [],
                'SecurityOpt': ['no-new-privileges', 'apparmor=docker-default'], 'PidMode': '', 'IpcMode': 'private',
                'CgroupnsMode': 'private', 'AutoRemove': True, 'Memory': 128 * 1024**2,
                'MemorySwap': 128 * 1024**2, 'PidsLimit': 64, 'NanoCpus': 10**9,
                'RestartPolicy': {'Name': 'no'},
            },
        }

    def test_fixed_create_arguments(self):
        args = create_args(self.name, self.token, 109)
        self.assertIn(IMAGE, args)
        self.assertIn('--group-add=109', args)
        self.assertIn('--device=/dev/kvm:/dev/kvm:rw', args)
        self.assertIn('--read-only', args)
        self.assertNotIn('--privileged', args)
        self.assertFalse(any(arg.startswith(('--volume', '-v=', '--mount', '--env-file')) for arg in args))

    def test_safe_configuration(self):
        self.assertEqual(validate_container(self.info, self.name, self.token)['user'], '1000:1000')

    def test_unsafe_configuration_rejected(self):
        for key, value in [('Privileged', True), ('ReadonlyRootfs', False), ('NetworkMode', 'host'),
                           ('PidMode', 'host'), ('CapAdd', ['SYS_ADMIN']), ('Devices', []),
                           ('AutoRemove', False), ('PidsLimit', 0),
                           ('SecurityOpt', ['no-new-privileges', 'apparmor=docker-default', 'seccomp=unconfined'])]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                info = copy.deepcopy(self.info)
                info['HostConfig'][key] = value
                validate_container(info, self.name, self.token)
        info = copy.deepcopy(self.info)
        info['Mounts'] = [{'Source': '/home'}]
        with self.assertRaises(ValueError):
            validate_container(info, self.name, self.token)

    def test_foreign_container_not_owned(self):
        with self.assertRaises(ValueError):
            owned_identity(self.info, self.name, 'wrong-owner')

    def test_result_must_be_unprivileged_api_only(self):
        good = {'uid': 1000, 'api_version': 12, 'vm_created': False}
        self.assertEqual(parse_result(json.dumps(good))['api_version'], 12)
        for change in [{'uid': 0}, {'api_version': 11}, {'vm_created': True}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                parse_result(json.dumps(good | change))

    def simulate_engine(self, unsafe=False, security=None, remove_race=False, metadata_changed=False):
        token = 'b' * 32
        name = 'pf-kvm-probe-' + token
        info = copy.deepcopy(self.info)
        info['Name'] = '/' + name
        info['Config']['Labels'][LABEL] = token
        info['HostConfig']['Privileged'] = unsafe
        state = {'exists': False, 'commands': []}

        def engine(command, **kwargs):
            args = command[1:]
            state['commands'].append(args)
            self.assertEqual(set(kwargs['env']), {'PATH', 'LANG', 'HOME', 'DOCKER_CONFIG'})
            if args[0] in {'version', 'pull'}:
                return subprocess.CompletedProcess(command, 0, 'ok', '')
            if args[0] == 'info':
                return subprocess.CompletedProcess(command, 0,
                                                   json.dumps(security if security is not None else
                                                              ['name=apparmor', 'name=seccomp,profile=builtin']), '')
            if args[0] == 'create':
                state['exists'] = True
                return subprocess.CompletedProcess(command, 0, 'a' * 64, '')
            if args[0] == 'inspect':
                return (subprocess.CompletedProcess(command, 0, json.dumps([info]), '') if state['exists']
                        else subprocess.CompletedProcess(command, 1, '', 'No such object: ' + name))
            if args[0] == 'start':
                state['exists'] = remove_race
                output = {'uid': 1000, 'api_version': 12, 'vm_created': False,
                          'device': {'major': 10, 'minor': 232}}
                return subprocess.CompletedProcess(command, 0, json.dumps(output), '')
            if args[0] == 'rm':
                state['exists'] = False
                if remove_race:
                    return subprocess.CompletedProcess(command, 1, '', 'No such container')
                return subprocess.CompletedProcess(command, 0, 'removed', '')
            self.fail('unexpected engine command: ' + repr(args))

        snapshot = {'gid': 109, 'major': 10, 'minor': 232, 'mode': 0o660, 'uid': 0}
        after = snapshot | {'mode': 0o666} if metadata_changed else snapshot
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(probe_container_kvm.uuid, 'uuid4', return_value=types.SimpleNamespace(hex=token)), \
                patch.object(probe_container_kvm, 'device_snapshot', side_effect=[snapshot, after]), \
                patch.object(probe_container_kvm.shutil, 'which', return_value='/usr/bin/docker'), \
                patch.object(probe_container_kvm.subprocess, 'run', side_effect=engine):
            result = probe_container_kvm.run_probe(Path(directory))
        return result, state

    def test_safe_run_and_autoremove(self):
        result, state = self.simulate_engine()
        self.assertTrue(result['success'])
        self.assertTrue(result['cleanup_confirmed'])
        self.assertFalse(state['exists'])

    def test_unsafe_configuration_never_starts_and_is_cleaned_up(self):
        result, state = self.simulate_engine(unsafe=True)
        self.assertFalse(result['success'])
        self.assertFalse(any(command[0] == 'start' for command in state['commands']))
        self.assertTrue(any(command[0] == 'rm' for command in state['commands']))
        self.assertFalse(state['exists'])

    def test_missing_engine_confinement_stops_before_create(self):
        result, state = self.simulate_engine(security=['name=seccomp,profile=builtin'])
        self.assertFalse(result['success'])
        self.assertFalse(any(command[0] == 'create' for command in state['commands']))

    def test_autoremove_race_is_confirmed_by_absence(self):
        result, _ = self.simulate_engine(remove_race=True)
        self.assertTrue(result['success'])
        self.assertTrue(result['cleanup_confirmed'])

    def test_changed_host_device_metadata_fails(self):
        result, _ = self.simulate_engine(metadata_changed=True)
        self.assertFalse(result['success'])
        self.assertFalse(result['device_metadata_unchanged'])

    def test_child_only_queries_api_and_closes_fd(self):
        fake_os = types.SimpleNamespace(geteuid=lambda: 1000, getgroups=lambda: [109],
                                        O_RDWR=os.O_RDWR, O_CLOEXEC=os.O_CLOEXEC,
                                        open=unittest.mock.Mock(return_value=17),
                                        close=unittest.mock.Mock(),
                                        fstat=lambda fd: types.SimpleNamespace(st_mode=stat.S_IFCHR,
                                                                              st_rdev=os.makedev(10, 232)),
                                        major=os.major, minor=os.minor)
        fake_fcntl = types.SimpleNamespace(ioctl=unittest.mock.Mock(return_value=12))
        with patch.object(kvm_api_probe, 'os', fake_os), patch.object(kvm_api_probe, 'fcntl', fake_fcntl):
            output = kvm_api_probe.inspect_kvm()
        fake_fcntl.ioctl.assert_called_once_with(17, 0xAE00, 0)
        fake_os.close.assert_called_once_with(17)
        self.assertFalse(output['vm_created'])


if __name__ == '__main__':
    unittest.main()
