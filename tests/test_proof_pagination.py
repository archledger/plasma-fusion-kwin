# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
import unittest

from ci.native.pagination import get_all
from ci.native.qualification import QualificationError


class API:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, resource, **parameters):
        self.calls.append((resource, parameters))
        return self.pages[parameters['page'] - 1]


class PaginationTest(unittest.TestCase):
    def test_all_pages_are_collected_until_a_short_page(self):
        api = API([[{'id': 1}, {'id': 2}], [{'id': 3}]])
        self.assertEqual(get_all(api, 'pulls/7/reviews', per_page=2),
                         [{'id': 1}, {'id': 2}, {'id': 3}])
        self.assertEqual(len(api.calls), 2)

    def test_full_final_allowed_page_is_not_silently_accepted(self):
        api = API([[{'id': 1}, {'id': 2}]])
        with self.assertRaises(QualificationError):
            get_all(api, 'pulls/7/reviews', per_page=2, max_pages=1)
        self.assertEqual(len(api.calls), 1)

    def test_full_count_still_requires_a_termination_page(self):
        api = API([{'total_count': 2, 'check_runs': [{'id': 1}, {'id': 2}]},
                   {'total_count': 2, 'check_runs': []}])
        self.assertEqual(len(get_all(api, 'checks', key='check_runs', per_page=2)), 2)
        self.assertEqual(len(api.calls), 2)

    def test_changed_count_and_missing_records_are_rejected(self):
        for last in [{'total_count': 4, 'check_runs': [{'id': 3}]},
                     {'total_count': 3, 'check_runs': []}]:
            with self.subTest(last=last), self.assertRaises(QualificationError):
                api = API([{'total_count': 3, 'check_runs': [{'id': 1}, {'id': 2}]}, last])
                get_all(api, 'checks', key='check_runs', per_page=2)

    def test_object_pages_cannot_duplicate_an_id_to_hide_a_missing_record(self):
        api = API([{'total_count': 3, 'check_runs': [{'id': 1}, {'id': 2}]},
                   {'total_count': 3, 'check_runs': [{'id': 2}]}])
        with self.assertRaises(QualificationError):
            get_all(api, 'checks', key='check_runs', per_page=2)

    def test_list_duplicates_are_retained_for_review_state_consistency_checks(self):
        api = API([[{'id': 1}, {'id': 2}], [{'id': 2}]])
        self.assertEqual(len(get_all(api, 'reviews', per_page=2)), 3)

    def test_invalid_shapes_and_oversized_pages_fail_closed(self):
        for page in [None, {}, 'text', [1, 2, 3]]:
            with self.subTest(page=page), self.assertRaises(QualificationError):
                get_all(API([page]), 'reviews', per_page=2)

    def test_check_filter_is_preserved_on_every_page(self):
        api = API([[{'id': 1}, {'id': 2}], []])
        get_all(api, 'checks', per_page=2, check_filter='all')
        self.assertTrue(all(params['check_filter'] == 'all' for _, params in api.calls))


if __name__ == '__main__':
    unittest.main()
