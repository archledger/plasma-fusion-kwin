# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Structural v2 contracts, not authenticated artifact or runtime evidence."""

import copy
import unittest
from dataclasses import replace

import test_qualification as fixtures

from ci.native.qualification import (
    CheckBinding,
    QualificationError,
    QualifiedEvidenceV2,
    match_admission_v2,
    validate_admission_v2,
    validate_receipt,
    validate_receipt_v2,
)


class CheckEvidenceTest(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.QualificationTest()
        fixture.setUp()
        self.policy, self.signed = fixture.policy, fixture.signed
        self.legacy = copy.deepcopy(fixture.receipt)
        self.receipt = copy.deepcopy(self.legacy)
        self.receipt['schema_version'] = 2
        self.receipt['evidence']['activation_check_run_id'] = 801
        self.admission = {
            'schema_version': 2,
            'identity': copy.deepcopy(self.receipt['identity']),
            'qualification_id': self.receipt['qualification_id'],
            'input_manifest_sha256': self.receipt['evidence']['input_manifest_sha256'],
            'checks': {'native_check_id': 800, 'activation_check_id': 801},
        }

    def test_v2_pair_matches_without_changing_identity_hash(self):
        before = copy.deepcopy((self.receipt, self.admission))
        result = validate_receipt_v2(self.receipt, self.policy, self.signed)
        self.assertEqual(result[1], self.legacy['identity']['execution'])
        self.assertEqual(
            validate_admission_v2(self.admission, self.policy, self.signed),
            CheckBinding(800, 801),
        )
        self.assertEqual(
            match_admission_v2(self.admission, self.receipt, self.policy, self.signed),
            CheckBinding(800, 801),
        )
        self.assertEqual((self.receipt, self.admission), before)

    def test_legacy_verification_does_not_implicitly_adopt_v2_or_reverse(self):
        validate_receipt(self.legacy, self.policy, self.signed)
        with self.assertRaises(QualificationError):
            validate_receipt_v2(self.legacy, self.policy, self.signed)
        with self.assertRaises(QualificationError):
            validate_receipt(self.receipt, self.policy, self.signed)

    def test_versions_and_top_level_keys_are_exact(self):
        for version in [None, 1, True, '2', 2.0, 3]:
            for source, validate in [
                (self.admission, validate_admission_v2),
                (self.receipt, validate_receipt_v2),
            ]:
                value = copy.deepcopy(source)
                value['schema_version'] = version
                with (
                    self.subTest(version=version),
                    self.assertRaises(QualificationError),
                ):
                    validate(value, self.policy, self.signed)
        for source, validate in [
            (self.admission, validate_admission_v2),
            (self.receipt, validate_receipt_v2),
        ]:
            for field in source:
                value = copy.deepcopy(source)
                del value[field]
                with self.subTest(missing=field), self.assertRaises(QualificationError):
                    validate(value, self.policy, self.signed)
            value = dict(source, unexpected=True)
            with self.assertRaises(QualificationError):
                validate(value, self.policy, self.signed)

    def test_invalid_pair_ids_are_rejected(self):
        for ident in [True, 0, -1, '801', 2**63, None]:
            for field in ['native_check_id', 'activation_check_id']:
                value = copy.deepcopy(self.admission)
                value['checks'][field] = ident
                with (
                    self.subTest(field=field, ident=ident),
                    self.assertRaises(QualificationError),
                ):
                    validate_admission_v2(value, self.policy, self.signed)
        with self.assertRaises(QualificationError):
            CheckBinding(800, 800)

    def test_receipt_pair_must_be_distinct_and_valid(self):
        for ident in [True, 800, 0, -1, '801', 2**63]:
            value = copy.deepcopy(self.receipt)
            value['evidence']['activation_check_run_id'] = ident
            with self.subTest(ident=ident), self.assertRaises(QualificationError):
                validate_receipt_v2(value, self.policy, self.signed)

    def test_same_identity_does_not_allow_swapped_or_changed_admitted_ids(self):
        for pair in [
            {'native_check_id': 801, 'activation_check_id': 800},
            {'native_check_id': 802, 'activation_check_id': 801},
            {'native_check_id': 800, 'activation_check_id': 802},
        ]:
            value = dict(self.admission, checks=pair)
            with self.subTest(pair=pair), self.assertRaises(QualificationError):
                match_admission_v2(value, self.receipt, self.policy, self.signed)

    def test_input_digest_must_match_independent_admission(self):
        value = dict(self.admission, input_manifest_sha256='9' * 64)
        with self.assertRaises(QualificationError):
            match_admission_v2(value, self.receipt, self.policy, self.signed)

    def test_identity_changes_and_wrong_policy_remain_invalid(self):
        for field, bad in [
            ('run_attempt', 2),
            ('run_id', True),
            ('launcher', '9' * 40),
            ('nonce', '9'),
        ]:
            value = copy.deepcopy(self.receipt)
            value['identity']['execution'][field] = bad
            with self.subTest(field=field), self.assertRaises(QualificationError):
                validate_receipt_v2(value, self.policy, self.signed)

    def test_policy_and_signature_are_not_replaced_by_matching_envelopes(self):
        for policy in [
            replace(self.policy, repository_id=101),
            replace(self.policy, launcher='9' * 40),
            replace(self.policy, workflow_id=201),
            replace(self.policy, fingerprint='B' * 40),
        ]:
            with self.subTest(policy=policy), self.assertRaises(QualificationError):
                match_admission_v2(self.admission, self.receipt, policy, self.signed)

    def test_new_proof_value_carries_exact_pair_nonce_and_native_job(self):
        values = {
            'qualification_id': self.receipt['qualification_id'],
            'run_id': 500,
            'signed_merge': self.signed,
            'producer_attestation': None,
            'tests': {},
            'check_run_id': 800,
            'run_number': 12,
            'activation_check_id': 801,
            'operation_nonce': '6' * 32,
            'native_job_id': 702,
        }
        result = QualifiedEvidenceV2(**values)
        self.assertEqual(
            (
                result.check_run_id,
                result.activation_check_id,
                result.operation_nonce,
                result.native_job_id,
            ),
            (800, 801, '6' * 32, 702),
        )
        for changes in [
            {'activation_check_id': 800},
            {'operation_nonce': 'bad'},
            {'native_job_id': True},
        ]:
            with self.subTest(changes=changes), self.assertRaises(QualificationError):
                QualifiedEvidenceV2(**dict(values, **changes))

    def test_malformed_mappings_are_uniformly_rejected(self):
        for value in [None, [], {}, {'schema_version': 2}]:
            for validate in [validate_admission_v2, validate_receipt_v2]:
                with (
                    self.subTest(value=value, validate=validate.__name__),
                    self.assertRaises(QualificationError),
                ):
                    validate(value, self.policy, self.signed)
        for field in ['identity', 'checks']:
            value = dict(self.admission, **{field: None})
            with self.assertRaises(QualificationError):
                validate_admission_v2(value, self.policy, self.signed)


if __name__ == '__main__':
    unittest.main()
