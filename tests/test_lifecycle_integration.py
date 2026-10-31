# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Cross-boundary fixtures only: no live API, native run or authority write."""

import copy
import json
import subprocess
import unittest
from unittest.mock import patch

import test_signed_coordination as signed_fixtures
from lifecycle_fixture import LifecycleFixture

from ci.native import producer
from ci.native.qualification import QualificationError, verify_qualification_v2
from ci.native.transport import decode, encode


class IntegratedReadinessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class Fixture(signed_fixtures.SignedCoordinationTest):
            pass

        cls.fixture_type = Fixture
        Fixture.setUpClass()
        cls.addClassCleanup(Fixture.doClassCleanups)

    def setUp(self):
        signer = self.fixture_type()
        signer.setUp()
        self.addCleanup(signer.doCleanups)
        self.flow = LifecycleFixture(signer)

    def finish(self):
        self.flow.admit()
        self.flow.native_result()
        self.flow.report()

    def assert_native_is_blocked(self):
        with (
            self.flow.transport(),
            patch.object(producer, 'WorkerClaim') as claim,
            patch.object(producer, 'run_guest') as guest,
            self.assertRaises(QualificationError),
        ):
            producer.native_job(self.flow.ctx)
        claim.assert_not_called()
        guest.assert_not_called()

    def test_fresh_admission_serialized_proof_and_signed_readiness_join(self):
        self.finish()
        before = len(self.flow.calls)
        result = self.flow.verify()
        self.assertTrue(result['ok'])
        self.assertFalse(result['merge_authorized'])
        self.assertEqual(result['native_test_entries'], 43)
        self.assertEqual(result['coordination_sha'], self.flow.state.commit)
        self.assertEqual(result['snapshot']['activation_check_id'], 1200)
        self.assertEqual(result['snapshot']['required_check_ids'], (1201,))
        self.assertEqual(
            [w[0] for w in self.flow.writes], ['POST', 'POST', 'PATCH', 'PATCH']
        )
        self.assertLessEqual(len(self.flow.calls) - before, 64)

    def test_unpublished_admission_never_starts_native_work(self):
        self.flow.admit(publish=False)
        self.assertTrue((self.flow.work / 'admission.json').exists())
        self.assert_native_is_blocked()
        self.assertEqual([w[0] for w in self.flow.writes], ['POST', 'POST'])

    def test_lost_second_create_keeps_unknown_intent_and_no_admission(self):
        original = self.flow.send

        def lost(method, resource, body=None):
            value = original(method, resource, body)
            if method == 'POST' and len(self.flow.checks) == 2:
                raise OSError('lost fixture acknowledgment')
            return value

        with (
            patch.object(self.flow, 'send', side_effect=lost),
            self.assertRaises(ValueError),
        ):
            self.flow.admit()
        self.assertFalse((self.flow.work / 'admission.json').exists())
        journal = json.loads((self.flow.work / 'admit-journal.json').read_bytes())
        self.assertEqual(
            [w['status'] for w in journal['writes']], ['confirmed', 'pending']
        )
        self.assert_native_is_blocked()
        self.assertEqual(len(self.flow.checks), 2)

    def test_existing_operation_is_not_adopted_from_a_fresh_local_journal(self):
        self.flow.admit()
        replay = self.flow.work.parent / 'replay'
        replay.mkdir(mode=0o700)
        self.flow.work = self.flow.ctx.work = replay
        with self.assertRaises(ValueError):
            self.flow.admit()
        self.assertEqual(len(self.flow.writes), 2)
        self.assertFalse((replay / 'admission.json').exists())

    def test_changed_signed_owner_fence_blocks_reporting_before_a_write(self):
        self.flow.admit()
        self.flow.native_result()
        old = self.flow.state
        self.flow.append(
            dict(
                old.record,
                state='finalizing',
                grant=dict(old.record['grant'], finalizer_nonce='c' * 32),
            )
        )
        changed = self.flow.state
        self.flow.state = old
        observations = iter([old, changed])
        self.flow.ctx.current = lambda: next(observations, changed)
        with self.assertRaises(ValueError):
            self.flow.report()
        self.assertEqual([w[0] for w in self.flow.writes], ['POST', 'POST'])

    def test_missing_native_artifact_reports_failure_without_releasing_authority(self):
        self.flow.admit()
        self.flow.native_result()
        held = self.flow.state.commit
        del self.flow.artifacts[900]
        with self.assertRaises(ValueError):
            self.flow.report()
        self.assertEqual(self.flow.state.commit, held)
        self.assertTrue(
            all(c['conclusion'] == 'failure' for c in self.flow.checks.values())
        )
        self.assertEqual(len(self.flow.writes), 4)

    def test_success_metadata_cannot_hide_partial_completion(self):
        self.flow.admit()
        self.flow.native_result()
        original = self.flow.send
        held = self.flow.state.commit

        def lost(method, resource, body=None):
            value = original(method, resource, body)
            if method == 'PATCH':
                raise OSError('lost completion acknowledgment')
            return value

        with (
            patch.object(self.flow, 'send', side_effect=lost),
            self.assertRaises(ValueError),
        ):
            self.flow.report()
        self.assertEqual(self.flow.state.commit, held)
        self.assertEqual(sum(w[0] == 'PATCH' for w in self.flow.writes), 1)
        self.assertEqual(self.flow.run['conclusion'], 'failure')
        self.flow.run['conclusion'] = self.flow.jobs['report']['conclusion'] = 'success'
        with self.assertRaises(QualificationError):
            self.flow.verify()

    def test_missing_or_mixed_version_artifacts_do_not_become_ready(self):
        self.finish()
        for version in [None, 1, True]:
            receipt = dict(self.flow.raw.receipt)
            if version is None:
                del receipt['schema_version']
            else:
                receipt['schema_version'] = version
            self.flow.publish(
                900,
                'native-qualification-500-1',
                {
                    'qualification.json': json.dumps(receipt),
                    'report.json': json.dumps(self.flow.raw.report),
                },
                '2026-10-30T00:29:59Z',
            )
            with self.subTest(version=version), self.assertRaises(QualificationError):
                self.flow.verify()

    def test_missing_admission_metadata_rejects_retained_archive_bytes(self):
        self.finish()
        retained = self.flow.artifacts.pop(901)[1]
        with self.assertRaises(QualificationError):
            self.flow.verify(admission_archive=retained)

    def test_altered_admitted_pair_cannot_qualify_after_successful_reporting(self):
        self.finish()
        admission = copy.deepcopy(self.flow.admission)
        admission['checks']['native_check_id'] += 1000
        self.assertNotEqual(
            admission['checks']['native_check_id'],
            admission['checks']['activation_check_id'],
        )
        self.flow.publish(
            901,
            'native-admission-500-1',
            {'admission.json': json.dumps(admission)},
            '2026-10-30T00:00:50Z',
        )
        with self.assertRaisesRegex(
            QualificationError, 'independently published admission'
        ):
            self.flow.verify()

    def test_fixture_refuses_unexpected_execution_and_network_commands(self):
        with self.flow.transport():
            for command in [
                ['curl', 'https://invalid.test'],
                ['ssh', 'invalid.test'],
                ['git', 'fetch', 'https://invalid.test'],
                ['git', 'push'],
                ['bash', '-c', 'exit 0'],
                ['gh', 'api', 'user'],
                ['git', '--version'],
            ]:
                with self.subTest(command=command), self.assertRaises(AssertionError):
                    subprocess.run(command, check=False)

    def test_cleanup_failure_cannot_be_hidden_by_successful_check_records(self):
        self.finish()
        report = copy.deepcopy(self.flow.raw.report)
        report['cleanup_confirmed'] = False
        self.flow.publish(
            900,
            'native-qualification-500-1',
            {
                'qualification.json': json.dumps(self.flow.raw.receipt),
                'report.json': json.dumps(report),
            },
            '2026-10-30T00:29:59Z',
        )
        with self.assertRaisesRegex(QualificationError, 'cleanup failed'):
            self.flow.verify()

    def test_native_nonce_and_qid_cannot_be_replaced_inside_a_valid_artifact(self):
        self.finish()
        for change in ['nonce', 'qid']:
            report = copy.deepcopy(self.flow.raw.report)
            if change == 'nonce':
                controller = report['controller']
                wire = decode(controller['wire'], controller['nonce'])
                controller['nonce'] = '9' * 32
                controller['wire'] = encode(
                    controller['nonce'], wire['files'], wire['metadata']
                )
            else:
                report['qualification_id'] = '9' * 64
            self.flow.publish(
                900,
                'native-qualification-500-1',
                {
                    'qualification.json': json.dumps(self.flow.raw.receipt),
                    'report.json': json.dumps(report),
                },
                '2026-10-30T00:29:59Z',
            )
            with self.subTest(change=change), self.assertRaises(QualificationError):
                self.flow.verify()

    def test_old_success_is_inert_after_a_new_signed_running_operation(self):
        self.finish()
        self.assertTrue(self.flow.verify()['ok'])
        old_checks = json.dumps(self.flow.checks, sort_keys=True)
        self.flow.advance_operation()
        self.assertEqual(self.flow.state.record['state'], 'running')
        with self.flow.transport():
            historical = verify_qualification_v2(
                self.flow.api,
                self.flow.policy,
                self.flow.raw.signed,
                artifact_id=900,
                archive=self.flow.artifacts[900][1],
                admission_archive=self.flow.artifacts[901][1],
            )
        self.assertEqual(historical.qualification_id, self.flow.ctx.qid)
        with self.assertRaisesRegex(
            QualificationError, 'current signed operation differs'
        ):
            self.flow.verify()
        self.assertEqual(json.dumps(self.flow.checks, sort_keys=True), old_checks)
        self.assertEqual(len(self.flow.writes), 4)

    def test_changed_signed_authority_between_observations_is_rejected(self):
        self.finish()
        old = self.flow.state
        self.flow.advance_operation()
        new = self.flow.state
        self.flow.state = old
        original = self.flow.api.get
        reads = 0

        def changing(resource, **parameters):
            nonlocal reads
            if resource == 'git/ref/heads/native-gate-coordination':
                reads += 1
                if reads == 2:
                    self.flow.state = new
            return original(resource, **parameters)

        self.flow.api.get = changing
        with self.assertRaisesRegex(QualificationError, 'coordination ref differs'):
            self.flow.verify()
        self.assertEqual(reads, 2)


if __name__ == '__main__':
    unittest.main()
