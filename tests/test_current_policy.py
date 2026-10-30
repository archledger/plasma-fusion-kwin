# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Policy fixtures are observations, not actual GitHub approvals or deployments."""
import copy
import unittest

from ci.native.current_policy import verify_current_policy
from ci.native.qualification import (
    ProducerAttestedInputs,
    QualificationError,
    QualificationPolicy,
    QualifiedEvidence,
)
from ci.native.signed_merge import SignedMerge


class API:
    repository = 'owner/downstream'

    def __init__(self, values):
        self.values = values
        self.calls = []

    def get(self, resource, **parameters):
        self.calls.append((resource, parameters))
        return copy.deepcopy(self.values[resource])


class CurrentPolicyTest(unittest.TestCase):
    def setUp(self):
        self.policy = QualificationPolicy('owner/downstream', 100, 200, 'a' * 40, 'A' * 40,
                                          ('fixture-package',), app_id=300)
        signed = SignedMerge('d' * 40, 'e' * 40, 'A' * 40, 'b' * 40, 'c' * 40)
        attested = ProducerAttestedInputs(7, 101, 'f' * 40, '1' * 64, '2' * 40,
                                          '3' * 64, 4096, 10, self.policy.upstream_commit)
        self.evidence = QualifiedEvidence('4' * 64, 500, signed, attested, {'qt_test_passed': 43},
                                          check_run_id=800, run_number=12)
        native = {'id': 800, 'name': self.policy.check_name, 'app': {'id': 300},
                  'head_sha': signed.commit, 'status': 'completed', 'conclusion': 'success',
                  'external_id': self.evidence.qualification_id,
                  'details_url': 'https://github.com/owner/downstream/actions/runs/500'}
        activation = copy.deepcopy(native)
        activation.update(id=801, name='Native KWin / activation #7', head_sha=signed.head)
        self.api = API({
            'pulls/7': {'number': 7, 'state': 'open', 'merged': False, 'draft': False,
                        'mergeable': True, 'merge_commit_sha': 'f' * 40, 'user': {'id': 1}, 'commits': 1,
                        'labels': [{'name': 'native-approved'}],
                        'base': {'ref': 'main', 'sha': signed.base,
                                 'repo': {'id': 100, 'full_name': 'owner/downstream'}},
                        'head': {'sha': signed.head, 'repo': {'id': 101}}},
            'git/ref/heads/main': {'ref': 'refs/heads/main',
                                   'object': {'type': 'commit', 'sha': signed.base}},
            'git/ref/pull/7/head': {'ref': 'refs/pull/7/head',
                                   'object': {'type': 'commit', 'sha': signed.head}},
            'git/ref/pull/7/merge': {'ref': 'refs/pull/7/merge',
                                    'object': {'type': 'commit', 'sha': 'f' * 40}},
            'rules/branches/main': [],
            'branches/main/protection': {
                'required_status_checks': {'strict': True, 'contexts': [self.policy.check_name],
                                           'checks': [{'context': self.policy.check_name, 'app_id': 300}]},
                'enforce_admins': {'enabled': True}, 'allow_force_pushes': {'enabled': False},
                'allow_deletions': {'enabled': False}, 'required_linear_history': {'enabled': False},
                'required_conversation_resolution': {'enabled': False}, 'lock_branch': {'enabled': False},
                'required_pull_request_reviews': {'required_approving_review_count': 1,
                    'dismiss_stale_reviews': True, 'require_code_owner_reviews': False,
                    'require_last_push_approval': False},
            },
            'branches/main/protection/required_signatures': {'enabled': True},
            'commits/' + signed.commit + '/check-runs': {'total_count': 1, 'check_runs': [native]},
            'commits/' + signed.head + '/check-runs': {'total_count': 1, 'check_runs': [activation]},
            'check-runs/800': native,
            'pulls/7/reviews': [{'id': 900, 'state': 'APPROVED', 'commit_id': signed.head,
                               'user': {'id': 2, 'login': 'reviewer'},
                               'submitted_at': '2026-10-30T00:00:00Z'}],
            'collaborators/reviewer/permission': {'permission': 'write', 'user': {'id': 2}},
            'pulls/7/commits': [{'sha': signed.head,
                                'commit': {'verification': {'verified': True, 'reason': 'valid'}}}],
            'actions/runs/500': {
                'id': 500, 'repository': {'id': 100, 'full_name': 'owner/downstream'},
                'head_repository': {'id': 100}, 'workflow_id': 200,
                'path': self.policy.workflow_path, 'head_branch': 'main', 'head_sha': 'a' * 40,
                'event': 'workflow_dispatch', 'status': 'completed', 'conclusion': 'success',
                'run_attempt': 1, 'run_number': 12,
            },
        })

    def verify(self):
        return verify_current_policy(self.api, self.policy, self.evidence)

    def test_supported_current_policy_returns_read_only_snapshot(self):
        result = self.verify()
        self.assertEqual(result.qualification_id, self.evidence.qualification_id)
        self.assertEqual(result.activation_check_id, 801)
        self.assertEqual(result.approving_review_ids, (900,))

    def test_protection_settings_are_not_silently_weakened(self):
        protection = self.api.values['branches/main/protection']
        for key, value in [('enforce_admins', False), ('allow_force_pushes', True),
                           ('allow_deletions', True), ('lock_branch', True),
                           ('required_linear_history', True), ('required_conversation_resolution', True)]:
            with self.subTest(key=key):
                original = protection[key]['enabled']
                protection[key]['enabled'] = value
                with self.assertRaises(QualificationError):
                    self.verify()
                protection[key]['enabled'] = original

    def test_effective_rulesets_require_separate_support(self):
        self.api.values['rules/branches/main'] = [{'type': 'pull_request'}]
        with self.assertRaises(QualificationError):
            self.verify()

    def test_missing_app_mapping_and_conflicting_contexts_fail(self):
        checks = self.api.values['branches/main/protection']['required_status_checks']
        for value in [[], [{'context': self.policy.check_name, 'app_id': -1}],
                      [{'context': self.policy.check_name, 'app_id': 300},
                       {'context': self.policy.check_name, 'app_id': 301}]]:
            with self.subTest(value=value):
                checks['checks'] = value
                with self.assertRaises(QualificationError):
                    self.verify()

    def test_pointer_for_new_signed_commit_blocks_old_native_success(self):
        pointer = self.api.values['commits/' + self.evidence.signed_merge.head + '/check-runs']['check_runs'][0]
        pointer['external_id'] = '9' * 64
        pointer['status'] = 'in_progress'
        pointer['conclusion'] = None
        with self.assertRaises(QualificationError):
            self.verify()

    def test_duplicate_or_wrong_app_pointer_is_not_authority(self):
        value = self.api.values['commits/' + self.evidence.signed_merge.head + '/check-runs']
        original = copy.deepcopy(value)
        value['check_runs'][0]['app']['id'] = 301
        with self.assertRaises(QualificationError):
            self.verify()
        value.update(original)
        value['check_runs'].append(dict(value['check_runs'][0], id=802))
        value['total_count'] = 2
        with self.assertRaises(QualificationError):
            self.verify()

    def test_unresolved_review_and_unsigned_commit_are_blocking(self):
        self.api.values['pulls/7/reviews'][0]['state'] = 'CHANGES_REQUESTED'
        with self.assertRaises(QualificationError):
            self.verify()
        self.api.values['pulls/7/reviews'][0]['state'] = 'APPROVED'
        self.api.values['pulls/7/commits'][0]['commit']['verification']['verified'] = False
        with self.assertRaises(QualificationError):
            self.verify()

    def test_codeowner_and_last_push_rules_are_not_ignored(self):
        rules = self.api.values['branches/main/protection']['required_pull_request_reviews']
        for name in ['require_code_owner_reviews', 'require_last_push_approval']:
            rules[name] = True
            with self.subTest(name=name), self.assertRaises(QualificationError):
                self.verify()
            rules[name] = False

    def test_native_required_app_is_checked_against_actual_check_app(self):
        value = self.api.values['commits/' + self.evidence.signed_merge.commit + '/check-runs']
        value['check_runs'][0]['app']['id'] = 301
        with self.assertRaises(QualificationError):
            self.verify()

    def test_wrong_app_same_name_does_not_replace_or_duplicate_the_required_pair(self):
        value = self.api.values['commits/' + self.evidence.signed_merge.commit + '/check-runs']
        spoof = copy.deepcopy(value['check_runs'][0])
        spoof.update(id=999, app={'id': 301})
        value['check_runs'].append(spoof)
        value['total_count'] = 2
        self.verify()

    def test_all_mapped_required_checks_must_succeed(self):
        requirement = self.api.values['branches/main/protection']['required_status_checks']
        requirement['contexts'].append('Fast check')
        requirement['checks'].append({'context': 'Fast check', 'app_id': 400})
        value = self.api.values['commits/' + self.evidence.signed_merge.commit + '/check-runs']
        fast = {'id': 802, 'name': 'Fast check', 'app': {'id': 400},
                'head_sha': self.evidence.signed_merge.commit,
                'status': 'completed', 'conclusion': 'success'}
        value['check_runs'].append(fast)
        value['total_count'] = 2
        self.verify()
        fast['conclusion'] = 'failure'
        with self.assertRaises(QualificationError):
            self.verify()

    def test_new_pointer_during_evaluation_is_detected(self):
        original = self.api.get
        pointer_resource = 'commits/' + self.evidence.signed_merge.head + '/check-runs'
        reads = 0

        def changing(resource, **parameters):
            nonlocal reads
            value = original(resource, **parameters)
            if resource == pointer_resource:
                reads += 1
                if reads == 2:
                    value['check_runs'][0]['external_id'] = '9' * 64
            return value

        self.api.get = changing
        with self.assertRaises(QualificationError):
            self.verify()

    def test_changed_review_during_evaluation_is_detected(self):
        original = self.api.get
        reads = 0

        def changing(resource, **parameters):
            nonlocal reads
            value = original(resource, **parameters)
            if resource == 'pulls/7/reviews':
                reads += 1
                if reads == 2:
                    value[0]['state'] = 'CHANGES_REQUESTED'
            return value

        self.api.get = changing
        with self.assertRaises(QualificationError):
            self.verify()

    def test_rerun_is_not_accepted_as_original_attempt(self):
        self.api.values['actions/runs/500']['run_attempt'] = 2
        with self.assertRaises(QualificationError):
            self.verify()

    def test_nested_status_enforcement_cannot_exempt_administrators(self):
        status = self.api.values['branches/main/protection']['required_status_checks']
        for value in ['non_admins', None, True]:
            with self.subTest(value=value):
                status['enforcement_level'] = value
                with self.assertRaises(QualificationError):
                    self.verify()

    def test_explicit_everyone_status_enforcement_is_supported(self):
        self.api.values['branches/main/protection']['required_status_checks']['enforcement_level'] = 'everyone'
        self.verify()

    def test_unknown_nested_enforcement_fields_are_refused(self):
        original = copy.deepcopy(self.api.values)
        for field in ['required_status_checks', 'enforce_admins', 'allow_force_pushes',
                      'allow_deletions', 'required_linear_history',
                      'required_conversation_resolution', 'lock_branch', 'block_creations',
                      'allow_fork_syncing', 'required_signatures']:
            with self.subTest(field=field):
                self.api.values = copy.deepcopy(original)
                protection = self.api.values['branches/main/protection']
                value = protection.setdefault(field, {'enabled': field == 'required_signatures'})
                value['future_enforcement'] = {'bypass': True}
                with self.assertRaises(QualificationError):
                    self.verify()

    def test_signature_and_review_subobjects_have_explicit_schemas(self):
        original = copy.deepcopy(self.api.values)
        mutations = ['signature', 'dismissal', 'bypass']
        for kind in mutations:
            with self.subTest(kind=kind):
                self.api.values = copy.deepcopy(original)
                if kind == 'signature':
                    self.api.values['branches/main/protection/required_signatures']['future_rule'] = False
                else:
                    reviews = self.api.values['branches/main/protection']['required_pull_request_reviews']
                    name = 'dismissal_restrictions' if kind == 'dismissal' else 'bypass_pull_request_allowances'
                    reviews[name] = {'future_rule': []}
                with self.assertRaises(QualificationError):
                    self.verify()

    def test_known_metadata_urls_do_not_change_enforcement(self):
        protection = self.api.values['branches/main/protection']
        for value in protection.values():
            if isinstance(value, dict):
                value['url'] = 'https://api.github.com/repos/owner/downstream/branches/main/protection'
        protection['required_status_checks']['contexts_url'] = (
            'https://api.github.com/repos/owner/downstream/branches/main/protection/required_status_checks/contexts')
        self.api.values['branches/main/protection/required_signatures']['url'] = (
            'https://api.github.com/repos/owner/downstream/branches/main/protection/required_signatures')
        self.verify()

    def test_empty_explicit_bypass_lists_are_not_a_bypass(self):
        reviews = self.api.values['branches/main/protection']['required_pull_request_reviews']
        reviews['bypass_pull_request_allowances'] = {'users': [], 'teams': [], 'apps': []}
        self.verify()
        del reviews['bypass_pull_request_allowances']['apps']
        with self.assertRaises(QualificationError):
            self.verify()


if __name__ == '__main__':
    unittest.main()
