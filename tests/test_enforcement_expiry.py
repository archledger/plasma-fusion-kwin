# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Expiry is a write boundary, not a retrospective proof expiry."""

import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_check_enforcement_probe as fixtures
import test_check_enforcement_runtime as runtime_fixtures

from ci.native.check_journal import CheckJournal
from ci.native.checks import CheckWriteError
from tools import probe_check_enforcement as probe

EXPIRES = "2026-11-01T01:00:00Z"


class ExpiryContractTests(unittest.TestCase):
    def test_expiry_requires_canonical_utc_seconds(self):
        self.assertEqual(probe.expiry_time(EXPIRES), probe.timestamp(EXPIRES))
        for bad in [
            None,
            True,
            "",
            "2026-11-01T01:00:00+00:00",
            "2026-11-01T01:00:00.1Z",
            EXPIRES + " ",
            "2026-02-30T01:00:00Z",
        ]:
            with self.subTest(value=bad), self.assertRaises(ValueError):
                probe.expiry_time(bad)

    def test_live_write_requires_strictly_future_expiry(self):
        for now, allowed in [
            ("2026-11-01T00:59:59Z", True),
            (EXPIRES, False),
            ("2026-11-01T01:00:01Z", False),
        ]:
            with (
                self.subTest(now=now),
                patch.object(probe, "_now", return_value=probe.timestamp(now)),
            ):
                if allowed:
                    self.assertEqual(
                        probe.require_unexpired(EXPIRES), probe.timestamp(EXPIRES)
                    )
                else:
                    with self.assertRaisesRegex(ValueError, "deadline expired"):
                        probe.require_unexpired(EXPIRES)

    def test_first_run_cannot_claim_more_than_one_hour(self):
        with patch.object(
            probe, "_now", return_value=probe.timestamp("2026-11-01T00:01:00Z")
        ):
            probe.require_unexpired(EXPIRES, created_at="2026-11-01T00:00:00Z")
            with self.assertRaisesRegex(ValueError, "one-hour"):
                probe.require_unexpired(
                    "2026-11-01T01:00:01Z", created_at="2026-11-01T00:00:00Z"
                )

    def test_expiry_must_follow_run_creation(self):
        with (
            patch.object(
                probe, "_now", return_value=probe.timestamp("2026-11-01T00:01:00Z")
            ),
            self.assertRaisesRegex(ValueError, "one-hour"),
        ):
            probe.require_unexpired(EXPIRES, created_at=EXPIRES)

    def test_parsing_historical_expiry_does_not_invalidate_evidence(self):
        with patch.object(
            probe, "_now", return_value=probe.timestamp("2026-12-01T00:00:00Z")
        ):
            self.assertEqual(probe.expiry_time(EXPIRES), probe.timestamp(EXPIRES))

    def test_stage_identity_binds_the_shared_expiry(self):
        first = probe.stage_qid("e" * 32, "A1", 500, "b" * 40, "1" * 32, EXPIRES)
        second = probe.stage_qid(
            "e" * 32, "A1", 500, "b" * 40, "1" * 32, "2026-11-01T01:00:01Z"
        )
        self.assertNotEqual(first, second)

    def test_v2_report_retains_expiry_for_later_verification(self):
        value = fixtures.report_fixture("A1")
        value.update(schema_version=2, expires_at=EXPIRES)
        value["records"][0]["qualification_id"] = probe.stage_qid(
            "e" * 32, "A1", 500, "b" * 40, "1" * 32, EXPIRES
        )
        value["records"][0]["check"]["external_id"] = value["records"][0][
            "qualification_id"
        ]
        with patch.object(
            probe, "_now", return_value=probe.timestamp("2026-12-01T00:00:00Z")
        ):
            self.assertEqual(probe.validate_report(value), value)

    def test_report_cannot_roll_expiry_without_changing_check_identity(self):
        value = fixtures.report_fixture("A1")
        value["expires_at"] = "2026-11-01T01:00:01Z"
        with self.assertRaises(ValueError):
            probe.validate_report(value)

    def test_legacy_report_version_is_not_accepted_as_current(self):
        value = fixtures.report_fixture("A1")
        value["schema_version"] = 1
        with self.assertRaisesRegex(ValueError, "report scope"):
            probe.validate_report(value)

    def test_authenticated_predecessor_cannot_change_expiry(self):
        factory = fixtures.PredecessorTests()
        for prior, stage in [("A1", "A2"), ("B2", "B3")]:
            with self.subTest(stage=stage):
                _, current, raw, values = factory.fixture(prior, "2026-11-01T01:00:01Z")
                with self.assertRaisesRegex(ValueError, "preceding expiry differs"):
                    factory.load(current, raw, values, stage)

    def test_stage_context_cannot_roll_the_valid_predecessor_expiry(self):
        for prior, stage, writer, nonce in [
            ("A1", "A2", 501, "2" * 32),
            ("B2", "B3", 505, "6" * 32),
        ]:
            previous = fixtures.report_fixture(prior)
            with (
                self.subTest(stage=stage),
                self.assertRaisesRegex(ValueError, "previous stage expiry differs"),
            ):
                probe.previous_records(
                    previous,
                    stage=stage,
                    experiment_id="e" * 32,
                    launcher="a" * 40,
                    authority="d" * 40,
                    fixtures=previous["fixtures"],
                    writer_run_id=writer,
                    writer_nonce=nonce,
                    expires_at="2026-11-01T01:00:01Z",
                )


