# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fixed in-process finalization limits, without I/O or live authority."""

import copy
import unittest

from ci.native.finalizer_limits import FinalizerLimitError, FinalizerLimits


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class FinalizerLimitsTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.limits = FinalizerLimits(clock=self.clock)

    def test_exact_fixed_phase_and_aggregate_limits(self):
        self.assertEqual(self.limits.deadline, 400.0)
        for name, count in [("preclaim", 48), ("postclaim", 48), ("outcome", 32)]:
            phase = self.limits.begin_phase(name)
            self.assertEqual(phase.max_calls, count)
            for _ in range(count):
                phase.consume()
            self.assertEqual(phase.remaining_calls, 0)
        snapshot = self.limits.snapshot()
        self.assertEqual(
            snapshot["requests"], {"preclaim": 48, "postclaim": 48, "outcome": 32}
        )
        self.assertEqual(snapshot["total_requests"], 128)
        self.assertFalse(snapshot["failed"])

    def test_excess_request_poisons_the_whole_invocation(self):
        phase = self.limits.begin_phase("preclaim")
        for _ in range(48):
            phase.consume()
        with self.assertRaises(FinalizerLimitError):
            phase.consume()
        with self.assertRaises(FinalizerLimitError):
            self.limits.abort_to_outcome()
        self.assertTrue(self.limits.snapshot()["failed"])
        self.assertEqual(self.limits.snapshot()["total_requests"], 48)

    def test_closed_phase_cannot_be_used_by_an_old_reader(self):
        previous = self.limits.begin_phase("preclaim")
        previous.consume()
        current = self.limits.begin_phase("postclaim")
        with self.assertRaises(FinalizerLimitError):
            previous.consume()
        with self.assertRaises(FinalizerLimitError):
            current.consume()
        self.assertEqual(self.limits.snapshot()["requests"]["postclaim"], 0)

    def test_phase_cannot_be_reopened_or_reordered(self):
        for sequence in [
            ("postclaim",),
            ("outcome",),
            ("preclaim", "preclaim"),
            ("preclaim", "outcome"),
            ("preclaim", "postclaim", "preclaim"),
        ]:
            with self.subTest(sequence=sequence):
                limits = FinalizerLimits(clock=self.clock)
                for name in sequence[:-1]:
                    limits.begin_phase(name)
                with self.assertRaises(FinalizerLimitError):
                    limits.begin_phase(sequence[-1])
                self.assertTrue(limits.snapshot()["failed"])

    def test_preclaim_abort_seals_unused_phase_without_reallocation(self):
        preclaim = self.limits.begin_phase("preclaim")
        for _ in range(5):
            preclaim.consume()
        outcome = self.limits.abort_to_outcome()
        self.assertEqual(outcome.max_calls, 32)
        for _ in range(32):
            outcome.consume()
        self.assertEqual(self.limits.snapshot()["total_requests"], 37)
        self.assertEqual(self.limits.snapshot()["requests"]["postclaim"], 0)
        with self.assertRaises(FinalizerLimitError):
            outcome.consume()

    def test_abort_is_not_available_after_claim_phase(self):
        self.limits.begin_phase("preclaim")
        self.limits.begin_phase("postclaim")
        with self.assertRaises(FinalizerLimitError):
            self.limits.abort_to_outcome()

    def test_fixed_deadline_is_not_renewed_by_phase_or_request(self):
        preclaim = self.limits.begin_phase("preclaim")
        self.clock.now = 399.5
        self.assertEqual(preclaim.timeout(20), 0.5)
        self.limits.begin_phase("postclaim")
        self.assertEqual(self.limits.deadline, 400.0)
        self.clock.now = 400.0
        with self.assertRaises(FinalizerLimitError):
            self.limits.remaining_seconds()
        with self.assertRaises(FinalizerLimitError):
            self.limits.begin_phase("outcome")

    def test_timeout_is_capped_and_cannot_be_overridden(self):
        self.assertEqual(self.limits.timeout(600), 300.0)
        self.assertEqual(self.limits.timeout(20), 20.0)
        with self.assertRaises(AttributeError):
            self.limits.deadline = 999.0

    def test_distinct_reader_views_share_consumption(self):
        first = self.limits.begin_phase("preclaim")
        second = copy.copy(first)
        for _ in range(47):
            first.consume()
        self.assertEqual(second.remaining_calls, 1)
        second.consume()
        self.assertEqual(first.remaining_calls, 0)
        with self.assertRaises(FinalizerLimitError):
            first.consume()

    def test_exact_total_can_be_used_but_not_exceeded(self):
        for name, count in [("preclaim", 48), ("postclaim", 48), ("outcome", 32)]:
            phase = self.limits.begin_phase(name)
            for _ in range(count):
                phase.consume()
        with self.assertRaises(FinalizerLimitError):
            phase.consume()
        self.assertEqual(self.limits.snapshot()["total_requests"], 128)

    def test_expired_request_does_not_consume_or_allow_cleanup(self):
        phase = self.limits.begin_phase("preclaim")
        self.clock.now = 400.0
        with self.assertRaises(FinalizerLimitError):
            phase.consume()
        self.clock.now = 200.0
        with self.assertRaises(FinalizerLimitError):
            self.limits.abort_to_outcome()
        self.assertEqual(self.limits.snapshot()["total_requests"], 0)

    def test_clock_cannot_move_backwards_or_change_to_invalid_data(self):
        for value in [149.0, float("nan"), float("inf"), True, "150", 10**1000]:
            with self.subTest(value=repr(value)[:30]):
                clock = Clock()
                limits = FinalizerLimits(clock=clock)
                clock.now = 150.0
                limits.remaining_seconds()
                clock.now = value
                with self.assertRaises(FinalizerLimitError):
                    limits.remaining_seconds()
                self.assertTrue(limits.snapshot()["failed"])

    def test_invalid_clock_at_creation_is_rejected(self):
        for value in [float("nan"), float("inf"), True, "100", 10**1000, 1e308]:
            with (
                self.subTest(value=repr(value)[:30]),
                self.assertRaises(FinalizerLimitError),
            ):
                FinalizerLimits(clock=lambda value=value: value)

    def test_invalid_timeout_poisons_the_invocation(self):
        for maximum in [0, -1, True, None, "20", float("inf"), float("nan"), 10**1000]:
            with self.subTest(maximum=repr(maximum)[:30]):
                limits = FinalizerLimits(clock=self.clock)
                with self.assertRaises(FinalizerLimitError):
                    limits.timeout(maximum)
                self.assertTrue(limits.snapshot()["failed"])

    def test_invalid_phase_and_abort_without_preclaim_fail_closed(self):
        for name in ["", None, [], True, "PRECLAIM"]:
            with self.subTest(name=name):
                limits = FinalizerLimits(clock=self.clock)
                with self.assertRaises(FinalizerLimitError):
                    limits.begin_phase(name)
                self.assertTrue(limits.snapshot()["failed"])
        with self.assertRaises(FinalizerLimitError):
            self.limits.abort_to_outcome()

    def test_snapshot_is_a_copy_and_does_not_spend_or_reset_budget(self):
        phase = self.limits.begin_phase("preclaim")
        phase.consume()
        snapshot = self.limits.snapshot()
        snapshot["requests"]["preclaim"] = 0
        snapshot["phase"] = "outcome"
        self.assertEqual(phase.remaining_calls, 47)
        self.assertEqual(self.limits.snapshot()["phase"], "preclaim")

    def test_clock_failure_is_sanitized_and_poisons_future_calls(self):
        class FailingClock(Clock):
            def __call__(self):
                if self.now == 150.0:
                    raise RuntimeError("private clock implementation detail")
                return self.now

        clock = FailingClock()
        limits = FinalizerLimits(clock=clock)
        phase = limits.begin_phase("preclaim")
        clock.now = 150.0
        with self.assertRaises(FinalizerLimitError) as caught:
            phase.consume()
        self.assertNotIn("private", str(caught.exception))
        self.assertTrue(limits.snapshot()["failed"])
        clock.now = 160.0
        with self.assertRaises(FinalizerLimitError):
            phase.consume()

    def test_old_phase_cannot_supply_a_timeout_for_later_work(self):
        old = self.limits.begin_phase("preclaim")
        self.limits.begin_phase("postclaim")
        with self.assertRaises(FinalizerLimitError):
            old.timeout(20)
        self.assertTrue(self.limits.snapshot()["failed"])

    def test_clock_interruption_still_poisons_the_invocation(self):
        class InterruptClock(Clock):
            def __call__(self):
                if self.now == 150.0:
                    raise KeyboardInterrupt
                return self.now

        clock = InterruptClock()
        limits = FinalizerLimits(clock=clock)
        clock.now = 150.0
        with self.assertRaises(KeyboardInterrupt):
            limits.remaining_seconds()
        self.assertTrue(limits.snapshot()["failed"])
        clock.now = 160.0
        with self.assertRaises(FinalizerLimitError):
            limits.remaining_seconds()

    def test_clock_origin_cannot_round_the_allotted_interval_up_or_down(self):
        for origin in [2**61, -(2**61), 10**18]:
            with self.subTest(origin=origin), self.assertRaises(FinalizerLimitError):
                FinalizerLimits(clock=lambda origin=origin: origin)


if __name__ == "__main__":
    unittest.main()
