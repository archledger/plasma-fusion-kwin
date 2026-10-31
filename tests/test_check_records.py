# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pure visible-record checks; these fixtures do not prove API completeness."""

import copy
import unittest

from ci.native.check_records import (
    CheckIdentity,
    select_check,
    validate_check,
    validate_creation,
)
from ci.native.qualification import QualificationError


class CheckRecordsTest(unittest.TestCase):
    def setUp(self):
        self.identity = CheckIdentity(
            'owner/repo', 300, 'b' * 40, 'Native KWin / trusted', 'c' * 64, 500
        )
        self.pending = {
            'id': 800,
            'app': {'id': 300},
            'head_sha': 'b' * 40,
            'name': self.identity.name,
            'external_id': 'c' * 64,
            'details_url': self.identity.details_url,
            'status': 'in_progress',
            'conclusion': None,
            'completed_at': None,
        }
        self.old = dict(
            self.pending,
            id=700,
            external_id='d' * 64,
            details_url='https://github.com/owner/repo/actions/runs/499',
            status='completed',
            conclusion='failure',
            completed_at='2026-10-31T00:00:00Z',
        )

    def test_fresh_creation_accepts_old_success_and_failure_without_mutation(self):
        for conclusion in ['success', 'failure']:
            records = [dict(self.old, conclusion=conclusion)]
            original = copy.deepcopy(records)
            validate_creation(records, self.identity)
            self.assertEqual(records, original)

    def test_selection_uses_exact_id_not_order_or_old_success(self):
        old = dict(self.old, id=999, conclusion='success')
        for records in [[old, self.pending], [self.pending, old]]:
            self.assertEqual(
                select_check(
                    records, self.identity, 800, status='in_progress', conclusion=None
                ),
                self.pending,
            )

    def test_current_success_is_not_rejected_as_history(self):
        current = dict(
            self.pending,
            status='completed',
            conclusion='success',
            completed_at='2026-10-31T00:00:01Z',
        )
        self.assertEqual(
            select_check(
                [self.old, current],
                self.identity,
                800,
                status='completed',
                conclusion='success',
            ),
            current,
        )

    def test_every_expected_identity_field_is_required(self):
        changes = {
            'id': 801,
            'app': {'id': 301},
            'head_sha': 'a' * 40,
            'name': 'other',
            'external_id': 'd' * 64,
            'details_url': 'https://github.com/owner/repo/actions/runs/501',
            'status': 'completed',
            'conclusion': 'failure',
            'completed_at': '2026-10-31T00:00:01Z',
        }
        for field, value in changes.items():
            with self.subTest(field=field), self.assertRaises(QualificationError):
                validate_check(
                    dict(self.pending, **{field: value}),
                    self.identity,
                    800,
                    status='in_progress',
                    conclusion=None,
                )

    def test_creation_rejects_current_qid_or_run_even_when_completed(self):
        for old in [
            dict(self.old, external_id=self.identity.qualification_id),
            dict(self.old, details_url=self.identity.details_url),
            self.pending,
        ]:
            with self.subTest(old=old), self.assertRaises(QualificationError):
                validate_creation([old], self.identity)

    def test_duplicate_qids_runs_and_ids_are_conflicts_in_visible_history(self):
        for other in [
            dict(self.old, id=701),
            dict(self.old, id=701, external_id='e' * 64),
            dict(self.old, id=701, details_url=self.identity.details_url),
            dict(self.old, name='other'),
        ]:
            with self.subTest(other=other), self.assertRaises(QualificationError):
                select_check(
                    [self.old, other, self.pending],
                    self.identity,
                    800,
                    status='in_progress',
                    conclusion=None,
                )

    def test_other_pending_or_malformed_matching_history_is_refused(self):
        for old in [
            dict(self.old, status='queued', conclusion=None, completed_at=None),
            dict(self.old, completed_at=None),
            dict(self.old, conclusion='future'),
            dict(self.old, completed_at='2026-10-31Z'),
            dict(self.old, completed_at='2026-02-30T00:00:00Z'),
            dict(self.old, head_sha='a' * 40),
            dict(self.old, external_id='bad'),
            dict(self.old, details_url=self.old['details_url'] + '?token=private'),
            dict(
                self.old, details_url='https://github.com/owner/repo/actions/runs/0499'
            ),
            dict(
                self.old, details_url='https://github.com/other/repo/actions/runs/499'
            ),
        ]:
            with self.subTest(old=old), self.assertRaises(QualificationError):
                validate_creation([old], self.identity)

    def test_same_historical_qid_cannot_name_two_different_old_runs(self):
        other = dict(
            self.old,
            id=701,
            details_url='https://github.com/owner/repo/actions/runs/498',
        )
        for history in [[self.old, other], [other, self.old]]:
            with self.subTest(history=history), self.assertRaises(QualificationError):
                validate_creation(history, self.identity)

    def test_direct_read_rejects_missing_and_wrongly_typed_fields(self):
        for field in self.pending:
            value = dict(self.pending)
            del value[field]
            with self.subTest(missing=field), self.assertRaises(QualificationError):
                validate_check(
                    value, self.identity, 800, status='in_progress', conclusion=None
                )
        for field, value in [
            ('id', True),
            ('app', {'id': True}),
            ('status', []),
            ('external_id', []),
            ('details_url', 500),
        ]:
            with self.subTest(field=field), self.assertRaises(QualificationError):
                validate_check(
                    dict(self.pending, **{field: value}),
                    self.identity,
                    800,
                    status='in_progress',
                    conclusion=None,
                )

    def test_unrelated_apps_and_names_are_not_authority_or_conflicts(self):
        records = [
            dict(self.pending, id=801, app={'id': 301}),
            dict(self.pending, id=802, name='other'),
        ]
        validate_creation(records, self.identity)
        with self.assertRaises(QualificationError):
            select_check(
                records, self.identity, 800, status='in_progress', conclusion=None
            )

    def test_missing_selected_id_and_unbound_same_qid_are_refused(self):
        for records in [
            [],
            [self.old],
            [dict(self.pending, id=801)],
            [self.pending, dict(self.pending, id=801)],
        ]:
            with self.subTest(records=records), self.assertRaises(QualificationError):
                select_check(
                    records, self.identity, 800, status='in_progress', conclusion=None
                )

    def test_invalid_types_and_oversized_inventory_are_refused(self):
        for records in [None, {}, [None], [dict(self.old, id=True)], [self.old] * 501]:
            with self.subTest(records=records), self.assertRaises(QualificationError):
                validate_creation(records, self.identity)
        for changes in [
            {'app_id': True},
            {'head': 'bad'},
            {'qualification_id': '0' * 64},
            {'run_id': True},
            {'repository': '../repo'},
            {'name': 'bad\nname'},
        ]:
            values = {
                'repository': 'owner/repo',
                'app_id': 300,
                'head': 'b' * 40,
                'name': self.identity.name,
                'qualification_id': 'c' * 64,
                'run_id': 500,
            }
            values.update(changes)
            with self.subTest(changes=changes), self.assertRaises(QualificationError):
                CheckIdentity(**values)


if __name__ == '__main__':
    unittest.main()
