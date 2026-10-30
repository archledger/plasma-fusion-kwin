# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Authenticate request/claim/acknowledgement artifacts; never write a grant.

The caller authorizes the branch and pinned source and supplies a state returned
by read_signed_coordination(). A validation-branch result is not production
admission, native-worker authorization, or evidence of runtime cleanup.
"""
import hashlib
import re
import zipfile
import zlib

from ci.native.coordination import SignedCoordination, validate_record
from ci.native.qualification import (
    MAX_RECEIPT_BYTES,
    QualificationPolicy,
    canonical_json,
    digest,
    identifier,
    read_archive,
    require,
    timestamp,
)


class HandoffError(ValueError):
    """The request does not belong to the expected operation and trusted run."""


def _execution(run, policy, operation, branch, run_id, status='completed'):
    require(identifier(run['id']) == run_id
            and identifier(run['workflow_id']) == policy.workflow_id
            and run['path'] == policy.workflow_path
            and identifier(run['repository']['id']) == policy.repository_id
            and run['repository']['full_name'] == policy.repository
            and identifier(run['head_repository']['id']) == policy.repository_id
            and run['head_branch'] == branch and run['head_sha'] == policy.launcher
            and run['event'] == 'workflow_dispatch' and run['status'] == status
            and run['conclusion'] == ('success' if status == 'completed' else None)
            and type(run['run_attempt']) is int
            and run['run_attempt'] == 1, 'request run is not the trusted original execution')
    require(identifier(run['actor']['id']) == operation['admission']['approver_id']
            == identifier(run['triggering_actor']['id']), 'request actor differs')
    return {'workflow_id': policy.workflow_id, 'run_id': run_id,
            'run_number': identifier(run['run_number']), 'run_attempt': 1,
            'launcher': policy.launcher, 'nonce': operation['nonce']}


def _verify_emission(api, policy, state, mode, *, run_id, artifact_id, archive, expected_branch):
    """Only pinned reviewed emitter code, not timestamps, authenticates the uploader."""
    try:
        phase, status, job_name, kind = {
            'completed-request': ('dispatching', 'completed', 'handoff-request', 'request'),
            'active-request': ('dispatching', 'in_progress', 'live-worker', 'request'),
            'claim': ('granted', 'in_progress', 'live-worker', 'claim'),
            'ack': ('running', 'completed', 'live-worker', 'ack'),
        }[mode]
        require(type(policy) is QualificationPolicy and type(state) is SignedCoordination,
                'expected trusted policy and authenticated state')
        require(api.repository == policy.repository and state.fingerprint == policy.fingerprint,
                'wrong repository or state signer')
        require(type(expected_branch) is str and (expected_branch == 'main' or re.fullmatch(
            r'validation/native-(?:handoff|worker)-[0-9]{8}', expected_branch)), 'unexpected request branch')
        validate_record(state.record, policy)
        require(state.record['state'] == phase, 'operation is not awaiting this emission')
        operation = state.record['operation']
        run_id, artifact_id = identifier(run_id), identifier(artifact_id)
        run = api.get(f'actions/runs/{run_id}')
        execution = _execution(run, policy, operation, expected_branch, run_id, status)
        artifact = api.get(f'actions/artifacts/{artifact_id}')
        association = artifact['workflow_run']
        require(identifier(artifact['id']) == artifact_id and artifact['expired'] is False
                and artifact['name'] == f'coordination-{kind}-{run_id}-1'
                and identifier(association['id']) == run_id
                and identifier(association['repository_id']) == policy.repository_id
                and identifier(association['head_repository_id']) == policy.repository_id
                and association['head_sha'] == policy.launcher, 'wrong request artifact provenance')
        member = kind + '.json'
        request = read_archive(archive, artifact, {member: MAX_RECEIPT_BYTES})[member]
        identity = {'schema_version': 1, 'admission': operation['admission'], 'execution': execution}
        inventory = api.get(f'actions/runs/{run_id}/attempts/1/jobs', page=1, per_page=100)
        require(type(inventory['total_count']) is int and inventory['total_count'] == 1
                and type(inventory['jobs']) is list and len(inventory['jobs']) == 1,
                'expected one request-only job')
        job = inventory['jobs'][0]
        identifier(job['id'])
        require(identifier(job['run_id']) == run_id and type(job['run_attempt']) is int
                and job['run_attempt'] == 1 and job['head_sha'] == policy.launcher
                and job['name'] == job_name and job['status'] == status
                and job['conclusion'] == ('success' if status == 'completed' else None), 'request job differs')
        require(timestamp(job['started_at']) <= timestamp(artifact['created_at']),
                'request artifact predates its job')
        if status == 'completed':
            require(timestamp(artifact['created_at']) <= timestamp(job['completed_at']),
                    'request artifact follows its job')
        expected = {'coordination_sha': state.commit, 'identity': identity}
        result = {'run_id': run_id, 'run_number': execution['run_number'], 'run_attempt': 1,
                  'qualification_id': hashlib.sha256(canonical_json(identity)).hexdigest(),
                  'native_job_id': None, 'worker_nonce': None, 'finalizer_nonce': None}
        if kind != 'request':
            grant = state.record['grant']
            require(all(grant[field] == result[field] for field in
                        ['run_id', 'run_number', 'run_attempt', 'qualification_id']), 'grant run differs')
            nonce = digest(request['worker_nonce'], 32)
            require(nonce != operation['nonce'], 'worker nonce must be a fresh invocation')
            expected.update(native_job_id=job['id'], worker_nonce=nonce)
            result = dict(grant, native_job_id=job['id'], worker_nonce=nonce)
            if kind == 'ack':
                require(grant['native_job_id'] == job['id'] and grant['worker_nonce'] == nonce,
                        'acknowledgement is from another invocation')
                expected.update(accepted=True, duplicate_refused=True,
                                unclaimed_grant_refused=True, native_executed=False)
                result = expected
        require(canonical_json(request) == canonical_json(expected), 'emission binding differs')
        permission = api.get('collaborators/' + run['actor']['login'] + '/permission')
        require(identifier(permission['user']['id']) == operation['admission']['approver_id']
                and permission['permission'] in {'admin', 'maintain', 'write'}, 'request actor revoked')
        require(_execution(api.get(f'actions/runs/{run_id}'), policy, operation, expected_branch, run_id, status)
                == execution, 'request run changed during verification')
        return result
    except (ValueError, KeyError, TypeError, IndexError, AttributeError, RuntimeError,
            EOFError, OSError, zipfile.BadZipFile, zlib.error) as error:
        raise HandoffError('cannot authenticate the expected handoff request') from error


def verify_handoff_request(api, policy, state, **inputs):
    """Original completed request-only probe; never accepts an active run."""
    return _verify_emission(api, policy, state, 'completed-request', **inputs)


def verify_active_request(api, policy, state, **inputs):
    """Bind one active, pinned no-op worker to a signed dispatching state."""
    return _verify_emission(api, policy, state, 'active-request', **inputs)


def verify_worker_claim(api, policy, state, **inputs):
    """Return a running-state proposal for the current grant and invocation."""
    return _verify_emission(api, policy, state, 'claim', **inputs)


def verify_worker_acknowledgement(api, policy, state, **inputs):
    """Require terminal no-op worker evidence before considering release."""
    return _verify_emission(api, policy, state, 'ack', **inputs)
