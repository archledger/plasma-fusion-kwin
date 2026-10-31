# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Validate exact check identities and conflicts in a supplied visible inventory.

These pure functions do not read GitHub, prove inventory completeness, authorize
an operation or authenticate the provenance of historical records.
"""

import re
from dataclasses import dataclass

from ci.native.qualification import (
    QualificationError,
    digest,
    identifier,
    require,
    timestamp,
)

MAX_RECORDS = 500
CONCLUSIONS = frozenset(
    {
        'action_required',
        'cancelled',
        'failure',
        'neutral',
        'success',
        'skipped',
        'stale',
        'timed_out',
    }
)
STATUSES = frozenset(
    {'queued', 'in_progress', 'completed', 'waiting', 'requested', 'pending'}
)


@dataclass(frozen=True)
class CheckIdentity:
    repository: str
    app_id: int
    head: str
    name: str
    qualification_id: str
    run_id: int

    def __post_init__(self):
        require(
            type(self.repository) is str
            and re.fullmatch(
                r'[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}', self.repository
            )
            and self.repository.split('/')[1] not in {'.', '..'},
            'invalid check repository',
        )
        identifier(self.app_id)
        identifier(self.run_id)
        digest(self.head)
        digest(self.qualification_id, 64)
        require(
            type(self.name) is str
            and 0 < len(self.name) <= 100
            and all(32 <= ord(c) < 127 for c in self.name),
            'invalid check name',
        )

    @property
    def details_url(self):
        return f'https://github.com/{self.repository}/actions/runs/{self.run_id}'


def _record(value, expected):
    """Validate a matching record; the URL is an identifier, not provenance."""
    try:
        require(
            type(value) is dict and type(value['app']) is dict, 'invalid check record'
        )
        check_id = identifier(value['id'])
        require(
            identifier(value['app']['id']) == expected.app_id
            and value['name'] == expected.name
            and value['head_sha'] == expected.head,
            'check context differs',
        )
        qid = digest(value['external_id'], 64)
        url = value['details_url']
        prefix = f'https://github.com/{expected.repository}/actions/runs/'
        require(type(url) is str and url.startswith(prefix), 'invalid check run URL')
        suffix = url[len(prefix) :]
        require(re.fullmatch(r'[1-9][0-9]{0,18}', suffix), 'invalid check run URL')
        run_id = identifier(int(suffix))
        status, conclusion = value['status'], value['conclusion']
        require(type(status) is str and status in STATUSES, 'invalid check status')
        if status == 'completed':
            require(
                type(conclusion) is str and conclusion in CONCLUSIONS,
                'invalid check conclusion',
            )
            completed = value['completed_at']
            require(
                type(completed) is str
                and re.fullmatch(
                    r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z',
                    completed,
                ),
                'invalid completion time',
            )
            timestamp(completed)
        else:
            require(
                conclusion is None and value['completed_at'] is None,
                'pending check retains completion fields',
            )
        return check_id, qid, run_id
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise QualificationError('invalid or mismatched check record') from error


def validate_check(value, expected, check_id, *, status, conclusion):
    """Require one exact pending or explicitly completed operation record."""
    identifier(check_id)
    require(type(expected) is CheckIdentity, 'missing expected check identity')
    require(
        (status == 'in_progress' and conclusion is None)
        or (
            status == 'completed'
            and type(conclusion) is str
            and conclusion in {'success', 'failure'}
        ),
        'invalid expected check state',
    )
    actual_id, qid, run_id = _record(value, expected)
    require(
        (actual_id, qid, run_id)
        == (check_id, expected.qualification_id, expected.run_id)
        and value['status'] == status
        and value['conclusion'] == conclusion,
        'check does not match expected operation',
    )
    return actual_id


def _inventory(records, expected, selected_id=None):
    require(type(expected) is CheckIdentity, 'missing expected check identity')
    require(
        type(records) is list and len(records) <= MAX_RECORDS, 'invalid check inventory'
    )
    ids, qids, runs = set(), set(), set()
    selected = None
    for record in records:
        require(
            type(record) is dict and type(record.get('app')) is dict,
            'invalid check inventory record',
        )
        check_id = identifier(record.get('id'))
        require(check_id not in ids, 'duplicate visible check ID')
        ids.add(check_id)
        app_id = identifier(record['app'].get('id'))
        name = record.get('name')
        require(type(name) is str, 'invalid visible check name')
        if (app_id, name) != (expected.app_id, expected.name):
            continue
        _, qid, run_id = _record(record, expected)
        require(
            qid not in qids and run_id not in runs,
            'conflicting visible operation records',
        )
        qids.add(qid)
        runs.add(run_id)
        if check_id == selected_id:
            selected = record
        else:
            require(
                qid != expected.qualification_id and run_id != expected.run_id,
                'operation already has a different check',
            )
            require(record['status'] == 'completed', 'another check is nonterminal')
    return selected


def validate_creation(records, expected):
    """Reject visible conflicts before creation; this is not a uniqueness lock."""
    _inventory(records, expected)


def select_check(records, expected, check_id, *, status, conclusion):
    """Select only the supplied admitted ID, never rank historical outcomes."""
    identifier(check_id)
    value = _inventory(records, expected, check_id)
    require(value is not None, 'expected check is absent from visible inventory')
    validate_check(value, expected, check_id, status=status, conclusion=conclusion)
    return value
