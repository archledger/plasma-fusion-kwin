# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
import copy
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

from ci.native.checks import CheckReporter, CheckWriteError
from tools import probe_check_lifecycle as probe


class CheckLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.policy = SimpleNamespace(
            repository='owner/repo',
            repository_id=100,
            app_id=300,
            check_name='Validation / check lifecycle #500',
        )
        self.writes, self.saved = [], []
        self.server = None
        self.ignore_clear = False
        self.client = CheckReporter(self.policy, 'fixture', 500, lambda: None)

    def send(self, method, resource, body=None):
        if method == 'GET':
            return copy.deepcopy(self.server)
        self.writes.append((method, resource, copy.deepcopy(body)))
        if method == 'POST':
            self.server = {
                'id': 800,
                'app': {'id': 300},
                'conclusion': None,
                'completed_at': None,
            }
        changes = dict(body)
        if self.ignore_clear:
            for key in ['conclusion', 'completed_at']:
                if changes.get(key, 'absent') is None:
                    changes.pop(key)
        self.server.update(changes)
        if self.server['conclusion'] is not None:
            self.server.update(status='completed', completed_at='2026-10-31T01:00:00Z')
        return copy.deepcopy(self.server)

    def execute(self):
        with (
            patch(
                'ci.native.checks.get_all',
                side_effect=lambda *a, **k: (
                    [] if self.server is None else [copy.deepcopy(self.server)]
                ),
            ),
            patch.object(self.client, '_send', side_effect=self.send),
        ):
            return probe.exercise(
                self.client,
                'b' * 40,
                500,
                lambda value: self.saved.append(copy.deepcopy(value)),
            )

    def test_failure_and_success_reopen_then_end_failed(self):
        result = self.execute()
        self.assertTrue(result['success'])
        self.assertIsNone(result['pending_write'])
        self.assertEqual(
            [item['phase'] for item in result['observations']],
            [
                'initial-failure',
                'legacy-update',
                'reopened-failure',
                'completed-success',
                'reopened-success',
                'final-failure',
            ],
        )
        self.assertEqual(self.server['conclusion'], 'failure')
        resets = [
            body
            for _, _, body in self.writes
            if body.get('conclusion', 'absent') is None
        ]
        self.assertEqual(len(resets), 2)
        self.assertTrue(all(body['completed_at'] is None for body in resets))
        self.assertEqual(
            len({item['external_id'] for item in result['observations']}), 3
        )

    def test_interruption_preserves_write_intent(self):
        with (
            patch.object(self, 'send', side_effect=KeyboardInterrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.execute()
        self.assertFalse(self.saved[-1]['success'])
        pending = self.saved[-1]['pending_write']
        self.assertEqual((pending['phase'], pending['action']), ('create', 'begin'))
        self.assertIsNone(pending['check_id'])
        self.assertEqual(len(pending['external_id']), 64)

    def test_failure_report_records_safe_http_cause(self):
        error = CheckWriteError('private-message')
        error.__cause__ = HTTPError(
            'https://private.invalid/token', 422, 'private-message', {}, None
        )
        with (
            patch.object(self, 'send', side_effect=error),
            self.assertRaises(CheckWriteError),
        ):
            self.execute()
        self.assertEqual(self.saved[-1]['diagnostics']['kind'], 'http')
        self.assertEqual(self.saved[-1]['diagnostics']['http_status'], 422)
        self.assertNotIn('private', str(self.saved[-1]))

    def test_uncleared_state_stops_without_another_write(self):
        self.ignore_clear = True
        with self.assertRaises(CheckWriteError):
            self.execute()
        self.assertFalse(self.saved[-1]['success'])
        self.assertEqual(self.saved[-1]['failed_phase'], 'reopened-failure')
        self.assertEqual(self.saved[-1]['error_type'], 'CheckWriteError')
        self.assertEqual(len(self.writes), 4)

    def test_legacy_control_is_fenced_and_errors_do_not_leak(self):
        calls = []

        def fence():
            calls.append(True)
            if len(calls) == 3:
                raise ValueError('private-fixture-value')

        self.client._before_write = fence
        with self.assertRaises(ValueError):
            self.execute()
        self.assertEqual(len(self.writes), 2)
        self.assertEqual(self.saved[-1]['failed_phase'], 'legacy-update')
        self.assertNotIn('private-fixture-value', str(self.saved[-1]))

    def test_legacy_control_must_reproduce(self):
        original = self.send

        def clearing_legacy(method, resource, body=None):
            if body and method == 'PATCH' and body.get('status') == 'in_progress':
                body = dict(body, conclusion=None, completed_at=None)
            return original(method, resource, body)

        with (
            patch.object(self, 'send', side_effect=clearing_legacy),
            self.assertRaises(ValueError),
        ):
            self.execute()
        self.assertEqual(self.saved[-1]['failed_phase'], 'legacy-update')


class ProbeBoundaryTest(unittest.TestCase):
    def test_report_size_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'report.json'
            with self.assertRaises(ValueError):
                probe.write_report(path, {'data': 'x' * probe.MAX_REPORT_BYTES})
            self.assertFalse(path.exists())

    def test_run_and_actor_must_remain_authorized(self):
        run = {
            'id': 500,
            'repository': {'id': probe.REPOSITORY_ID, 'full_name': probe.REPOSITORY},
            'head_repository': {
                'id': probe.REPOSITORY_ID,
                'full_name': probe.REPOSITORY,
            },
            'head_sha': 'a' * 40,
            'head_branch': 'main',
            'path': probe.WORKFLOW,
            'event': 'workflow_dispatch',
            'run_attempt': 1,
            'workflow_id': 600,
            'status': 'in_progress',
            'conclusion': None,
            'actor': {'id': 700, 'login': 'owner'},
            'triggering_actor': {'id': 700},
        }
        permission = {'user': {'id': 700}, 'permission': 'write'}
        api = SimpleNamespace(
            get=lambda path: run if path.startswith('actions/') else permission
        )
        probe.verify_run(api, 500, 'a' * 40)
        for field, value in [
            ('head_sha', 'b' * 40),
            ('status', 'completed'),
            ('run_attempt', 2),
            ('path', '.github/workflows/other.yml'),
            ('event', 'pull_request'),
        ]:
            with self.subTest(field=field):
                bad = copy.deepcopy(run)
                bad[field] = value
                with self.assertRaises(ValueError):
                    probe.verify_run(
                        SimpleNamespace(get=lambda path, bad=bad: bad), 500, 'a' * 40
                    )
        permission['permission'] = 'read'
        with self.assertRaises(ValueError):
            probe.verify_run(api, 500, 'a' * 40)

    def test_non_main_context_refused_before_api_creation(self):
        with (
            patch.dict(
                os.environ,
                {
                    'GITHUB_ACTIONS': 'true',
                    'GITHUB_REPOSITORY': probe.REPOSITORY,
                    'GITHUB_REF': 'refs/heads/untrusted',
                    'GITHUB_EVENT_NAME': 'workflow_dispatch',
                    'GITHUB_RUN_ATTEMPT': '1',
                },
            ),
            patch.object(probe, 'ProofAPI') as api,
            self.assertRaises(ValueError),
        ):
            probe.runtime_context()
        api.assert_not_called()

    def test_fence_binds_all_three_refs(self):
        refs = {
            'refs/heads/main': 'a' * 40,
            probe.AUTHORITY_REF: 'c' * 40,
            probe.PROBE_REF: 'b' * 40,
        }
        with (
            patch.object(probe, 'verify_run'),
            patch.object(probe, 'read_refs', return_value=refs),
        ):
            self.assertEqual(
                probe.check_fence(
                    None, 500, 'a' * 40, 'c' * 40, None, work=Path('/unused')
                ),
                'b' * 40,
            )
            for key in refs:
                changed = dict(refs)
                changed[key] = 'd' * 40
                with (
                    patch.object(probe, 'read_refs', return_value=changed),
                    self.assertRaises(ValueError),
                ):
                    probe.check_fence(
                        None, 500, 'a' * 40, 'c' * 40, 'b' * 40, work=Path('/unused')
                    )

    def test_git_probe_does_not_inherit_credentials(self):
        rows = '\n'.join(
            f'{sha * 40}\t{ref}'
            for sha, ref in [
                ('a', 'refs/heads/main'),
                ('b', probe.PROBE_REF),
                ('c', probe.AUTHORITY_REF),
            ]
        )
        with (
            patch.dict(
                os.environ,
                {'GH_TOKEN': 'private-token', 'NATIVE_GATE_PRIVATE_KEY': 'private-key'},
            ),
            patch.object(
                probe.subprocess,
                'run',
                return_value=subprocess.CompletedProcess([], 0, rows, ''),
            ) as run,
        ):
            probe.read_refs(Path('/unused'))
        self.assertNotIn('GH_TOKEN', run.call_args.kwargs['env'])
        self.assertNotIn('NATIVE_GATE_PRIVATE_KEY', run.call_args.kwargs['env'])

    def test_failed_preflight_never_mints_app_token(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {'RUNNER_TEMP': directory}),
            patch.object(
                probe, 'runtime_context', return_value=(None, 500, 'a' * 40, 'c' * 40)
            ),
            patch.object(probe, 'check_fence', side_effect=ValueError('changed ref')),
            patch.object(probe.CheckReporter, 'from_key') as mint,
        ):
            self.assertEqual(probe.main(), 1)
        mint.assert_not_called()

    def test_main_passes_only_a_validation_target_to_the_app(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(
                os.environ,
                {'RUNNER_TEMP': directory, 'NATIVE_GATE_PRIVATE_KEY': 'fixture'},
            ),
            patch.object(
                probe, 'runtime_context', return_value=(None, 500, 'a' * 40, 'c' * 40)
            ),
            patch.object(probe, 'check_fence', return_value='b' * 40),
            patch.object(probe.CheckReporter, 'from_key') as mint,
            patch.object(
                probe, 'exercise', return_value={'success': True, 'scope': 'fixture'}
            ) as exercise,
        ):
            self.assertEqual(probe.main(), 0)
        target = mint.call_args.args[0]
        self.assertEqual(target.repository, probe.REPOSITORY)
        self.assertEqual(target.app_id, probe.APP_ID)
        self.assertEqual(target.check_name, 'Validation / check lifecycle #500')
        self.assertEqual(exercise.call_args.args[1:3], ('b' * 40, 500))

    def test_probe_workflow_is_manual_main_only_and_has_no_native_job(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / '.github/workflows/check-lifecycle-probe.yml').read_text()
        self.assertIn('workflow_dispatch:', text)
        self.assertIn("github.ref == 'refs/heads/main'", text)
        self.assertIn('environment: native-gate-reporter', text)
        self.assertNotIn('contents: write', text)
        self.assertNotIn('workflow_call:', text)
        self.assertNotIn('pull_request:', text)
        self.assertNotIn('tools.trusted_native', text)


if __name__ == '__main__':
    unittest.main()
