# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Authenticate a completed request emitter; return data, never write a grant.

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
    identifier,
    read_archive,
    require,
    timestamp,
)


class HandoffError(ValueError):
    """The request does not belong to the expected operation and trusted run."""


def _execution(run, policy, operation, branch, run_id):
    require(identifier(run['id']) == run_id
            and identifier(run['workflow_id']) == policy.workflow_id
            and run['path'] == policy.workflow_path
            and identifier(run['repository']['id']) == policy.repository_id
            and run['repository']['full_name'] == policy.repository
            and identifier(run['head_repository']['id']) == policy.repository_id
            and run['head_branch'] == branch and run['head_sha'] == policy.launcher
            and run['event'] == 'workflow_dispatch' and run['status'] == 'completed'
            and run['conclusion'] == 'success' and type(run['run_attempt']) is int
            and run['run_attempt'] == 1, 'request run is not the trusted original execution')
    require(identifier(run['actor']['id']) == operation['admission']['approver_id']
            == identifier(run['triggering_actor']['id']), 'request actor differs')
    return {'workflow_id': policy.workflow_id, 'run_id': run_id,
            'run_number': identifier(run['run_number']), 'run_attempt': 1,
            'launcher': policy.launcher, 'nonce': operation['nonce']}


def verify_handoff_request(api, policy, state, *, run_id, artifact_id, archive, expected_branch):
    """Bind one completed request artifact to a signed dispatching snapshot.

    Requires reviewed source with one request-only job and no untrusted code or
    other uploader. Job metadata alone does not authenticate an artifact's author.
    This bounded probe does not admit a still-running native worker.
    """
    try:
        require(type(policy) is QualificationPolicy and type(state) is SignedCoordination,
                'expected trusted policy and authenticated state')
        require(api.repository == policy.repository and state.fingerprint == policy.fingerprint,
                'wrong repository or state signer')
        require(type(expected_branch) is str and (expected_branch == 'main' or re.fullmatch(
            r'validation/native-handoff-[0-9]{8}', expected_branch)), 'unexpected request branch')
        validate_record(state.record, policy)
        require(state.record['state'] == 'dispatching', 'operation is not awaiting a request')
        operation = state.record['operation']
        run_id, artifact_id = identifier(run_id), identifier(artifact_id)
        run = api.get(f'actions/runs/{run_id}')
        execution = _execution(run, policy, operation, expected_branch, run_id)
        artifact = api.get(f'actions/artifacts/{artifact_id}')
        association = artifact['workflow_run']
        require(identifier(artifact['id']) == artifact_id and artifact['expired'] is False
                and artifact['name'] == f'coordination-request-{run_id}-1'
                and identifier(association['id']) == run_id
                and identifier(association['repository_id']) == policy.repository_id
                and identifier(association['head_repository_id']) == policy.repository_id
                and association['head_sha'] == policy.launcher, 'wrong request artifact provenance')
        request = read_archive(archive, artifact, {'request.json': MAX_RECEIPT_BYTES})['request.json']
        identity = {'schema_version': 1, 'admission': operation['admission'], 'execution': execution}
        require(canonical_json(request) == canonical_json({
            'coordination_sha': state.commit, 'identity': identity}), 'request binding differs')
        inventory = api.get(f'actions/runs/{run_id}/attempts/1/jobs', page=1, per_page=100)
        require(type(inventory['total_count']) is int and inventory['total_count'] == 1
                and type(inventory['jobs']) is list and len(inventory['jobs']) == 1,
                'expected one request-only job')
        job = inventory['jobs'][0]
        identifier(job['id'])
        require(identifier(job['run_id']) == run_id and type(job['run_attempt']) is int
                and job['run_attempt'] == 1 and job['head_sha'] == policy.launcher
                and job['name'] == 'handoff-request' and job['status'] == 'completed'
                and job['conclusion'] == 'success', 'request job differs')
        require(timestamp(job['started_at']) <= timestamp(artifact['created_at'])
                <= timestamp(job['completed_at']), 'request artifact is outside its job')
        permission = api.get('collaborators/' + run['actor']['login'] + '/permission')
        require(identifier(permission['user']['id']) == operation['admission']['approver_id']
                and permission['permission'] in {'admin', 'maintain', 'write'}, 'request actor revoked')
        require(_execution(api.get(f'actions/runs/{run_id}'), policy, operation, expected_branch, run_id)
                == execution, 'request run changed during verification')
        return {'run_id': run_id, 'run_number': execution['run_number'], 'run_attempt': 1,
                'qualification_id': hashlib.sha256(canonical_json(identity)).hexdigest(),
                'native_job_id': None, 'worker_nonce': None, 'finalizer_nonce': None}
    except (ValueError, KeyError, TypeError, IndexError, AttributeError, RuntimeError,
            EOFError, OSError, zipfile.BadZipFile, zlib.error) as error:
        raise HandoffError('cannot authenticate the expected handoff request') from error
