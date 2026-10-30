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
    identifier,
    require,
)


@dataclass(frozen=True)
class CurrentAssociation:
    qualification_id: str
    repository_id: int
    pr_number: int
    head_repository_id: int
    base: str
    head: str
    synthetic_merge: str
    signed_merge: str


def verify_current_association(api, policy, evidence):
    """Refuse if issuer-recorded PR/fork/merge metadata is not currently authentic."""
    try:
        require(type(evidence) is QualifiedEvidence, 'expected verified historical evidence')
        require(api.repository == policy.repository, 'wrong association API repository')
        attestation, signed = evidence.producer_attestation, evidence.signed_merge
        number = identifier(attestation.pr_number)

        def verify_pr(pr):
            require(identifier(pr['number']) == number
                    and identifier(pr['base']['repo']['id']) == policy.repository_id
                    and pr['base']['repo']['full_name'] == policy.repository
                    and pr['base']['ref'] == 'main' and pr['base']['sha'] == signed.base
                    and identifier(pr['head']['repo']['id']) == attestation.head_repository_id
                    and pr['head']['sha'] == signed.head
                    and pr['merge_commit_sha'] == attestation.synthetic_merge,
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
                (f'git/ref/pull/{number}/merge', f'refs/pull/{number}/merge', attestation.synthetic_merge)]:
            value = api.get(resource)
            require(value['ref'] == ref and value['object']['type'] == 'commit'
                    and value['object']['sha'] == sha, 'current Git ref differs from qualification')
        verify_pr(api.get(f'pulls/{number}'))
        return CurrentAssociation(evidence.qualification_id, policy.repository_id, number,
                                  attestation.head_repository_id, signed.base, signed.head,
                                  attestation.synthetic_merge, signed.commit)
    except QualificationError:
        raise
    except (ValueError, KeyError, TypeError, IndexError, AttributeError):
        raise QualificationError('invalid or unavailable current PR association') from None
