# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""One maintainer process, one existing durable ref, no recovery or main writes."""

import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path

from ci.native.coordination import read_signed_coordination
from ci.native.current_candidate import (
    verify_admission_association,
    verify_current_association,
)
from ci.native.handoff import verify_active_request, verify_worker_claim
from ci.native.merge_update import MergeCommit, git
from ci.native.producer import COORDINATION_REF, ENDPOINT, REPOSITORY
from ci.native.proof_api import ProofAPI
from ci.native.qualification import (
    QualificationPolicy,
    canonical_json,
    identifier,
    require,
    validate_admission,
    verify_qualification,
)
from ci.native.signed_merge import verify_signed_merge


def coordinate(
    source, policy, signoff, keyring, admission, anchor, journal, *, apply=False
):
    """Qualify one explicitly supplied admission; default does not sign/dispatch/push."""
    require(
        os.environ.get('GITHUB_ACTIONS') != 'true',
        'coordinator must run on the maintainer host',
    )
    require(
        type(policy) is QualificationPolicy and type(apply) is bool,
        'invalid coordinator policy or mode',
    )
    require(
        type(anchor) is dict
        and set(anchor)
        == {'commit', 'parent', 'generation', 'epoch', 'cutover_digest'},
        'invalid expected state fields',
    )
    require(
        policy.repository == REPOSITORY
        and policy.repository_id == 1439058578
        and policy.workflow_path == '.github/workflows/trusted-native.yml',
        'unexpected coordinator policy',
    )
    validate_admission(admission)
    token = subprocess.check_output(
        ['gh', 'auth', 'token'], text=True, timeout=15
    ).strip()
    api = ProofAPI(REPOSITORY, token=token, max_calls=64)
    require(
        git(source, 'ls-remote', '--refs', ENDPOINT, 'refs/heads/main')
        == policy.launcher + '\trefs/heads/main',
        'main does not match the pinned launcher',
    )
    for sha in [admission['signed_merge'], anchor['commit']]:
        git(source, 'fetch', '--no-tags', ENDPOINT, sha)
    signed = verify_signed_merge(
        source,
        MergeCommit(
            admission['base'],
            admission['head'],
            admission['signed_merge'],
            admission['tree'],
        ),
        keyring=keyring,
        fingerprint=policy.fingerprint,
        signoff=signoff,
    )
    verify_admission_association(api, policy, admission, signed)
    current = read_signed_coordination(
        source,
        anchor['commit'],
        policy=policy,
        keyring=keyring,
        signoff=signoff,
        expected_parent=anchor['parent'],
        expected_generation=anchor['generation'],
        expected_epoch=anchor['epoch'],
        expected_cutover_digest=anchor['cutover_digest'],
    )
    require(
        current.record['state'] == 'idle'
        and git(source, 'ls-remote', '--refs', ENDPOINT, COORDINATION_REF)
        == current.commit + '\t' + COORDINATION_REF,
        'coordination is not the expected idle state',
    )
    if not apply:
        return {
            'ready': True,
            'mode': 'dry-run',
            'merge_authorized': False,
            'coordination_sha': current.commit,
        }
    require(journal is not None, 'apply requires a new private journal')
    journal = Path(journal)
    parent = journal.parent.resolve(strict=True)
    info = parent.stat()
    require(
        info.st_uid == os.geteuid() and not info.st_mode & 0o022,
        'journal directory is not privately writable',
    )
    journal = parent / journal.name
    state = {
        'status': 'starting',
        'transitions': [],
        'initial': current.commit,
        'admission': admission,
    }
    with journal.open('x') as file:
        os.chmod(journal, 0o600)
        file.write(json.dumps(state, indent=2) + '\n')

    def save():
        journal.write_text(json.dumps(state, indent=2) + '\n')

    def write_git(*args, data):
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        return (
            subprocess.check_output(
                ['git', *args],
                cwd=source,
                env=env,
                input=data,
                stderr=subprocess.PIPE,
                timeout=60,
            )
            .decode()
            .strip()
        )

    def append(value):
        nonlocal current
        require(
            git(source, 'ls-remote', '--refs', ENDPOINT, COORDINATION_REF)
            == current.commit + '\t' + COORDINATION_REF,
            'coordination ownership changed',
        )
        value = dict(value, generation=current.record['generation'] + 1)
        blob = write_git('hash-object', '-w', '--stdin', data=canonical_json(value))
        tree = write_git(
            'mktree', data=f'100644 blob {blob}\tcoordination.json\n'.encode()
        )
        sha = write_git(
            'commit-tree',
            '-S' + policy.fingerprint,
            tree,
            '-p',
            current.commit,
            data=f'Coordinate native qualification {value["state"]}\n\n{signoff}\n'.encode(),
        )
        verified = read_signed_coordination(
            source,
            sha,
            policy=policy,
            keyring=keyring,
            signoff=signoff,
            expected_parent=current.commit,
            expected_generation=value['generation'],
            expected_epoch=anchor['epoch'],
            expected_cutover_digest=anchor['cutover_digest'],
        )
        state['transitions'].append(
            {
                'from': current.commit,
                'to': sha,
                'phase': value['state'],
                'outcome': 'unknown',
            }
        )
        save()
        git(
            source,
            'push',
            '--porcelain',
            '--no-follow-tags',
            '--recurse-submodules=no',
            f'--force-with-lease={COORDINATION_REF}:{current.commit}',
            ENDPOINT,
            f'{sha}:{COORDINATION_REF}',
        )
        require(
            git(source, 'ls-remote', '--refs', ENDPOINT, COORDINATION_REF)
            == sha + '\t' + COORDINATION_REF,
            'coordination update not reconciled',
        )
        state['transitions'][-1]['outcome'] = 'confirmed'
        current = verified
        save()

    nonce = uuid.uuid4().hex
    operation = {
        'id': nonce,
        'nonce': nonce,
        'holder_fingerprint': policy.fingerprint,
        'admission': admission,
        'workflow_id': policy.workflow_id,
        'launcher': policy.launcher,
    }
    append(
        dict(
            current.record,
            state='reserved',
            operation=operation,
            grant=None,
            completion=None,
        )
    )
    reserved = current.commit
    append(dict(current.record, state='dispatching'))
    inputs = {
        'coordination_sha': current.commit,
        'parent_sha': reserved,
        'expected_generation': str(current.record['generation']),
        'epoch': anchor['epoch'],
        'cutover_digest': anchor['cutover_digest'],
    }
    state['status'] = 'dispatch-unknown'
    save()
    dispatch = subprocess.run(
        [
            'gh',
            'workflow',
            'run',
            str(policy.workflow_id),
            '--repo',
            REPOSITORY,
            '--ref',
            'main',
            '--json',
        ],
        input=json.dumps(inputs),
        text=True,
        capture_output=True,
        timeout=60,
        check=True,
    )
    found = re.findall(
        r'https://github.com/archledger/plasma-fusion-kwin/actions/runs/([0-9]+)',
        dispatch.stdout,
    )
    require(len(found) == 1, 'dispatch outcome needs reconciliation')
    run_id = identifier(int(found[0]))
    state.update(status='running', run_id=run_id)
    save()
    deadline = time.monotonic() + 7200

    def artifact(name):
        while time.monotonic() < deadline:
            value = json.loads(
                subprocess.check_output(
                    [
                        'gh',
                        'api',
                        f'repos/{REPOSITORY}/actions/runs/{run_id}/artifacts?per_page=100',
                    ],
                    timeout=30,
                )
            )
            require(value['total_count'] <= 10, 'unexpected artifact inventory')
            found = [item for item in value['artifacts'] if item['name'] == name]
            require(len(found) <= 1, 'duplicate artifact name')
            if found:
                item = found[0]
                require(
                    0 < item['size_in_bytes'] <= 32 * 1024**2,
                    'oversized proof artifact',
                )
                raw = subprocess.check_output(
                    [
                        'gh',
                        'api',
                        f'repos/{REPOSITORY}/actions/artifacts/{identifier(item["id"])}/zip',
                    ],
                    timeout=60,
                )
                require(len(raw) <= 32 * 1024**2, 'oversized proof download')
                return item['id'], raw
            run = json.loads(
                subprocess.check_output(
                    ['gh', 'api', f'repos/{REPOSITORY}/actions/runs/{run_id}'],
                    timeout=30,
                )
            )
            require(
                run['status'] != 'completed',
                'worker finished without required artifact; retain lock',
            )
            time.sleep(2)
        raise TimeoutError('workflow deadline exceeded; retain lock')

    artifact_id, raw = artifact(f'coordination-request-{run_id}-1')
    grant = verify_active_request(
        api,
        policy,
        current,
        run_id=run_id,
        artifact_id=artifact_id,
        archive=raw,
        expected_branch='main',
    )
    append(dict(current.record, state='granted', grant=grant))
    artifact_id, raw = artifact(f'coordination-claim-{run_id}-1')
    grant = verify_worker_claim(
        api,
        policy,
        current,
        run_id=run_id,
        artifact_id=artifact_id,
        archive=raw,
        expected_branch='main',
    )
    append(dict(current.record, state='running', grant=grant))
    subprocess.run(
        ['gh', 'run', 'watch', str(run_id), '--repo', REPOSITORY, '--exit-status'],
        capture_output=True,
        timeout=max(1, deadline - time.monotonic()),
        check=True,
    )
    result_id, result = artifact(f'native-qualification-{run_id}-1')
    _, approved = artifact(f'native-admission-{run_id}-1')
    evidence = verify_qualification(
        api,
        policy,
        signed,
        artifact_id=result_id,
        archive=result,
        admission_archive=approved,
    )
    verify_current_association(api, policy, evidence)
    require(
        evidence.qualification_id == grant['qualification_id'],
        'completed proof differs from grant',
    )
    append(
        dict(
            current.record,
            state='idle',
            operation=None,
            grant=None,
            completion={
                'operation_id': nonce,
                'qualification_id': evidence.qualification_id,
                'outcome': 'qualified-only',
                'main_sha': None,
            },
        )
    )
    state.update(status='qualified-only', result_artifact_id=result_id)
    save()
    return {
        'ready': True,
        'mode': 'qualified-only',
        'merge_authorized': False,
        'qualification_id': evidence.qualification_id,
        'run_id': run_id,
        'coordination_sha': current.commit,
    }
