# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bind authenticated API observations; never interpret PR artifacts as authority.

The caller must fetch these observations from GitHub using trusted code. These
pure checks do not authenticate caller-supplied JSON or publish a native result.
Refs and the candidate must be fetched again before reporting; a successful bind
is only a point-in-time observation, not a merge authorization.
"""
import re
from dataclasses import dataclass


class CandidateError(ValueError):
    """The observation is incomplete, stale, or outside the admitted PR lane."""


def require(condition, message):
    if not condition:
        raise CandidateError(message)


def positive_id(value):
    require(type(value) is int and value > 0, 'invalid numeric identity')
    return value


def sha(value):
    require(type(value) is str and re.fullmatch(r'[0-9a-f]{40}', value)
            and value != '0' * 40, 'invalid Git object identity')
    return value


@dataclass(frozen=True)
class Policy:
    """Trusted configuration, never loaded from the candidate checkout.

    The label is a request signal only; it cannot authorize a revision. A caller
    must separately verify immutable admission before any expensive execution.
    """

    repository_id: int
    repository: str
    workflow_id: int
    workflow_path: str
    base_branch: str = 'main'
    approval_label: str = 'native-approved'


@dataclass(frozen=True)
class Candidate:
    repository_id: int
    head_repository_id: int
    pr_number: int
    run_id: int
    run_attempt: int
    base: str
    head: str
    merge: str
    tree: str


def ref_sha(observation, expected_ref):
    require(observation['ref'] == expected_ref, 'unexpected Git ref')
    require(observation['object']['type'] == 'commit', 'ref must identify a commit')
    return sha(observation['object']['sha'])


def repository_matches(repository, policy):
    return (positive_id(repository['id']) == policy.repository_id
            and repository['full_name'] == policy.repository)


def bind_candidate(policy, *, run_id, run_attempt, run, pr, base_ref, head_ref,
                   merge_ref, merge_commit):
    """Observe one opted-in, open PR with an exact current two-parent merge.

    API reference: https://docs.github.com/en/rest/actions/workflow-runs
    Git refs/parents: https://docs.github.com/en/rest/git
    The intake conclusion, display name and any artifacts are deliberately unused.
    An intake failure can notify the trusted worker but cannot qualify native CI.
    """
    try:
        positive_id(policy.repository_id)
        positive_id(policy.workflow_id)
        require(positive_id(run['id']) == positive_id(run_id), 'wrong intake run')
        require(positive_id(run['run_attempt']) == positive_id(run_attempt),
                'stale intake attempt')
        require(repository_matches(run['repository'], policy), 'wrong run repository')
        require(positive_id(run['workflow_id']) == policy.workflow_id,
                'wrong intake workflow')
        require(run['path'] == policy.workflow_path, 'wrong intake workflow path')
        require(run['event'] == 'pull_request' and run['status'] == 'completed',
                'intake must be a completed pull_request run')

        number = positive_id(pr['number'])
        associations = run['pull_requests']
        require(type(associations) is list and len(associations) == 1,
                'ambiguous or missing PR association')
        require(positive_id(associations[0]['number']) == number, 'wrong PR association')
        require(pr['state'] == 'open' and pr['merged'] is False and pr['draft'] is False,
                'PR must be open, unmerged and ready for review')
        require(pr['mergeable'] is True, 'mergeability is not established')
        labels = pr['labels']
        require(type(labels) is list and any(label['name'] == policy.approval_label
                                           for label in labels), 'native validation was not requested')
        require(repository_matches(pr['base']['repo'], policy), 'wrong base repository')
        require(pr['base']['ref'] == policy.base_branch, 'wrong base branch')
        base = ref_sha(base_ref, 'refs/heads/' + policy.base_branch)
        require(sha(pr['base']['sha']) == base, 'PR base observation is stale')

        head_repository_id = positive_id(pr['head']['repo']['id'])
        require(positive_id(run['head_repository']['id']) == head_repository_id,
                'wrong head repository')
        branch = pr['head']['ref']
        require(type(branch) is str and 0 < len(branch) <= 255
                and not any(ord(character) < 32 or ord(character) == 127
                            for character in branch), 'invalid head branch')
        require(run['head_branch'] == branch, 'wrong intake head branch')
        head = ref_sha(head_ref, 'refs/heads/' + branch)
        require(sha(pr['head']['sha']) == head == sha(run['head_sha']), 'stale PR head')

        merge = ref_sha(merge_ref, f'refs/pull/{number}/merge')
        require(sha(pr['merge_commit_sha']) == merge == sha(merge_commit['sha']),
                'stale candidate merge')
        parents = merge_commit['parents']
        require(type(parents) is list and len(parents) == 2, 'expected two merge parents')
        require([sha(parent['sha']) for parent in parents] == [base, head],
                'candidate parents do not match live base and head')
        require(len({base, head, merge}) == 3, 'candidate is not a distinct merge commit')
        tree = sha(merge_commit['tree']['sha'])
        return Candidate(policy.repository_id, head_repository_id, number, run_id,
                         run_attempt, base, head, merge, tree)
    except (KeyError, TypeError, IndexError) as error:
        raise CandidateError('incomplete or malformed GitHub observation') from error
