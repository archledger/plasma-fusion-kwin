# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci.native.check_journal import CheckJournal
from ci.native.qualification import QualificationError


class CheckJournalTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'writes.json'

    def journal(self, phase='admit', **changes):
        values = {
            'qualification_id': 'a' * 64,
            'run_id': 500,
            'nonce': 'b' * 32,
            'phase': phase,
        }
        values.update(changes)
        return CheckJournal(self.path, **values)

    def read(self):
        return json.loads(self.path.read_text())

    def test_creation_records_intent_before_acknowledgment(self):
        journal = self.journal()
        journal.begin('activation', 'create')
        self.assertEqual(
            self.read()['writes'],
            [
                {
                    'role': 'activation',
                    'action': 'create',
                    'expected_check_id': None,
                    'acknowledged_check_id': None,
                    'status': 'pending',
                }
            ],
        )
        journal.confirm(800)
        self.assertEqual(self.read()['writes'][0]['acknowledged_check_id'], 800)
        self.assertEqual(self.read()['writes'][0]['status'], 'confirmed')
        journal.begin('native', 'create')
        journal.confirm(801)
        self.assertEqual(
            [w['acknowledged_check_id'] for w in self.read()['writes']], [800, 801]
        )
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_completion_can_only_acknowledge_expected_id(self):
        journal = self.journal('report')
        journal.begin('native', 'complete', check_id=800)
        with self.assertRaises(QualificationError):
            journal.confirm(801)
        self.assertEqual(self.read()['writes'][0]['status'], 'pending')
        with self.assertRaises(QualificationError):
            journal.begin('activation', 'complete', check_id=802)
        with self.assertRaises(QualificationError):
            journal.confirm(800)

    def test_pending_attempt_cannot_be_repeated_or_bypassed(self):
        journal = self.journal()
        journal.begin('activation', 'create')
        for role in ['activation', 'native']:
            with self.subTest(role=role), self.assertRaises(QualificationError):
                journal.begin(role, 'create')
        self.assertEqual(len(self.read()['writes']), 1)

    def test_confirmed_role_cannot_be_replayed(self):
        journal = self.journal()
        journal.begin('activation', 'create')
        journal.confirm(800)
        with self.assertRaises(QualificationError):
            journal.begin('activation', 'create')
        with self.assertRaises(QualificationError):
            journal.confirm(800)

    def test_invalid_actions_and_ids_do_not_create_intent(self):
        journal = self.journal()
        for role, action, ident in [
            ('other', 'create', None),
            ('native', 'complete', 800),
            ('native', 'create', 800),
            ('native', 'reset', None),
        ]:
            with (
                self.subTest(role=role, action=action),
                self.assertRaises(QualificationError),
            ):
                journal.begin(role, action, check_id=ident)
        self.assertEqual(self.read()['writes'], [])

    def test_existing_journal_is_not_resumed_or_overwritten(self):
        self.path.write_text('private preexisting content')
        with self.assertRaises(QualificationError):
            self.journal()
        self.assertEqual(self.path.read_text(), 'private preexisting content')

    def test_unsafe_parent_and_symlink_are_rejected(self):
        self.root.chmod(0o755)
        with self.assertRaises(QualificationError):
            self.journal()
        self.root.chmod(0o700)
        target = self.root / 'target'
        target.write_text('preserve')
        self.path.symlink_to(target)
        with self.assertRaises(QualificationError):
            self.journal()
        self.assertEqual(target.read_text(), 'preserve')

    def test_persistence_failure_keeps_previous_intent_and_poisoned_instance(self):
        journal = self.journal()
        journal.begin('native', 'create')
        before = self.path.read_bytes()
        with (
            patch(
                'ci.native.check_journal.os.replace',
                side_effect=OSError('private error'),
            ),
            self.assertRaises(QualificationError),
        ):
            journal.confirm(800)
        self.assertEqual(self.path.read_bytes(), before)
        with self.assertRaises(QualificationError):
            journal.confirm(800)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['writes.json'])

    def test_staging_cleanup_error_still_poisoned_and_sanitized(self):
        journal = self.journal()
        before = self.path.read_bytes()
        sentinel = self.root / 'unrelated'
        sentinel.write_text('preserve this file')
        with (
            patch(
                'ci.native.check_journal.os.replace',
                side_effect=OSError('private-error'),
            ),
            patch(
                'ci.native.check_journal.Path.unlink',
                side_effect=OSError('private-path'),
            ),
            self.assertRaises(QualificationError) as caught,
        ):
            journal.begin('native', 'create')
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(sentinel.read_text(), 'preserve this file')
        self.assertNotIn('private', str(caught.exception))
        with self.assertRaises(QualificationError):
            journal.begin('native', 'create')

    def test_directory_sync_failure_after_replacement_forbids_continuation(self):
        journal = self.journal()
        journal.begin('native', 'create')
        real = os.fsync

        def fsync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError('private directory sync error')
            real(fd)

        with (
            patch('ci.native.check_journal.os.fsync', side_effect=fsync),
            self.assertRaises(QualificationError),
        ):
            journal.confirm(800)
        self.assertEqual(self.read()['writes'][0]['status'], 'confirmed')
        with self.assertRaises(QualificationError):
            journal.begin('activation', 'create')
        with self.assertRaises(QualificationError):
            journal.confirm(800)

    def test_replaced_or_hardlinked_journal_cannot_be_overwritten(self):
        journal = self.journal()
        os.link(self.path, self.root / 'alias')
        with self.assertRaises(QualificationError):
            journal.begin('native', 'create')
        self.assertEqual(self.read()['writes'], [])

    def test_changed_parent_permissions_are_refused_before_persistence(self):
        journal = self.journal()
        self.root.chmod(0o755)
        with self.assertRaises(QualificationError):
            journal.begin('native', 'create')
        self.assertEqual(self.read()['writes'], [])

    def test_replaced_inode_is_not_adopted(self):
        journal = self.journal()
        other = self.root / 'other'
        other.write_text('unrelated content')
        other.replace(self.path)
        with self.assertRaises(QualificationError):
            journal.begin('native', 'create')
        self.assertEqual(self.path.read_text(), 'unrelated content')

    def test_both_roles_cannot_acknowledge_the_same_id(self):
        journal = self.journal()
        journal.begin('activation', 'create')
        journal.confirm(800)
        journal.begin('native', 'create')
        with self.assertRaises(QualificationError):
            journal.confirm(800)
        self.assertEqual(self.read()['writes'][-1]['status'], 'pending')

    def test_size_limit_failure_is_fail_closed(self):
        with (
            patch('ci.native.check_journal.MAX_JOURNAL_BYTES', 1),
            self.assertRaises(QualificationError),
        ):
            self.journal()
        self.assertFalse(self.path.exists())

    def test_initial_write_failure_leaves_nonreusable_marker(self):
        with (
            patch(
                'ci.native.check_journal.os.fsync', side_effect=OSError('private error')
            ),
            self.assertRaises(QualificationError),
        ):
            self.journal()
        self.assertTrue(self.path.exists())
        with self.assertRaises(QualificationError):
            self.journal()

    def test_short_writes_are_completed_without_losing_json(self):
        real = os.write
        with patch(
            'ci.native.check_journal.os.write',
            side_effect=lambda fd, data: real(fd, data[:3]),
        ):
            journal = self.journal()
            journal.begin('activation', 'create')
            journal.confirm(800)
        self.assertEqual(self.read()['writes'][0]['status'], 'confirmed')

    def test_invalid_identity_is_refused_before_file_creation(self):
        for changes in [
            {'run_id': True},
            {'nonce': 'x'},
            {'qualification_id': 'x'},
            {'phase': 'native'},
        ]:
            with self.subTest(changes=changes), self.assertRaises(QualificationError):
                self.journal(**changes)
            self.assertFalse(self.path.exists())


if __name__ == '__main__':
    unittest.main()
