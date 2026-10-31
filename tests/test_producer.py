# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
import copy
import hashlib
import io
import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import test_qualification as qualification_fixtures

from ci.native import producer
from ci.native.producer import read_files, source_matches
from ci.native.qualification import canonical_json


class ProducerTest(unittest.TestCase):
    def test_candidate_materialization_uses_only_verified_data_blobs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / 'repo'
            repo.mkdir()
            work = root / 'work'
            work.mkdir()
            env = {
                'PATH': os.defpath,
                'HOME': directory,
                'GIT_CONFIG_NOSYSTEM': '1',
                'GIT_CONFIG_GLOBAL': os.devnull,
                'GIT_AUTHOR_NAME': 'Fixture',
                'GIT_AUTHOR_EMAIL': 'fixture@example.invalid',
                'GIT_COMMITTER_NAME': 'Fixture',
                'GIT_COMMITTER_EMAIL': 'fixture@example.invalid',
            }

            def git(*args):
                return subprocess.check_output(
                    ['git', *args], cwd=repo, env=env, text=True, stderr=subprocess.PIPE
                ).strip()

            git('init', '-q')
            (repo / 'README.md').write_text('fixture\n')
            git('add', '.')
            git('commit', '-qm', 'base')
            base = git('rev-parse', 'HEAD')
            source = Path(__file__).resolve().parents[1]
            manifest = json.loads((source / 'patchset.json').read_text())
            paths = ['patchset.json', *[p['file'] for p in manifest['patches']]]
            digests = []
            for name in paths:
                target = repo / name
                target.parent.mkdir(exist_ok=True)
                raw = (source / name).read_bytes()
                target.write_bytes(raw)
                digests.append(
                    {'path': name, 'sha256': hashlib.sha256(raw).hexdigest()}
                )
            git('add', '.')
            git('commit', '-qm', 'candidate')
            head = git('rev-parse', 'HEAD')
            ctx = SimpleNamespace(
                work=work,
                objects=repo,
                fetch=lambda sha: None,
                git=git,
                policy=SimpleNamespace(upstream_commit=manifest['upstream']['commit']),
                signed=SimpleNamespace(base=base, commit=head),
                admission={
                    'payload_sha256': hashlib.sha256(
                        canonical_json(sorted(digests, key=lambda d: d['path']))
                    ).hexdigest()
                },
            )
            candidate, parsed = producer.candidate_data(ctx)
            self.assertEqual(
                {
                    str(p.relative_to(candidate))
                    for p in candidate.rglob('*')
                    if p.is_file()
                },
                set(paths),
            )
            self.assertEqual(parsed.commit, manifest['upstream']['commit'])
            (repo / 'README.md').write_text('changed control plane\n')
            git('add', '.')
            git('commit', '-qm', 'forbidden')
            ctx.signed = SimpleNamespace(base=head, commit=git('rev-parse', 'HEAD'))
            with self.assertRaises(ValueError):
                producer.candidate_data(ctx)

    def test_preparer_requires_its_private_no_device_container(self):
        token = 'a' * 32
        name = 'pf-prepare-' + token
        volume = name + '-data'
        info = {
            'Id': 'b' * 64,
            'Name': '/' + name,
            'Image': 'sha256:' + producer.BUILDER_CONFIG,
            'Config': {
                'Image': producer.BUILDER_REFERENCE,
                'User': '1000:1000',
                'Labels': {producer.LABEL: token},
            },
            'AppArmorProfile': 'docker-default',
            'Mounts': [
                {'Type': 'volume', 'Name': volume, 'Destination': '/work', 'RW': True}
            ],
            'HostConfig': {
                'ReadonlyRootfs': True,
                'Privileged': False,
                'NetworkMode': 'none',
                'Binds': None,
                'Devices': [],
                'DeviceRequests': None,
                'CapDrop': ['ALL'],
                'CapAdd': None,
                'Memory': 2 * 1024**3,
                'MemorySwap': 2 * 1024**3,
                'PidsLimit': 256,
                'NanoCpus': 2 * 10**9,
                'CgroupnsMode': 'private',
                'PidMode': 'private',
                'IpcMode': 'private',
                'UTSMode': 'private',
                'RestartPolicy': {'Name': 'no'},
                'SecurityOpt': ['no-new-privileges', 'apparmor=docker-default'],
            },
        }
        producer.validate_preparer(info, name, token, volume)
        for field, value in [
            ('Privileged', True),
            ('NetworkMode', 'host'),
            ('Devices', [{'PathOnHost': '/dev/kvm'}]),
            ('Binds', ['/tmp:/host']),
            ('Memory', 0),
            ('CapAdd', ['SYS_ADMIN']),
        ]:
            changed = copy.deepcopy(info)
            changed['HostConfig'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                producer.validate_preparer(changed, name, token, volume)
        changed = copy.deepcopy(info)
        changed['Mounts'].append({'Type': 'bind', 'Destination': '/host'})
        with self.assertRaises(ValueError):
            producer.validate_preparer(changed, name, token, volume)

    def archive(self, entries):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            for name, data in entries:
                archive.writestr(name, data)
        return buffer.getvalue()

    def test_only_exact_bounded_regular_artifact_members_are_read(self):
        raw = self.archive([('input.json', b'{}'), ('source.tar', b'abc')])
        digest = 'sha256:' + hashlib.sha256(raw).hexdigest()
        self.assertEqual(
            read_files(raw, digest, {'input.json': 10, 'source.tar': 10}),
            {'input.json': b'{}', 'source.tar': b'abc'},
        )
        for content in [
            self.archive([('../input.json', b'{}')]),
            self.archive([('input.json', b'x' * 11)]),
            self.archive([('input.json', b'{}'), ('input.json', b'{}')]),
        ]:
            with self.subTest(content=content), self.assertRaises(ValueError):
                read_files(
                    content,
                    'sha256:' + hashlib.sha256(content).hexdigest(),
                    {'input.json': 10},
                )

    def test_source_bytes_must_match_admitted_digest_and_length(self):
        source = b'archive bytes'
        expected = {
            'tree': 'a' * 40,
            'sha256': hashlib.sha256(source).hexdigest(),
            'bytes': len(source),
        }
        source_matches(source, expected)
        with self.assertRaises(ValueError):
            source_matches(source + b'x', expected)


class ProducerIntegrationTest(unittest.TestCase):
    def setUp(self):
        fixture = qualification_fixtures.QualificationTest()
        fixture.setUp()
        self.fixture = fixture
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.current = SimpleNamespace(
            commit='f' * 40,
            record={
                'state': 'running',
                'grant': {
                    'native_job_id': 702,
                    'qualification_id': fixture.receipt['qualification_id'],
                },
            },
        )
        self.ctx = SimpleNamespace(
            work=self.work,
            root=self.work,
            policy=fixture.policy,
            admission=fixture.receipt['identity']['admission'],
            execution=fixture.receipt['identity']['execution'],
            identity=fixture.receipt['identity'],
            qid=fixture.receipt['qualification_id'],
            signed=fixture.signed,
            run_id=500,
            current=lambda: self.current,
            association=MagicMock(),
            api=fixture.api,
            verify_run=MagicMock(),
            jobs=lambda: {
                j['name']: j
                for j in fixture.api.responses['actions/runs/500/attempts/1/jobs'][
                    'jobs'
                ]
            },
        )
        self.receipt = copy.deepcopy(fixture.receipt)
        self.report = copy.deepcopy(fixture.report)
        self.reporter = MagicMock()

    def test_untrusted_branch_fails_before_credentials_or_network(self):
        with (
            patch.dict(
                'os.environ',
                {
                    'GITHUB_REPOSITORY': producer.REPOSITORY,
                    'GITHUB_REF': 'refs/heads/pr',
                    'GITHUB_EVENT_NAME': 'workflow_dispatch',
                    'GITHUB_RUN_ATTEMPT': '1',
                },
            ),
            patch.object(producer, 'ProofAPI') as api,
            self.assertRaises(ValueError),
        ):
            producer.Context()
        api.assert_not_called()

    def test_current_operation_walks_only_two_authenticated_successors(self):
        context = object.__new__(producer.Context)
        context.policy = self.fixture.policy
        context.run_id = 500
        context.qid = self.ctx.qid
        context.execution = self.ctx.execution
        context.objects = self.work
        context.keyring = self.work
        operation = {'id': '1' * 32}
        context.initial = SimpleNamespace(
            commit='a' * 40,
            record={
                'state': 'dispatching',
                'operation': operation,
                'generation': 2,
                'epoch': 'e' * 32,
                'cutover_digest': 'f' * 64,
            },
        )
        grant = {'run_id': 500, 'run_number': 12, 'qualification_id': context.qid}
        records = {
            'b' * 40: SimpleNamespace(
                commit='b' * 40,
                record={
                    'state': 'granted',
                    'operation': operation,
                    'grant': grant,
                    'generation': 3,
                },
            ),
            'c' * 40: SimpleNamespace(
                commit='c' * 40,
                record={
                    'state': 'running',
                    'operation': operation,
                    'grant': dict(grant),
                    'generation': 4,
                },
            ),
        }
        context.git = lambda *args: 'c' * 40 + '\t' + producer.COORDINATION_REF
        context.fetch = MagicMock()

        def raw(source, sha, *args, **kwargs):
            parent = 'a' * 40 if sha == 'b' * 40 else 'b' * 40
            return ('tree ' + 'd' * 40 + '\nparent ' + parent + '\n\nfixture').encode()

        with (
            patch.object(producer, 'read_raw_object', side_effect=raw),
            patch.object(
                producer,
                'read_signed_coordination',
                side_effect=lambda source, sha, **kw: records[sha],
            ) as read,
        ):
            self.assertEqual(context.current().commit, 'c' * 40)
            self.assertEqual(
                [call.kwargs['expected_parent'] for call in read.call_args_list],
                ['a' * 40, 'b' * 40],
            )
            records['c' * 40].record['grant']['run_id'] = 501
            with self.assertRaises(ValueError):
                context.current()

    def test_failed_producer_job_cannot_supply_a_trusted_artifact(self):
        context = object.__new__(producer.Context)
        context.policy = self.fixture.policy
        context.run_id = 500
        context.api = self.fixture.api
        artifact = copy.deepcopy(self.fixture.api.responses['actions/artifacts/900'])
        self.fixture.api.responses['actions/runs/500/artifacts'] = {
            'total_count': 1,
            'artifacts': [artifact],
        }
        self.fixture.api.responses['actions/runs/500/attempts/1/jobs']['jobs'][2][
            'conclusion'
        ] = 'failure'
        with (
            patch.object(producer.subprocess, 'run') as download,
            self.assertRaises(ValueError),
        ):
            context.artifact(artifact['name'], {'report.json': 1024}, 'native')
        download.assert_not_called()

    def test_altered_preparation_stops_before_app_key_use(self):
        metadata = {
            'identity': self.ctx.identity,
            'qualification_id': self.ctx.qid,
            'input_manifest_sha256': '4' * 64,
        }
        self.ctx.artifact = lambda *args: (
            {'prepared.json': canonical_json(metadata), 'patched-source.tar': b'wrong'},
            {'id': 900},
        )
        with (
            patch.object(producer.CheckReporter, 'from_key') as key,
            self.assertRaises(ValueError),
        ):
            producer.admit_job(self.ctx)
        key.assert_not_called()

    def artifact(self, name, limits, role):
        if role == 'native':
            return {
                'qualification.json': canonical_json(self.receipt),
                'report.json': canonical_json(self.report),
            }, {'id': 900}
        if role == 'admit':
            return {
                'admission.json': canonical_json(
                    {
                        'identity': self.ctx.identity,
                        'qualification_id': self.ctx.qid,
                        'input_manifest_sha256': self.receipt['evidence'][
                            'input_manifest_sha256'
                        ],
                    }
                )
            }, {'id': 901}
        raise AssertionError(name)

    def check(self, ctx, head, name):
        return {'id': 800 if name == ctx.policy.check_name else 801}

    def test_reporter_accepts_real_parser_fixture_and_publishes_two_successes(self):
        self.ctx.artifact = self.artifact
        with (
            patch.dict('os.environ', NATIVE_GATE_PRIVATE_KEY='fixture'),
            patch.object(producer, 'check_record', side_effect=self.check),
            patch.object(
                producer.CheckReporter, 'from_key', return_value=self.reporter
            ),
        ):
            producer.report_job(self.ctx)
        self.assertEqual(len(self.reporter.complete.call_args_list), 2)
        self.assertTrue(
            all(
                call.kwargs['success'] for call in self.reporter.complete.call_args_list
            )
        )

    def test_failed_native_evidence_never_publishes_success(self):
        self.ctx.artifact = self.artifact
        self.report['cleanup_confirmed'] = False
        with (
            patch.dict('os.environ', NATIVE_GATE_PRIVATE_KEY='fixture'),
            patch.object(producer, 'check_record', side_effect=self.check),
            patch.object(
                producer.CheckReporter, 'from_key', return_value=self.reporter
            ),
            self.assertRaises(ValueError),
        ):
            producer.report_job(self.ctx)
        self.assertEqual(len(self.reporter.complete.call_args_list), 2)
        self.assertFalse(
            any(
                call.kwargs['success'] for call in self.reporter.complete.call_args_list
            )
        )

    def test_missing_native_artifact_is_reported_as_failure(self):
        self.ctx.artifact = MagicMock(side_effect=ValueError('missing'))
        with (
            patch.dict('os.environ', NATIVE_GATE_PRIVATE_KEY='fixture'),
            patch.object(producer, 'check_record', side_effect=self.check),
            patch.object(
                producer.CheckReporter, 'from_key', return_value=self.reporter
            ),
            self.assertRaises(ValueError),
        ):
            producer.report_job(self.ctx)
        self.assertFalse(
            any(
                call.kwargs['success'] for call in self.reporter.complete.call_args_list
            )
        )

    def test_report_write_fence_rejects_a_changed_operation(self):
        self.ctx.artifact = self.artifact

        def reporter(policy, key, run_id, fence):
            self.ctx.current = lambda: SimpleNamespace(commit='a' * 40)
            fence()

        with (
            patch.dict('os.environ', NATIVE_GATE_PRIVATE_KEY='fixture'),
            patch.object(producer, 'check_record', side_effect=self.check),
            patch.object(producer.CheckReporter, 'from_key', side_effect=reporter),
            self.assertRaises(ValueError),
        ):
            producer.report_job(self.ctx)

    def test_native_claim_precedes_bound_runner_and_never_loads_app_key(self):
        self.fixture.api.responses['actions/runs/500/attempts/1/jobs']['jobs'][2][
            'status'
        ] = 'in_progress'
        metadata = {
            'identity': self.ctx.identity,
            'qualification_id': self.ctx.qid,
            'input_manifest_sha256': self.receipt['evidence']['input_manifest_sha256'],
        }
        files = {
            'prepared.json': canonical_json(metadata),
            'inputs.json': b'{}',
            'patched-source.tar': b'fixture',
        }
        self.ctx.artifact = lambda name, limits, role: (
            (files, {'id': 902})
            if role == 'prepare'
            else self.artifact(name, limits, role)
        )
        grant = {
            'run_id': 500,
            'run_number': 12,
            'run_attempt': 1,
            'qualification_id': self.ctx.qid,
        }
        granted = SimpleNamespace(
            commit='e' * 40, record={'state': 'granted', 'grant': grant}
        )
        running = self.current
        self.ctx.current = lambda: granted
        self.ctx.objects = self.work
        self.ctx.keyring = self.work

        def wait(*args):
            self.ctx.current = lambda: running
            return running

        worker = MagicMock()
        worker.nonce = '7' * 32
        self.report['success'] = True
        with (
            patch.object(
                producer, 'prepared_inputs', return_value=(self.work, metadata)
            ),
            patch.object(
                producer, 'prepared_admission', return_value=(files, metadata)
            ),
            patch.object(
                producer,
                'check_record',
                return_value={'id': 800, 'status': 'in_progress', 'conclusion': None},
            ),
            patch.object(producer, 'WorkerClaim', return_value=worker),
            patch.object(producer, 'upload'),
            patch.object(producer, 'wait_next', side_effect=wait),
            patch.object(producer.subprocess, 'run'),
            patch.object(
                producer, 'regular_digest', return_value=(producer.GUEST_SHA256, 10)
            ),
            patch.object(producer, 'run_guest', return_value=self.report) as run,
            patch.object(producer.CheckReporter, 'from_key') as key,
            patch.dict('os.environ', NATIVE_JOB_STARTED=str(__import__('time').time())),
        ):
            self.assertTrue(producer.native_job(self.ctx))
        worker.accept.assert_called_once_with(running)
        self.assertEqual(run.call_args.kwargs['nonce'], self.ctx.execution['nonce'])
        self.assertEqual(run.call_args.kwargs['qualification_id'], self.ctx.qid)
        self.assertEqual(run.call_args.kwargs['expected_input_manifest'], metadata['input_manifest_sha256'])
        self.assertEqual(run.call_args.kwargs['expected_source'], self.ctx.admission['source'])
        key.assert_not_called()
        written = json.loads((self.work / 'result/qualification.json').read_text())
        self.assertNotIn('reporter_job_id', written['evidence'])


class ProducerWorkflowTest(unittest.TestCase):
    def test_only_main_reporter_jobs_reference_the_app_key(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / '.github/workflows/trusted-native.yml').read_text()
        self.assertNotIn('contents: write', text)
        self.assertIn('actions: read', text)
        self.assertNotIn('pull_request_target', text)
        jobs = text.split('jobs:\n', 1)[1]
        sections = {}
        for role in ['prepare', 'admit', 'native', 'report']:
            section = jobs.split('  ' + role + ':\n', 1)[1]
            next_job = next(
                (
                    section.find('\n  ' + other + ':\n')
                    for other in ['prepare', 'admit', 'native', 'report']
                    if section.find('\n  ' + other + ':\n') >= 0
                ),
                len(section),
            )
            sections[role] = section[:next_job]
            self.assertIn("github.ref == 'refs/heads/main'", sections[role])
        for role in ['prepare', 'native']:
            self.assertNotIn('NATIVE_GATE_PRIVATE_KEY', sections[role])
            self.assertNotIn('environment: native-gate-reporter', sections[role])
        for role in ['admit', 'report']:
            self.assertIn('environment: native-gate-reporter', sections[role])
            self.assertIn('NATIVE_GATE_PRIVATE_KEY', sections[role])


if __name__ == '__main__':
    unittest.main()
