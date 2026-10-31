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
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import test_current_policy as policy_fixtures
import test_signed_coordination as signed_fixtures

from ci.native import merge_update, readiness
from ci.native.current_policy import ReadinessSnapshot
from ci.native.merge_update import MergeCommit
from ci.native.qualification import (
    ProducerAttestedInputs,
    QualificationError,
    QualificationPolicy,
    QualifiedEvidence,
    QualifiedEvidenceV2,
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
        self.policy = QualificationPolicy(
            'owner/downstream',
            100,
            200,
            'a' * 40,
            'A' * 40,
            ('fixture-package',),
            app_id=300,
        )
        self.signed = SignedMerge('d' * 40, 'e' * 40, 'A' * 40, 'b' * 40, 'c' * 40)
        attested = ProducerAttestedInputs(
            7,
            101,
            'f' * 40,
            '1' * 64,
            '2' * 40,
            '3' * 64,
            4096,
            10,
            self.policy.upstream_commit,
        )
        self.evidence = QualifiedEvidenceV2(
            '4' * 64,
            500,
            self.signed,
            attested,
            {'qt_test_passed': 43},
            check_run_id=800,
            run_number=12,
            activation_check_id=801,
            operation_nonce='6' * 32,
            native_job_id=702,
        )
        self.evidence, self.coordination = policy_fixtures.running_state(
            self.policy, self.evidence
        )
        self.anchor = {
            'commit': self.coordination.commit,
            'parent': self.coordination.parent,
            'generation': self.coordination.record['generation'],
            'epoch': self.coordination.record['epoch'],
            'cutover_digest': self.coordination.record['cutover_digest'],
        }
        anchor_read = patch(
            'ci.native.readiness.verify_coordination_anchor',
            return_value=self.coordination,
        )
        self.anchor_read = anchor_read.start()
        self.addCleanup(anchor_read.stop)
        self.snapshot = ReadinessSnapshot(
            self.evidence.qualification_id, (800,), 801, (900,)
        )
        self.metadata = {
            'id': 1000,
            'expired': False,
            'workflow_run': {
                'repository_id': 100,
                'head_repository_id': 100,
                'head_sha': 'a' * 40,
            },
        }
        self.api = unittest.mock.Mock(repository='owner/downstream')
        self.api.get.return_value = self.metadata
        self.receipt = {
            'identity': {
                'admission': {
                    'base': 'b' * 40,
                    'head': 'c' * 40,
                    'signed_merge': 'd' * 40,
                    'tree': 'e' * 40,
                }
            }
        }

    def call(self):
        return verify_readiness(
            self.api,
            self.policy,
            source=Path('/trusted/source'),
            keyring=Path('/trusted/public-keyring'),
            signoff='Signed-off-by: Fixture',
            artifact_id=1000,
            archive=b'fixture',
            admission_archive=b'admission',
            coordination_anchor=self.anchor,
        )

    def test_composition_uses_trusted_signature_policy_and_never_authorizes_merge(self):
        with (
            patch(
                'ci.native.readiness.read_archive',
                return_value={'qualification.json': self.receipt},
            ),
            patch(
                'ci.native.readiness.verify_signed_merge', return_value=self.signed
            ) as signer,
            patch(
                'ci.native.readiness.verify_qualification_v2',
                return_value=self.evidence,
            ) as native,
            patch(
                'ci.native.readiness.verify_current_policy', return_value=self.snapshot
            ),
        ):
            result = self.call()
        self.assertTrue(result['ok'])
        self.assertFalse(result['merge_authorized'])
        self.assertEqual(result['mode'], 'read-only')
        signer.assert_called_once_with(
            Path('/trusted/source'),
            MergeCommit('b' * 40, 'c' * 40, 'd' * 40, 'e' * 40),
            keyring=Path('/trusted/public-keyring'),
            fingerprint='A' * 40,
            signoff='Signed-off-by: Fixture',
        )
        self.assertEqual(native.call_args.kwargs['admission_archive'], b'admission')
        self.assertEqual(result['qualification_id'], self.evidence.qualification_id)
        self.assertEqual(result['coordination_sha'], self.coordination.commit)
        self.assertEqual(self.anchor_read.call_count, 2)

    def test_bad_artifact_provenance_stops_before_signature_work(self):
        self.metadata['workflow_run']['repository_id'] = 101
        with patch('ci.native.readiness.verify_signed_merge') as signer, \
                self.assertRaises(QualificationError):
            self.call()
        signer.assert_not_called()

    def test_failed_signature_stops_before_native_or_policy_acceptance(self):
        with (
            patch(
                'ci.native.readiness.read_archive',
                return_value={'qualification.json': self.receipt},
            ),
            patch(
                'ci.native.readiness.verify_signed_merge',
                side_effect=SignatureError('bad signature'),
            ),
            patch('ci.native.readiness.verify_qualification_v2') as native,
            self.assertRaises(QualificationError),
        ):
            self.call()
        native.assert_not_called()

    def test_current_policy_refusal_is_not_converted_to_success(self):
        with (
            patch(
                'ci.native.readiness.read_archive',
                return_value={'qualification.json': self.receipt},
            ),
            patch('ci.native.readiness.verify_signed_merge', return_value=self.signed),
            patch(
                'ci.native.readiness.verify_qualification_v2',
                return_value=self.evidence,
            ),
            patch(
                'ci.native.readiness.verify_current_policy',
                side_effect=QualificationError('not current'),
            ),
            self.assertRaises(QualificationError),
        ):
            self.call()

    def test_result_from_another_qualification_is_not_combined(self):
        other = ReadinessSnapshot('9' * 64, (800,), 801, (900,))
        with (
            patch(
                'ci.native.readiness.read_archive',
                return_value={'qualification.json': self.receipt},
            ),
            patch('ci.native.readiness.verify_signed_merge', return_value=self.signed),
            patch(
                'ci.native.readiness.verify_qualification_v2',
                return_value=self.evidence,
            ),
            patch('ci.native.readiness.verify_current_policy', return_value=other),
            self.assertRaises(QualificationError),
        ):
            self.call()

    def test_changed_authority_after_observations_cannot_return_readiness(self):
        self.anchor_read.side_effect = [
            self.coordination,
            QualificationError('changed authority'),
        ]
        with (
            patch(
                'ci.native.readiness.read_archive',
                return_value={'qualification.json': self.receipt},
            ),
            patch('ci.native.readiness.verify_signed_merge', return_value=self.signed),
            patch(
                'ci.native.readiness.verify_qualification_v2',
                return_value=self.evidence,
                create=True,
            ),
            patch(
                'ci.native.readiness.verify_current_policy', return_value=self.snapshot
            ),
            self.assertRaises(QualificationError),
        ):
            self.call()
        self.assertEqual(self.anchor_read.call_count, 2)

    def test_readiness_cannot_combine_a_different_activation_id(self):
        wrong = ReadinessSnapshot(self.evidence.qualification_id, (800,), 802, (900,))
        with (
            patch(
                'ci.native.readiness.read_archive',
                return_value={'qualification.json': self.receipt},
            ),
            patch('ci.native.readiness.verify_signed_merge', return_value=self.signed),
            patch(
                'ci.native.readiness.verify_qualification_v2',
                return_value=self.evidence,
                create=True,
            ),
            patch('ci.native.readiness.verify_current_policy', return_value=wrong),
            self.assertRaises(QualificationError),
        ):
            self.call()

    def test_legacy_evidence_cannot_escape_through_a_mocked_policy_layer(self):
        value = self.evidence
        legacy = QualifiedEvidence(
            value.qualification_id,
            value.run_id,
            value.signed_merge,
            value.producer_attestation,
            value.tests,
            value.check_run_id,
            value.run_number,
        )
        with (
            patch(
                'ci.native.readiness.read_archive',
                return_value={'qualification.json': self.receipt},
            ),
            patch('ci.native.readiness.verify_signed_merge', return_value=self.signed),
            patch('ci.native.readiness.verify_qualification_v2', return_value=legacy),
            patch(
                'ci.native.readiness.verify_current_policy', return_value=self.snapshot
            ),
            self.assertRaises(QualificationError),
        ):
            self.call()

    def test_invalid_anchor_file_is_rejected_before_api_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = root / 'policy.json'
            policy.write_text(
                json.dumps(
                    dataclasses.asdict(self.policy)
                    | {'signoff': 'Signed-off-by: Fixture'}
                )
            )
            anchor = root / 'anchor.json'
            anchor.write_text('{"unexpected":true}')
            with (
                patch('ci.native.readiness.ProofAPI') as api,
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(
                    readiness._worker(
                        {'policy': str(policy), 'coordination_anchor': str(anchor)}
                    ),
                    1,
                )
            api.assert_not_called()

    def test_worker_passes_the_parsed_anchor_to_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy, anchor, artifact = (
                root / 'policy',
                root / 'anchor',
                root / 'artifact',
            )
            policy.write_text(
                json.dumps(
                    dataclasses.asdict(self.policy)
                    | {'signoff': 'Signed-off-by: Fixture'}
                )
            )
            anchor.write_text(json.dumps(self.anchor))
            artifact.write_bytes(b'fixture')
            options = {
                'policy': str(policy),
                'coordination_anchor': str(anchor),
                'source': directory,
                'keyring': directory,
                'artifact_id': 1,
                'artifact': str(artifact),
                'admission': str(artifact),
            }
            with (
                patch(
                    'ci.native.readiness.verify_readiness', return_value={'ok': True}
                ) as verify,
                patch('ci.native.readiness.ProofAPI'),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(readiness._worker(options), 0)
            self.assertEqual(
                verify.call_args.kwargs['coordination_anchor'], self.anchor
            )

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


class AnchorShapeTest(unittest.TestCase):
    def setUp(self):
        self.anchor = {
            'commit': 'a' * 40,
            'parent': 'b' * 40,
            'generation': 1,
            'epoch': 'c' * 32,
            'cutover_digest': 'd' * 64,
        }

    def load(self, value):
        return readiness.load_coordination_anchor(json.dumps(value).encode())

    def test_exact_anchor_and_explicit_bootstrap_round_trip(self):
        self.assertEqual(self.load(self.anchor), self.anchor)
        bootstrap = dict(self.anchor, parent=None, generation=0)
        self.assertEqual(self.load(bootstrap), bootstrap)

    def test_extra_missing_or_invalid_fields_are_not_trusted(self):
        invalid = [None, [], {}, dict(self.anchor, endpoint='https://invalid.test')]
        invalid += [
            {key: value for key, value in self.anchor.items() if key != field}
            for field in self.anchor
        ]
        invalid += [
            dict(self.anchor, **{field: value})
            for field, value in [
                ('commit', '0' * 40),
                ('commit', 'bad'),
                ('generation', True),
                ('generation', 1.0),
                ('generation', -1),
                ('generation', 2**63),
                ('parent', None),
                ('parent', 'bad'),
                ('epoch', '0' * 32),
                ('cutover_digest', '0' * 64),
                ('generation', 0),
            ]
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(QualificationError):
                self.load(value)

    def test_json_and_size_limits_are_preserved(self):
        raw = json.dumps(self.anchor).encode()
        maximum = readiness.MAX_ANCHOR_BYTES
        self.assertEqual(
            readiness.load_coordination_anchor(raw + b' ' * (maximum - len(raw))),
            self.anchor,
        )
        for value in [
            raw + b' ' * (maximum + 1 - len(raw)),
            raw.decode(),
            b'\xff',
            b'[]',
            raw.replace(b'"generation": 1', b'"generation": 1, "generation": 1'),
            raw.replace(b'"generation": 1', b'"generation": NaN'),
        ]:
            with (
                self.subTest(value=repr(value)[:60]),
                self.assertRaises(QualificationError),
            ):
                readiness.load_coordination_anchor(value)


class AnchorSignatureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class Fixture(signed_fixtures.SignedCoordinationTest):
            pass

        cls.fixture_type = Fixture
        Fixture.setUpClass()
        cls.addClassCleanup(Fixture.doClassCleanups)

    def setUp(self):
        self.fixture = self.fixture_type()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.anchor = {
            'commit': self.fixture.commit_id,
            'parent': self.fixture.parent,
            'generation': 1,
            'epoch': 'e' * 32,
            'cutover_digest': 'f' * 64,
        }
        self.api = unittest.mock.Mock(repository=self.fixture.policy.repository)
        self.api.get.return_value = {
            'ref': 'refs/heads/native-gate-coordination',
            'object': {'type': 'commit', 'sha': self.anchor['commit']},
        }

    def verify(self, **kwargs):
        options = {
            'source': self.fixture.source,
            'keyring': self.fixture.public,
            'signoff': signed_fixtures.SIGNOFF,
            'anchor': self.anchor,
        }
        options.update(kwargs)
        return readiness.verify_coordination_anchor(
            self.api, self.fixture.policy, **options
        )

    def test_real_signature_and_exact_remote_tip_are_both_required(self):
        raw = json.dumps(self.anchor).encode()
        path = self.fixture.source / 'anchor.json'
        path.write_bytes(raw)
        anchor = readiness.load_coordination_anchor(
            read_regular(path, readiness.MAX_ANCHOR_BYTES)
        )
        result = self.verify(anchor=anchor)
        self.assertEqual(result.commit, self.anchor['commit'])
        self.assertEqual(result.record, self.fixture.record)
        self.api.get.assert_called_once_with('git/ref/heads/native-gate-coordination')

    def test_changed_remote_sha_ref_or_type_is_refused(self):
        for value in [
            {
                'ref': 'refs/heads/native-gate-coordination',
                'object': {'type': 'commit', 'sha': 'a' * 40},
            },
            {
                'ref': 'refs/heads/other',
                'object': {'type': 'commit', 'sha': self.anchor['commit']},
            },
            {
                'ref': 'refs/heads/native-gate-coordination',
                'object': {'type': 'tag', 'sha': self.anchor['commit']},
            },
        ]:
            self.api.get.return_value = value
            with self.subTest(value=value), self.assertRaises(QualificationError):
                self.verify()

    def test_malformed_remote_response_never_returns_verified_state(self):
        for value in [
            None,
            [],
            {},
            {'ref': 'refs/heads/native-gate-coordination', 'object': []},
        ]:
            self.api.get.return_value = value
            with self.subTest(value=value), self.assertRaises(QualificationError):
                self.verify()

    def test_direct_invalid_anchor_is_refused_before_signature_or_network(self):
        for value in [
            dict(self.anchor, generation=True),
            dict(self.anchor, url='https://invalid.test'),
        ]:
            with (
                patch.object(readiness, 'read_signed_coordination') as read,
                self.assertRaises(QualificationError),
            ):
                self.verify(anchor=value)
            read.assert_not_called()
        self.api.get.assert_not_called()

    def test_unavailable_local_object_is_not_fetched(self):
        original = merge_update.git
        with (
            patch.object(merge_update, 'git', wraps=original) as calls,
            self.assertRaises(QualificationError),
        ):
            self.verify(anchor=dict(self.anchor, commit='a' * 40))
        self.assertTrue(calls.call_args_list)
        self.assertTrue(
            all(call.args[1] == 'cat-file' for call in calls.call_args_list)
        )
        self.api.get.assert_not_called()

    def test_wrong_signer_and_stale_anchor_metadata_are_refused(self):
        commit = self.fixture.commit(self.fixture.record, self.fixture.parent, signer=1)
        values = [
            dict(self.anchor, commit=commit),
            dict(self.anchor, parent='b' * 40),
            dict(self.anchor, generation=2),
            dict(self.anchor, epoch='a' * 32),
            dict(self.anchor, cutover_digest='a' * 64),
        ]
        for anchor in values:
            with self.subTest(anchor=anchor), self.assertRaises(QualificationError):
                self.verify(anchor=anchor)
        self.api.get.assert_not_called()


class DeadlineTest(unittest.TestCase):
    def test_cli_requires_an_explicit_coordination_anchor(self):
        args = [
            '--policy',
            'policy',
            '--source',
            'source',
            '--keyring',
            'keys',
            '--artifact',
            'proof',
            '--admission',
            'admission',
            '--artifact-id',
            '1',
        ]
        with (
            patch('tools.verify_native_proof.run_isolated') as child,
            redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as error,
        ):
            main(args)
        self.assertEqual(error.exception.code, 2)
        child.assert_not_called()

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
            script = (
                Path(__file__).resolve().parents[1] / 'tools/verify_native_proof.py'
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    '--policy',
                    str(bad),
                    '--source',
                    str(root),
                    '--keyring',
                    str(root),
                    '--artifact-id',
                    '1',
                    '--artifact',
                    str(bad),
                    '--admission',
                    str(bad),
                    '--coordination-anchor',
                    str(bad),
                    '--timeout',
                    '5',
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
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
            value = {
                'ok': False,
                'mode': 'read-only',
                'merge_authorized': False,
                'error': 'fixture',
            }
            return subprocess.CompletedProcess(
                command, 1, json.dumps(value).encode(), b''
            )

        arguments = [
            '--policy',
            'policy',
            '--source',
            'source',
            '--keyring',
            'keyring',
            '--artifact',
            'artifact',
            '--admission',
            'admission',
            '--artifact-id',
            '1',
            '--coordination-anchor',
            'anchor',
        ]
        with (
            patch.dict(
                os.environ,
                {
                    'GH_TOKEN': 'fixture-read-token',
                    'NATIVE_GATE_PRIVATE_KEY': 'not-a-real-key',
                    'PYTHONPATH': '/untrusted/fixture',
                },
            ),
            patch('tools.verify_native_proof.run_isolated', side_effect=child),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(main(arguments), 1)
        self.assertEqual(captured['GH_TOKEN'], 'fixture-read-token')
        self.assertNotIn('NATIVE_GATE_PRIVATE_KEY', captured)
        self.assertNotIn('PYTHONPATH', captured)
        self.assertEqual(execution['command'][:2], [sys.executable, '-I'])
        self.assertIn('-B', execution['command'])
        self.assertEqual(execution['timeout'], 120)
        self.assertEqual(
            json.loads(execution['command'][-1])['coordination_anchor'],
            str(Path('anchor').absolute()),
        )


if __name__ == '__main__':
    unittest.main()
