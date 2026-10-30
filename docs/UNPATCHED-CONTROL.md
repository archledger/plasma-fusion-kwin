<!-- SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com> -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
# One-time unpatched implementation control

This experiment branch must not be merged as the production patch set.

The test file and CTest registration are identical to those in the qualified
opacity patch. The implementation changes are absent. Against upstream commit
`ab7df7ccb7c6af20f4b279cd6220f7cd3d2267d7`, this yields tree
`d586852a2f581949afa9c66484f7723e0dc5d8f0`. The original implementation blob is
`7852409cdef063d153963d47d1918bcb0d532475`.

The normal renderer, native test assertions, result verifier and cleanup gates
are unchanged. The expected result is a successful renderer check followed by
a **failed native job**, with specific opacity-regression failures and no skips.
An infrastructure failure does not establish test sensitivity. Review the
retained source identities, CTest/QtTest logs and cleanup independently.

This runs only on the explicit `ci/unpatched-control` branch. No test failure
is suppressed or converted into a normal passing native result. The production
branch and its successful qualification remain unchanged.
