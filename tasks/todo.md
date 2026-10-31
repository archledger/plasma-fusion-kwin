<!-- SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com> -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
# Hosted native qualification tasks

The completed checklist below is retained. The current proposed checklist is
[Fresh-check lifecycle tasks](#fresh-check-lifecycle-tasks).

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

## Fresh-check lifecycle tasks

Status: plan and [specification](../docs/FRESH-CHECK-LIFECYCLE.md) approved by the
owner on 2026-10-31; FC-01 through FC-05 are complete locally. See the
[plan](plan.md#fresh-check-lifecycle-implementation) for source-wide commands,
dependency graph and separate operational approvals. Every implementation box
starts unchecked; historical tests and reviews are not completion evidence.

### FC-01: Shared exact-record validation

- [x] Add pure expected-identity and visible-conflict validation, without writes.
- [x] Reject wrong App/commit/name/ID/QID/URL, conflicting QID/run mappings,
  duplicate IDs and other nonterminal records; retain unrelated terminal history
  as inert observations, never authenticated qualification.
- [x] Verify: `python3 -B -m unittest discover -s tests -p 'test_check_records.py' -v`.

Dependencies: none. Scope: two proposed files, `ci/native/check_records.py` and
`tests/test_check_records.py`. Do not change pagination limits or claim global
history completeness.

### FC-02: Bounded one-shot write journal

- [x] Persist intent before a write and its validated acknowledgment before the
  next write; prevent repeated action/role attempts and unsafe journal reuse.
- [x] Refuse unavailable/unsafe persistence; cap the record at 64 KiB and record
  only safe fields. Simulate short writes, acknowledgment-save failure and loss.
- [x] Verify: `python3 -B -m unittest discover -s tests -p 'test_check_journal.py' -v`.

Dependencies: none. Scope: two proposed files, `ci/native/check_journal.py` and
`tests/test_check_journal.py`. A journal is diagnostic evidence, not authority or
a restart/recovery mechanism.

### FC-03: Strict v2 evidence contracts

- [x] Validate exact v2 admission/result fields, distinct native/activation IDs,
  unchanged identity/QID calculation, and the verified nonce/native job binding.
- [x] Reject missing versions, v1/mixed envelopes, extra fields, changed IDs and
  boolean/invalid numeric identifiers. Introduce helpers before switching callers.
- [x] Verify: `python3 -B -m unittest discover -s tests -p 'test_check_evidence.py' -v`.

Dependencies: none. Scope: `ci/native/qualification.py` and proposed
`tests/test_check_evidence.py`. Legacy historical helpers must not become a
fallback for current v2 entry points.

### Checkpoint A: Foundations

- [x] FC-01–03 focused tests and full-suite verification recipes pass.
- [x] No existing assertion, native case, page/size limit or check was weakened.
- [x] Save exact diff/test evidence; none of these helpers authorizes a live run.

Checkpoint A: 456 tests and 11 targeted mutation checks passed, with Ruff,
compilation, manifest validation and pinned REUSE passing. Independent scoped
reviews have no remaining required findings. These helpers are not yet wired
into production; FC-04 is next. Evidence is retained in the shared project handoff.

### FC-04: Create-only client with an API-only probe

- [x] Wire shared validation/journal into fresh POST and exact-ID completion;
  remove reset PATCHes, refuse replay and stop after any uncertain write.
- [x] Adapt the validation-only probe for one real run/one pair and authenticated
  predecessor evidence. Stable validation contexts survive across actual runs;
  tests do not fabricate live identities or invoke production authority writes.
- [x] Verify: `python3 -B -m unittest discover -s tests -p 'test_check*.py' -v`.

Dependencies: FC-01–03. Scope: `ci/native/checks.py`, `tests/test_checks.py`,
`tools/probe_check_lifecycle.py`, `tests/test_check_lifecycle_probe.py`, and
`.github/workflows/check-lifecycle-probe.yml`. Preserve safe diagnostics and the
HTTP 422 regression; no workflow dispatch is part of this task.

FC-04: full suite 469 tests, Ruff, compilation, manifest, pinned REUSE and
Actionlint passed. Scoped source and downloader-delta reviews have no required
findings. The optional download-buffer bound was implemented and tested. No
App/native dispatch occurred. Production callers still need FC-05/06 wiring;
they cannot write checks without the new journal.

### FC-05: Producer admission and partial-failure binding

- [x] Publish v2 admission only after both fresh IDs are verified; native and
  report jobs consume that exact pair and never scan to adopt a replacement.
- [x] Export admit/report journals with separate always-attempt artifact names;
  retain success-only admission and existing job dependencies. Failure, missing
  upload or partial completion cannot start native work or release authority.
- [x] Verify: `python3 -B -m unittest discover -s tests -p 'test_producer.py' -v`
  and `python3 -B -m unittest discover -s tests -p 'test_workflow.py' -v`.

Dependencies: FC-04. Scope: `ci/native/producer.py`, `tests/test_producer.py`,
`.github/workflows/trusted-native.yml`, `tests/test_workflow.py`.

FC-05: 483 full-suite tests, seven targeted mutations, Ruff, compilation,
manifest validation, pinned REUSE and Actionlint passed. Source review has no
remaining findings. The added post-claim missing-check test prevents guest
execution and catches removal of the final pair check. FC-06 must still update
completed qualification and coordinator verification; no live run is authorized.

### FC-06: Both-check qualification and v2 coordinator

- [ ] Verify both admitted IDs, successful terminal identities, report-job time
  bounds and unchanged native/cleanup provenance. Preserve reporter job-ID derivation.
- [ ] Switch the coordinator's completed-proof path to v2-only bindings; match
  the issued grant/nonce and retain qualified-only completion with no main write.
- [ ] Verify: `python3 -B -m unittest discover -s tests -p 'test_*qualification*.py' -v`,
  `python3 -B -m unittest discover -s tests -p 'test_check_evidence.py' -v` and
  `python3 -B -m unittest discover -s tests -p 'test_coordinator.py' -v`.

Dependencies: FC-03, FC-05. Scope: `ci/native/qualification.py`,
`ci/native/coordinator.py`, `tests/test_qualification.py`,
`tests/test_check_evidence.py`, `tests/test_coordinator.py`.

### Checkpoint B: Coherent producer contract

- [ ] FC-04–06 focused tests and full-suite verification recipes pass.
- [ ] No production caller can use reset, unversioned admission, v1 fallback or
  a scan-derived check ID. A failed journal/upload remains a failure.
- [ ] Review the write/credential boundaries and preserve source evidence.

### FC-07: Fixed authority read and anchor loading

- [ ] Allow read-only access to only the fixed coordination-ref endpoint; reject
  arbitrary ref/URL expansion and retain bounded transport/pagination behavior.
- [ ] Parse exact trusted anchor fields with existing regular-file/JSON limits,
  validate its local signed object and exact remote tip, without automatic fetch.
- [ ] Verify: `python3 -B -m unittest discover -s tests -p 'test_proof_api.py' -v`
  and `python3 -B -m unittest discover -s tests -p 'test_readiness.py' -v`.

Dependencies: FC-03. Scope: `ci/native/proof_api.py`, `ci/native/readiness.py`,
`tests/test_proof_api.py`, `tests/test_readiness.py`.

### FC-08: Current signed operation in readiness

- [ ] Require `--coordination-anchor` and verified v2 bindings. Enforce exact
  running/finalizing predicates or qualified-only idle with matching nonce/QID
  and null main result; reject every other state or changed tip.
- [ ] Select only the admitted check pair, recheck visible conflicts and preserve
  all existing protection/review/association gates and `merge_authorized: false`.
- [ ] Verify: `python3 -B -m unittest discover -s tests -p 'test_current_policy.py' -v`
  and `python3 -B -m unittest discover -s tests -p 'test_readiness.py' -v`.

Dependencies: FC-01, FC-06–07. Scope: `ci/native/current_policy.py`,
`ci/native/readiness.py`, `tools/verify_native_proof.py`,
`tests/test_current_policy.py`, `tests/test_readiness.py`.

### FC-09: Integrated failure and replay coverage

- [ ] Cover fresh admission through qualification/readiness, old success after
  new signed activation, missing v2 evidence, partial create/publication/report
  failure, and changed authority between observations using deterministic fixtures.
- [ ] Mutation checks must catch removed ID/QID/nonce/version/fence validation,
  adopted records and ignored partial failure. No live identities are invented.
- [ ] Verify the full-suite recipes in `plan.md`; save each targeted mutation
  result and do not substitute a previous revision's passing output.

Dependencies: FC-05–08. Scope: `tests/test_producer.py`,
`tests/test_coordinator.py`, `tests/test_current_policy.py`,
`tests/test_readiness.py`, `tests/test_check_lifecycle_probe.py`.

### Checkpoint C: Current authority and regressions

- [ ] FC-07–09 focused tests, full suite, lint, compile and manifest checks pass.
- [ ] Fixtures demonstrate the claimed refusal paths, not just helper call counts.
- [ ] No source test is presented as GitHub/native operational qualification.

### FC-10: Documentation and source closeout

- [ ] Update lifecycle, evidence, read-only command and integration docs to the
  implemented contracts; describe unresolved live gates and exact approval scope.
- [ ] Run full source validation, pinned Actionlint/REUSE and exact public-source
  patch application; resolve scoped review findings and retain evidence.
- [ ] Prepare signed source commits and a reviewable source-only PR under existing
  attribution rules. Do not merge or start any operational gate.

Dependencies: FC-09. Scope: `docs/CHECK-LIFECYCLE-PROBE.md`,
`docs/QUALIFICATION-EVIDENCE.md`, `docs/READ-ONLY-FINALIZER.md`,
`docs/MAIN-INTEGRATION.md`. Progress updates to this checklist are administrative,
not a license to broaden an implementation slice.

### Outside this plan

- [ ] Obtain separate approval for main merge after source review.
- [ ] Obtain separate approval and evidence for old-operation reconciliation.
- [ ] Obtain explicitly counted approvals for App-only dispatches.
- [ ] Obtain separate approval for a fresh native qualification.
- [ ] Keep finalizing-write/protected-ref/enforcement work separately scoped.

These are approval reminders, not implementation tasks or permission to execute.
