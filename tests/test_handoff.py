# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Request provenance tests; API and already-authenticated state are fixtures."""
import copy
import hashlib
import io
import json
import os
import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

from ci.native.coordination import SignedCoordination
from ci.native.handoff import (
    HandoffError,
    verify_active_request,
    verify_handoff_request,
    verify_worker_acknowledgement,
    verify_worker_claim,
)
from ci.native.qualification import QualificationPolicy
from ci.native.worker_claim import WorkerClaim
from tools import probe_live_worker as live


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

    def archive(self, request=None, member='request.json'):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr(member, json.dumps(request or self.request))
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

    def active(self):
        self.run.update(status='in_progress', conclusion=None)
        self.job.update(name='live-worker', status='in_progress', conclusion=None, completed_at=None)

    def live_verify(self, function, request, member):
        return function(self, self.policy, self.state, run_id=500, artifact_id=800,
                        archive=self.archive(request, member), expected_branch=self.branch)

    def test_live_request_claim_and_terminal_acknowledgement(self):
        self.active()
        grant = self.live_verify(verify_active_request, self.request, 'request.json')
        self.state = replace(self.state, commit='d' * 40, record=dict(
            self.state.record, state='granted', generation=3, grant=grant))
        claim = {'coordination_sha': self.state.commit, 'identity': self.request['identity'],
                 'native_job_id': 700, 'worker_nonce': '5' * 32}
        self.artifact['name'] = 'coordination-claim-500-1'
        running_grant = self.live_verify(verify_worker_claim, claim, 'claim.json')
        self.assertEqual(running_grant['worker_nonce'], '5' * 32)
        self.assertEqual(running_grant['native_job_id'], 700)
        self.state = replace(self.state, commit='e' * 40, record=dict(
            self.state.record, state='running', generation=4, grant=running_grant))
        ack = dict(claim, coordination_sha=self.state.commit, accepted=True,
                   duplicate_refused=True, unclaimed_grant_refused=True, native_executed=False)
        self.artifact['name'] = 'coordination-ack-500-1'
        with self.assertRaises(HandoffError):
            self.live_verify(verify_worker_acknowledgement, ack, 'ack.json')
        self.run.update(status='completed', conclusion='success')
        self.job.update(status='completed', conclusion='success', completed_at='2026-10-30T21:00:10Z')
        self.assertEqual(self.live_verify(verify_worker_acknowledgement, ack, 'ack.json'), ack)
        for field, value in [('duplicate_refused', False), ('unclaimed_grant_refused', False),
                             ('native_executed', True), ('coordination_sha', 'f' * 40)]:
            with self.subTest(field=field), self.assertRaises(HandoffError):
                self.live_verify(verify_worker_acknowledgement, dict(ack, **{field: value}), 'ack.json')

    def test_completed_request_is_not_an_active_worker(self):
        self.job['name'] = 'live-worker'
        with self.assertRaises(HandoffError):
            self.live_verify(verify_active_request, self.request, 'request.json')

    def test_producer_request_waits_for_prepare_and_claim_waits_for_admission(self):
        self.policy=replace(self.policy,workflow_path='.github/workflows/trusted-native.yml')
        self.run['path']=self.policy.workflow_path
        self.active();self.job['name']='admit'
        prepare=dict(self.job,id=699,name='prepare',status='completed',conclusion='success',
                     started_at='2026-10-30T20:59:00Z',completed_at='2026-10-30T21:00:00Z')
        self.jobs={'total_count':2,'jobs':[prepare,self.job]}
        grant=self.live_verify(verify_active_request,self.request,'request.json')
        self.state=replace(self.state,commit='d'*40,record=dict(self.state.record,state='granted',grant=grant))
        admit=dict(self.job,status='completed',conclusion='success',completed_at='2026-10-30T21:00:10Z')
        native=dict(self.job,id=701,name='native',started_at='2026-10-30T21:00:10Z')
        self.jobs={'total_count':3,'jobs':[prepare,admit,native]}
        self.artifact.update(name='coordination-claim-500-1',created_at='2026-10-30T21:00:11Z')
        claim={'coordination_sha':self.state.commit,'identity':self.request['identity'],
               'native_job_id':701,'worker_nonce':'5'*32}
        self.assertEqual(self.live_verify(verify_worker_claim,claim,'claim.json')['native_job_id'],701)
        prepare['conclusion']='failure'
        with self.assertRaises(HandoffError):self.live_verify(verify_worker_claim,claim,'claim.json')

    def test_active_claim_binds_job_nonce_and_grant(self):
        self.active()
        grant = self.live_verify(verify_active_request, self.request, 'request.json')
        self.state = replace(self.state, commit='d' * 40, record=dict(
            self.state.record, state='granted', generation=3, grant=grant))
        claim = {'coordination_sha': self.state.commit, 'identity': self.request['identity'],
                 'native_job_id': 700, 'worker_nonce': '5' * 32}
        self.artifact['name'] = 'coordination-claim-500-1'
        for field, value in [('coordination_sha', 'f' * 40), ('native_job_id', 701),
                             ('worker_nonce', '1' * 32), ('worker_nonce', 'invalid')]:
            with self.subTest(field=field, value=value), self.assertRaises(HandoffError):
                self.live_verify(verify_worker_claim, dict(claim, **{field: value}), 'claim.json')

    def test_live_wait_binds_exact_parent_and_rejects_changed_operation(self):
        following = replace(self.state, commit='d' * 40, record=dict(self.state.record, state='granted'))
        ref = 'refs/heads/validation/native-worker-state-20261030'
        with patch.dict(os.environ, STATE_REF=ref), patch.object(live, 'git', return_value='d' * 40 + ' ' + ref), \
                patch.object(live, 'read_signed_coordination', return_value=following) as reader:
            self.assertEqual(live.wait_next('.', self.state, 'granted', self.policy, '.', 10**20), following)
            self.assertEqual(reader.call_args.kwargs['expected_parent'], self.state.commit)
            changed = copy.deepcopy(following.record)
            changed['operation']['nonce'] = '9' * 32
            reader.return_value = replace(following, record=changed)
            with self.assertRaises(ValueError):
                live.wait_next('.', self.state, 'granted', self.policy, '.', 10**20)

    def test_live_wait_deadline_prevents_further_fetching(self):
        with patch.object(live, 'git') as git, patch.object(live.time, 'monotonic', return_value=20), \
                patch.dict(os.environ, STATE_REF='refs/heads/validation/native-worker-state-20261030'), \
                self.assertRaises(ValueError):
            live.wait_next('.', self.state, 'granted', self.policy, '.', 10)
        git.assert_not_called()

    def test_live_upload_is_one_named_file_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, GITHUB_RUN_ID='500', PROBE_NODE='/trusted/node'), \
                patch.object(live.subprocess, 'run') as run:
            live.upload('claim', {'public': True}, Path(directory), 10**20)
            self.assertEqual(json.loads((Path(directory) / 'claim.json').read_text()), {'public': True})
            self.assertEqual(run.call_args.kwargs['env']['INPUT_OVERWRITE'], 'false')
            self.assertEqual(run.call_args.kwargs['env']['INPUT_NAME'], 'coordination-claim-500-1')
            self.assertEqual(run.call_args.args[0], ['/trusted/node', '_probe-uploader/dist/upload/index.js'])

    def test_one_foreground_worker_emits_request_claim_and_ack(self):
        self.state = replace(self.state, record=dict(self.state.record, generation=7))
        grant = self.verify()
        granted = replace(self.state, commit='d' * 40, record=dict(
            self.state.record, state='granted', generation=8, grant=grant))
        workers, emitted = [], []

        def create(directory, policy, **identity):
            worker = WorkerClaim(directory, policy, **identity)
            workers.append(worker)
            return worker

        def next_state(source, current, phase, policy, keyring, deadline):
            if phase == 'granted':
                return granted
            return replace(granted, commit='e' * 40, record=dict(granted.record,
                state='running', generation=9, grant=dict(grant, native_job_id=700,
                                                        worker_nonce=workers[0].nonce)))

        self.job.update(name='live-worker', status='in_progress', conclusion=None)
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'fixture public key'
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            'GITHUB_REPOSITORY': live.REPOSITORY, 'GITHUB_EVENT_NAME': 'workflow_dispatch',
            'GITHUB_RUN_ATTEMPT': '1', 'GITHUB_REF': 'refs/heads/validation/native-worker-20261030',
            'STATE_REF': 'refs/heads/validation/native-worker-state-20261030',
            'OPERATION_JSON': json.dumps(self.state.record['operation']), 'GITHUB_SHA': self.policy.launcher,
            'GITHUB_RUN_ID': '500', 'GITHUB_RUN_NUMBER': '12', 'RUNNER_TEMP': directory,
            'COORDINATION_SHA': self.state.commit, 'PARENT_SHA': self.state.parent,
            'EXPECTED_GENERATION': '7', 'EPOCH': 'e' * 32, 'CUTOVER_DIGEST': 'f' * 64}), \
                patch.object(live, 'PUBLIC_KEY_SHA256', hashlib.sha256(b'fixture public key').hexdigest()), \
                patch.object(live, 'build_opener') as opener, \
                patch.object(live, 'QualificationPolicy', return_value=self.policy), \
                patch.object(live, 'read_signed_coordination', return_value=self.state) as read_state, \
                patch.object(live, 'ProofAPI') as api, patch.object(live, 'git'), \
                patch.object(live.subprocess, 'run'), patch.object(live, 'WorkerClaim', side_effect=create), \
                patch.object(live, 'wait_next', side_effect=next_state), \
                patch.object(live, 'upload', side_effect=lambda kind, value, *_: emitted.append((kind, value))):
            opener.return_value.open.return_value = response
            api.return_value.get.return_value = self.jobs
            live.main()
        self.assertEqual(read_state.call_args.kwargs['expected_generation'], 7)
        self.assertEqual(len(workers), 1)
        self.assertEqual([kind for kind, _ in emitted], ['request', 'claim', 'ack'])
        self.assertTrue(emitted[-1][1]['accepted'])
        self.assertTrue(emitted[-1][1]['duplicate_refused'])
        self.assertTrue(emitted[-1][1]['unclaimed_grant_refused'])
        self.assertFalse(emitted[-1][1]['native_executed'])


class HandoffWorkflowTest(unittest.TestCase):
    def test_registration_does_not_depend_on_a_changed_file_list(self):
        path = Path(__file__).resolve().parents[1] / '.github/workflows/handoff-probe.yml'
        push = path.read_text().split('  push:\n', 1)[1].split('  workflow_dispatch:', 1)[0]
        self.assertIn("branches: ['validation/native-handoff-*']", push)
        self.assertNotIn('paths', push)

    def test_live_worker_receives_explicit_expected_generation(self):
        path = Path(__file__).resolve().parents[1] / '.github/workflows/live-worker-probe.yml'
        text = path.read_text()
        self.assertIn('      expected_generation:\n', text)
        self.assertIn('EXPECTED_GENERATION: ${{ inputs.expected_generation }}', text)


if __name__ == '__main__':
    unittest.main()
