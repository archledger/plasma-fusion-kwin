# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Local state/CAS experiment; it does not authenticate a production grant."""
import copy
import hashlib
import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from ci.native.qualification import QualificationPolicy
from tools.probe_coordination import (
    LocalCoordinationProbe,
    Outcome,
    ProbeEvidence,
    proposed,
)


class CoordinationProbeTest(unittest.TestCase):
    def setUp(self):
        self.policy = QualificationPolicy('owner/downstream', 100, 200, 'a' * 40, 'A' * 40,
                                          ('fixture',), app_id=300)
        self.probe = LocalCoordinationProbe(self.policy)
        self.addCleanup(self.probe.close)

    def operation(self, nonce='1' * 32):
        target = self.probe.candidate
        return {'id': nonce, 'holder_fingerprint': self.policy.fingerprint,
                'workflow_id': self.policy.workflow_id, 'launcher': self.policy.launcher,
                'nonce': nonce, 'admission': {
                    'repository_id': 100, 'pr_number': 7, 'head_repository_id': 101,
                    'base': target.base, 'head': target.head, 'merge': 'f' * 40,
                    'signed_merge': target.commit, 'tree': target.tree, 'payload_sha256': '2' * 64,
                    'source': {'tree': '3' * 40, 'sha256': '4' * 64, 'bytes': 4096},
                    'approver_id': 10, 'upstream_commit': self.policy.upstream_commit}}

    def move(self, phase, evidence=None, **changes):
        current = self.probe.record()
        target = proposed(current, phase, **changes)
        result = self.probe.cas(self.probe.tip(), target, evidence or ProbeEvidence())
        self.assertEqual(result.outcome, Outcome.APPLIED)
        return result.sha, target

    def grant(self):
        operation = self.operation()
        self.move('reserved', operation=operation, grant=None, completion=None)
        self.move('dispatching')
        execution = {'workflow_id': 200, 'run_id': 500, 'run_number': 12, 'run_attempt': 1,
                     'launcher': self.policy.launcher, 'nonce': operation['nonce']}
        identity = {'schema_version': 1, 'admission': operation['admission'], 'execution': execution}
        qid = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        grant = {'run_id': 500, 'run_number': 12, 'run_attempt': 1, 'qualification_id': qid,
                 'native_job_id': None, 'worker_nonce': None, 'finalizer_nonce': None}
        return self.move('granted', ProbeEvidence(grant_authenticated=True), grant=grant)

    def running(self):
        _, state = self.grant()
        grant = dict(state['grant'], native_job_id=700, worker_nonce='5' * 32)
        return self.move('running', ProbeEvidence(worker_authenticated=True), grant=grant)

    def quiet(self, **changes):
        return ProbeEvidence(writers_quiescent=True, runtimes_quiescent=True,
                             dispatches_quiescent=True, **changes)

    def finalizing(self):
        _, state = self.running()
        return self.move('finalizing', self.quiet(),
                         grant=dict(state['grant'], finalizer_nonce='6' * 32))

    def completion(self, state, outcome, main_sha=None):
        return {'operation_id': state['operation']['id'],
                'qualification_id': state['grant']['qualification_id'] if state['grant'] else None,
                'outcome': outcome, 'main_sha': main_sha}

    def test_two_acquisitions_have_one_winner(self):
        old = self.probe.tip()
        initial = self.probe.record(old)
        barrier = threading.Barrier(2)

        def acquire(nonce):
            state = proposed(initial, 'reserved', operation=self.operation(nonce),
                             grant=None, completion=None)
            return self.probe.cas(old, state, ProbeEvidence(), before_push=barrier.wait).outcome

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(acquire, ['1' * 32, '2' * 32]))
        self.assertEqual(results.count(Outcome.APPLIED), 1)
        self.assertTrue(all(result in {Outcome.APPLIED, Outcome.REJECTED, Outcome.UNKNOWN} for result in results))
        self.assertEqual(self.probe.record()['state'], 'reserved')
        self.assertEqual(self.probe.record()['operation']['id'],
                         ['1' * 32, '2' * 32][results.index(Outcome.APPLIED)])

    def test_same_grant_has_one_native_consumer(self):
        old, state = self.grant()
        barrier = threading.Barrier(2)

        def consume(nonce):
            following = proposed(state, 'running', grant=dict(state['grant'],
                                  native_job_id=700, worker_nonce=nonce))
            return self.probe.cas(old, following, ProbeEvidence(worker_authenticated=True),
                                  before_push=barrier.wait).outcome

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(consume, ['5' * 32, '6' * 32]))
        self.assertEqual(results.count(Outcome.APPLIED), 1)
        self.assertTrue(all(result in {Outcome.APPLIED, Outcome.REJECTED, Outcome.UNKNOWN} for result in results))
        self.assertEqual(self.probe.record()['grant']['worker_nonce'],
                         ['5' * 32, '6' * 32][results.index(Outcome.APPLIED)])

    def test_worker_exit_without_runtime_cleanup_cannot_release(self):
        old, state = self.running()
        idle = proposed(state, 'idle', operation=None, grant=None,
                        completion=self.completion(state, 'failed'))
        with self.assertRaises(ValueError):
            self.probe.cas(old, idle, ProbeEvidence(writers_quiescent=True,
                                                    dispatches_quiescent=True))
        self.assertEqual(self.probe.tip(), old)
        with self.assertRaises(ValueError):
            self.move('reserved', operation=self.operation('9' * 32), grant=None, completion=None)

    def test_reserved_abort_is_allowed_but_dispatch_uncertainty_is_held(self):
        _, state = self.move('reserved', operation=self.operation(), grant=None, completion=None)
        self.move('idle', ProbeEvidence(no_dispatch=True), operation=None, grant=None,
                  completion=self.completion(state, 'aborted-before-dispatch'))
        self.move('reserved', operation=self.operation('2' * 32), grant=None, completion=None)
        old, state = self.move('dispatching')
        with self.assertRaises(ValueError):
            self.probe.cas(old, proposed(state, 'idle', operation=None, grant=None,
                           completion=self.completion(state, 'aborted-before-dispatch')),
                           ProbeEvidence(no_dispatch=True))

    def test_wrong_generation_and_changed_candidate_are_rejected(self):
        old, state = self.move('reserved', operation=self.operation(), grant=None, completion=None)
        next_record = proposed(state, 'dispatching')
        next_record['generation'] = state['generation']
        with self.assertRaises(ValueError):
            self.probe.cas(old, next_record, ProbeEvidence())
        next_record = proposed(state, 'dispatching')
        next_record['operation']['admission']['payload_sha256'] = '9' * 64
        with self.assertRaises(ValueError):
            self.probe.cas(old, next_record, ProbeEvidence())
        self.assertEqual(self.probe.tip(), old)

    def test_finalizing_holds_lock_through_actual_main_update(self):
        old, state = self.finalizing()
        entered, proceed = threading.Event(), threading.Event()

        def pause():
            entered.set()
            self.assertTrue(proceed.wait(5))

        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(self.probe.push_main, old, before_push=pause)
            self.assertTrue(entered.wait(5))
            with self.assertRaises(ValueError):
                self.move('reserved', operation=self.operation('9' * 32), grant=None, completion=None)
            proceed.set()
            self.assertEqual(result.result().outcome, Outcome.APPLIED)
        self.assertEqual(self.probe.main_tip(), self.probe.candidate.commit)
        self.move('idle', self.quiet(update_status='applied', update_acknowledged=True,
                                    main_sha=self.probe.main_tip()), operation=None, grant=None,
                  completion=self.completion(state, 'finalized', self.probe.main_tip()))
        self.move('reserved', operation=self.operation('9' * 32), grant=None, completion=None)

    def test_verified_rejection_can_release_without_overwriting_new_main(self):
        old, state = self.finalizing()
        advanced = self.probe.advance_main()
        self.assertEqual(self.probe.push_main(old).outcome, Outcome.REJECTED)
        idle = proposed(state, 'idle', operation=None, grant=None,
                        completion=self.completion(state, 'rejected', advanced))
        with self.assertRaises(ValueError):
            self.probe.cas(old, idle, self.quiet(update_status='unknown', main_sha=advanced))
        self.probe.cas(old, idle, self.quiet(update_status='rejected', main_sha=advanced,
                                           update_acknowledged=True, definitively_not_applied=True))
        self.assertEqual(self.probe.main_tip(), advanced)
        self.assertEqual(self.probe.record()['state'], 'idle')

    def test_malformed_record_cannot_be_published(self):
        state = proposed(self.probe.record(), 'reserved', operation=self.operation(),
                         grant=None, completion=None)
        for field, value in [('generation', True), ('state', 'unknown'), ('extra', 1)]:
            with self.subTest(field=field):
                candidate = copy.deepcopy(state)
                candidate[field] = value
                with self.assertRaises(ValueError):
                    self.probe.cas(self.probe.tip(), candidate, ProbeEvidence())


if __name__ == '__main__':
    unittest.main()
