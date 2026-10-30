# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
import copy
import json
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from ci.hypervisor.boot_guest import GUEST_SHA256
from ci.native.controller import native_command
from tools import run_native_guest
from tools.probe_hosted_boot import LABEL


class NativeRunnerTest(unittest.TestCase):
    def controller(self):
        return {'success': True, 'uid': 1000, 'mode': 'render', 'apparmor': 'docker-default (enforce)',
                'input_manifest_sha256': 'a' * 64, 'base_sha256': GUEST_SHA256,
                'command': native_command(Path('/work')), 'qemu_exit': 0,
                'nonce': 'e' * 32, 'wire': 'mock frame'}

    def test_controller_identity_and_command_are_required(self):
        value = self.controller()
        run_native_guest.verify_controller(value, 'render', 'a' * 64)
        for key, changed in [('uid', 0), ('apparmor', 'docker-default (complain)'), ('command', []),
                             ('mode', 'native'), ('input_manifest_sha256', 'b' * 64), ('qemu_exit', False)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                run_native_guest.verify_controller(value | {key: changed}, 'render', 'a' * 64)

    def simulate(self, failure=None, mode='render'):
        token = 'd' * 32
        name, volume = 'pf-native-' + token, 'pf-native-data-' + token
        info = {
            'Id': 'c' * 64, 'Name': '/' + name, 'Image': run_native_guest.CONFIG,
            'Config': {'User': '1000:1000', 'Image': run_native_guest.IMAGE, 'Labels': {LABEL: token},
                       'Entrypoint': ['/usr/bin/python3'],
                       'Cmd': ['-I', '-B', '-c', run_native_guest.BOOTSTRAP, mode]},
            'AppArmorProfile': 'docker-default',
            'Mounts': [{'Type': 'volume', 'Name': volume, 'Driver': 'local', 'Destination': '/work', 'RW': True}],
            'HostConfig': {'Privileged': failure == 'unsafe', 'ReadonlyRootfs': True,
                           'NetworkMode': 'none', 'Binds': None,
                           'Devices': [{'PathOnHost': '/dev/kvm', 'PathInContainer': '/dev/kvm',
                                        'CgroupPermissions': 'rw'}], 'GroupAdd': ['993'],
                           'CapAdd': [], 'CapDrop': ['ALL'], 'SecurityOpt': ['apparmor=docker-default', 'no-new-privileges'],
                           'IpcMode': 'private', 'CgroupnsMode': 'private', 'PidMode': '', 'UTSMode': '',
                           'AutoRemove': True, 'RestartPolicy': {'Name': 'no'}, 'Memory': 8*1024**3,
                           'MemorySwap': 8*1024**3, 'PidsLimit': 256, 'NanoCpus': 2*10**9},
        }
        volume_info = {'Name': volume, 'Labels': {LABEL: token}, 'Driver': 'local', 'Scope': 'local', 'Options': None}
        state = {'clock': 100.0, 'volume': False, 'container': False, 'commands': []}

        def engine(command, **kwargs):
            args = command[1:]
            state['commands'].append(args)
            self.assertEqual(set(kwargs['env']), {'PATH', 'LANG', 'HOME', 'DOCKER_CONFIG'})
            code, out, err = 0, '', ''
            if args[0] == 'info':
                out = json.dumps(['name=apparmor', 'name=seccomp,profile=builtin'])
            elif args[:2] == ['image', 'inspect']:
                out = run_native_guest.CONFIG
            elif args[:2] == ['volume', 'create']:
                state['volume'] = True
            elif args[0] == 'create':
                state['container'] = True
                out = info['Id']
            elif args[0] == 'inspect':
                if state['container']:
                    out = json.dumps([info])
                else:
                    code, err = 1, 'No such object'
            elif args[:2] == ['volume', 'inspect']:
                if state['volume']:
                    out = json.dumps([volume_info])
                else:
                    code, err = 1, 'no such volume'
            elif args[0] == 'cp':
                if failure == 'budget':
                    state['clock'] += 61 if mode == 'render' else 331
            elif args[0] == 'start':
                if failure == 'timeout':
                    kwargs['stdout'].write('partial output before timeout')
                    raise subprocess.TimeoutExpired(command, 750, stderr=b'controller timed out')
                if failure == 'startup':
                    return subprocess.CompletedProcess(command, 125, '', 'container startup failed')
                if failure == 'partial':
                    kwargs['stdout'].write('{"partial":')
                    return subprocess.CompletedProcess(command, 1, '', 'partial failure')
                kwargs['stdout'].write(json.dumps(self.controller() | {'mode': mode}))
                state['container'] = False
            elif args[0] == 'rm':
                state['container'] = False
            elif args[:2] == ['volume', 'rm']:
                if failure == 'volume':
                    code, err = 1, 'volume still in use'
                else:
                    state['volume'] = False
            elif args[0] != 'pull':
                self.fail('unexpected command: ' + repr(args))
            return subprocess.CompletedProcess(command, code, out, err)

        snapshot = {'uid': 0, 'gid': 993, 'mode': 0o660, 'major': 10, 'minor': 232}
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(run_native_guest.uuid, 'uuid4', return_value=types.SimpleNamespace(hex=token)), \
                patch.object(run_native_guest.time, 'monotonic', side_effect=lambda: state['clock']), \
                patch.object(run_native_guest, 'device_snapshot', return_value=snapshot), \
                patch.object(run_native_guest, 'pack_inputs', return_value=({'source': {}}, [], 'a' * 64)), \
                patch.object(run_native_guest, 'decode', return_value={'files': {'build.log': 'ok'}, 'metadata': {}}), \
                patch.object(run_native_guest, 'verify_envelope', return_value={'mode': 'render'}), \
                patch.object(run_native_guest.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=20*1024**3)), \
                patch.object(run_native_guest.subprocess, 'run', side_effect=engine):
            report = run_native_guest.run_guest(Path(directory), Path(directory), Path(directory)/'base',
                                                 Path(directory), mode, 900 if mode == 'render' else 4200)
        return copy.deepcopy(report), state

    def test_success_requires_owned_cleanup(self):
        report, state = self.simulate()
        self.assertTrue(report['success'])
        self.assertFalse(state['container'] or state['volume'])

    def test_runtime_evidence_omits_environment_and_unrelated_settings(self):
        info = {
            'Id': 'a' * 64, 'Name': '/fixture', 'Image': 'image-id',
            'Config': {'Image': 'image-ref', 'User': '1000:1000', 'Entrypoint': [], 'Cmd': [],
                       'Env': ['PRIVATE_FIXTURE=not-a-real-secret'],
                       'Labels': {LABEL: 'owned', 'private-fixture': 'do-not-copy'}},
            'Mounts': [{'Type': 'volume', 'Name': 'fixture', 'Destination': '/work',
                        'RW': True, 'Driver': 'local', 'Source': '/private/fixture'}],
            'HostConfig': {'Binds': None, 'LogConfig': {'private-fixture': 'do-not-copy'}},
            'AppArmorProfile': 'docker-default',
        }
        value = run_native_guest.runtime_evidence(info)
        self.assertNotIn('Env', value['Config'])
        self.assertEqual(value['Config']['Labels'], {LABEL: 'owned'})
        self.assertNotIn('Source', value['Mounts'][0])
        self.assertNotIn('LogConfig', value['HostConfig'])

    def test_refusals_and_timeout_leave_no_guest(self):
        for reason in ['unsafe', 'budget', 'timeout']:
            with self.subTest(reason=reason):
                report, state = self.simulate(reason)
                self.assertFalse(report['success'])
                self.assertFalse(state['container'] or state['volume'])
                if reason != 'timeout':
                    self.assertFalse(any(command[0] == 'start' for command in state['commands']))

    def test_failed_volume_removal_never_passes(self):
        report, _ = self.simulate('volume')
        self.assertFalse(report['success'])
        self.assertFalse(report['cleanup_confirmed'])

    def test_native_mode_budget_is_checked_before_start(self):
        report, state = self.simulate('budget', mode='native')
        self.assertFalse(report['success'])
        self.assertFalse(any(command[0] == 'start' for command in state['commands']))
        self.assertFalse(state['container'] or state['volume'])

    def test_startup_partial_and_timeout_diagnostics_survive_cleanup(self):
        for reason, stderr in [('startup', 'container startup failed'), ('partial', 'partial failure'),
                               ('timeout', 'controller timed out')]:
            with self.subTest(reason=reason):
                report, state = self.simulate(reason)
                self.assertFalse(report['success'])
                self.assertEqual(report['controller_stderr'], stderr)
                if reason != 'startup':
                    self.assertTrue(report['controller_output_tail'])
                self.assertFalse(state['container'] or state['volume'])


if __name__ == '__main__':
    unittest.main()
