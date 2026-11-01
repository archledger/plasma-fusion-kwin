# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""One invocation's fixed limits, not a lock, authority or recovery mechanism."""

import math
import time
from dataclasses import dataclass

_PHASES = ("preclaim", "postclaim", "outcome")
_CAPS = (48, 48, 32)
_TOTAL = 128
_SECONDS = 300


class FinalizerLimitError(ValueError):
    """The invocation cannot continue after a limit or ordering failure."""


@dataclass(frozen=True)
class _PhaseBudget:
    _owner: "FinalizerLimits"
    name: str

    @property
    def max_calls(self):
        self._owner._check_phase(self.name)
        return _CAPS[_PHASES.index(self.name)]

    @property
    def remaining_calls(self):
        return self.max_calls - self._owner._used[self.name]

    def consume(self):
        self._owner._consume(self.name)

    def timeout(self, maximum):
        remaining = self._owner._check_phase(self.name)
        return self._owner._timeout(maximum, remaining)


class FinalizerLimits:
    """All reader views share this object; closed phases cannot be reused.

    The clock is a trusted test seam. Callers still enforce the returned time
    bounds on blocking I/O; this class cannot interrupt a request itself.
    """

    def __init__(self, *, clock=None):
        self._clock = time.monotonic if clock is None else clock
        self._failed = False
        self._last = None
        self._phase = None
        self._used = dict.fromkeys(_PHASES, 0)
        start = self._now()
        self._deadline = start + _SECONDS
        if not math.isfinite(self._deadline) or self._deadline - start != _SECONDS:
            self._fail("invalid finalizer deadline")

    def _fail(self, message):
        self._failed = True
        raise FinalizerLimitError(message) from None

    def _now(self):
        try:
            value = self._clock()
        except (OSError, RuntimeError, ValueError, TypeError, OverflowError):
            self._fail("cannot read finalizer clock")
        except BaseException:
            self._failed = True
            raise
        try:
            if type(value) not in {int, float}:
                self._fail("invalid finalizer clock")
            value = float(value)
        except (ValueError, TypeError, OverflowError):
            self._fail("cannot read finalizer clock")
        if not math.isfinite(value) or (self._last is not None and value < self._last):
            self._fail("invalid or backwards finalizer clock")
        self._last = value
        return value

    @property
    def deadline(self):
        return self._deadline

    def remaining_seconds(self):
        if self._failed:
            self._fail("finalizer limits are unusable after failure")
        remaining = self._deadline - self._now()
        if remaining <= 0:
            self._fail("finalizer deadline reached")
        return remaining

    def _timeout(self, maximum, remaining):
        if type(maximum) not in {int, float}:
            self._fail("invalid per-operation timeout")
        try:
            maximum = float(maximum)
        except (ValueError, OverflowError):
            self._fail("invalid per-operation timeout")
        if not math.isfinite(maximum) or maximum <= 0:
            self._fail("invalid per-operation timeout")
        return min(maximum, remaining)

    def timeout(self, maximum):
        return self._timeout(maximum, self.remaining_seconds())

    def _check_phase(self, name):
        remaining = self.remaining_seconds()
        if name != self._phase:
            self._fail("reader phase is not active")
        return remaining

    def begin_phase(self, name):
        self.remaining_seconds()
        expected = {None: "preclaim", "preclaim": "postclaim", "postclaim": "outcome"}
        if type(name) is not str or name != expected.get(self._phase):
            self._fail("invalid or repeated finalizer phase")
        self._phase = name
        return _PhaseBudget(self, name)

    def abort_to_outcome(self):
        """Seal the unentered postclaim allocation; the caller proves no claim."""
        self._check_phase("preclaim")
        self._phase = "outcome"
        return _PhaseBudget(self, "outcome")

    def _consume(self, name):
        self._check_phase(name)
        limit = _CAPS[_PHASES.index(name)]
        if self._used[name] >= limit or sum(self._used.values()) >= _TOTAL:
            self._fail("finalizer request budget exhausted")
        self._used[name] += 1

    def snapshot(self):
        return {
            "phase": self._phase,
            "deadline": self._deadline,
            "requests": dict(self._used),
            "total_requests": sum(self._used.values()),
            "failed": self._failed,
        }
