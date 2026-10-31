# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Read-only merge-readiness observations, never authorization to perform a push.

The activation pointer must be maintained by a trusted serialized producer.
Finalization must also serialize with activation; repeated reads do not close
that cross-system race. Unsupported protections fail closed instead of being
disabled or treated as satisfied.
"""
from dataclasses import dataclass

from ci.native.check_records import CheckIdentity, select_check, validate_check
from ci.native.coordination import SignedCoordination, validate_record
from ci.native.current_candidate import verify_current_association
from ci.native.pagination import get_all
from ci.native.qualification import (
    QualificationError,
    QualifiedEvidenceV2,
    canonical_json,
    digest,
    identifier,
    require,
    verify_run,
)
from ci.native.reviews import evaluate_reviews


@dataclass(frozen=True)
class ReadinessSnapshot:
    qualification_id: str
    required_check_ids: tuple[int, ...]
    activation_check_id: int
    approving_review_ids: tuple[int, ...]


def rule_fields(value, allowed, required=()):
    require(type(value) is dict and set(value) <= set(allowed)
            and set(required) <= set(value), 'unsupported or missing protection fields')
    for name, item in value.items():
        if name == 'url' or name.endswith('_url'):
            require(type(item) is str and item.startswith('https://api.github.com/')
                    and len(item) <= 4096 and all(ord(char) >= 32 for char in item),
                    'invalid protection metadata URL')


def enabled_rule(value, expected=None):
    rule_fields(value, {'url', 'enabled'}, {'enabled'})
    require(type(value['enabled']) is bool, 'invalid protection flag')
    require(expected is None or value['enabled'] is expected, 'unsupported or unsafe protection flag')
    return value['enabled']


def actor_restrictions(value, *, bypass=False):
    rule_fields(value, {'url', 'users_url', 'teams_url', 'apps_url', 'users', 'teams', 'apps'},
                {'users', 'teams', 'apps'} if bypass else ())
    for field in ['users', 'teams', 'apps']:
        if field in value:
            require(type(value[field]) is list, 'invalid restriction actor list')
            require(not bypass or not value[field], 'review bypass allowances are unsupported')


def protection_requirements(value, policy):
    known = {'url', 'required_status_checks', 'enforce_admins', 'required_pull_request_reviews',
             'restrictions', 'required_linear_history', 'allow_force_pushes', 'allow_deletions',
             'block_creations', 'required_conversation_resolution', 'lock_branch',
             'allow_fork_syncing', 'required_signatures'}
    rule_fields(value, known)
    enabled_rule(value['enforce_admins'], True)
    require(value.get('restrictions') is None, 'push restrictions need separate operator verification')
    for field in ['allow_force_pushes', 'allow_deletions', 'required_linear_history',
                  'required_conversation_resolution', 'lock_branch']:
        enabled_rule(value[field], False)
    for field in ['block_creations', 'allow_fork_syncing']:
        if field in value:
            enabled_rule(value[field], False)
    if 'required_signatures' in value:
        enabled_rule(value['required_signatures'])
    status = value['required_status_checks']
    rule_fields(status, {'url', 'contexts_url', 'strict', 'contexts', 'checks', 'enforcement_level'},
                {'strict', 'contexts', 'checks'})
    require('enforcement_level' not in status or (type(status['enforcement_level']) is str
                                                and status['enforcement_level'] == 'everyone'),
            'status checks do not explicitly cover everyone')
    require(status['strict'] is True, 'up-to-date status checks are required')
    contexts, checks = status['contexts'], status['checks']
    require(type(contexts) is list and type(checks) is list and 0 < len(checks) <= 32,
            'missing App-specific required checks')
    required = []
    for check in checks:
        require(type(check) is dict and set(check) == {'context', 'app_id'},
                'ambiguous required check mapping')
        name = check['context']
        require(type(name) is str and 0 < len(name) <= 100, 'invalid required check context')
        required.append((name, identifier(check['app_id'])))
    names = [name for name, _ in required]
    require(all(type(context) is str for context in contexts)
            and len(set(contexts)) == len(contexts) == len(names) == len(set(names))
            and set(contexts) == set(names), 'legacy or conflicting required check contexts')
    require((policy.check_name, policy.app_id) in required, 'native App check is not required')
    return tuple(sorted(required))


def required_reviews(protection):
    value = protection.get('required_pull_request_reviews')
    if value is None:
        return 0
    known = {'url', 'dismissal_restrictions', 'dismiss_stale_reviews', 'require_code_owner_reviews',
             'required_approving_review_count', 'require_last_push_approval', 'bypass_pull_request_allowances'}
    rule_fields(value, known, {'required_approving_review_count'})
    require(value.get('require_code_owner_reviews', False) is False
            and value.get('require_last_push_approval', False) is False,
            'code-owner or last-push review requirements need separate verification')
    require('dismiss_stale_reviews' not in value or type(value['dismiss_stale_reviews']) is bool,
            'invalid stale-review protection')
    bypass = value.get('bypass_pull_request_allowances')
    if bypass is not None:
        actor_restrictions(bypass, bypass=True)
    restrictions = value.get('dismissal_restrictions')
    if restrictions is not None:
        actor_restrictions(restrictions)
    count = value['required_approving_review_count']
    require(type(count) is int and 0 <= count <= 6, 'unsupported required approval count')
    return count


def successful_check(checks, name, app_id, head):
    matched = [check for check in checks if check['name'] == name and check['app']['id'] == app_id]
    require(len(matched) == 1, 'missing or duplicate App-owned check')
    check = matched[0]
    identifier(check['id'])
    require(identifier(check['app']['id']) == app_id and check['head_sha'] == head
            and check['status'] == 'completed' and check['conclusion'] == 'success',
            'required check is not successful for this commit')
    return check


def verify_current_operation(policy, evidence, coordination):
    """Require the authenticated signed operation, never a newest successful check."""
    require(
        type(evidence) is QualifiedEvidenceV2
        and type(coordination) is SignedCoordination
        and coordination.fingerprint == policy.fingerprint,
        'current readiness requires verified v2 evidence and signed coordination',
    )
    value = coordination.record
    validate_record(value, policy)
    if value['state'] in {'running', 'finalizing'}:
        operation, grant = value['operation'], value['grant']
        require(
            operation['id'] == operation['nonce'] == evidence.operation_nonce
            and grant['qualification_id'] == evidence.qualification_id
            and grant['run_id'] == evidence.run_id
            and grant['run_number'] == evidence.run_number
            and grant['native_job_id'] == evidence.native_job_id,
            'current signed operation differs from the verified execution',
        )
    else:
        require(value['state'] == 'idle', 'current operation is not ready')
        completion = value['completion']
        require(
            completion is not None
            and completion['outcome'] == 'qualified-only'
            and completion['qualification_id'] == evidence.qualification_id
            and completion['operation_id'] == evidence.operation_nonce
            and completion['main_sha'] is None,
            'idle completion is not the verified qualified-only operation',
        )


def _check_fields(value):
    return (
        value['id'],
        value['app']['id'],
        value['name'],
        value['head_sha'],
        value['external_id'],
        value['details_url'],
        value['status'],
        value['conclusion'],
        value['completed_at'],
    )


def _exact_check(api, records, expected, check_id):
    observed = select_check(
        records, expected, check_id, status='completed', conclusion='success'
    )
    value = api.get('check-runs/' + str(check_id))
    validate_check(value, expected, check_id, status='completed', conclusion='success')
    require(
        _check_fields(observed) == _check_fields(value),
        'admitted check changed between reads',
    )
    return value


def current_checks(api, policy, evidence, required):
    signed = evidence.signed_merge
    checks = get_all(
        api,
        'commits/' + signed.commit + '/check-runs',
        key='check_runs',
        check_filter='all',
    )
    native = _exact_check(
        api,
        checks,
        CheckIdentity(
            policy.repository,
            policy.app_id,
            signed.commit,
            policy.check_name,
            evidence.qualification_id,
            evidence.run_id,
        ),
        evidence.check_run_id,
    )
    identifiers = tuple(
        native['id']
        if (name, app) == (policy.check_name, policy.app_id)
        else successful_check(checks, name, app, signed.commit)['id']
        for name, app in required
    )
    head_checks = get_all(
        api,
        'commits/' + signed.head + '/check-runs',
        key='check_runs',
        check_filter='all',
    )
    pointer = _exact_check(
        api,
        head_checks,
        CheckIdentity(
            policy.repository,
            policy.app_id,
            signed.head,
            f'Native KWin / activation #{evidence.producer_attestation.pr_number}',
            evidence.qualification_id,
            evidence.run_id,
        ),
        evidence.activation_check_id,
    )
    return identifiers, pointer['id'], (_check_fields(native), _check_fields(pointer))


def current_reviews(api, policy, evidence, author_id, count):
    records = get_all(api, f'pulls/{evidence.producer_attestation.pr_number}/reviews')
    return evaluate_reviews(api, policy, records, head=evidence.signed_merge.head,
                            author_id=author_id, required=count)


def verify_current_policy(api, policy, evidence, *, coordination):
    """Observe all supported current gates; the caller must not treat this as a lock."""
    try:
        verify_current_operation(policy, evidence, coordination)
        association = verify_current_association(api, policy, evidence)
        require(
            api.get('rules/branches/main') == [],
            'effective rulesets need separate verification',
        )
        protection = api.get('branches/main/protection')
        required = protection_requirements(protection, policy)
        count = required_reviews(protection)
        signatures = api.get('branches/main/protection/required_signatures')
        enabled_rule(signatures)
        if 'required_signatures' in protection:
            require(
                protection['required_signatures']['enabled'] is signatures['enabled'],
                'signed-commit protection changed',
            )
        pr = api.get(f'pulls/{association.pr_number}')
        require(
            pr['head']['sha'] == association.head
            and pr['base']['sha'] == association.base,
            'PR changed while reading policy',
        )
        author_id = identifier(pr['user']['id'])
        observed_checks = current_checks(api, policy, evidence, required)
        check_ids, pointer_id, _ = observed_checks
        approvals = current_reviews(api, policy, evidence, author_id, count)
        if signatures['enabled']:
            commits = get_all(api, f'pulls/{association.pr_number}/commits')
            commit_ids = [digest(commit['sha']) for commit in commits]
            require(
                len(commits) == identifier(pr['commits'])
                and commits
                and commit_ids[-1] == association.head
                and len(set(commit_ids)) == len(commits),
                'PR commit inventory is incomplete or changed',
            )
            require(
                all(
                    commit['commit']['verification']['verified'] is True
                    and commit['commit']['verification']['reason'] == 'valid'
                    for commit in commits
                ),
                'PR commits do not satisfy signature protection',
            )
        require(
            current_checks(api, policy, evidence, required) == observed_checks,
            'required checks or activation changed',
        )
        require(
            current_reviews(api, policy, evidence, author_id, count) == approvals,
            'review state changed during verification',
        )
        require(
            canonical_json(api.get('branches/main/protection'))
            == canonical_json(protection)
            and canonical_json(api.get('branches/main/protection/required_signatures'))
            == canonical_json(signatures)
            and api.get('rules/branches/main') == [],
            'protection changed during verification',
        )
        require(
            verify_current_association(api, policy, evidence) == association,
            'current association changed',
        )
        verify_run(
            api.get(f'actions/runs/{evidence.run_id}'),
            policy,
            {'run_id': evidence.run_id, 'run_number': evidence.run_number},
        )
        return ReadinessSnapshot(
            evidence.qualification_id, check_ids, pointer_id, approvals
        )
    except QualificationError:
        raise
    except (ValueError, KeyError, TypeError, IndexError, AttributeError):
        raise QualificationError(
            'invalid or unavailable current merge policy'
        ) from None
