# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Authenticate one local coordination record against explicit trusted anchors.

This is not a transition authorizer or a latest-ref check. It never fetches,
pushes, launches workers or proves runtime quiescence. Policy, expected identity
and public keyring must come from the caller, not the record being checked.
"""
import hashlib
import json
import re
from dataclasses import dataclass

from ci.native.merge_update import read_raw_object
from ci.native.proof_api import (
    bounded_integer,
    finite_float,
    object_pairs,
    reject_constant,
)
from ci.native.qualification import (
    QualificationPolicy,
    canonical_json,
    digest,
    identifier,
    keys,
    require,
    validate_admission,
)
from ci.native.signed_merge import verify_signed_commit

MAX_RECORD_BYTES = 64 * 1024


class CoordinationError(ValueError):
    """The record could not be authenticated against the supplied identity."""


@dataclass(frozen=True)
class SignedCoordination:
    commit: str
    parent: str | None
    tree: str
    fingerprint: str
    record: dict


def validate_record(value, policy):
    """Shared record schema only; no signature, history or authority is inferred."""
    keys(value, ['version', 'repository_id', 'target_ref', 'epoch', 'cutover_digest',
                 'generation', 'state', 'operation', 'grant', 'completion'])
    require(type(value['version']) is int and value['version'] == 1, 'wrong coordination version')
    require(identifier(value['repository_id']) == policy.repository_id
            and value['target_ref'] == 'refs/heads/main', 'wrong coordination target')
    digest(value['epoch'], 32)
    digest(value['cutover_digest'], 64)
    require(type(value['generation']) is int and 0 <= value['generation'] < 2**63,
            'invalid coordination generation')
    phase = value['state']
    require(type(phase) is str and phase in {'idle', 'reserved', 'dispatching', 'granted', 'running', 'finalizing'},
            'unknown coordination state')
    if phase == 'idle':
        require(value['operation'] is None and value['grant'] is None, 'idle record has live authority')
        completion = value['completion']
        if value['generation'] == 0:
            require(completion is None, 'initial record has a completion')
        else:
            keys(completion, ['operation_id', 'qualification_id', 'outcome', 'main_sha'])
            digest(completion['operation_id'], 32)
            if completion['qualification_id'] is not None:
                digest(completion['qualification_id'], 64)
            if completion['main_sha'] is not None:
                digest(completion['main_sha'])
            outcome = completion['outcome']
            require(type(outcome) is str and outcome in {'aborted-before-dispatch', 'failed', 'cancelled',
                                                        'qualified-only', 'finalized', 'rejected'},
                    'invalid completion')
            require((completion['qualification_id'] is None) == (outcome == 'aborted-before-dispatch'),
                    'completion qualification is inconsistent')
            require((completion['main_sha'] is not None) == (outcome in {'finalized', 'rejected'}),
                    'completion main result is inconsistent')
        return
    require(value['generation'] > 0 and value['completion'] is None, 'live record has a completion')
    operation = value['operation']
    keys(operation, ['id', 'holder_fingerprint', 'admission', 'workflow_id', 'launcher', 'nonce'])
    digest(operation['id'], 32)
    require(operation['id'] == operation['nonce'] and operation['holder_fingerprint'] == policy.fingerprint,
            'wrong operation owner or nonce')
    require(identifier(operation['workflow_id']) == policy.workflow_id
            and digest(operation['launcher']) == policy.launcher, 'wrong operation launcher')
    validate_admission(operation['admission'])
    require(operation['admission']['repository_id'] == policy.repository_id
            and operation['admission']['upstream_commit'] == policy.upstream_commit, 'wrong operation source')
    grant = value['grant']
    if phase in {'reserved', 'dispatching'}:
        require(grant is None, 'ungranted state has a grant')
        return
    keys(grant, ['run_id', 'run_number', 'run_attempt', 'qualification_id',
                 'native_job_id', 'worker_nonce', 'finalizer_nonce'])
    identifier(grant['run_id'])
    identifier(grant['run_number'])
    require(type(grant['run_attempt']) is int and grant['run_attempt'] == 1, 'rerun grant is forbidden')
    identity = {'schema_version': 1, 'admission': operation['admission'], 'execution': {
        'workflow_id': operation['workflow_id'], 'run_id': grant['run_id'],
        'run_number': grant['run_number'], 'run_attempt': 1,
        'launcher': operation['launcher'], 'nonce': operation['nonce']}}
    require(grant['qualification_id'] == hashlib.sha256(canonical_json(identity)).hexdigest(),
            'grant does not bind the operation')
    if phase == 'granted':
        require(grant['native_job_id'] is None and grant['worker_nonce'] is None
                and grant['finalizer_nonce'] is None, 'unconsumed grant has a consumer')
    else:
        identifier(grant['native_job_id'])
        digest(grant['worker_nonce'], 32)
        if phase == 'running':
            require(grant['finalizer_nonce'] is None, 'running grant has a finalizer')
        else:
            digest(grant['finalizer_nonce'], 32)


def read_signed_coordination(source, commit, *, policy, keyring, signoff, expected_parent,
                             expected_generation, expected_epoch, expected_cutover_digest):
    """Verify one snapshot, including its raw parent; never claim it is the latest.

    None is allowed for expected_parent only at generation zero. A pinned parent
    identifies the predecessor but does not authenticate its history or authorize
    the transition. The returned record is data, not a write/worker capability.
    """
    try:
        require(type(policy) is QualificationPolicy, 'expected trusted qualification policy')
        require(type(expected_generation) is int and 0 <= expected_generation < 2**63,
                'invalid expected generation')
        require((expected_parent is None) == (expected_generation == 0), 'invalid bootstrap expectation')
        if expected_parent is not None:
            digest(expected_parent)
        digest(expected_epoch, 32)
        digest(expected_cutover_digest, 64)
        raw, fingerprint = verify_signed_commit(source, commit, keyring=keyring,
                                                fingerprint=policy.fingerprint, signoff=signoff)
        headers, separator, _ = raw.partition('\n\n')
        lines = headers.split('\n')
        require(separator and re.fullmatch(r'tree [0-9a-f]{40}', lines[0]), 'invalid state tree header')
        tree = digest(lines[0][5:])
        expected = [f'tree {tree}'] + ([f'parent {expected_parent}'] if expected_parent else [])
        require(lines[:len(expected)] == expected
                and [line for line in lines if line.startswith(('tree ', 'parent '))] == expected,
                'state raw parent differs')
        # A raw tree entry is mode + name + NUL + the binary object ID. Matching
        # the whole tree refuses other paths, duplicate entries and link modes.
        raw_tree = read_raw_object(source, tree, 'tree', limit=128)
        entry = re.fullmatch(rb'100644 coordination\.json\x00(.{20})', raw_tree, re.DOTALL)
        require(entry is not None, 'expected only the regular coordination.json file')
        blob = read_raw_object(source, entry[1].hex(), 'blob', limit=MAX_RECORD_BYTES)
        record = json.loads(blob.decode('utf-8'), object_pairs_hook=object_pairs,
                            parse_int=bounded_integer, parse_float=finite_float, parse_constant=reject_constant)
        validate_record(record, policy)
        require(record['generation'] == expected_generation and record['epoch'] == expected_epoch
                and record['cutover_digest'] == expected_cutover_digest, 'state identity differs')
        return SignedCoordination(commit, expected_parent, tree, fingerprint, record)
    except (ValueError, TypeError, KeyError, RecursionError) as error:
        raise CoordinationError('cannot authenticate the expected coordination record') from error