class ExpiryRuntimeTests(unittest.TestCase):
    def test_workflow_requires_the_shared_expiry_input(self):
        text = (Path(probe.__file__).resolve().parents[1] / probe.WORKFLOW).read_text()
        self.assertIn("      expires_at:\n", text)
        self.assertRegex(text, r"expires_at:\n(?:        .*\n)*        required: true")
        self.assertIn("ENFORCEMENT_EXPIRES_AT: ${{ inputs.expires_at }}", text)

    def test_expired_queued_creation_and_cleanup_never_mint(self):
        for stage in ["A1", "B3"]:
            with (
                self.subTest(stage=stage),
                runtime_fixtures.runtime(stage) as harness,
                patch.object(probe, "_now", return_value=probe.timestamp(EXPIRES)),
                patch.object(probe, "ProofAPI") as api,
            ):
                with self.assertRaisesRegex(ValueError, "deadline expired"):
                    probe.run_worker()
                api.assert_not_called()
                self.assertFalse(harness.minted)
                self.assertEqual(harness.writes, [])

    def test_missing_expiry_never_reaches_api_or_mint(self):
        with (
            runtime_fixtures.runtime("A1") as harness,
            patch.object(probe, "ProofAPI") as api,
        ):
            del probe.os.environ["ENFORCEMENT_EXPIRES_AT"]
            with self.assertRaises(KeyError):
                probe.run_worker()
            api.assert_not_called()
            self.assertFalse(harness.minted)

    def test_overlong_run_expiry_is_rejected_before_mint(self):
        with (
            runtime_fixtures.runtime("A1") as harness,
            patch.dict(
                probe.os.environ, {"ENFORCEMENT_EXPIRES_AT": "2026-11-01T02:00:00Z"}
            ),
        ):
            with self.assertRaisesRegex(ValueError, "one-hour"):
                probe.run_worker()
            self.assertFalse(harness.minted)
            self.assertEqual(harness.writes, [])

    def test_slow_pre_mint_fence_cannot_pass_expiry(self):
        with (
            runtime_fixtures.runtime("A1") as harness,
            patch.object(
                probe, "_now", return_value=probe.timestamp("2026-11-01T00:31:00Z")
            ) as clock,
        ):
            calls = 0

            def read_refs(*args):
                nonlocal calls
                calls += 1
                if calls == 2:
                    clock.return_value = probe.timestamp(EXPIRES)
                return copy.deepcopy(harness.refs)

            with (
                patch.object(probe, "read_refs", side_effect=read_refs),
                self.assertRaisesRegex(ValueError, "deadline expired"),
            ):
                probe.run_worker()
            self.assertFalse(harness.minted)
            self.assertEqual(harness.writes, [])

    def test_slow_write_fence_stops_before_recording_write_intent(self):
        for stage, journal in [("A1", "admit"), ("B3", "report")]:
            with (
                self.subTest(stage=stage),
                runtime_fixtures.runtime(stage) as harness,
                patch.object(
                    probe, "_now", return_value=probe.timestamp("2026-11-01T00:31:00Z")
                ) as clock,
            ):

                def read_refs(*args):
                    if harness.minted:
                        clock.return_value = probe.timestamp(EXPIRES)
                    return copy.deepcopy(harness.refs)

                with (
                    patch.object(probe, "read_refs", side_effect=read_refs),
                    self.assertRaises(CheckWriteError),
                ):
                    probe.run_worker()
                self.assertEqual(harness.writes, [])
                value = json.loads(
                    (
                        Path(harness.env["RUNNER_TEMP"])
                        / f"check-enforcement-probe/{journal}-journal.json"
                    ).read_text()
                )
                self.assertEqual(value["writes"], [])

    def test_expiry_after_journal_intent_still_blocks_the_request(self):
        original = CheckJournal.begin
        for stage, journal in [("A1", "admit"), ("B3", "report")]:
            with (
                self.subTest(stage=stage),
                runtime_fixtures.runtime(stage) as harness,
                patch.object(
                    probe, "_now", return_value=probe.timestamp("2026-11-01T00:31:00Z")
                ) as clock,
            ):

                def begin_then_expire(self, *args, **kwargs):
                    original(self, *args, **kwargs)
                    clock.return_value = probe.timestamp(EXPIRES)

                with (
                    patch.object(CheckJournal, "begin", begin_then_expire),
                    self.assertRaises(CheckWriteError),
                ):
                    probe.run_worker()
                self.assertEqual(harness.writes, [])
                value = json.loads(
                    (
                        Path(harness.env["RUNNER_TEMP"])
                        / f"check-enforcement-probe/{journal}-journal.json"
                    ).read_text()
                )
                self.assertEqual(value["writes"][0]["status"], "pending")

    def test_expiry_after_creation_never_forces_completion(self):
        with (
            runtime_fixtures.runtime("A1") as harness,
            patch.object(
                probe, "_now", return_value=probe.timestamp("2026-11-01T00:31:00Z")
            ) as clock,
        ):
            send = harness.send

            def expire_after_post(method, *args, **kwargs):
                value = send(method, *args, **kwargs)
                if method == "POST":
                    clock.return_value = probe.timestamp(EXPIRES)
                return value

            with (
                patch("ci.native.checks._request", side_effect=expire_after_post),
                self.assertRaises(CheckWriteError),
            ):
                probe.run_worker()
            self.assertEqual(harness.writes, ["POST"])
            self.assertEqual(harness.server[900]["status"], "in_progress")


if __name__ == "__main__":
    unittest.main()
