# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
import copy
import hashlib
import json
import os
import subprocess
import tarfile
import tempfile
import textwrap
import time
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

    def simulate(self, failure=None, mode='render', **identity):
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
                value = self.controller() | {'mode': mode, 'nonce': identity.get('nonce', token)}
                if identity.get('qualification_id'):
                    value['qualification_id'] = identity['qualification_id']
                if failure == 'wrong-nonce':
                    value['nonce'] = 'f' * 32
                if failure == 'wrong-qid':
                    value['qualification_id'] = 'f' * 64
                kwargs['stdout'].write(json.dumps(value))
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
        def pack(*args, **kwargs):
            state['invocation'] = kwargs.get('invocation')
            return {'source': {}}, [], 'a' * 64

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(run_native_guest.uuid, 'uuid4', return_value=types.SimpleNamespace(hex=token)), \
                patch.object(run_native_guest.time, 'monotonic', side_effect=lambda: state['clock']), \
                patch.object(run_native_guest, 'device_snapshot', return_value=snapshot), \
                patch.object(run_native_guest, 'pack_inputs', side_effect=pack), \
                patch.object(run_native_guest, 'decode', return_value={'files': {'build.log': 'ok'}, 'metadata': {}}), \
                patch.object(run_native_guest, 'verify_envelope', return_value={'mode': 'render'}), \
                patch.object(run_native_guest.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=20*1024**3)), \
                patch.object(run_native_guest.subprocess, 'run', side_effect=engine):
            report = run_native_guest.run_guest(Path(directory), Path(directory), Path(directory)/'base',
                                                 Path(directory), mode, 900 if mode == 'render' else 4200, **identity)
        return copy.deepcopy(report), state

    def test_success_requires_owned_cleanup(self):
        report, state = self.simulate()
        self.assertTrue(report['success'])
        self.assertFalse(state['container'] or state['volume'])

    def test_admission_identity_is_packed_and_verified(self):
        identity = {'qualification_id': 'b' * 64, 'nonce': 'e' * 32,
                    'expected_input_manifest': 'a' * 64}
        report, state = self.simulate(**identity)
        self.assertTrue(report['success'])
        self.assertEqual(state['invocation'], {'nonce': 'e' * 32, 'qualification_id': 'b' * 64})
        self.assertEqual(report['qualification_id'], identity['qualification_id'])
        for failure in ['wrong-nonce', 'wrong-qid']:
            with self.subTest(failure=failure):
                report, state = self.simulate(failure, **identity)
                self.assertFalse(report['success'])
                self.assertTrue(report['cleanup_confirmed'])

    def test_wrong_input_manifest_refuses_before_container_creation(self):
        report, state = self.simulate(qualification_id='b' * 64, nonce='e' * 32,
                                      expected_input_manifest='c' * 64)
        self.assertFalse(report['success'])
        self.assertFalse(any(command[0] in {'create', 'start'} for command in state['commands']))

    def test_partial_admission_never_reaches_device_or_engine(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(run_native_guest, 'device_snapshot') as device:
            for identity in [{'nonce': 'e' * 32}, {'qualification_id': 'b' * 64},
                             {'qualification_id': 'b' * 64, 'nonce': 'bad', 'expected_input_manifest': 'a' * 64}]:
                with self.subTest(identity=identity), self.assertRaises(ValueError):
                    run_native_guest.run_guest(Path(directory), Path(directory), Path(directory)/'base',
                                               Path(directory), 'native', 4200, **identity)
            device.assert_not_called()

    def test_invocation_is_covered_by_the_input_checksum_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / 'ci/builder').mkdir(parents=True)
            (root / 'ci/builder/packages.lock').write_text('fixture-package\n')
            source = b'fixture source'
            inputs = {'source': {'tree': 'a' * 40, 'sha256': hashlib.sha256(source).hexdigest(),
                                 'bytes': len(source)}, 'builder': {'files': []}}
            (root / 'inputs.json').write_text(json.dumps(inputs))
            (root / 'patched-source.tar').write_bytes(source)
            invocation = {'nonce': 'b' * 32, 'qualification_id': 'c' * 64}

            def stage(base, archive):
                with tarfile.open(fileobj=archive, mode='w'):
                    pass
                archive.seek(0)

            with tempfile.TemporaryFile() as archive, \
                    patch.object(run_native_guest, 'load_manifest', return_value=types.SimpleNamespace(expected_tree='a' * 40)), \
                    patch.object(run_native_guest, 'verify_image_directory', return_value={'files': []}), \
                    patch.object(run_native_guest, 'stage_image', side_effect=stage) as stage_image, \
                    patch.object(run_native_guest, 'PAYLOAD', ()):
                _, _, manifest_sha = run_native_guest.pack_inputs(root, root, root/'base', archive, invocation=invocation)
                with tarfile.open(fileobj=archive, mode='r') as packed:
                    data = packed.extractfile('native-input/invocation.json').read()
                    self.assertEqual(json.loads(data), invocation)
                    sums = packed.extractfile('native-input/SHA256SUMS').read().decode()
                    self.assertIn(hashlib.sha256(data).hexdigest() + '  invocation.json\n', sums)
                with tempfile.TemporaryFile() as preparation, \
                        patch.object(run_native_guest, 'load_manifest', side_effect=AssertionError('must use admitted source')):
                    _, _, prepared_sha = run_native_guest.pack_inputs(root, root, None, preparation,
                        invocation=invocation, expected_source=inputs['source'])
                self.assertEqual(prepared_sha, manifest_sha)
                stage_image.assert_called_once()

    def test_native_action_forwards_complete_admission_and_rejects_partial_input(self):
        action = Path(__file__).resolve().parents[1] / '.github/actions/native-guest/action.yml'
        script = textwrap.dedent(action.read_text().split('      run: |\n', 1)[1].split('\n    - name:', 1)[0])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / 'bin').mkdir()
            (root / 'bin/python3').write_text('#!/bin/sh\nprintf "%s\\n" "$@" >> "$CALLS"\n')
            (root / 'bin/curl').write_text('#!/bin/sh\nexit 0\n')
            for name in ['python3', 'curl']:
                (root / 'bin' / name).chmod(0o700)
            calls = root / 'calls'
            env = dict(os.environ, PATH=str(root/'bin') + ':' + os.environ['PATH'], CALLS=str(calls),
                       RUNNER_TEMP=str(root), GITHUB_OUTPUT=str(root/'outputs'), NATIVE_MODE='native',
                       NATIVE_BUDGET='4200', NATIVE_JOB_STARTED=str(int(time.time())),
                       QUALIFICATION_ID='b'*64, NATIVE_NONCE='c'*32, INPUT_MANIFEST_SHA256='d'*64)
            result = subprocess.run(['bash', '-c', script], env=env, capture_output=True, timeout=10, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            arguments = calls.read_text().splitlines()
            for flag, value in [('--qualification-id', 'b'*64), ('--nonce', 'c'*32),
                                ('--expected-input-manifest', 'd'*64)]:
                self.assertEqual(arguments[arguments.index(flag) + 1], value)
            calls.unlink()
            env['NATIVE_NONCE'] = ''
            result = subprocess.run(['bash', '-c', script], env=env, capture_output=True, timeout=10, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(calls.exists())

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
