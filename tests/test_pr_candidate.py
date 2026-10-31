# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fail-closed revision binding, without network or GitHub write credentials."""
import copy
import unittest

from ci.native.pr_candidate import CandidateError, Policy, bind_candidate

BASE = 'a' * 40
HEAD = 'b' * 40
MERGE = 'c' * 40


class CandidateTest(unittest.TestCase):
    def setUp(self):
        self.policy = Policy(repository_id=100, repository='owner/downstream',
                             workflow_id=200, workflow_path='.github/workflows/intake.yml')
        repository = {'id': 100, 'full_name': 'owner/downstream'}
        fork = {'id': 101, 'full_name': 'contributor/downstream'}
        self.observed = {
            'run': {'id': 300, 'run_attempt': 2, 'repository': repository,
                    'workflow_id': 200, 'path': '.github/workflows/intake.yml',
                    'event': 'pull_request', 'status': 'completed', 'conclusion': 'success',
                    'head_sha': HEAD, 'head_branch': 'fix/opacity', 'head_repository': fork,
                    'pull_requests': [{'number': 7}]},
            'pr': {'number': 7, 'state': 'open', 'draft': False, 'merged': False,
                   'mergeable': True, 'merge_commit_sha': MERGE,
                   'labels': [{'name': 'native-approved'}],
                   'base': {'repo': repository, 'ref': 'main', 'sha': BASE},
                   'head': {'repo': fork, 'ref': 'fix/opacity', 'sha': HEAD}},
            'base_ref': {'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': BASE}},
            'head_ref': {'ref': 'refs/heads/fix/opacity',
                         'object': {'type': 'commit', 'sha': HEAD}},
            'merge_ref': {'ref': 'refs/pull/7/merge',
                          'object': {'type': 'commit', 'sha': MERGE}},
            'merge_commit': {'sha': MERGE, 'tree': {'sha': 'd' * 40},
                             'parents': [{'sha': BASE}, {'sha': HEAD}]},
        }

    def bind(self):
        return bind_candidate(self.policy, run_id=300, run_attempt=2, **self.observed)

    def test_fork_candidate_binds_exact_source_and_attempt(self):
        candidate = self.bind()
        self.assertEqual((candidate.repository_id, candidate.head_repository_id), (100, 101))
        self.assertEqual((candidate.pr_number, candidate.run_id, candidate.run_attempt), (7, 300, 2))
        self.assertEqual((candidate.base, candidate.head, candidate.merge), (BASE, HEAD, MERGE))
        self.assertEqual(candidate.tree, 'd' * 40)

    def test_intake_conclusion_does_not_assert_native_success(self):
        candidate = self.bind()
        for conclusion in ['failure', 'skipped', 'cancelled', None]:
            with self.subTest(conclusion=conclusion):
                self.observed['run']['conclusion'] = conclusion
                self.assertEqual(self.bind(), candidate)

    def test_wrong_or_stale_run_is_rejected(self):
        original = copy.deepcopy(self.observed)
        for key, value in [('id', 301), ('run_attempt', 3), ('workflow_id', 201),
                           ('path', '.github/workflows/spoof.yml'), ('event', 'push'),
                           ('status', 'in_progress'), ('head_sha', 'e' * 40),
                           ('head_branch', 'another'), ('repository', {'id': 999}),
                           ('head_repository', {'id': 999}), ('pull_requests', []),
                           ('pull_requests', [{'number': 8}]),
                           ('pull_requests', [{'number': 7}, {'number': 8}])]:
            with self.subTest(key=key, value=value):
                self.observed = copy.deepcopy(original)
                self.observed['run'][key] = value
                with self.assertRaises(CandidateError):
                    self.bind()

    def test_closed_draft_unapproved_or_unknown_mergeability_is_rejected(self):
        original = copy.deepcopy(self.observed)
        for key, value in [('state', 'closed'), ('draft', True), ('draft', None),
                           ('merged', True), ('mergeable', None), ('mergeable', False),
                           ('labels', []), ('merge_commit_sha', None)]:
            with self.subTest(key=key, value=value):
                self.observed = copy.deepcopy(original)
                self.observed['pr'][key] = value
                with self.assertRaises(CandidateError):
                    self.bind()

    def test_live_refs_and_ordered_merge_parents_must_agree(self):
        original = copy.deepcopy(self.observed)
        mutations = [
            ('base_ref', 'object', {'type': 'commit', 'sha': 'e' * 40}),
            ('head_ref', 'object', {'type': 'commit', 'sha': 'e' * 40}),
            ('merge_ref', 'object', {'type': 'commit', 'sha': 'e' * 40}),
            ('merge_ref', 'ref', 'refs/pull/8/merge'),
            ('head_ref', 'ref', 'refs/heads/another'),
            ('base_ref', 'object', {'type': 'tag', 'sha': BASE}),
            ('merge_commit', 'sha', 'e' * 40),
            ('merge_commit', 'parents', [{'sha': HEAD}, {'sha': BASE}]),
            ('merge_commit', 'parents', [{'sha': BASE}]),
            ('merge_commit', 'parents', [{'sha': BASE}, {'sha': HEAD}, {'sha': 'e' * 40}]),
        ]
        for obj, key, value in mutations:
            with self.subTest(obj=obj, key=key, value=value):
                self.observed = copy.deepcopy(original)
                self.observed[obj][key] = value
                with self.assertRaises(CandidateError):
                    self.bind()

    def test_cached_pr_base_cannot_hide_changed_live_base(self):
        self.observed['base_ref']['object']['sha'] = 'e' * 40
        with self.assertRaises(CandidateError):
            self.bind()

    def test_stale_pr_base_is_rejected_even_when_other_observations_agree(self):
        self.observed['pr']['base']['sha'] = 'e' * 40
        with self.assertRaises(CandidateError):
            self.bind()

    def test_wrong_repository_and_base_branch_are_rejected(self):
        original = copy.deepcopy(self.observed)
        for obj, key, value in [('base', 'repo', {'id': 999, 'full_name': 'owner/downstream'}),
                                ('base', 'repo', {'id': 100, 'full_name': 'owner/other'}),
                                ('base', 'ref', 'release'), ('head', 'repo', None)]:
            with self.subTest(obj=obj, key=key):
                self.observed = copy.deepcopy(original)
                self.observed['pr'][obj][key] = value
                with self.assertRaises(CandidateError):
                    self.bind()

    def test_same_repository_pr_uses_the_same_binding_rules(self):
        repository = self.observed['pr']['base']['repo']
        self.observed['pr']['head']['repo'] = repository
        self.observed['run']['head_repository'] = repository
        self.assertEqual(self.bind().head_repository_id, self.policy.repository_id)

    def test_missing_approval_or_altered_tree_cannot_reuse_candidate(self):
        candidate = self.bind()
        self.observed['merge_commit']['tree']['sha'] = 'e' * 40
        self.assertNotEqual(self.bind(), candidate)
        self.observed['pr']['labels'] = [{'name': 'native-approved-spoof'}]
        with self.assertRaises(CandidateError):
            self.bind()

    def test_malformed_values_fail_without_coercion(self):
        original = copy.deepcopy(self.observed)
        for obj, key, values in [('run', 'id', [True, '300', 0, -1, None]),
                                 ('run', 'run_attempt', [True, '2', 0]),
                                 ('pr', 'number', [True, '7', None]),
                                 ('run', 'head_sha', ['', '--help', 'b' * 39, 'B' * 40,
                                                      '0' * 40, HEAD + '\n'])]:
            for value in values:
                with self.subTest(obj=obj, key=key, value=value):
                    self.observed = copy.deepcopy(original)
                    self.observed[obj][key] = value
                    with self.assertRaises(CandidateError):
                        self.bind()

    def test_missing_or_wrong_shaped_observations_fail_closed(self):
        original = copy.deepcopy(self.observed)
        for key in original:
            for value in [None, [], {}, 'spoof']:
                with self.subTest(key=key, value=value):
                    self.observed = copy.deepcopy(original)
                    self.observed[key] = value
                    with self.assertRaises(CandidateError):
                        self.bind()

    def test_malformed_nested_observations_fail_closed(self):
        original = copy.deepcopy(self.observed)
        for obj, key, value in [('pr', 'labels', [None]),
                                ('pr', 'head', {'repo': []}),
                                ('run', 'pull_requests', [None]),
                                ('base_ref', 'object', None),
                                ('merge_commit', 'parents', [None, {}]),
                                ('merge_commit', 'tree', {'sha': 'invalid'})]:
            with self.subTest(obj=obj, key=key):
                self.observed = copy.deepcopy(original)
                self.observed[obj][key] = value
                with self.assertRaises(CandidateError):
                    self.bind()


if __name__ == '__main__':
    unittest.main()
