# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Real signed state records with disposable keys, never maintainer credentials."""
import copy
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from ci.native.coordination import CoordinationError, read_signed_coordination
from ci.native.qualification import QualificationPolicy
from ci.native.worker_claim import WorkerClaim, WorkerClaimError

SIGNOFF = 'Signed-off-by: Fixture <fixture@example.invalid>'


class SignedCoordinationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='signed-state-', dir='/tmp/opencode'
                                                   if Path('/tmp/opencode').is_dir() else None)
        cls.root = Path(cls.temporary.name)
        cls.keys, cls.public = cls.root / 'keys', cls.root / 'public'
        cls.keys.mkdir(mode=0o700)
        cls.public.mkdir(mode=0o700)
        cls.env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
        cls.env.update(GNUPGHOME=str(cls.keys), GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                       GIT_AUTHOR_NAME='Fixture', GIT_AUTHOR_EMAIL='fixture@example.invalid',
                       GIT_COMMITTER_NAME='Fixture', GIT_COMMITTER_EMAIL='fixture@example.invalid')
        cls.addClassCleanup(cls.clean_keys)
        cls.fingerprints = []
        for name in ['approved', 'other']:
            uid = f'{name} <{name}@example.invalid>'
            subprocess.run(['gpg', '--batch', '--pinentry-mode', 'loopback', '--passphrase', '',
                            '--quick-generate-key', uid, 'ed25519', 'sign', '0'],
                           env=cls.env, check=True, capture_output=True, timeout=30)
            listing = subprocess.check_output(['gpg', '--with-colons', '--list-keys', uid],
                                              env=cls.env, text=True, stderr=subprocess.PIPE)
            cls.fingerprints.append(next(line.split(':')[9] for line in listing.splitlines()
                                         if line.startswith('fpr:')))
        public = subprocess.check_output(['gpg', '--export'], env=cls.env)
        subprocess.run(['gpg', '--homedir', str(cls.public), '--batch', '--import'],
                       input=public, env=cls.env, check=True, capture_output=True, timeout=30)

    @classmethod
    def clean_keys(cls):
        for directory in [cls.keys, cls.public]:
            subprocess.run(['gpgconf', '--homedir', str(directory), '--kill', 'gpg-agent'],
                           env=cls.env, check=False, capture_output=True, timeout=15)
        cls.temporary.cleanup()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='repo-', dir=self.root)
        self.addCleanup(temporary.cleanup)
        self.source = Path(temporary.name)
        self.git('init', '-q')
        self.git('config', 'gpg.program', shutil.which('gpg'))
        self.policy = QualificationPolicy('owner/downstream', 100, 200, 'a' * 40,
                                          self.fingerprints[0], ('fixture',), app_id=300)
        self.initial = {'version': 1, 'repository_id': 100, 'target_ref': 'refs/heads/main',
                        'epoch': 'e' * 32, 'cutover_digest': 'f' * 64, 'generation': 0,
                        'state': 'idle', 'operation': None, 'grant': None, 'completion': None}
        self.parent = self.commit(self.initial)
        self.record = dict(self.initial, generation=1, completion={
            'operation_id': '1' * 32, 'qualification_id': None,
            'outcome': 'aborted-before-dispatch', 'main_sha': None})
        self.commit_id = self.commit(self.record, self.parent)

    def git(self, *args, data=None):
        return subprocess.check_output(['git', *args], cwd=self.source, env=self.env,
                                       input=data, stderr=subprocess.PIPE)

    def commit(self, record, *parents, signer=0, entries=None, raw=None, message=None):
        blob = self.git('hash-object', '-w', '--stdin', data=raw if raw is not None else
                        json.dumps(record).encode()).decode().strip()
        tree = self.git('mktree', data=(entries or f'100644 blob {blob}\tcoordination.json\n').encode())
        args = ['commit-tree', tree.decode().strip()]
        for parent in parents:
            args.extend(['-p', parent])
        if signer is not None:
            args.append('-S' + self.fingerprints[signer])
        return self.git(*args, data=(message or 'State\n\n' + SIGNOFF + '\n').encode()).decode().strip()

    def read(self, commit=None, **overrides):
        arguments = {'policy': self.policy, 'keyring': self.public, 'signoff': SIGNOFF,
                     'expected_parent': self.parent, 'expected_generation': 1,
                     'expected_epoch': 'e' * 32, 'expected_cutover_digest': 'f' * 64}
        arguments.update(overrides)
        return read_signed_coordination(self.source, commit or self.commit_id, **arguments)

    def test_signed_successor_and_explicit_bootstrap(self):
        result = self.read()
        self.assertEqual(result.commit, self.commit_id)
        self.assertEqual(result.parent, self.parent)
        self.assertEqual(result.fingerprint, self.fingerprints[0])
        self.assertEqual(result.record, self.record)
        self.assertEqual(self.read(self.parent, expected_parent=None, expected_generation=0).record,
                         self.initial)

    def test_unsigned_and_wrong_signer_are_rejected(self):
        for signer in [None, 1]:
            with self.subTest(signer=signer), self.assertRaises(CoordinationError):
                self.read(self.commit(self.record, self.parent, signer=signer))

    def test_tampered_signed_tree_is_rejected(self):
        other = self.commit(dict(self.record, epoch='d' * 32), self.parent)
        tree = self.git('cat-file', '-p', other).splitlines()[0]
        raw = self.git('cat-file', 'commit', self.commit_id)
        raw = tree + b'\n' + raw.split(b'\n', 1)[1]
        tampered = self.git('hash-object', '-t', 'commit', '-w', '--stdin', data=raw).decode().strip()
        with self.assertRaises(CoordinationError):
            self.read(tampered, expected_epoch='d' * 32)

    def test_stale_or_mismatched_trusted_anchors_are_rejected(self):
        for name, value in [('expected_parent', 'b' * 40), ('expected_generation', 2),
                            ('expected_epoch', 'd' * 32), ('expected_cutover_digest', 'd' * 64),
                            ('expected_generation', True), ('expected_parent', None)]:
            with self.subTest(name=name, value=value), self.assertRaises(CoordinationError):
                self.read(**{name: value})

    def test_wrong_repository_and_target_are_rejected(self):
        for name, value in [('repository_id', 101), ('target_ref', 'refs/heads/other')]:
            with self.subTest(name=name), self.assertRaises(CoordinationError):
                self.read(self.commit(dict(self.record, **{name: value}), self.parent))

    def test_root_or_multiple_parents_cannot_substitute_for_successor(self):
        for parents in [(), (self.parent, self.commit_id)]:
            with self.subTest(parents=parents), self.assertRaises(CoordinationError):
                self.read(self.commit(self.record, *parents))

    def test_grafts_and_replacements_cannot_disguise_raw_parent(self):
        other = self.commit(self.record)
        (self.source / '.git/info/grafts').write_text(f'{other} {self.parent}\n')
        with self.assertRaises(CoordinationError):
            self.read(other)
        self.git('replace', other, self.commit_id)
        with self.assertRaises(CoordinationError):
            self.read(other)

    def test_tree_must_contain_only_one_regular_state_file(self):
        blob = self.git('hash-object', '-w', '--stdin', data=json.dumps(self.record).encode()).decode().strip()
        entries = [f'100755 blob {blob}\tcoordination.json\n',
                   f'120000 blob {blob}\tcoordination.json\n',
                   f'100644 blob {blob}\tother.json\n',
                   f'100644 blob {blob}\tcoordination.json\n100644 blob {blob}\textra\n']
        for entry in entries:
            with self.subTest(entry=entry), self.assertRaises(CoordinationError):
                self.read(self.commit(self.record, self.parent, entries=entry))

    def test_strict_json_and_schema(self):
        raw = json.dumps(self.record).encode()
        for value in [raw.replace(b'"version": 1', b'"version": 1, "version": 1'),
                      raw.replace(b'"generation": 1', b'"generation": NaN'),
                      raw.replace(b'"generation": 1', b'"generation": 1e999'),
                      raw.replace(b'"generation": 1', b'"generation": true'),
                      raw[:-1] + b', "extra": null}', b'[]', b'{', b'\xff']:
            with self.subTest(raw=value), self.assertRaises(CoordinationError):
                self.read(self.commit(None, self.parent, raw=value))

    def test_oversized_blob_and_commit_are_rejected(self):
        raw = json.dumps(self.record).encode()
        with self.assertRaises(CoordinationError):
            self.read(self.commit(None, self.parent, raw=raw + b' ' * (65537 - len(raw))))
        with self.assertRaises(CoordinationError):
            self.read(self.commit(self.record, self.parent, message='x' * 65537 + '\n\n' + SIGNOFF + '\n'))

    def test_exact_blob_size_limit_is_accepted(self):
        raw = json.dumps(self.record).encode()
        commit = self.commit(None, self.parent, raw=raw + b' ' * (65536 - len(raw)))
        self.assertEqual(self.read(commit).record, self.record)

    def test_raw_object_bytes_preserve_json_line_endings(self):
        raw = json.dumps(self.record, indent=2).replace('\n', '\r\n').encode()
        commit = self.commit(None, self.parent, raw=raw)
        self.assertEqual(self.read(commit).record, self.record)

    def test_missing_key_and_inexact_signoff_are_rejected(self):
        with tempfile.TemporaryDirectory(dir=self.root) as empty, self.assertRaises(CoordinationError):
            self.read(keyring=empty)
        with self.assertRaises(CoordinationError):
            self.read(self.commit(self.record, self.parent, message='State\n\n' + SIGNOFF + ' \n'))

    def operation(self):
        return {'id': '1' * 32, 'nonce': '1' * 32, 'holder_fingerprint': self.policy.fingerprint,
                'workflow_id': 200, 'launcher': self.policy.launcher, 'admission': {
                    'repository_id': 100, 'pr_number': 7, 'head_repository_id': 101,
                    'base': '1' * 40, 'head': '2' * 40, 'merge': '3' * 40,
                    'signed_merge': '4' * 40, 'tree': '5' * 40, 'payload_sha256': '6' * 64,
                    'source': {'tree': '7' * 40, 'sha256': '8' * 64, 'bytes': 4096},
                    'approver_id': 10, 'upstream_commit': self.policy.upstream_commit}}

    def test_live_record_uses_shared_operation_and_grant_schema(self):
        operation = self.operation()
        state = dict(self.record, state='reserved', operation=operation, completion=None)
        self.assertEqual(self.read(self.commit(state, self.parent)).record, state)
        invalid = copy.deepcopy(state)
        invalid['operation']['admission']['base'] = 'not-an-object'
        with self.assertRaises(CoordinationError):
            self.read(self.commit(invalid, self.parent))
        identity = {'schema_version': 1, 'admission': operation['admission'], 'execution': {
            'workflow_id': 200, 'run_id': 500, 'run_number': 12, 'run_attempt': 1,
            'launcher': self.policy.launcher, 'nonce': operation['nonce']}}
        qid = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        state.update(state='granted', grant={
            'run_id': 500, 'run_number': 12, 'run_attempt': 1, 'qualification_id': qid,
            'native_job_id': None, 'worker_nonce': None, 'finalizer_nonce': None})
        self.assertEqual(self.read(self.commit(state, self.parent)).record, state)
        state['grant']['qualification_id'] = '9' * 64
        with self.assertRaises(CoordinationError):
            self.read(self.commit(state, self.parent))

    def worker(self, *, run_id=500, job_id=700):
        directory = self.source / 'worker'
        directory.mkdir(mode=0o700, exist_ok=True)
        return WorkerClaim(directory, self.policy, run_id=run_id, job_id=job_id)

    def granted(self):
        operation = self.operation()
        identity = {'schema_version': 1, 'admission': operation['admission'], 'execution': {
            'workflow_id': 200, 'run_id': 500, 'run_number': 12, 'run_attempt': 1,
            'launcher': self.policy.launcher, 'nonce': operation['nonce']}}
        qid = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        state = dict(self.record, state='granted', operation=operation, completion=None, grant={
            'run_id': 500, 'run_number': 12, 'run_attempt': 1, 'qualification_id': qid,
            'native_job_id': None, 'worker_nonce': None, 'finalizer_nonce': None})
        return self.commit(state, self.parent), state

    def running(self, worker):
        parent, state = self.granted()
        state.update(state='running', generation=2, grant=dict(
            state['grant'], native_job_id=700, worker_nonce=worker.nonce))
        commit = self.commit(state, parent)
        return self.read(commit, expected_parent=parent, expected_generation=2)

    def test_selected_signed_worker_claim_can_be_accepted_only_once(self):
        worker = self.worker()
        signed = self.running(worker)
        self.assertEqual(worker.accept(signed), signed.commit)
        with self.assertRaises(WorkerClaimError):
            worker.accept(signed)
        files = list((self.source / 'worker').iterdir())
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].read_text(), signed.commit + '\n')

    def test_restarted_or_wrong_run_job_cannot_adopt_a_signed_claim(self):
        worker = self.worker()
        signed = self.running(worker)
        for other in [self.worker(), self.worker(run_id=501), self.worker(job_id=701)]:
            self.assertNotEqual(other.nonce, worker.nonce)
            with self.assertRaises(WorkerClaimError):
                other.accept(signed)
        self.assertEqual(list((self.source / 'worker').iterdir()), [])
        self.assertEqual(worker.accept(signed), signed.commit)

    def test_run_and_job_are_checked_even_when_nonce_matches(self):
        for run_id, job_id in [(501, 700), (500, 701)]:
            worker = self.worker(run_id=run_id, job_id=job_id)
            signed = self.running(worker)
            with self.subTest(run_id=run_id, job_id=job_id), self.assertRaises(WorkerClaimError):
                worker.accept(signed)
        self.assertEqual(list((self.source / 'worker').iterdir()), [])

    def test_unclaimed_and_finalizing_states_are_not_worker_claims(self):
        worker = self.worker()
        parent, _ = self.granted()
        with self.assertRaises(WorkerClaimError):
            worker.accept(self.read(parent))
        running = self.running(worker)
        record = copy.deepcopy(running.record)
        record.update(state='finalizing', generation=3)
        record['grant']['finalizer_nonce'] = '6' * 32
        finalizing = self.commit(record, running.commit)
        with self.assertRaises(WorkerClaimError):
            worker.accept(self.read(finalizing, expected_parent=running.commit, expected_generation=3))
        self.assertEqual(list((self.source / 'worker').iterdir()), [])

    def test_duplicate_acceptance_calls_have_one_winner(self):
        worker = self.worker()
        signed = self.running(worker)
        barrier = threading.Barrier(2)

        def accept():
            barrier.wait(timeout=5)
            try:
                return worker.accept(signed)
            except WorkerClaimError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: accept(), range(2)))
        self.assertCountEqual(results, [signed.commit, None])

    def test_marker_write_failure_does_not_allow_retry(self):
        for behavior in [{'side_effect': OSError('fixture disk failure')}, {'return_value': 0}]:
            with self.subTest(behavior=behavior), tempfile.TemporaryDirectory(dir=self.source) as directory:
                worker = WorkerClaim(directory, self.policy, run_id=500, job_id=700)
                signed = self.running(worker)
                with patch('ci.native.worker_claim.os.write', **behavior), self.assertRaises(WorkerClaimError):
                    worker.accept(signed)
                with self.assertRaises(WorkerClaimError):
                    worker.accept(signed)
                self.assertEqual(len(list(Path(directory).iterdir())), 1)

    def test_group_writable_workspace_is_refused(self):
        worker = self.worker()
        signed = self.running(worker)
        (self.source / 'worker').chmod(0o770)
        with self.assertRaises(WorkerClaimError):
            worker.accept(signed)
        self.assertEqual(list((self.source / 'worker').iterdir()), [])

    def test_signed_cas_picks_one_worker_nonce(self):
        parent, state = self.granted()
        workers = [self.worker(), self.worker()]
        commits = []
        for worker in workers:
            running = dict(state, state='running', generation=2, grant=dict(
                state['grant'], native_job_id=700, worker_nonce=worker.nonce))
            commits.append(self.commit(running, parent))
        remote = self.source / 'remote.git'
        self.git('init', '--bare', '-q', str(remote))
        ref = 'refs/heads/claim-probe'
        self.git('push', '-q', str(remote), f'{parent}:{ref}')
        barrier = threading.Barrier(2)

        def claim(commit):
            barrier.wait(timeout=5)
            return subprocess.run(['git', 'push', '--porcelain', f'--force-with-lease={ref}:{parent}',
                                   str(remote), f'{commit}:{ref}'], cwd=self.source, env=self.env,
                                  capture_output=True, timeout=30, check=False).returncode

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, commits))
        self.assertEqual(results.count(0), 1)
        winner = results.index(0)
        actual = self.git('--git-dir=' + str(remote), 'rev-parse', ref).decode().strip()
        self.assertEqual(actual, commits[winner])
        signed = self.read(actual, expected_parent=parent, expected_generation=2)
        with self.assertRaises(WorkerClaimError):
            workers[1 - winner].accept(signed)
        self.assertEqual(workers[winner].accept(signed), actual)

    def test_missing_blob_cannot_trigger_a_lazy_fetch(self):
        marker = self.source / 'remote-was-run'
        helper = self.source / 'git-remote-probe'
        helper.write_text(f'#!/bin/sh\ntouch "{marker}"\nexit 1\n')
        helper.chmod(0o700)
        self.git('config', 'extensions.partialClone', 'origin')
        self.git('config', 'remote.origin.url', 'probe::fixture')
        self.git('config', 'remote.origin.promisor', 'true')
        tree = self.git('mktree', '--missing', data=b'100644 blob ' + b'9' * 40
                        + b'\tcoordination.json\n').decode().strip()
        commit = self.git('commit-tree', tree, '-p', self.parent, '-S' + self.fingerprints[0],
                          data=('State\n\n' + SIGNOFF + '\n').encode()).decode().strip()
        with patch.dict(os.environ, {'PATH': str(self.source) + os.pathsep + os.environ['PATH']}), \
                self.assertRaises(CoordinationError):
            self.read(commit)
        self.assertFalse(marker.exists())

    def test_reader_does_not_change_refs_or_use_a_configured_verifier(self):
        fake = self.source / 'fake-gpg'
        marker = self.source / 'was-run'
        fake.write_text(f'#!/bin/sh\ntouch "{marker}"\nexit 0\n')
        fake.chmod(0o700)
        self.git('config', 'gpg.openpgp.program', str(fake))
        before = self.git('for-each-ref')
        with patch.dict(os.environ, {'GIT_DIR': '/does-not-exist'}):
            self.read()
        self.assertFalse(marker.exists())
        self.assertEqual(self.git('for-each-ref'), before)


if __name__ == '__main__':
    unittest.main()
