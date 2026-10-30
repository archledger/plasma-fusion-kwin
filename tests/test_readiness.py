# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
import dataclasses
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from ci.native.current_policy import ReadinessSnapshot
from ci.native.merge_update import MergeCommit
from ci.native.qualification import (
    ProducerAttestedInputs,
    QualificationError,
    QualificationPolicy,
    QualifiedEvidence,
)
from ci.native.readiness import load_policy, read_regular, verify_readiness
from ci.native.signed_merge import SignatureError, SignedMerge
from tools.verify_native_proof import (
    DeadlineExceeded,
    VerificationInterrupted,
    handled_interruptions,
    main,
    run_isolated,
)


class ReadinessTest(unittest.TestCase):
    def setUp(self):
        self.policy = QualificationPolicy('owner/downstream', 100, 200, 'a' * 40, 'A' * 40,
                                          ('fixture-package',), app_id=300)
        self.signed = SignedMerge('d' * 40, 'e' * 40, 'A' * 40, 'b' * 40, 'c' * 40)
        attested = ProducerAttestedInputs(7, 101, 'f' * 40, '1' * 64, '2' * 40,
                                          '3' * 64, 4096, 10, self.policy.upstream_commit)
        self.evidence = QualifiedEvidence('4' * 64, 500, self.signed, attested, {'qt_test_passed': 43},
                                          check_run_id=800, run_number=12)
        self.snapshot = ReadinessSnapshot('4' * 64, (800,), 801, (900,))
        self.metadata = {'id': 1000, 'expired': False, 'workflow_run': {
            'repository_id': 100, 'head_repository_id': 100, 'head_sha': 'a' * 40}}
        self.api = unittest.mock.Mock(repository='owner/downstream')
        self.api.get.return_value = self.metadata
        self.receipt = {'identity': {'admission': {'base': 'b' * 40, 'head': 'c' * 40,
                                                  'signed_merge': 'd' * 40, 'tree': 'e' * 40}}}

    def call(self):
        return verify_readiness(self.api, self.policy, source=Path('/trusted/source'),
                                keyring=Path('/trusted/public-keyring'), signoff='Signed-off-by: Fixture',
                                artifact_id=1000, archive=b'fixture', admission_archive=b'admission')

    def test_composition_uses_trusted_signature_policy_and_never_authorizes_merge(self):
        with patch('ci.native.readiness.read_archive', return_value={'qualification.json': self.receipt}), \
                patch('ci.native.readiness.verify_signed_merge', return_value=self.signed) as signer, \
                patch('ci.native.readiness.verify_qualification', return_value=self.evidence) as native, \
                patch('ci.native.readiness.verify_current_policy', return_value=self.snapshot):
            result = self.call()
        self.assertTrue(result['ok'])
        self.assertFalse(result['merge_authorized'])
        self.assertEqual(result['mode'], 'read-only')
        signer.assert_called_once_with(Path('/trusted/source'),
            MergeCommit('b' * 40, 'c' * 40, 'd' * 40, 'e' * 40), keyring=Path('/trusted/public-keyring'),
            fingerprint='A' * 40, signoff='Signed-off-by: Fixture')
        self.assertEqual(native.call_args.kwargs['admission_archive'], b'admission')
        self.assertEqual(result['qualification_id'], '4' * 64)

    def test_bad_artifact_provenance_stops_before_signature_work(self):
        self.metadata['workflow_run']['repository_id'] = 101
        with patch('ci.native.readiness.verify_signed_merge') as signer, \
                self.assertRaises(QualificationError):
            self.call()
        signer.assert_not_called()

    def test_failed_signature_stops_before_native_or_policy_acceptance(self):
        with patch('ci.native.readiness.read_archive', return_value={'qualification.json': self.receipt}), \
                patch('ci.native.readiness.verify_signed_merge', side_effect=SignatureError('bad signature')), \
                patch('ci.native.readiness.verify_qualification') as native, \
                self.assertRaises(QualificationError):
            self.call()
        native.assert_not_called()

    def test_current_policy_refusal_is_not_converted_to_success(self):
        with patch('ci.native.readiness.read_archive', return_value={'qualification.json': self.receipt}), \
                patch('ci.native.readiness.verify_signed_merge', return_value=self.signed), \
                patch('ci.native.readiness.verify_qualification', return_value=self.evidence), \
                patch('ci.native.readiness.verify_current_policy', side_effect=QualificationError('not current')), \
                self.assertRaises(QualificationError):
            self.call()

    def test_result_from_another_qualification_is_not_combined(self):
        other = ReadinessSnapshot('9' * 64, (800,), 801, (900,))
        with patch('ci.native.readiness.read_archive', return_value={'qualification.json': self.receipt}), \
                patch('ci.native.readiness.verify_signed_merge', return_value=self.signed), \
                patch('ci.native.readiness.verify_qualification', return_value=self.evidence), \
                patch('ci.native.readiness.verify_current_policy', return_value=other), \
                self.assertRaises(QualificationError):
            self.call()

    def test_policy_requires_explicit_fields_and_no_unknown_settings(self):
        data = dataclasses.asdict(self.policy) | {'signoff': 'Signed-off-by: Fixture'}
        policy, signoff = load_policy(json.dumps(data).encode())
        self.assertEqual(policy, self.policy)
        self.assertEqual(signoff, data['signoff'])
        for invalid in [data | {'unknown': True}, data | {'repository_id': True}, {'repository': 'only'}]:
            with self.subTest(invalid=invalid), self.assertRaises(QualificationError):
                load_policy(json.dumps(invalid).encode())

    def test_input_files_are_regular_bounded_and_not_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = root / 'input'
            value.write_bytes(b'valid')
            self.assertEqual(read_regular(value, 5), b'valid')
            with self.assertRaises(QualificationError):
                read_regular(value, 4)
            linked = root / 'linked'
            linked.symlink_to(value)
            with self.assertRaises(QualificationError):
                read_regular(linked, 100)
            fifo = root / 'fifo'
            os.mkfifo(fifo)
            with self.assertRaises(QualificationError):
                read_regular(fifo, 100)


