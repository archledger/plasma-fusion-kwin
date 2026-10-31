# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
import copy
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

from ci.native.check_journal import CheckJournal
from ci.native.checks import CheckReporter, CheckWriteError
from tools import probe_check_lifecycle as probe


class FreshProbeTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.server, self.writes, self.saved = {}, [], []
        self.policy = SimpleNamespace(
            repository=probe.REPOSITORY,
            repository_id=probe.REPOSITORY_ID,
            app_id=probe.APP_ID,
            check_name='Validation / check lifecycle native',
            activation_name='Validation / check lifecycle activation',
        )

    def send(self, method, resource, body=None):
        if method == 'POST':
            check_id = len(self.server) + 800
            self.server[check_id] = dict(
                body,
                id=check_id,
                app={'id': probe.APP_ID},
                conclusion=None,
                completed_at=None,
            )
        else:
            check_id = int(resource.split('/')[-1])
            if method == 'PATCH':
                self.server[check_id].update(body, completed_at='2026-10-31T01:00:00Z')
        if method != 'GET':
            self.writes.append((method, resource, copy.deepcopy(body)))
        return copy.deepcopy(self.server[check_id])

    def execute(self, stage='A', previous=None):
        run_id = 500 + ord(stage) - ord('A')
        nonce = str(ord(stage) - ord('A') + 1) * 32
        qid = probe.probe_qid(run_id, 'b' * 40, nonce)
        journals = {
            phase: CheckJournal(
                Path(self.directory.name) / f'{stage}-{phase}.json',
                qualification_id=qid,
                run_id=run_id,
                nonce=nonce,
                phase=phase,
            )
            for phase in ['admit', 'report']
        }
        self.client = CheckReporter(
            self.policy, 'fixture', run_id, lambda: None, journal=journals['admit']
        )
        with (
            patch(
                'ci.native.checks.get_all',
                side_effect=lambda *a, **k: list(self.server.values()),
            ),
            patch.object(
                probe, 'get_all', side_effect=lambda *a, **k: list(self.server.values())
            ),
            patch.object(self.client, '_send', side_effect=self.send),
        ):
            return probe.exercise(
                self.client,
                'b' * 40,
                run_id,
                lambda value: self.saved.append(copy.deepcopy(value)),
                stage=stage,
                launcher='a' * 40,
                authority='c' * 40,
                nonce=nonce,
                report_journal=journals['report'],
                previous=previous,
            )

    def test_each_actual_run_creates_one_pair_and_preserves_earlier_results(self):
        first = self.execute()
        old = copy.deepcopy(self.server)
        second = self.execute('B', first)
        third = self.execute('C', second)
        self.assertTrue(all(r['success'] for r in [first, second, third]))
        self.assertEqual(
            [r['outcome'] for r in [first, second, third]],
            ['failure', 'success', 'failure'],
        )
        self.assertEqual(
            [w[0] for w in self.writes], ['POST', 'POST', 'PATCH', 'PATCH'] * 3
        )
        self.assertEqual({i: self.server[i] for i in old}, old)
        self.assertEqual(len({v['external_id'] for v in self.server.values()}), 3)
        self.assertEqual(len({v['name'] for v in self.server.values()}), 2)

    def test_uncertain_second_create_stops_before_any_completion(self):
        original = self.send

        def failing(method, resource, body=None):
            if method == 'POST' and self.server:
                raise OSError('private transport error')
            return original(method, resource, body)

        with (
            patch.object(self, 'send', side_effect=failing),
            self.assertRaises(CheckWriteError),
        ):
            self.execute()
        self.assertEqual([w[0] for w in self.writes], ['POST'])
        self.assertFalse(self.saved[-1]['success'])
        self.assertNotIn('private transport', json.dumps(self.saved[-1]))
        state = json.loads((Path(self.directory.name) / 'A-admit.json').read_text())
        self.assertEqual(
            [w['status'] for w in state['writes']], ['confirmed', 'pending']
        )

    def test_changed_predecessor_check_stops_before_new_write(self):
        first = self.execute()
        self.server[800]['conclusion'] = 'success'
        self.writes.clear()
        with self.assertRaises(ValueError):
            self.execute('B', first)
        self.assertEqual(self.writes, [])

    def predecessor_fixture(self):
        first = self.execute()
        run = {'workflow_id': 600, 'created_at': '2026-10-31T02:00:00Z'}
        old = dict(run, id=500)
        job = {
            'id': 700,
            'name': 'probe',
            'run_id': 500,
            'run_attempt': 1,
            'head_sha': 'a' * 40,
            'status': 'completed',
            'conclusion': 'success',
            'started_at': '2026-10-31T00:00:00Z',
            'completed_at': '2026-10-31T01:30:00Z',
        }
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            archive.writestr('report.json', json.dumps(first))
        raw = output.getvalue()
        artifact = {
            'id': 900,
            'name': 'check-lifecycle-500-1',
            'expired': False,
            'size_in_bytes': len(raw),
            'created_at': '2026-10-31T01:15:00Z',
            'digest': 'sha256:' + hashlib.sha256(raw).hexdigest(),
            'workflow_run': {
                'id': 500,
                'repository_id': probe.REPOSITORY_ID,
                'head_repository_id': probe.REPOSITORY_ID,
                'head_sha': 'a' * 40,
            },
        }
        return first, run, old, job, artifact, raw

    def load_fixture(self, run, old, jobs, artifacts, raw):
        with (
            patch.object(probe, 'verify_run', side_effect=[run, old]),
            patch.object(probe, 'get_all', side_effect=[jobs, artifacts]),
            patch.object(probe, 'download_artifact', return_value=raw),
            patch.dict(os.environ, {'GH_TOKEN': 'fixture'}),
        ):
            return probe.load_predecessor(
                None, 'B', '500', 501, 'a' * 40, 'c' * 40, 'b' * 40
            )

    def test_successor_requires_authenticated_predecessor_artifact(self):
        first, run, old, job, artifact, raw = self.predecessor_fixture()
        result = self.load_fixture(run, old, [job], [artifact], raw)
        self.assertEqual(result, first)

    def test_wrong_job_artifact_digest_association_and_time_are_rejected(self):
        _first, run, old, job, artifact, raw = self.predecessor_fixture()
        cases = [
            ([dict(job, conclusion='failure')], [artifact], raw),
            ([dict(job, run_attempt=2)], [artifact], raw),
            ([dict(job, head_sha='d' * 40)], [artifact], raw),
            ([dict(job, completed_at='2026-10-31T03:00:00Z')], [artifact], raw),
            ([job, job], [artifact], raw),
            ([job], [], raw),
            ([job], [artifact, artifact], raw),
            ([job], [dict(artifact, expired=True)], raw),
            ([job], [dict(artifact, digest='sha256:' + 'f' * 64)], raw),
            ([job], [dict(artifact, created_at='2026-10-30T00:00:00Z')], raw),
            (
                [job],
                [dict(artifact, workflow_run=dict(artifact['workflow_run'], id=501))],
                raw,
            ),
        ]
        for jobs, artifacts, data in cases:
            with (
                self.subTest(jobs=jobs, artifacts=artifacts),
                self.assertRaises(ValueError),
            ):
                self.load_fixture(run, old, jobs, artifacts, data)
        self.assertEqual(
            len(self.writes), 4
        )  # Fixture only; verifier never creates a check.

    def test_authenticated_bytes_still_need_exact_report_contract(self):
        first, run, old, job, artifact, _ = self.predecessor_fixture()
        for field, value in [
            ('schema_version', 1),
            ('stage', 'B'),
            ('success', False),
            ('run_id', 501),
            ('authority', 'e' * 40),
            ('head', 'f' * 40),
        ]:
            changed = dict(first, **{field: value})
            output = io.BytesIO()
            with zipfile.ZipFile(output, 'w') as archive:
                archive.writestr('report.json', json.dumps(changed))
            raw = output.getvalue()
            item = dict(
                artifact,
                size_in_bytes=len(raw),
                digest='sha256:' + hashlib.sha256(raw).hexdigest(),
            )
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.load_fixture(run, old, [job], [item], raw)

    def test_first_stage_rejects_a_predecessor_and_successor_requires_one(self):
        for stage, previous in [('A', '500'), ('B', ''), ('B', '501'), ('C', '0')]:
            with (
                self.subTest(stage=stage, previous=previous),
                self.assertRaises(ValueError),
            ):
                probe.load_predecessor(
                    None, stage, previous, 501, 'a' * 40, 'c' * 40, 'b' * 40
                )

    def test_interruption_preserves_durable_write_intent(self):
        with (
            patch.object(self, 'send', side_effect=KeyboardInterrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.execute()
        self.assertFalse(self.saved[-1]['success'])
        state = json.loads((Path(self.directory.name) / 'A-admit.json').read_text())
        self.assertEqual(state['writes'][0]['status'], 'pending')
        self.assertIsNone(state['writes'][0]['acknowledged_check_id'])

    def test_failure_report_records_safe_http_422_cause(self):
        error = CheckWriteError('private-message')
        error.__cause__ = HTTPError(
            'https://private.invalid/token', 422, 'private-message', {}, None
        )
        self.addCleanup(error.__cause__.close)
        with (
            patch.object(self, 'send', side_effect=error),
            self.assertRaises(CheckWriteError),
        ):
            self.execute()
        self.assertEqual(self.saved[-1]['diagnostics']['kind'], 'http')
        self.assertEqual(self.saved[-1]['diagnostics']['http_status'], 422)
        self.assertNotIn('private', str(self.saved[-1]))

    def test_changed_fence_stops_before_completion_without_leaking(self):
        original = CheckReporter._context
        calls = []

        def context(client, *args):
            role = original(client, *args)
            calls.append(role)
            if len(calls) == 3:
                client._before_write = lambda: (_ for _ in ()).throw(
                    ValueError('private-marker')
                )
            return role

        with (
            patch.object(CheckReporter, '_context', context),
            self.assertRaises(CheckWriteError),
        ):
            self.execute()
        self.assertEqual([w[0] for w in self.writes], ['POST', 'POST'])
        self.assertNotIn('private-marker', str(self.saved[-1]))


class ProbeBoundaryTest(unittest.TestCase):
    def test_artifact_stream_is_capped_and_credentials_are_scoped(self):
        start = subprocess.Popen
        for count in [16, 17]:
            processes = []

            def child(command, *, count=count, processes=processes, **options):
                self.assertEqual(
                    command[-1], f'repos/{probe.REPOSITORY}/actions/artifacts/900/zip'
                )
                self.assertNotIn('NATIVE_GATE_PRIVATE_KEY', options['env'])
                process = start(
                    [sys.executable, '-c', f'import os; os.write(1, b"x"*{count})'],
                    **options,
                )
                processes.append(process)
                return process

            with (
                self.subTest(count=count),
                patch.object(probe.subprocess, 'Popen', child),
                patch.object(probe, 'MAX_DOWNLOAD_BYTES', 16, create=True),
                patch.dict(
                    os.environ,
                    {'GH_TOKEN': 'fixture', 'NATIVE_GATE_PRIVATE_KEY': 'private'},
                ),
            ):
                if count == 16:
                    self.assertEqual(probe.download_artifact(900), b'x' * 16)
                else:
                    with self.assertRaises(ValueError):
                        probe.download_artifact(900)
                self.assertIsNotNone(processes[0].poll())

    def test_artifact_failure_and_deadline_reap_the_owned_process(self):
        start = subprocess.Popen
        for expired in [False, True]:
            processes = []

            def child(command, *, processes=processes, **options):
                process = start(
                    [sys.executable, '-c', 'raise SystemExit(2)'], **options
                )
                processes.append(process)
                return process

            with (
                self.subTest(expired=expired),
                patch.object(probe.subprocess, 'Popen', child),
                patch.dict(os.environ, {'GH_TOKEN': 'fixture'}),
            ):
                if expired:
                    with (
                        patch.object(probe.time, 'monotonic', side_effect=[100, 161]),
                        self.assertRaises(ValueError),
                    ):
                        probe.download_artifact(900)
                else:
                    with self.assertRaises(ValueError):
                        probe.download_artifact(900)
                self.assertIsNotNone(processes[0].poll())

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
                {
                    'RUNNER_TEMP': directory,
                    'NATIVE_GATE_PRIVATE_KEY': 'fixture',
                    'CHECK_PROBE_STAGE': 'A',
                    'CHECK_PREDECESSOR_RUN': '',
                },
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
            self.assertTrue(
                (Path(directory) / 'check-lifecycle-probe/admit-journal.json').is_file()
            )
            self.assertTrue(
                (
                    Path(directory) / 'check-lifecycle-probe/report-journal.json'
                ).is_file()
            )
            self.assertNotEqual(
                exercise.call_args.kwargs['report_journal'].path.name, 'report.json'
            )
        target = mint.call_args.args[0]
        self.assertEqual(target.repository, probe.REPOSITORY)
        self.assertEqual(target.app_id, probe.APP_ID)
        self.assertEqual(target.check_name, probe.NAMES['native'])
        self.assertEqual(target.activation_name, probe.NAMES['activation'])
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
        self.assertIn('CHECK_PREDECESSOR_RUN:', text)
        self.assertIn('check-lifecycle-journals-${{ github.run_id }}-1', text)


if __name__ == '__main__':
    unittest.main()
