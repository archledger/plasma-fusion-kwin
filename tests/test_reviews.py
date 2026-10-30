# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
import copy
import types
import unittest

from ci.native.qualification import QualificationError
from ci.native.reviews import evaluate_reviews

HEAD = 'a' * 40


class API:
    repository = 'owner/downstream'

    def __init__(self):
        self.permissions = {2: 'write', 3: 'admin', 4: 'read'}
        self.calls = []

    def get(self, resource):
        self.calls.append(resource)
        user_id = int(resource.split('/')[1].removeprefix('user'))
        return {'permission': self.permissions[user_id], 'user': {'id': user_id}}


def review(identifier, user_id, state, commit=HEAD):
    return {'id': identifier, 'user': {'id': user_id, 'login': f'user{user_id}'},
            'state': state, 'commit_id': commit,
            'submitted_at': f'2026-10-30T00:00:{identifier:02d}Z'}


class ReviewsTest(unittest.TestCase):
    def setUp(self):
        self.api = API()
        self.policy = types.SimpleNamespace(repository=self.api.repository)

    def evaluate(self, reviews, count=1, author_id=1):
        return evaluate_reviews(self.api, self.policy, reviews, head=HEAD,
                                author_id=author_id, required=count)

    def test_current_authorized_approval_counts(self):
        self.assertEqual(self.evaluate([review(1, 2, 'APPROVED')]), (1,))

    def test_comments_and_pending_reviews_do_not_erase_approval(self):
        pending = review(3, 2, 'PENDING')
        del pending['submitted_at']
        self.assertEqual(self.evaluate([review(1, 2, 'APPROVED'),
                                        review(2, 2, 'COMMENTED'), pending]), (1,))

    def test_dismissed_approval_reveals_earlier_changes_request(self):
        rows = [review(1, 2, 'CHANGES_REQUESTED'), review(2, 2, 'DISMISSED'),
                review(3, 3, 'APPROVED')]
        with self.assertRaises(QualificationError):
            self.evaluate(rows)

    def test_dismissed_changes_request_does_not_block(self):
        self.assertEqual(self.evaluate([review(1, 2, 'DISMISSED'), review(2, 3, 'APPROVED')]), (2,))
        self.assertEqual(self.evaluate([review(1, 2, 'APPROVED'), review(2, 2, 'DISMISSED')]), (1,))

    def test_dismissal_of_older_review_does_not_erase_newer_approval(self):
        self.assertEqual(self.evaluate([review(2, 2, 'APPROVED'), review(1, 2, 'DISMISSED')]), (2,))

    def test_new_approval_resolves_older_changes_request(self):
        self.assertEqual(self.evaluate([review(1, 2, 'CHANGES_REQUESTED'),
                                        review(2, 2, 'APPROVED')]), (2,))

    def test_permission_loss_does_not_resolve_changes_request(self):
        self.api.permissions[2] = 'read'
        with self.assertRaises(QualificationError):
            self.evaluate([review(1, 2, 'CHANGES_REQUESTED'), review(2, 3, 'APPROVED')])

    def test_permission_loss_removes_approval_credit(self):
        self.api.permissions[2] = 'read'
        with self.assertRaises(QualificationError):
            self.evaluate([review(1, 2, 'APPROVED')])

    def test_author_and_stale_approvals_do_not_count(self):
        for row, author in [(review(1, 2, 'APPROVED'), 2),
                            (review(1, 2, 'APPROVED', 'b' * 40), 1)]:
            with self.subTest(row=row), self.assertRaises(QualificationError):
                self.evaluate([row], author_id=author)

    def test_duplicate_pages_do_not_count_twice(self):
        row = review(1, 2, 'APPROVED')
        self.assertEqual(self.evaluate([row, copy.deepcopy(row)]), (1,))
        with self.assertRaises(QualificationError):
            self.evaluate([row, copy.deepcopy(row)], count=2)

    def test_conflicting_duplicates_are_not_a_consistent_snapshot(self):
        row = review(1, 2, 'APPROVED')
        with self.assertRaises(QualificationError):
            self.evaluate([row, dict(row, state='DISMISSED')])

    def test_latest_changes_request_blocks_even_with_no_approval_requirement(self):
        with self.assertRaises(QualificationError):
            self.evaluate([review(1, 2, 'APPROVED'), review(2, 2, 'CHANGES_REQUESTED')], count=0)

    def test_permission_identity_must_match_reviewer(self):
        self.api.get = lambda _: {'permission': 'admin', 'user': {'id': 99}}
        with self.assertRaises(QualificationError):
            self.evaluate([review(1, 2, 'APPROVED')])


if __name__ == '__main__':
    unittest.main()
