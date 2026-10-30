# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""One foreground, no-op GitHub worker; no signing, ref writes or native code."""
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.request import build_opener

from ci.native.coordination import read_signed_coordination
from ci.native.proof_api import NoRedirect, ProofAPI, object_pairs
from ci.native.qualification import (
    QualificationPolicy,
    canonical_json,
    digest,
    identifier,
    require,
)
from ci.native.worker_claim import WorkerClaim, WorkerClaimError

REPOSITORY = 'archledger/plasma-fusion-kwin'
ENDPOINT = 'https://github.com/' + REPOSITORY + '.git'
FINGERPRINT = 'F35053398E3C80FE20891B82C10B8492BD7F30C6'
PUBLIC_KEY_SHA256 = 'e5948bb647b22b8d081b9f8eb97ce92d03d26df50bd6f253ecd19578cfe303e1'
SIGNOFF = 'Signed-off-by: Wisbendji Fimerlus <archledger236@gmail.com>'


def remaining(deadline):
    value = deadline - time.monotonic()
    require(value > 0, 'live worker deadline expired')
    return min(30, value)


def git(source, *args, deadline):
    env = {'PATH': os.environ['PATH'], 'HOME': str(source), 'LANG': 'C.UTF-8',
           'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
           'GIT_TERMINAL_PROMPT': '0', 'GIT_NO_REPLACE_OBJECTS': '1'}
    result = subprocess.run(['git', '--git-dir=' + str(source), *args], env=env,
                            capture_output=True, text=True, timeout=remaining(deadline), check=True)
    return result.stdout.strip()


def wait_next(source, current, phase, policy, keyring, deadline):
    """Wait only for the exact next signed transition, never follow a new owner."""
    ref = os.environ['STATE_REF']
    while True:
        remaining(deadline)
        observed = git(source, 'ls-remote', '--refs', ENDPOINT, ref, deadline=deadline).split()
        require(len(observed) == 2 and observed[1] == ref, 'missing or ambiguous state ref')
        sha = digest(observed[0])
        if sha != current.commit:
            git(source, 'fetch', '--no-tags', '--depth=5', ENDPOINT, sha, deadline=deadline)
            result = read_signed_coordination(source, sha, policy=policy, keyring=keyring,
                signoff=SIGNOFF, expected_parent=current.commit,
                expected_generation=current.record['generation'] + 1,
                expected_epoch=current.record['epoch'], expected_cutover_digest=current.record['cutover_digest'])
            require(result.record['state'] == phase and canonical_json(result.record['operation'])
                    == canonical_json(current.record['operation']), 'operation changed while waiting')
            if current.record['grant'] is not None:
                require(all(result.record['grant'][field] == current.record['grant'][field]
                            for field in ['run_id', 'run_number', 'run_attempt', 'qualification_id']),
                        'grant changed while waiting')
            return result
        time.sleep(min(2, remaining(deadline)))


def upload(kind, value, root, deadline):
    """Use the pinned official action entrypoint, not another artifact client."""
    raw = canonical_json(value)
    require(kind in {'request', 'claim', 'ack'} and len(raw) <= 65536, 'invalid output artifact')
    path = root / (kind + '.json')
    path.write_bytes(raw)
    env = dict(os.environ, INPUT_NAME=f"coordination-{kind}-{os.environ['GITHUB_RUN_ID']}-1",
               INPUT_PATH=str(path), INPUT_OVERWRITE='false', INPUT_ARCHIVE='true')
    env.update({'INPUT_IF-NO-FILES-FOUND': 'error', 'INPUT_RETENTION-DAYS': '14',
                'INPUT_COMPRESSION-LEVEL': '6', 'INPUT_INCLUDE-HIDDEN-FILES': 'false'})
    subprocess.run([os.environ['PROBE_NODE'], '_probe-uploader/dist/upload/index.js'], env=env,
                   timeout=remaining(deadline), check=True)


