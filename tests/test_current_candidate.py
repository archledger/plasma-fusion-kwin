# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Current association tests start with a fixture for already-verified evidence."""
import copy
import unittest

from ci.native.current_candidate import verify_current_association
from ci.native.qualification import (
    ProducerAttestedInputs,
    QualificationError,
    QualificationPolicy,
    QualifiedEvidence,
    QualifiedEvidenceV2,
)
from ci.native.signed_merge import SignedMerge


class API:
    repository = 'owner/downstream'

    def __init__(self, values):
        self.values = values
        self.calls = []

    def get(self, resource):
        self.calls.append(resource)
        return copy.deepcopy(self.values[resource])


class CurrentCandidateTest(unittest.TestCase):
    def setUp(self):
        self.policy = QualificationPolicy('owner/downstream', 100, 200, 'a' * 40, 'A' * 40,
                                          ('fixture-package',), app_id=300)
        signed = SignedMerge('d' * 40, 'e' * 40, 'A' * 40, 'b' * 40, 'c' * 40)
        attested = ProducerAttestedInputs(7, 101, 'f' * 40, '1' * 64, '2' * 40,
                                          '3' * 64, 4096, 10, self.policy.upstream_commit)
        self.evidence = QualifiedEvidence('4' * 64, 500, signed, attested, {'qt_test_passed': 43},
                                          check_run_id=800, run_number=12)
        self.api = API({
            'pulls/7': {'number': 7, 'state': 'open', 'merged': False, 'draft': False,
                        'mergeable': True, 'merge_commit_sha': 'f' * 40,
                        'labels': [{'name': 'native-approved'}],
                        'base': {'ref': 'main', 'sha': 'b' * 40,
                                 'repo': {'id': 100, 'full_name': 'owner/downstream'}},
                        'head': {'sha': 'c' * 40, 'repo': {'id': 101}}},
            'git/ref/heads/main': {'ref': 'refs/heads/main',
                                   'object': {'type': 'commit', 'sha': 'b' * 40}},
            'git/ref/pull/7/head': {'ref': 'refs/pull/7/head',
                                   'object': {'type': 'commit', 'sha': 'c' * 40}},
            'git/ref/pull/7/merge': {'ref': 'refs/pull/7/merge',
                                    'object': {'type': 'commit', 'sha': 'f' * 40}},
        })

    def verify(self):
        return verify_current_association(self.api, self.policy, self.evidence)

    def test_current_pr_and_fork_association_pass(self):
        result = self.verify()
        self.assertEqual((result.pr_number, result.head_repository_id), (7, 101))
        self.assertEqual(result.signed_merge, 'd' * 40)
        self.assertEqual(self.api.calls.count('pulls/7'), 2)

    def test_explicit_verified_v2_type_retains_the_same_association_checks(self):
        self.evidence = QualifiedEvidenceV2(
            **vars(self.evidence),
            activation_check_id=801,
            operation_nonce='6' * 32,
            native_job_id=702,
        )
        self.assertEqual(self.verify().qualification_id, self.evidence.qualification_id)
        self.api.values['pulls/7']['labels'] = []
        with self.assertRaises(QualificationError):
            self.verify()

    def test_unregistered_evidence_subclass_is_not_duck_typed(self):
        class UnknownEvidence(QualifiedEvidence):
            pass

        self.evidence = UnknownEvidence(**vars(self.evidence))
        with self.assertRaises(QualificationError):
            self.verify()
        self.assertEqual(self.api.calls, [])

    def test_existing_commit_does_not_prove_the_pr_or_fork_association(self):
        self.api.values['pulls/7']['number'] = 8
        with self.assertRaises(QualificationError):
            self.verify()
        self.api.values['pulls/7']['number'] = 7
        self.api.values['pulls/7']['head']['repo']['id'] = 102
        with self.assertRaises(QualificationError):
            self.verify()

    def test_current_merge_ref_must_point_to_the_qualified_commit(self):
        self.api.values['git/ref/pull/7/merge']['object']['sha'] = '9' * 40
        with self.assertRaises(QualificationError):
            self.verify()

    def test_stale_base_head_and_wrong_ref_types_are_refused(self):
        original = copy.deepcopy(self.api.values)
        for resource in ['git/ref/heads/main', 'git/ref/pull/7/head']:
            for key, value in [('sha', '9' * 40), ('type', 'tag')]:
                with self.subTest(resource=resource, key=key):
                    self.api.values = copy.deepcopy(original)
                    self.api.values[resource]['object'][key] = value
                    with self.assertRaises(QualificationError):
                        self.verify()

    def test_closed_draft_unknown_mergeability_and_withdrawn_request_are_refused(self):
        original = copy.deepcopy(self.api.values['pulls/7'])
        for key, value in [('state', 'closed'), ('merged', True), ('draft', True),
                           ('mergeable', None), ('labels', [])]:
            with self.subTest(key=key):
                self.api.values['pulls/7'] = copy.deepcopy(original)
                self.api.values['pulls/7'][key] = value
                with self.assertRaises(QualificationError):
                    self.verify()

    def test_second_pr_read_detects_a_concurrent_head_change(self):
        get = self.api.get
        count = 0

        def changing(resource):
            nonlocal count
            value = get(resource)
            if resource == 'pulls/7':
                count += 1
                if count == 2:
                    value['head']['sha'] = '9' * 40
            return value

        self.api.get = changing
        with self.assertRaises(QualificationError):
            self.verify()


if __name__ == '__main__':
    unittest.main()
