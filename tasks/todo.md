<!-- SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com> -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
# Hosted native qualification tasks

- [x] Confirm the bounded native-run plan before workload execution.
- [x] Add strict native-result and private runtime-QML staging tests/helpers.
  - Verify exact suites/cases, zero failures/skips/blacklists, renderer and hashes.
- [x] Add digest-preserving source/image input preparation and text transport.
  - Reject altered blobs/trees, unsafe names, missing evidence and size overruns.
- [x] Add and review the offline guest/outer controllers and cleanup deadlines.
  - Require the fixed unprivileged isolation and guest-only vgem device.
- [x] Run and verify a small hosted renderer preflight before the full build.
- [x] Run one bounded fresh hosted build and all three native suites.
  - Preserve any failure and stop; do not silently retry or skip tests.
- [x] Review actual evidence, document limitations and archive with checksum checks.

## Final gate

- [x] Python tests, lint, workflow/shell checks, REUSE and source review pass.
- [x] Runtime receipts prove the exact inputs, all required tests and cleanup.
- [x] Shared handoff/index and source/evidence backups are current.
- [x] No merge, deployment, host installation or upstream resubmission occurred.
