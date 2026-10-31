# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Conservative review reduction for a read-only merge-readiness observation."""
import re
from dataclasses import dataclass
from datetime import datetime

from ci.native.qualification import (
    QualificationError,
    canonical_json,
    digest,
    identifier,
    require,
    timestamp,
)


@dataclass(frozen=True)
class Review:
    id: int
    user_id: int
    login: str
    state: str
    commit: str
    submitted: datetime


def latest_effective_reviews(records):
    """Remove dismissed reviews individually, then select each user's latest decision.

    A dismissed approval can expose an earlier unresolved changes request.
    Comments and drafts are not decisions and cannot erase earlier decisions.
    """
    require(type(records) is list and len(records) <= 500, 'invalid or oversized review inventory')
    seen, latest = {}, {}
    for record in records:
        review_id = identifier(record['id'])
        if review_id in seen:
            require(canonical_json(record) == seen[review_id], 'review changed across pages')
            continue
        seen[review_id] = canonical_json(record)
        state = record['state']
        require(state in {'APPROVED', 'CHANGES_REQUESTED', 'COMMENTED', 'DISMISSED', 'PENDING'},
                'unknown review state')
        if state in {'COMMENTED', 'DISMISSED', 'PENDING'}:
            continue
        user = record['user']
        user_id = identifier(user['id'])
        login = user['login']
        require(type(login) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}', login),
                'unsupported review principal')
        review = Review(review_id, user_id, login, state, digest(record['commit_id']),
                        timestamp(record['submitted_at']))
        previous = latest.get(user_id)
        if previous is None or (review.submitted, review.id) > (previous.submitted, previous.id):
            latest[user_id] = review
    return tuple(latest.values())


def evaluate_reviews(api, policy, records, *, head, author_id, required):
    """Count current authorized approvals; permission loss never clears a negative review."""
    try:
        require(api.repository == policy.repository, 'wrong review API repository')
        digest(head)
        identifier(author_id)
        require(type(required) is int and 0 <= required <= 6, 'unsupported approval count')
        effective = latest_effective_reviews(records)
        require(not any(review.state == 'CHANGES_REQUESTED' for review in effective),
                'outstanding changes requested')
        approved = []
        for review in effective:
            if review.user_id == author_id or review.commit != head:
                continue
            permission = api.get('collaborators/' + review.login + '/permission')
            require(identifier(permission['user']['id']) == review.user_id,
                    'reviewer permission identity differs')
            role = permission['permission']
            require(role in {'admin', 'maintain', 'write', 'triage', 'read', 'none'},
                    'unknown reviewer permission')
            if role in {'admin', 'maintain', 'write'}:
                approved.append(review.id)
        require(len(approved) >= required, 'current authorized approvals are missing')
        return tuple(sorted(approved))
    except QualificationError:
        raise
    except (ValueError, KeyError, TypeError, IndexError, AttributeError):
        raise QualificationError('invalid or unavailable review evidence') from None
