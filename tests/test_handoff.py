# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Request provenance tests; API and already-authenticated state are fixtures."""
import copy
import hashlib
import io
import json
import unittest
import zipfile

from ci.native.coordination import SignedCoordination
from ci.native.handoff import HandoffError, verify_handoff_request
from ci.native.qualification import QualificationPolicy


class HandoffTest(unittest.TestCase):
    def setUp(self):
        self.policy = QualificationPolicy('owner/downstream', 100, 200, 'a' * 40,
                                          'A' * 40, ('fixture',), app_id=300,
                                          workflow_path='.github/workflows/handoff-probe.yml')
        admission = {'repository_id': 100, 'pr_number': 7, 'head_repository_id': 101,
                     'base': '1' * 40, 'head': '2' * 40, 'merge': '3' * 40,
                     'signed_merge': '4' * 40, 'tree': '5' * 40, 'payload_sha256': '6' * 64,
                     'source': {'tree': '7' * 40, 'sha256': '8' * 64, 'bytes': 4096},
                     'approver_id': 10, 'upstream_commit': self.policy.upstream_commit}
        operation = {'id': '1' * 32, 'nonce': '1' * 32, 'holder_fingerprint': self.policy.fingerprint,
                     'workflow_id': 200, 'launcher': self.policy.launcher, 'admission': admission}
        record = {'version': 1, 'repository_id': 100, 'target_ref': 'refs/heads/main',
                  'epoch': 'e' * 32, 'cutover_digest': 'f' * 64, 'generation': 2,
                  'state': 'dispatching', 'operation': operation, 'grant': None, 'completion': None}
        self.state = SignedCoordination('c' * 40, 'b' * 40, 'd' * 40, self.policy.fingerprint, record)
        self.branch = 'validation/native-handoff-20261030'
        self.execution = {'workflow_id': 200, 'run_id': 500, 'run_number': 12,
                          'run_attempt': 1, 'launcher': self.policy.launcher, 'nonce': operation['nonce']}
        self.request = {'coordination_sha': self.state.commit, 'identity': {
            'schema_version': 1, 'admission': admission, 'execution': self.execution}}
        self.run = {'id': 500, 'workflow_id': 200, 'path': self.policy.workflow_path,
                    'repository': {'id': 100, 'full_name': self.policy.repository},
                    'head_repository': {'id': 100}, 'head_sha': self.policy.launcher,
                    'head_branch': self.branch, 'event': 'workflow_dispatch',
                    'status': 'completed', 'conclusion': 'success', 'run_attempt': 1, 'run_number': 12,
                    'actor': {'id': 10, 'login': 'owner'}, 'triggering_actor': {'id': 10}}
        self.job = {'id': 700, 'run_id': 500, 'run_attempt': 1, 'head_sha': self.policy.launcher,
                    'name': 'handoff-request', 'status': 'completed', 'conclusion': 'success',
                    'started_at': '2026-10-30T21:00:00Z', 'completed_at': '2026-10-30T21:00:10Z'}
        self.artifact = {'id': 800, 'expired': False, 'name': 'coordination-request-500-1',
                         'created_at': '2026-10-30T21:00:05Z', 'workflow_run': {
                             'id': 500, 'repository_id': 100, 'head_repository_id': 100,
                             'head_sha': self.policy.launcher}}
        self.permission = {'user': {'id': 10}, 'permission': 'admin'}
        self.jobs = {'total_count': 1, 'jobs': [self.job]}
        self.repository = self.policy.repository
        self.reads = 0

    def get(self, path, **kwargs):
        if path == 'actions/runs/500':
            self.reads += 1
            run = copy.deepcopy(self.run)
            if self.reads == 2 and getattr(self, 'rerun', False):
                run['run_attempt'] = 2
            return run
        return {'actions/artifacts/800': self.artifact,
                'actions/runs/500/attempts/1/jobs': self.jobs,
                'collaborators/owner/permission': self.permission}[path]

    def archive(self, request=None):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('request.json', json.dumps(request or self.request))
        raw = buffer.getvalue()
        self.artifact['digest'] = 'sha256:' + hashlib.sha256(raw).hexdigest()
        return raw

    def verify(self, **overrides):
        arguments = {'run_id': 500, 'artifact_id': 800, 'expected_branch': self.branch}
        if 'archive' not in overrides:
            arguments['archive'] = self.archive()
        arguments.update(overrides)
        return verify_handoff_request(self, self.policy, self.state, **arguments)

    def test_exact_request_yields_only_an_unconsumed_grant_proposal(self):
        grant = self.verify()
        self.assertEqual(grant, {'run_id': 500, 'run_number': 12, 'run_attempt': 1,
                                'qualification_id': hashlib.sha256(json.dumps(
                                    self.request['identity'], sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest(),
                                'native_job_id': None, 'worker_nonce': None, 'finalizer_nonce': None})
        self.assertEqual(self.state.record['state'], 'dispatching')
        self.assertIsNone(self.state.record['grant'])

    def test_wrong_run_source_event_branch_actor_and_attempt_are_rejected(self):
        for key, value in [('id', 501), ('workflow_id', 201), ('path', 'wrong.yml'),
                           ('head_sha', '9' * 40), ('head_branch', 'main'), ('event', 'push'),
                           ('run_attempt', 2), ('run_attempt', True), ('status', 'in_progress'),
                           ('conclusion', 'failure'), ('actor', {'id': 11, 'login': 'owner'}),
                           ('triggering_actor', {'id': 11}), ('head_repository', {'id': 101})]:
            before = copy.deepcopy(self.run)
            self.run[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(HandoffError):
                self.verify()
            self.run = before

    def test_replayed_or_changed_request_is_rejected_even_with_new_digest(self):
        for field, value in [('coordination_sha', '9' * 40), ('extra', True)]:
            altered = copy.deepcopy(self.request)
            altered[field] = value
            with self.subTest(field=field), self.assertRaises(HandoffError):
                self.verify(archive=self.archive(altered))
        for field in ['admission', 'execution']:
            altered = copy.deepcopy(self.request)
            altered['identity'][field]['extra'] = 1
            with self.subTest(field=field), self.assertRaises(HandoffError):
                self.verify(archive=self.archive(altered))
        altered = copy.deepcopy(self.request)
        altered['identity']['execution']['run_attempt'] = True
        with self.assertRaises(HandoffError):
            self.verify(archive=self.archive(altered))

    def test_wrong_artifact_provenance_or_bytes_are_rejected(self):
        for key, value in [('id', 801), ('expired', True), ('name', 'other'),
                           ('workflow_run', {'id': 501}), ('created_at', '2026-10-30T22:00:00Z')]:
            before = copy.deepcopy(self.artifact)
            self.artifact[key] = value
            with self.subTest(key=key), self.assertRaises(HandoffError):
                self.verify()
            self.artifact = before
        archive = self.archive()
        self.artifact['digest'] = 'sha256:' + '9' * 64
        with self.assertRaises(HandoffError):
            self.verify(archive=archive)

    def test_missing_duplicate_failed_or_wrong_attempt_job_is_rejected(self):
        for jobs in [{'total_count': 0, 'jobs': []}, {'total_count': 2, 'jobs': [self.job, self.job]},
                     {'total_count': 1, 'jobs': [dict(self.job, run_attempt=2)]},
                     {'total_count': 1, 'jobs': [dict(self.job, name='other')]},
                     {'total_count': 1, 'jobs': [dict(self.job, conclusion='skipped')]}]:
            self.jobs = jobs
            with self.subTest(jobs=jobs), self.assertRaises(HandoffError):
                self.verify()

    def test_revoked_actor_and_rerun_during_verification_are_rejected(self):
        self.permission['permission'] = 'read'
        with self.assertRaises(HandoffError):
            self.verify()
        self.permission['permission'] = 'admin'
        self.reads = 0
        self.rerun = True
        with self.assertRaises(HandoffError):
            self.verify()

    def test_an_existing_grant_or_unsigned_input_cannot_be_adopted(self):
        grant = self.verify()
        for phase, value in [('reserved', None), ('granted', grant)]:
            self.state.record.update(state=phase, grant=value)
            with self.subTest(phase=phase), self.assertRaises(HandoffError):
                self.verify()
        with self.assertRaises(HandoffError):
            verify_handoff_request(self, self.policy, self.state.record, run_id=500,
                                   artifact_id=800, archive=self.archive(), expected_branch=self.branch)


if __name__ == '__main__':
    unittest.main()