def main():
    require(os.environ['GITHUB_REPOSITORY'] == REPOSITORY
            and os.environ['GITHUB_EVENT_NAME'] == 'workflow_dispatch'
            and os.environ['GITHUB_RUN_ATTEMPT'] == '1', 'unexpected workflow invocation')
    require(re.fullmatch(r'refs/heads/validation/native-worker-[0-9]{8}', os.environ['GITHUB_REF'])
            and re.fullmatch(r'refs/heads/validation/native-worker-state-[0-9]{8}', os.environ['STATE_REF']),
            'not a validation-only worker or state ref')
    deadline = time.monotonic() + 180
    operation = json.loads(os.environ['OPERATION_JSON'], object_pairs_hook=object_pairs)
    policy = QualificationPolicy(REPOSITORY, 1439058578, operation['workflow_id'],
        os.environ['GITHUB_SHA'], FINGERPRINT, ('fixture-only',),
        workflow_path='.github/workflows/live-worker-probe.yml')
    run_id, run_number = int(os.environ['GITHUB_RUN_ID']), int(os.environ['GITHUB_RUN_NUMBER'])
    with tempfile.TemporaryDirectory(prefix='live-worker-', dir=os.environ['RUNNER_TEMP']) as directory:
        root = Path(directory)
        keyring, source = root / 'keys', root / 'state.git'
        keyring.mkdir(mode=0o700)
        with build_opener(NoRedirect()).open('https://github.com/archledger.gpg', timeout=remaining(deadline)) as response:
            public_key = response.read(65537)
        require(hashlib.sha256(public_key).hexdigest() == PUBLIC_KEY_SHA256, 'public verification key changed')
        public_path = root / 'public.asc'
        public_path.write_bytes(public_key)
        subprocess.run(['gpg', '--no-options', '--homedir', str(keyring), '--batch', '--no-autostart',
                        '--no-auto-check-trustdb', '--import', str(public_path)],
                       capture_output=True, timeout=remaining(deadline), check=True)
        git(source, 'init', '--bare', '-q', str(source), deadline=deadline)
        sha = digest(os.environ['COORDINATION_SHA'])
        git(source, 'fetch', '--no-tags', '--depth=5', ENDPOINT, sha, deadline=deadline)
        current = read_signed_coordination(source, sha, policy=policy, keyring=keyring, signoff=SIGNOFF,
            expected_parent=os.environ['PARENT_SHA'], expected_generation=2,
            expected_epoch=os.environ['EPOCH'], expected_cutover_digest=os.environ['CUTOVER_DIGEST'])
        require(current.record['state'] == 'dispatching'
                and canonical_json(current.record['operation']) == canonical_json(operation), 'wrong dispatch')
        jobs = ProofAPI(REPOSITORY, max_calls=1).get(f'actions/runs/{run_id}/attempts/1/jobs', page=1)
        require(type(jobs['jobs']) is list and jobs['total_count'] == len(jobs['jobs']) == 1,
                'expected one live worker job')
        job = jobs['jobs'][0]
        require(job['name'] == 'live-worker' and job['status'] == 'in_progress'
                and job['conclusion'] is None and identifier(job['run_id']) == run_id
                and job['head_sha'] == policy.launcher and type(job['run_attempt']) is int
                and job['run_attempt'] == 1, 'wrong live worker job')
        worker = WorkerClaim(root, policy, run_id=run_id, job_id=job['id'])
        identity = {'schema_version': 1, 'admission': operation['admission'], 'execution': {
            'workflow_id': policy.workflow_id, 'run_id': run_id, 'run_number': run_number,
            'run_attempt': 1, 'launcher': policy.launcher, 'nonce': operation['nonce']}}
        upload('request', {'coordination_sha': current.commit, 'identity': identity}, root, deadline)
        granted = wait_next(source, current, 'granted', policy, keyring, deadline)
        require(granted.record['grant']['run_id'] == run_id
                and granted.record['grant']['run_number'] == run_number, 'grant belongs to another run')
        try:
            worker.accept(granted)
        except WorkerClaimError:
            pass
        else:
            raise ValueError('unclaimed grant was accepted')
        claim = {'coordination_sha': granted.commit, 'identity': identity,
                 'native_job_id': job['id'], 'worker_nonce': worker.nonce}
        upload('claim', claim, root, deadline)
        running = wait_next(source, granted, 'running', policy, keyring, deadline)
        worker.accept(running)
        try:
            worker.accept(running)
        except WorkerClaimError:
            pass
        else:
            raise ValueError('duplicate acceptance was allowed')
        upload('ack', dict(claim, coordination_sha=running.commit, accepted=True,
                          duplicate_refused=True, unclaimed_grant_refused=True, native_executed=False), root, deadline)


if __name__ == '__main__':
    main()
