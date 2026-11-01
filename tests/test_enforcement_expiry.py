# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Expiry is a write boundary, not a retrospective proof expiry."""

import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
