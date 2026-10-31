# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Disposable LOCAL state/CAS probe, not a production authority implementation.

Repositories and unsigned fixture commits are created internally. No remote URL,
real signing key, GitHub credential or external repository can be supplied. The
evidence booleans below are simulations, not proof of real runtime quiescence.
"""
import copy
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ci.native.coordination import validate_record
from ci.native.merge_update import MergeCommit
from ci.native.qualification import (
    canonical_json,
    digest,
    require,
)


class Outcome(Enum):
    APPLIED = 'applied'
    REJECTED = 'rejected'
    UNKNOWN = 'unknown'


@dataclass(frozen=True)
class Result:
    outcome: Outcome
    sha: str


@dataclass(frozen=True)
class ProbeEvidence:
    """Only a test double. Production evidence acquisition is not implemented."""
    grant_authenticated: bool = False
    worker_authenticated: bool = False
    no_dispatch: bool = False
    writers_quiescent: bool = False
    runtimes_quiescent: bool = False
    dispatches_quiescent: bool = False
    update_status: str = 'not-started'
    update_acknowledged: bool = False
    definitively_not_applied: bool = False
    main_sha: str | None = None


def proposed(current, state, **changes):
    value = copy.deepcopy(current)
    value.update(state=state, generation=current['generation'] + 1, **changes)
    return value


def quiescent(evidence):
    return (evidence.writers_quiescent is True and evidence.runtimes_quiescent is True
            and evidence.dispatches_quiescent is True)


def validate_transition(before, after, policy, evidence):
    validate_record(before, policy)
    validate_record(after, policy)
    require(type(evidence) is ProbeEvidence, 'not local probe evidence')
    require(after['generation'] == before['generation'] + 1, 'generation must advance exactly once')
    for key in ['version', 'repository_id', 'target_ref', 'epoch', 'cutover_digest']:
        require(before[key] == after[key], 'coordination identity changed')
    transition = before['state'], after['state']
    allowed = {('idle', 'reserved'), ('reserved', 'dispatching'), ('reserved', 'idle'),
               ('dispatching', 'granted'), ('granted', 'running'), ('granted', 'idle'),
               ('running', 'finalizing'), ('running', 'idle'), ('finalizing', 'idle')}
    require(transition in allowed, 'forbidden coordination transition')
    if before['state'] == 'idle':
        if before['completion']:
            require(after['operation']['id'] != before['completion']['operation_id'], 'operation reused')
        return
    if after['state'] != 'idle':
        require(canonical_json(before['operation']) == canonical_json(after['operation']),
                'operation changed during ownership')
        if before['grant'] is not None:
            changed = 'native_job_id', 'worker_nonce', 'finalizer_nonce'
            for key in set(before['grant']) - set(changed):
                require(before['grant'][key] == after['grant'][key], 'grant binding changed')
        if transition == ('reserved', 'dispatching'):
            return
        if transition == ('dispatching', 'granted'):
            require(evidence.grant_authenticated is True, 'grant request not authenticated')
        elif transition == ('granted', 'running'):
            require(evidence.worker_authenticated is True, 'worker request not authenticated')
        else:
            require(quiescent(evidence) and evidence.update_status == 'not-started', 'work is not quiescent')
            require(after['grant']['native_job_id'] == before['grant']['native_job_id']
                    and after['grant']['worker_nonce'] == before['grant']['worker_nonce'], 'worker claim changed')
        return
    completion = after['completion']
    require(completion['operation_id'] == before['operation']['id'], 'completion belongs to another operation')
    expected_qid = before['grant']['qualification_id'] if before['grant'] else None
    require(completion['qualification_id'] == expected_qid, 'completion qualification differs')
    outcome = completion['outcome']
    if before['state'] == 'reserved':
        require(evidence.no_dispatch is True and outcome == 'aborted-before-dispatch'
                and completion['main_sha'] is None and evidence.update_status == 'not-started',
                'unsafe pre-dispatch abort')
        return
    require(quiescent(evidence), 'cannot release live or unknown workloads')
    if outcome in {'failed', 'cancelled', 'qualified-only'}:
        require(outcome != 'qualified-only' or before['state'] == 'running', 'qualification never ran')
        require(evidence.update_status == 'not-started' and completion['main_sha'] is None,
                'update outcome is not safely known')
    elif outcome == 'finalized':
        target = before['operation']['admission']['signed_merge']
        require(before['state'] == 'finalizing' and evidence.update_status == 'applied'
                and evidence.update_acknowledged is True
                and completion['main_sha'] == evidence.main_sha == target, 'main was not confirmed at target')
    elif outcome == 'rejected':
        target = before['operation']['admission']['signed_merge']
        digest(evidence.main_sha)
        require(before['state'] == 'finalizing' and evidence.update_status == 'rejected'
                and evidence.update_acknowledged is True and evidence.definitively_not_applied is True
                and completion['main_sha'] == evidence.main_sha != target, 'rejection is not verified')
    else:
        raise ValueError('invalid terminal outcome')


class LocalCoordinationProbe:
    """Owns both bare repositories; there is intentionally no remote-path argument."""
    ref = 'refs/heads/coordination-probe'

    def __init__(self, policy):
        self.policy = policy
        self._temporary = tempfile.TemporaryDirectory(prefix='coordination-probe-',
            dir='/tmp/opencode' if Path('/tmp/opencode').is_dir() else None)
        self._root = Path(self._temporary.name)
        self._source, self._remote = self._root / 'client.git', self._root / 'remote.git'
        self._env = {'PATH': os.defpath, 'HOME': str(self._root), 'LANG': 'C.UTF-8',
                     'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
                     'GIT_AUTHOR_NAME': 'Fixture', 'GIT_AUTHOR_EMAIL': 'fixture@example.invalid',
                     'GIT_COMMITTER_NAME': 'Fixture', 'GIT_COMMITTER_EMAIL': 'fixture@example.invalid'}
        for path in [self._source, self._remote]:
            subprocess.run(['git', 'init', '--bare', '-q', str(path)], env=self._env, check=True, timeout=10)
        initial = {'version': 1, 'repository_id': policy.repository_id, 'target_ref': 'refs/heads/main',
                   'epoch': 'e' * 32, 'cutover_digest': 'f' * 64, 'generation': 0, 'state': 'idle',
                   'operation': None, 'grant': None, 'completion': None}
        validate_record(initial, policy)
        first = self._record_commit(initial)
        require(self._push(self.ref, first, '').outcome is Outcome.APPLIED, 'local initialization failed')
        self._tree = self._git('mktree', data=b'').stdout.decode().strip()
        base = self._commit(self._tree, 'Fixture base')
        head = self._commit(self._tree, 'Fixture head', base)
        merge = self._commit(self._tree, 'Fixture merge', base, head)
        self.candidate = MergeCommit(base, head, merge, self._tree)
        require(self._push('refs/heads/main', base, '').outcome is Outcome.APPLIED, 'local main init failed')

    def close(self):
        self._temporary.cleanup()

    def _git(self, *args, data=None, checked=True):
        require(self._source.parent == self._root and self._remote.parent == self._root,
                'probe paths left their owned root')
        result = subprocess.run(['git', '-c', 'protocol.allow=never', '-c', 'protocol.file.allow=always',
                                 '-c', 'commit.gpgsign=false', '--no-replace-objects',
                                 '--git-dir=' + str(self._source), *args],
                                env=self._env, input=data, capture_output=True, timeout=15, check=False)
        require(not checked or result.returncode == 0, 'local probe Git command failed')
        return result

    def _commit(self, tree, message, *parents):
        args = ['commit-tree', tree, '-m', message]
        for parent in parents:
            args.extend(['-p', parent])
        return self._git(*args).stdout.decode().strip()

    def _record_commit(self, record, parent=None):
        raw = canonical_json(record)
        require(len(raw) <= 64 * 1024, 'coordination record too large')
        blob = self._git('hash-object', '-w', '--stdin', data=raw).stdout.decode().strip()
        tree = self._git('mktree', data=f'100644 blob {blob}\tcoordination.json\n'.encode()).stdout.decode().strip()
        return self._commit(tree, 'Fixture coordination state', *([parent] if parent else []))

    def _push(self, ref, sha, expected, before_push=None):
        if before_push:
            before_push()
        result = self._git('push', '--porcelain', '--no-follow-tags', '--recurse-submodules=no',
                           f'--force-with-lease={ref}:{expected}', self._remote.as_uri(), f'{sha}:{ref}',
                           checked=False)
        records = [line.split('\t') for line in result.stdout.decode().splitlines() if '\t' in line]
        records = [item for item in records if len(item) == 3 and item[1] == f'{sha}:{ref}']
        outcome = Outcome.UNKNOWN
        if len(records) == 1:
            flag, _, reason = records[0]
            if result.returncode == 0 and flag in {'*', ' '}:
                outcome = Outcome.APPLIED
            elif flag == '=' or (flag == '!' and reason == '[rejected] (stale info)'):
                outcome = Outcome.REJECTED
        return Result(outcome, sha)

    def tip(self, ref=None):
        text = self._git('ls-remote', '--refs', self._remote.as_uri(), ref or self.ref).stdout.decode().strip()
        require(len(text.splitlines()) == 1, 'missing or ambiguous local ref')
        return digest(text.split('\t')[0])

    def record(self, sha=None):
        raw = self._git('cat-file', 'blob', (sha or self.tip()) + ':coordination.json').stdout
        value = json.loads(raw)
        validate_record(value, self.policy)
        return value

    def cas(self, expected, value, evidence, *, before_push=None):
        digest(expected)
        validate_transition(self.record(expected), value, self.policy, evidence)
        commit = self._record_commit(value, expected)
        return self._push(self.ref, commit, expected, before_push)

    def main_tip(self):
        return self.tip('refs/heads/main')

    def push_main(self, coordination_sha, *, before_push=None):
        require(self.tip() == coordination_sha, 'coordination ownership changed')
        record = self.record(coordination_sha)
        require(record['state'] == 'finalizing', 'main update requires finalizing ownership')
        admission = record['operation']['admission']
        raw = self._git('cat-file', 'commit', admission['signed_merge']).stdout
        headers = raw.split(b'\n\n', 1)[0].decode().splitlines()
        expected = [f"tree {admission['tree']}", f"parent {admission['base']}", f"parent {admission['head']}"]
        require(headers[:3] == expected
                and [line for line in headers if line.startswith(('tree ', 'parent '))] == expected,
                'wrong local merge identity')
        return self._push('refs/heads/main', admission['signed_merge'], admission['base'], before_push)

    def advance_main(self):
        before = self.main_tip()
        after = self._commit(self._tree, 'Fixture concurrent main advance', before)
        require(self._push('refs/heads/main', after, before).outcome is Outcome.APPLIED, 'advance failed')
        return after