class DeadlineTest(unittest.TestCase):
    def test_completed_process_output_is_returned(self):
        result = run_isolated([sys.executable, '-c', 'print("fixture")'], env={}, timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b'fixture\n')

    def test_deadline_kills_and_reaps_the_isolated_process(self):
        with self.assertRaises(DeadlineExceeded) as caught:
            run_isolated([sys.executable, '-c', 'import signal; signal.pause()'], env={}, timeout=0.2)
        with self.assertRaises(ProcessLookupError):
            os.kill(caught.exception.process_id, 0)

    def test_interruption_also_cleans_up_the_owned_process_group(self):
        process = unittest.mock.Mock(pid=4242)
        process.communicate.side_effect = [KeyboardInterrupt(), (b'', b'')]
        with patch('tools.verify_native_proof.subprocess.Popen', return_value=process) as spawn, \
                patch('tools.verify_native_proof.os.killpg') as kill, self.assertRaises(KeyboardInterrupt):
            run_isolated(['fixture'], env={}, timeout=1)
        kill.assert_called_once_with(4242, signal.SIGKILL)
        self.assertEqual(process.communicate.call_count, 2)
        self.assertTrue(spawn.call_args.kwargs['start_new_session'])
        self.assertEqual(spawn.call_args.kwargs['stdin'], subprocess.DEVNULL)

    def test_signal_handlers_raise_for_cleanup_and_are_restored(self):
        previous = {number: signal.getsignal(number)
                    for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
        with self.assertRaises(VerificationInterrupted), handled_interruptions():
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        self.assertEqual({number: signal.getsignal(number) for number in previous}, previous)

    def test_cli_invalid_policy_fails_without_network_or_merge_authorization(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bad = root / 'bad.json'
            bad.write_text('{"unexpected":true}')
            script = Path(__file__).resolve().parents[1] / 'tools/verify_native_proof.py'
            result = subprocess.run([sys.executable, str(script), '--policy', str(bad),
                                     '--source', str(root), '--keyring', str(root),
                                     '--artifact-id', '1', '--artifact', str(bad),
                                     '--admission', str(bad), '--timeout', '5'],
                                    capture_output=True, text=True, timeout=10, check=False)
        self.assertNotEqual(result.returncode, 0)
        value = json.loads(result.stdout)
        self.assertFalse(value['ok'])
        self.assertFalse(value['merge_authorized'])

    def test_cli_does_not_forward_app_keys_or_python_path(self):
        captured = {}
        execution = {}

        def child(command, *, env, timeout):
            captured.update(env)
            execution.update(command=command, timeout=timeout)
            value = {'ok': False, 'mode': 'read-only', 'merge_authorized': False, 'error': 'fixture'}
            return subprocess.CompletedProcess(command, 1, json.dumps(value).encode(), b'')

        arguments = ['--policy', 'policy', '--source', 'source', '--keyring', 'keyring',
                     '--artifact', 'artifact', '--admission', 'admission', '--artifact-id', '1']
        with patch.dict(os.environ, {'GH_TOKEN': 'fixture-read-token',
                                     'NATIVE_GATE_PRIVATE_KEY': 'not-a-real-key',
                                     'PYTHONPATH': '/untrusted/fixture'}), \
                patch('tools.verify_native_proof.run_isolated', side_effect=child), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(main(arguments), 1)
        self.assertEqual(captured['GH_TOKEN'], 'fixture-read-token')
        self.assertNotIn('NATIVE_GATE_PRIVATE_KEY', captured)
        self.assertNotIn('PYTHONPATH', captured)
        self.assertEqual(execution['command'][:2], [sys.executable, '-I'])
        self.assertIn('-B', execution['command'])
        self.assertEqual(execution['timeout'], 120)


if __name__ == '__main__':
    unittest.main()
