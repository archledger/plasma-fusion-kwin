# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Local lifecycle integration: real validators/signatures, fake API/native output.

Never contact GitHub, dispatch a job, or execute a guest. Native evidence comes
from the existing deterministic qualification fixture, not a new runtime test.
"""

import copy
import hashlib
import io
import json
import os
import re
import subprocess
import zipfile
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import test_current_policy as policy_fixtures
import test_qualification as qualification_fixtures

from ci.native import producer
from ci.native.checks import CheckReporter
from ci.native.coordination import read_signed_coordination
from ci.native.qualification import canonical_json
from ci.native.readiness import verify_readiness


class LifecycleFixture:
    def __init__(self, signer):
        self.signer = signer
        self.source = signer.source
        self.work = self.source / 'work'
        self.work.mkdir(mode=0o700)
        self.policy = replace(
            signer.policy,
            repository=producer.REPOSITORY,
            repository_id=1439058578,
            packages=('fixture-package',),
        )
        self.raw = qualification_fixtures.QualificationV2Test()
        self.raw.setUp()
        self.raw.policy = self.policy
        self.checks, self.artifacts, self.writes, self.calls = {}, {}, [], []
        self.next_id = 1200

        initial = dict(signer.initial, repository_id=self.policy.repository_id)
        self.state = None
        self.append(initial)
        base = self.state.commit
        head = signer.commit({'fixture': 'candidate'}, base)
        commit = signer.commit({'fixture': 'merge'}, base, head)
        tree = signer.git('rev-parse', commit + '^{tree}').decode().strip()
        self.raw.signed = replace(
            self.raw.signed,
            commit=commit,
            tree=tree,
            fingerprint=self.policy.fingerprint,
            base=base,
            head=head,
        )
        source = b'deterministic source fixture\n'
        admission = self.raw.receipt['identity']['admission']
        admission.update(
            repository_id=self.policy.repository_id,
            base=base,
            head=head,
            signed_merge=commit,
            tree=tree,
            source={
                'tree': '2' * 40,
                'sha256': hashlib.sha256(source).hexdigest(),
                'bytes': len(source),
            },
        )
        self.raw.report = self.raw.native_report()
        self.raw.pack()
        identity = self.raw.receipt['identity']
        qid = self.raw.receipt['qualification_id']
        operation = {
            'id': identity['execution']['nonce'],
            'nonce': identity['execution']['nonce'],
            'holder_fingerprint': self.policy.fingerprint,
            'admission': admission,
            'workflow_id': self.policy.workflow_id,
            'launcher': self.policy.launcher,
        }
        self.append(dict(self.state.record, state='reserved', operation=operation))
        self.append(dict(self.state.record, state='dispatching'))
        dispatched = self.state
        grant = {
            'run_id': 500,
            'run_number': 12,
            'run_attempt': 1,
            'qualification_id': qid,
            'native_job_id': None,
            'worker_nonce': None,
            'finalizer_nonce': None,
        }
        self.append(dict(self.state.record, state='granted', grant=grant))

        policy_data = policy_fixtures.CurrentPolicyTest()
        policy_data.setUp()
        self.responses = copy.deepcopy(policy_data.api.values)
        self.run = copy.deepcopy(self.raw.api.responses['actions/runs/500'])
        self.run.update(
            repository={
                'id': self.policy.repository_id,
                'full_name': self.policy.repository,
            },
            head_repository={'id': self.policy.repository_id},
            status='in_progress',
            conclusion=None,
        )
        self.jobs = {
            j['name']: copy.deepcopy(j)
            for j in self.raw.api.responses['actions/runs/500/attempts/1/jobs']['jobs']
        }
        for role in ['admit', 'native', 'report']:
            self.jobs[role].update(
                status='in_progress' if role == 'admit' else 'queued',
                conclusion=None,
                completed_at=None,
            )
        pr = self.responses['pulls/7']
        pr.update(merge_commit_sha=admission['merge'])
        pr['base'].update(
            sha=base,
            repo={'id': self.policy.repository_id, 'full_name': self.policy.repository},
        )
        pr['head'].update(sha=head, repo={'id': admission['head_repository_id']})
        for resource, sha in [
            ('git/ref/heads/main', base),
            ('git/ref/pull/7/head', head),
            ('git/ref/pull/7/merge', admission['merge']),
        ]:
            self.responses[resource]['object']['sha'] = sha
        self.responses['pulls/7/reviews'][0]['commit_id'] = head
        self.responses['pulls/7/commits'][0]['sha'] = head
        self.responses['git/commits/' + admission['merge']] = {
            'sha': admission['merge'],
            'tree': {'sha': tree},
            'parents': [{'sha': base}, {'sha': head}],
        }
        self.responses['collaborators/maintainer/permission'] = {
            'permission': 'admin',
            'user': {'id': admission['approver_id']},
        }
        self.api = SimpleNamespace(repository=self.policy.repository, get=self.get)
        self.ctx = SimpleNamespace(
            policy=self.policy,
            run_id=500,
            identity=identity,
            execution=identity['execution'],
            qid=qid,
            admission=admission,
            signed=self.raw.signed,
            api=self.api,
            current=lambda: self.state,
            initial=dispatched,
            work=self.work,
            root=self.source,
            objects=self.source,
            keyring=signer.public,
        )
        self.ctx.jobs = lambda: producer.Context.jobs(self.ctx)
        self.ctx.artifact = lambda *args: producer.Context.artifact(self.ctx, *args)
        self.ctx.verify_run = lambda run: producer.Context.verify_run(self.ctx, run)
        self.ctx.association = lambda: producer.Context.association(self.ctx)
        self.publish(
            902,
            'native-prepared-500-1',
            {
                'prepared.json': canonical_json(
                    {
                        'identity': identity,
                        'qualification_id': qid,
                        'input_manifest_sha256': '4' * 64,
                    }
                ),
                'inputs.json': b'{}',
                'patched-source.tar': source,
            },
            '2026-10-30T00:00:29Z',
        )

    def append(self, record):
        parent = self.state.commit if self.state is not None else None
        generation = (
            self.state.record['generation'] + 1 if self.state is not None else 0
        )
        record = dict(record, generation=generation)
        commit = self.signer.commit(record, *([parent] if parent else []))
        self.state = read_signed_coordination(
            self.source,
            commit,
            policy=self.policy,
            keyring=self.signer.public,
            signoff='Signed-off-by: Fixture <fixture@example.invalid>',
            expected_parent=parent,
            expected_generation=generation,
            expected_epoch=record['epoch'],
            expected_cutover_digest=record['cutover_digest'],
        )

    def get(self, resource, **parameters):
        self.calls.append(resource)
        if resource == 'actions/runs/500':
            return copy.deepcopy(self.run)
        if resource == 'actions/runs/500/attempts/1/jobs':
            return {'total_count': 4, 'jobs': copy.deepcopy(list(self.jobs.values()))}
        if resource == 'actions/runs/500/artifacts':
            return {
                'total_count': len(self.artifacts),
                'artifacts': [copy.deepcopy(a[0]) for a in self.artifacts.values()],
            }
        if resource.startswith('actions/artifacts/'):
            return copy.deepcopy(self.artifacts[int(resource.rsplit('/', 1)[1])][0])
        if resource == 'git/ref/heads/native-gate-coordination':
            return {
                'ref': 'refs/heads/native-gate-coordination',
                'object': {'type': 'commit', 'sha': self.state.commit},
            }
        if resource.startswith('check-runs/'):
            return copy.deepcopy(self.checks[int(resource.rsplit('/', 1)[1])])
        if resource.startswith('commits/') and resource.endswith('/check-runs'):
            checks = [
                copy.deepcopy(c)
                for c in self.checks.values()
                if c['head_sha'] == resource.split('/')[1]
            ]
            return {'total_count': len(checks), 'check_runs': checks}
        return copy.deepcopy(self.responses[resource])

    def publish(self, artifact_id, name, members, created):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for member, data in members.items():
                archive.writestr(member, data)
        raw = stream.getvalue()
        metadata = {
            'id': artifact_id,
            'name': name,
            'expired': False,
            'size_in_bytes': len(raw),
            'digest': 'sha256:' + hashlib.sha256(raw).hexdigest(),
            'created_at': created,
            'workflow_run': {
                'id': 500,
                'repository_id': self.policy.repository_id,
                'head_repository_id': self.policy.repository_id,
                'head_sha': self.policy.launcher,
            },
        }
        self.artifacts[artifact_id] = metadata, raw

    def send(self, method, resource, body=None):
        if method == 'GET':
            return self.get(resource)
        self.writes.append((method, resource, copy.deepcopy(body)))
        if method == 'POST':
            check_id = self.next_id
            self.next_id += 1
            self.checks[check_id] = dict(
                body,
                id=check_id,
                app={'id': self.policy.app_id},
                conclusion=None,
                completed_at=None,
            )
        else:
            if method != 'PATCH':
                raise AssertionError('unexpected fixture write')
            check_id = int(resource.rsplit('/', 1)[1])
            self.checks[check_id].update(body, completed_at='2026-10-30T00:30:30Z')
        return copy.deepcopy(self.checks[check_id])

    @contextmanager
    def transport(self):
        real_run = subprocess.run

        def run(command, **options):
            if options.get('shell') or options.get('executable'):
                raise AssertionError('fixture forbids command overrides')
            if type(command) not in {list, tuple} or not all(
                type(arg) is str for arg in command
            ):
                raise AssertionError('unexpected fixture command')
            if len(command) == 3 and command[:2] == ['gh', 'api']:
                match = re.fullmatch(
                    r'repos/'
                    + re.escape(self.policy.repository)
                    + r'/actions/artifacts/([1-9][0-9]*)/zip',
                    command[2],
                )
                if match:
                    return subprocess.CompletedProcess(
                        command, 0, self.artifacts[int(match[1])][1], b''
                    )
            local_object = (
                len(command) == 5
                and command[:3] == ['git', '--no-replace-objects', 'cat-file']
                and command[3] in {'-t', '-s', 'commit', 'tree', 'blob'}
            )
            local_signature = (
                len(command) == 11
                and command[:4]
                == ['git', '--no-replace-objects', '-c', 'gpg.format=openpgp']
                and command[4] == command[6] == '-c'
                and command[5].startswith('gpg.program=')
                and command[7].startswith('gpg.openpgp.program=')
                and command[8:10] == ['verify-commit', '--raw']
            )
            if (local_object or local_signature) and re.fullmatch(
                r'[0-9a-f]{40}', command[-1]
            ):
                return real_run(command, **options)
            raise AssertionError(
                'fixture refused an unexpected execution or network command'
            )

        def client(policy, key, run_id, fence, **options):
            reporter = CheckReporter(policy, 'fixture-token', run_id, fence, **options)
            reporter._send, reporter.reader = self.send, self.api
            return reporter

        with (
            patch.object(producer.subprocess, 'run', side_effect=run),
            patch.object(producer.CheckReporter, 'from_key', side_effect=client),
            patch.dict(
                os.environ,
                {
                    'GH_TOKEN': 'fixture-token',
                    'NATIVE_GATE_PRIVATE_KEY': 'fixture-only',
                },
            ),
        ):
            yield

    def admit(self, *, publish=True):
        with (
            self.transport(),
            patch.object(producer, 'wait_next', return_value=self.state),
        ):
            producer.admit_job(self.ctx)
        self.admission = json.loads((self.work / 'admission.json').read_bytes())
        self.jobs['admit'].update(
            status='completed',
            conclusion='success',
            completed_at='2026-10-30T00:01:00Z',
        )
        if publish:
            self.publish(
                901,
                'native-admission-500-1',
                {'admission.json': canonical_json(self.admission)},
                '2026-10-30T00:00:50Z',
            )

    def native_result(self):
        self.append(
            dict(
                self.state.record,
                state='running',
                grant=dict(
                    self.state.record['grant'], native_job_id=702, worker_nonce='7' * 32
                ),
            )
        )
        self.jobs['native'].update(
            status='completed',
            conclusion='success',
            completed_at='2026-10-30T00:30:00Z',
        )
        self.jobs['report'].update(status='in_progress', conclusion=None)
        self.raw.receipt['evidence'].update(
            check_run_id=self.admission['checks']['native_check_id'],
            activation_check_run_id=self.admission['checks']['activation_check_id'],
        )
        self.publish(
            900,
            'native-qualification-500-1',
            {
                'qualification.json': canonical_json(self.raw.receipt),
                'report.json': canonical_json(self.raw.report),
            },
            '2026-10-30T00:29:59Z',
        )

    def report(self):
        success = False
        try:
            with self.transport():
                producer.report_job(self.ctx)
            success = True
        finally:
            self.jobs['report'].update(
                status='completed',
                conclusion='success' if success else 'failure',
                completed_at='2026-10-30T00:31:00Z',
            )
            self.run.update(
                status='completed', conclusion='success' if success else 'failure'
            )

    def advance_operation(self):
        """Append a new local signed running operation; retain old check history."""
        old = self.state.record['operation']
        self.append(
            dict(
                self.state.record,
                state='idle',
                operation=None,
                grant=None,
                completion={
                    'operation_id': old['nonce'],
                    'qualification_id': self.ctx.qid,
                    'outcome': 'qualified-only',
                    'main_sha': None,
                },
            )
        )
        operation = dict(old, id='a' * 32, nonce='a' * 32)
        self.append(
            dict(
                self.state.record,
                state='reserved',
                operation=operation,
                completion=None,
            )
        )
        self.append(dict(self.state.record, state='dispatching'))
        identity = {
            'schema_version': 1,
            'admission': operation['admission'],
            'execution': dict(
                self.ctx.execution, nonce=operation['nonce'], run_id=501, run_number=13
            ),
        }
        qid = hashlib.sha256(canonical_json(identity)).hexdigest()
        grant = {
            'run_id': 501,
            'run_number': 13,
            'run_attempt': 1,
            'qualification_id': qid,
            'native_job_id': None,
            'worker_nonce': None,
            'finalizer_nonce': None,
        }
        self.append(dict(self.state.record, state='granted', grant=grant))
        self.append(
            dict(
                self.state.record,
                state='running',
                grant=dict(grant, native_job_id=703, worker_nonce='b' * 32),
            )
        )

    def verify(self, *, anchor=None, admission_archive=None):
        anchor = anchor or {
            'commit': self.state.commit,
            'parent': self.state.parent,
            'generation': self.state.record['generation'],
            'epoch': self.state.record['epoch'],
            'cutover_digest': self.state.record['cutover_digest'],
        }
        with self.transport():
            return verify_readiness(
                self.api,
                self.policy,
                source=self.source,
                keyring=self.signer.public,
                signoff='Signed-off-by: Fixture <fixture@example.invalid>',
                artifact_id=900,
                archive=self.artifacts[900][1],
                admission_archive=(
                    self.artifacts[901][1]
                    if admission_archive is None
                    else admission_archive
                ),
                coordination_anchor=anchor,
            )
