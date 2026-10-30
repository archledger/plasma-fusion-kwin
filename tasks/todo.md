<!-- SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com> -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
# Hosted native qualification tasks

- [x] Confirm the bounded native-run plan before workload execution.
- [x] Add strict native-result and private runtime-QML staging tests/helpers.
  - Verify exact suites/cases, zero failures/skips/blacklists, renderer and hashes.
- [ ] Add digest-preserving source/image input preparation and text transport.
  - Reject altered blobs/trees, unsafe names, missing evidence and size overruns.
- [ ] Add and review the offline guest/outer controllers and cleanup deadlines.
  - Require the fixed unprivileged isolation and guest-only vgem device.
- [ ] Run and verify a small hosted renderer preflight before the full build.
- [ ] Run one bounded fresh hosted build and all three native suites.
  - Preserve any failure and stop; do not silently retry or skip tests.
- [ ] Review actual evidence, document limitations and archive with checksum checks.

## Final gate

- [ ] Python tests, lint, workflow/shell checks, REUSE and source review pass.
- [ ] Runtime receipts prove the exact inputs, all required tests and cleanup.
- [ ] Shared handoff/index and source/evidence backups are current.
- [ ] No merge, deployment, host installation or upstream resubmission occurred.
