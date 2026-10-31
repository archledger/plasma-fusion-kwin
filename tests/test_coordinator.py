# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ci.native import coordinator
from ci.native.qualification import QualificationPolicy


class CoordinatorTest(unittest.TestCase):
    def test_workflow_cannot_become_the_maintainer_coordinator(self):
        with (
            patch.dict(os.environ, GITHUB_ACTIONS='true'),
            patch.object(coordinator.subprocess, 'check_output') as call,
            self.assertRaises(ValueError),
        ):
            coordinator.coordinate('.', None, '', None, None, None, None)
        call.assert_not_called()

    def fixture(self):
        policy = QualificationPolicy(
            coordinator.REPOSITORY, 1439058578, 200, 'a' * 40, 'A' * 40, ('pkg',)
        )
        admission = {
            'repository_id': policy.repository_id,
            'head_repository_id': 101,
            'pr_number': 7,
            'base': 'a' * 40,
            'head': 'b' * 40,
            'merge': 'c' * 40,
            'signed_merge': 'd' * 40,
            'tree': 'e' * 40,
            'payload_sha256': 'f' * 64,
            'source': {'tree': '1' * 40, 'sha256': '2' * 64, 'bytes': 10},
            'approver_id': 10,
            'upstream_commit': policy.upstream_commit,
        }
        anchor = {
            'commit': '3' * 40,
            'parent': None,
            'generation': 0,
            'epoch': '4' * 32,
            'cutover_digest': '5' * 64,
        }
        return policy, admission, anchor

    def test_dry_run_never_signs_dispatches_or_pushes(self):
        policy, admission, anchor = self.fixture()
        commands = []

        def git(source, *args):
            commands.append(args)
            if args[0] == 'ls-remote':
                ref = args[-1]
                sha = policy.launcher if ref == 'refs/heads/main' else anchor['commit']
                return sha + '\t' + ref
            return ''

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, GITHUB_ACTIONS='false'),
            patch.object(coordinator, 'git', side_effect=git),
            patch.object(coordinator.subprocess, 'check_output', return_value='token'),
            patch.object(coordinator, 'ProofAPI'),
            patch.object(coordinator, 'verify_signed_merge'),
            patch.object(coordinator, 'verify_admission_association'),
            patch.object(
                coordinator,
                'read_signed_coordination',
                return_value=SimpleNamespace(
                    commit=anchor['commit'], record={'state': 'idle'}
                ),
            ),
        ):
            journal = Path(directory) / 'must-not-exist'
            result = coordinator.coordinate(
                '.',
                policy,
                'Signed-off-by: Fixture <x@example.invalid>',
                directory,
                admission,
                anchor,
                journal,
            )
            self.assertEqual(result['mode'], 'dry-run')
            self.assertFalse(result['merge_authorized'])
            self.assertFalse(journal.exists())
        self.assertTrue(all(args[0] in {'ls-remote', 'fetch'} for args in commands))

    def exercise(self, failure=None):
        policy, admission, anchor = self.fixture()
        records = {
            anchor['commit']: {
                'version': 1,
                'repository_id': policy.repository_id,
                'target_ref': 'refs/heads/main',
                'epoch': anchor['epoch'],
                'cutover_digest': anchor['cutover_digest'],
                'generation': 0,
                'state': 'idle',
                'operation': None,
                'grant': None,
                'completion': None,
            }
        }
        state = {
            'tip': anchor['commit'],
            'next': 10,
            'pending': None,
            'pushes': [],
            'dispatches': 0,
        }

        def git(source, *args):
            if args[0] == 'ls-remote':
                ref = args[-1]
                sha = policy.launcher if ref == 'refs/heads/main' else state['tip']
                return sha + '\t' + ref
            if args[0] == 'push':
                self.assertIn(
                    '--force-with-lease='
                    + coordinator.COORDINATION_REF
                    + ':'
                    + state['tip'],
                    args,
                )
                sha, ref = args[-1].split(':', 1)
                self.assertEqual(ref, coordinator.COORDINATION_REF)
                state['pushes'].append(records[sha]['state'])
                state['tip'] = sha
                if failure == 'push-timeout':
                    raise subprocess.TimeoutExpired('git', 30)
                if failure == 'readback':
                    state['tip'] = 'f' * 40
            return ''

        def output(command, **kwargs):
            if command[:3] == ['gh', 'auth', 'token']:
                return 'fixture-token'
            if command[:2] == ['gh', 'api']:
                if command[-1].endswith('/zip'):
                    return b'fixture archive'
                names = [
                    'coordination-request-500-1',
                    'coordination-claim-500-1',
                    'native-qualification-500-1',
                    'native-admission-500-1',
                ]
                return json.dumps(
                    {
                        'total_count': 4,
                        'artifacts': [
                            {'name': name, 'id': i + 800, 'size_in_bytes': 20}
                            for i, name in enumerate(names)
                        ],
                    }
                ).encode()
            if 'hash-object' in command:
                state['pending'] = json.loads(kwargs['input'])
                return b'b' * 40 + b'\n'
            if 'mktree' in command:
                return b'c' * 40 + b'\n'
            if 'commit-tree' in command:
                sha = f'{state["next"]:040x}'
                state['next'] += 1
                records[sha] = state['pending']
                return (sha + '\n').encode()
            self.fail(command)

        def signed(source, sha, **kwargs):
            return SimpleNamespace(commit=sha, record=records[sha])

        def grant(*args, **kwargs):
            op = records[state['tip']]['operation']
            identity = {
                'schema_version': 1,
                'admission': admission,
                'execution': {
                    'workflow_id': policy.workflow_id,
                    'run_id': 500,
                    'run_number': 12,
                    'run_attempt': 1,
                    'launcher': policy.launcher,
                    'nonce': op['nonce'],
                },
            }
            qid = hashlib.sha256(
                json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()
            ).hexdigest()
            return {
                'run_id': 500,
                'run_number': 12,
                'run_attempt': 1,
                'qualification_id': qid,
                'native_job_id': None,
                'worker_nonce': None,
                'finalizer_nonce': None,
            }

        def running(*args, **kwargs):
            return dict(grant(), native_job_id=702, worker_nonce='6' * 32)

        def run(command, **kwargs):
            if command[:3] == ['gh', 'workflow', 'run']:
                state['dispatches'] += 1
                if failure == 'dispatch-timeout':
                    raise subprocess.TimeoutExpired(command, 60)
                if failure == 'ambiguous-dispatch':
                    return subprocess.CompletedProcess(command, 0, '', '')
            if command[:3] == ['gh', 'run', 'watch'] and failure == 'workflow-failure':
                raise subprocess.CalledProcessError(1, command)
            return subprocess.CompletedProcess(
                command,
                0,
                'https://github.com/archledger/plasma-fusion-kwin/actions/runs/500\n',
                '',
            )

        def evidence(*args, **kwargs):
            if failure == 'qualification':
                raise ValueError('unverified native cleanup')
            return SimpleNamespace(
                qualification_id=records[state['tip']]['grant']['qualification_id']
            )

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, GITHUB_ACTIONS='false'),
            patch.object(coordinator, 'git', side_effect=git),
            patch.object(coordinator.subprocess, 'check_output', side_effect=output),
            patch.object(coordinator.subprocess, 'run', side_effect=run),
            patch.object(coordinator, 'ProofAPI'),
            patch.object(coordinator, 'verify_signed_merge'),
            patch.object(coordinator, 'verify_admission_association'),
            patch.object(coordinator, 'read_signed_coordination', side_effect=signed),
            patch.object(coordinator, 'verify_active_request', side_effect=grant),
            patch.object(coordinator, 'verify_worker_claim', side_effect=running),
            patch.object(coordinator, 'verify_qualification', side_effect=evidence),
            patch.object(
                coordinator,
                'verify_current_association',
                side_effect=ValueError('stale association')
                if failure == 'association'
                else None,
            ),
        ):
            journal = Path(directory) / 'journal.json'
            result = None
            try:
                result = coordinator.coordinate(
                    '.',
                    policy,
                    'Signed-off-by: Fixture <x@example.invalid>',
                    directory,
                    admission,
                    anchor,
                    journal,
                    apply=True,
                )
            except (ValueError, subprocess.SubprocessError) as error:
                state['error'] = type(error).__name__
            state['journal'] = json.loads(journal.read_text())
        return state, result, records

    def test_coordinator_appends_only_one_operation_and_never_updates_main(self):
        state, result, records = self.exercise()
        self.assertEqual(
            state['pushes'], ['reserved', 'dispatching', 'granted', 'running', 'idle']
        )
        self.assertFalse(result['merge_authorized'])
        self.assertEqual(
            records[state['tip']]['completion']['outcome'], 'qualified-only'
        )

    def test_unknown_push_never_dispatches_or_releases(self):
        for failure in ['push-timeout', 'readback']:
            with self.subTest(failure=failure):
                state, result, _ = self.exercise(failure)
                self.assertIsNone(result)
                self.assertEqual(state['dispatches'], 0)
                self.assertEqual(state['pushes'], ['reserved'])
                self.assertEqual(
                    state['journal']['transitions'][-1]['outcome'], 'unknown'
                )

    def test_unknown_dispatch_never_grants_or_releases(self):
        for failure in ['dispatch-timeout', 'ambiguous-dispatch']:
            with self.subTest(failure=failure):
                state, result, _ = self.exercise(failure)
                self.assertIsNone(result)
                self.assertEqual(state['pushes'], ['reserved', 'dispatching'])
                self.assertEqual(state['journal']['status'], 'dispatch-unknown')

    def test_failed_workflow_proof_or_association_keeps_running_held(self):
        for failure in ['workflow-failure', 'qualification', 'association']:
            with self.subTest(failure=failure):
                state, result, _ = self.exercise(failure)
                self.assertIsNone(result)
                self.assertEqual(
                    state['pushes'], ['reserved', 'dispatching', 'granted', 'running']
                )
                self.assertNotEqual(state['journal']['status'], 'qualified-only')


if __name__ == '__main__':
    unittest.main()
