# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Authenticate the current PR/fork/ref association, not its historical state.

Historical issuer metadata is not proof that a SHA belonged to a PR or fork.
This additional read-only gate checks that association now. Reviews, branch
protection, attempt authority and a fresh pre-push recheck remain separate.
"""
from dataclasses import dataclass

from ci.native.qualification import (
    QualificationError,
    QualifiedEvidence,
    QualifiedEvidenceV2,
    identifier,
    require,
    validate_admission,
)


@dataclass(frozen=True)
class CurrentAssociation:
    qualification_id: str | None
    repository_id: int
    pr_number: int
    head_repository_id: int
    base: str
    head: str
    synthetic_merge: str
    signed_merge: str


def verify_admission_association(api, policy, admission, signed, qualification_id=None):
    """Authenticate the same PR/ref binding before production or after qualification."""
    try:
        require(api.repository == policy.repository, 'wrong association API repository')
        validate_admission(admission)
        number = identifier(admission['pr_number'])

        def verify_pr(pr):
            require(identifier(pr['number']) == number
                    and identifier(pr['base']['repo']['id']) == policy.repository_id
                    and pr['base']['repo']['full_name'] == policy.repository
                    and pr['base']['ref'] == 'main' and pr['base']['sha'] == signed.base
                    and identifier(pr['head']['repo']['id']) == admission['head_repository_id']
                    and pr['head']['sha'] == signed.head
                    and pr['merge_commit_sha'] == admission['merge'],
                    'producer PR or fork association is not current')
            require(pr['state'] == 'open' and pr['merged'] is False and pr['draft'] is False
                    and pr['mergeable'] is True, 'PR is not open and mergeable')
            require(type(pr['labels']) is list
                    and any(label['name'] == 'native-approved' for label in pr['labels']),
                    'native validation request was withdrawn')

        verify_pr(api.get(f'pulls/{number}'))
        for resource, ref, sha in [
                ('git/ref/heads/main', 'refs/heads/main', signed.base),
                (f'git/ref/pull/{number}/head', f'refs/pull/{number}/head', signed.head),
                (f'git/ref/pull/{number}/merge', f'refs/pull/{number}/merge', admission['merge'])]:
            value = api.get(resource)
            require(value['ref'] == ref and value['object']['type'] == 'commit'
                    and value['object']['sha'] == sha, 'current Git ref differs from qualification')
        verify_pr(api.get(f'pulls/{number}'))
        return CurrentAssociation(qualification_id, policy.repository_id, number,
                                  admission['head_repository_id'], signed.base, signed.head,
                                  admission['merge'], signed.commit)
    except QualificationError:
        raise
    except (ValueError, KeyError, TypeError, IndexError, AttributeError):
        raise QualificationError('invalid or unavailable current PR association') from None


def verify_current_association(api, policy, evidence):
    """Preserve the historical-evidence type boundary for finalizer callers."""
    require(
        type(evidence) in {QualifiedEvidence, QualifiedEvidenceV2},
        'expected verified historical evidence',
    )
    attestation, signed = evidence.producer_attestation, evidence.signed_merge
    admission = {
        'repository_id': policy.repository_id,
        'pr_number': attestation.pr_number,
        'head_repository_id': attestation.head_repository_id,
        'base': signed.base,
        'head': signed.head,
        'merge': attestation.synthetic_merge,
        'signed_merge': signed.commit,
        'tree': signed.tree,
        'payload_sha256': attestation.payload_sha256,
        'source': {
            'tree': attestation.source_tree,
            'sha256': attestation.source_sha256,
            'bytes': attestation.source_bytes,
        },
        'approver_id': attestation.approver_id,
        'upstream_commit': attestation.upstream_commit,
    }
    return verify_admission_association(
        api, policy, admission, signed, evidence.qualification_id
    )
