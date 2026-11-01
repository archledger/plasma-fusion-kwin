<!-- SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com> -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
# Finalizer-integration implementation tasks

Status: **FI-01 validated; FI-02 not started**.
The owner approved the [specification](../../SPEC-finalizer-integration.md) and
[technical plan](plan.md) and this task breakdown on 2026-11-01. No later task
has started; signing is still required before carrying the increment forward.
Approval of a document must not turn a verification checkbox into a completed task.

Baseline: `928ae15c2f6352ab250f9990dfa04cdd539d3f28`. Reverify source and worktree
before implementation. Keep the existing default CLI/qualification-only behavior,
wire schemas, authority domain and production configuration unchanged.

## Working rules

- Execute sequentially in the order below, using test-first changes and focused
  review. Preserve the existing root task files and all R4 audit history.
- Each task names at most five likely source/test files. If it cannot fit a
  focused session, split it at a tested boundary before implementation.
- The new callable is production-capable. Its tests must enforce local-only Git,
  fake HTTP/native boundaries, no credential lookup and no unexpected commands.
- New focused test commands below become runnable when their task creates the
  named file. Do not create empty passing tests to satisfy the command early.
- After each task, record the exact diff, test scope/result and remaining gaps.
  Run Ruff and whitespace checks on changed files. Commit only with required
  signing and DCO; if signing remains unavailable, preserve work and stop before
  publication rather than creating an unsigned substitute.
- No implementation approval here grants source merge, live qualification,
  production invocation, new native runs, installation or recovery authority.

## FI-01: Fixed finalizer budgets

Deliver one finalizer-specific, in-process budget/deadline object.

Acceptance:
- [x] Enforce ordered one-shot phases of48 preclaim,48 postclaim and32 outcome
  requests, with aggregate128; failed requests count and no reader can reset,
  restart, transfer or borrow allowance.
- [x] Mint one300-second monotonic deadline at finalizer entry; nested callers
  receive the same deadline and remaining allowance rather than a new clock.
- [x] Refuse late/exhausted entry and invalid phase changes. The narrow preclaim
  failure path may seal the unused postclaim phase and enter outcome without
  borrowing its allowance; an exhausted/poisoned budget never permits cleanup.

Verify: `python3 -B -m unittest discover -s tests -p 'test_finalizer_limits.py' -v`.
Include exact-boundary, failed-call, new-reader-reset, phase-skip and expired-clock
negatives using a deterministic clock. Also run the common task checks below.

Dependencies: none. Scope: S.
Files: `ci/native/finalizer_limits.py`, `tests/test_finalizer_limits.py`.

Local verification:20 focused tests and647 full-suite tests passed; eight
isolated guard mutations were caught. Scoped review closed the clock-origin
rounding finding with a failing-before-fix regression. This is a source-only
increment, not integrated I/O enforcement or live finalization. A separate
controller-test isolation correction removed a host-proc dependency without
changing production confinement. Use the verified minihost headless signer for
signed checkpoints and GitHub pushes; do not transfer private keys.

## FI-02: Bound blocking reads and child execution

Add opt-in deadline support to strict API reads and the execution primitive used
by later Git/signature work.

Acceptance:
- [ ] Keep normal `ProofAPI` behavior, endpoint/JSON restrictions and per-reader
  maximum64 unchanged. Deadline-enabled readers debit the shared parent budget
  before their single allowed request, without hidden retries or redirects.
- [ ] A narrow GET-only helper bounds connect/header/body waits and the complete
  response; the parent retains strict parsing and rejects incomplete or oversized
  output. The helper has no signing, state-transition or update protocol. Tokens
  never appear in argv/logs and helper requests use private pipes.
- [ ] Timeout, cap, launch failure and interruption terminate/reap only owned
  helper/command process groups. Reuse that bounded command runner later; killing
  a local Git process must never be classified as remote cancellation.

Verify: focused `test_proof_api.py`, `test_bounded_read.py` and
`test_finalizer_limits.py` suites. Exercise slow headers/body, missing EOF,
oversized output, invalid helper requests, process descendants, interrupts and
the unchanged reader path. Use the real request counter/parser with a fake HTTP
boundary, not only a replacement `SimpleNamespace` API.
Include a paginated sequence whose successive pages share the same phase and
aggregate counters and deadline. Missing/incomplete pages, changing totals or
exhaustion must prevent mutation; opening another page must not renew allowance.

Dependencies: FI-01. Scope: M.
Files: `ci/native/proof_api.py`, `ci/native/bounded_read.py`,
`ci/native/finalizer_limits.py`, `tests/test_proof_api.py`,
`tests/test_bounded_read.py`.

## FI-03: Carry the deadline through existing validators

Extend the existing local verifier call chain with an optional bounded-execution
context; do not use global monkeypatching in production.

Acceptance:
- [ ] Propagate the same context through readiness/anchor checks, signed
  coordination, signed commit/merge verification and raw commit/tree/blob reads.
  Every Git/GPG subprocess uses remaining time, bounded output and owned cleanup.
- [ ] Omitted context preserves existing public behavior and timeout defaults.
  A nested helper cannot silently drop or renew the finalizer deadline.
- [ ] Near-expiry failure during raw reads or signature verification prevents
  later claim/update/release, with no orphaned verifier descendants or false
  successful evidence. Retain the documented limitation for uninterruptible
  filesystem stalls; recheck before any later external mutation.

Verify: `test_finalizer_validation.py` plus existing `test_signed_merge.py`,
`test_signed_coordination.py`, `test_merge_update.py` and `test_readiness.py`.
Check exact current test filenames before execution; a renamed suite must not
be silently omitted. Include default-call compatibility and full-chain expiry.

Dependencies: FI-02. Scope: M.
Files: `ci/native/merge_update.py`, `ci/native/signed_merge.py`,
`ci/native/coordination.py`, `ci/native/readiness.py`,
`tests/test_finalizer_validation.py`.

### Checkpoint A: Bounds and validator chain

- [ ] FI-01–03 focused tests, full existing suite, Ruff and changed-file whitespace
  checks pass; no existing assertion, permission or endpoint restriction weakened.
- [ ] Review actual request counting and near-expiry behavior, including the
  static four-reviewer capacity case. A route exceeding48 must fail closed;
  do not raise the approved cap or borrow allowance to make it pass.
- [ ] Preserve validation evidence and close required review findings before
  starting journal/update integration.

## FI-04: Durable coordinator journal

Replace direct rewrites with one private, fail-closed coordinator journal.

Acceptance:
- [ ] Preserve existing qualification-only fields/values; add finalization data
  only for opted-in invocations. Enforce exclusive creation, private ownership,
  regular-file/inode/link checks and a64KiB journal cap.
- [ ] Persist intent with complete writes, file fsync, atomic replacement and
  directory fsync before any external mutation. Any persistence failure poisons
  the writer; existing files/markers cannot be adopted or reset.
- [ ] Keep raw mutation streams in exclusive private sidecars, capped at64KiB
  per stream and bound by byte count/SHA-256 in the journal. Missing, changed or
  incompletely persisted evidence cannot authorize acceptance or another attempt.

Verify: `test_coordinator_journal.py` and existing `test_coordinator.py`.
Cover short writes, no-progress writes, fsync/replace failures, path/inode/link
changes, oversized journal/sidecars and reopening after an interrupted write.

Dependencies: FI-03. Scope: M.
Files: `ci/native/coordinator_journal.py`, `ci/native/coordinator.py`,
`tests/test_coordinator_journal.py`, `tests/test_coordinator.py`.

## FI-05: Exact-update outcome evidence

Add the internal one-attempt result path without changing the legacy wrapper.

Acceptance:
- [ ] Preserve `push_exact_merge()` defaults, dry-run behavior and legacy result/
  error contract. Retain raw ordered-parent/tree checks and the explicit expected
  base lease, with no extra refs/tags or replacement merge.
- [ ] Classify applied/not-applied/unknown from complete bounded per-ref evidence,
  process result and exact readback. Extra, missing, contradictory, forced/new/
  deleted/no-op or truncated status records never count as accepted finalization.
- [ ] Generic nonzero exit, `!` alone, timeout, lost acknowledgment or a later
  matching tip cannot prove this invocation's result. Definite rejection requires
  no outstanding attempt and a reconciled target different from the candidate.

Verify: `test_finalizer_update.py` and existing `test_merge_update.py`, using real
local bare Git, exact lease races, configured-hook behavior, true rejection and
accepted-but-ack-lost injections. Inspect emitted refspecs and retained evidence.

Dependencies: FI-04. Scope: M.
Files: `ci/native/merge_update.py`, `tests/test_finalizer_update.py`,
`tests/test_merge_update.py`.

## FI-06: Full local integration fixture

Compose the existing signed/v2 evidence fixtures with actual local Git effects.

Acceptance:
- [ ] Use ephemeral keys, real signatures, serialized archives and production
  validators, plus local bare `main` and `native-gate-coordination` refs. Fake only
  the external HTTP/native boundary; do not replace the whole proof verifier.
- [ ] The harness refuses production/network Git endpoints, credential lookup,
  unplanned commands and unsafe inherited configuration before effects. Every
  remote mutation in a test is confined to its owned temporary directory.
- [ ] Exercise real `ProofAPI` counting/parsing, including paginated finalizer
  responses under shared limits, and authenticated state transitions; preserve
  existing fixture strictness and clearly label all simulated evidence.

Verify: `python3 -B -m unittest discover -s tests -p 'test_finalizer_fixture.py' -v`.
Include explicit denied-command/network cases and compare the complete local ref
inventory before/after. No live token, App key, native executable or remote run.

Dependencies: FI-05. Scope: M.
Files: `tests/finalizer_fixture.py`, `tests/test_finalizer_fixture.py`,
`tests/lifecycle_fixture.py` only for narrow reuse if needed.

### Checkpoint B: Durable intent and local effects

- [ ] FI-04–06 and retained reader/signature/coordinator tests pass; the complete
  fixture cannot escape its local endpoints or manufacture a verified result.
- [ ] Review journal/sidecar crash behavior and exact outcome classification;
  no implicit retry or fabricated acknowledgment remains.
- [ ] Confirm file scope and source hashes before introducing the finalizer core.

## FI-07: Finalizer core

Implement the complete same-invocation claim/revalidate/update/release path.

Acceptance:
- [ ] Consume typed verified inputs and a coordinator-owned session, binding the
  exact operation, grant, worker nonce, signed merge and journal. A fresh process,
  readiness JSON, existing journal or old qualified-only result cannot adopt it.
- [ ] Require terminal writers/cleanup, preclaim readiness, an authenticated exact
  CAS claim with a new internal nonce, then full postclaim readiness. Persist the
  one update intent and recheck owner/deadline before its only submission.
- [ ] Implement only the planned outcomes: narrow healthy preclaim
  `running -> idle / failed` without claim/update; postclaim failure from the exact
  finalizing tip; acknowledged finalized/rejected release; no release on unknown
  update. Post-update verification uses acknowledgment, target, ownership and
  terminal-writer evidence, not the invalidated old-base readiness predicate.
  Never use successful qualification-only as a failure fallback.

Verify: `test_finalizer.py` using FI-06's real local fixture. Trace every intended
mutation and signed state, including preclaim policy refusal and a full positive
flow. Include an accepted update that changes PR/base metadata: the correct
post-update checks must recognize its result without rerunning old-base readiness.
No standalone CLI or arbitrary append/ref interface.

Dependencies: FI-06. Scope: M.
Files: `ci/native/finalizer.py`, `tests/test_finalizer.py`,
`tests/finalizer_fixture.py`.

## FI-08: Ambiguity and race controls

Add adversarial integrated regressions and repair only demonstrated core gaps.

Acceptance:
- [ ] Cover stale owner/base, competing claims, changed PR/reviews/protection/
  admitted checks, partial proof/cleanup, deadline/budget exhaustion and wrong
  operation/nonce. Only one valid contender may update; old checks are not authority.
- [ ] Cover interruption at every intent/effect boundary. Lost update acknowledgment
  never permits release/retry; lost release acknowledgment reports `release-unknown`
  even if remote is already idle or a new owner has acquired it. Never rewind or
  touch that new owner.
- [ ] Kill targeted mutations of ownership, exact-merge, freshness, no-replay,
  acknowledgment and budget/deadline guards. Restore the exact source after each
  experiment; preserve failures without deleting, skipping or weakening tests.

Verify: `test_finalizer_failures.py` and `test_finalizer.py`; inspect the actual
local histories and command counts, not only mocked callback counts. Record
mutant names, changed guard, failing test and restored-source hash.

Dependencies: FI-07. Scope: M.
Files: `tests/test_finalizer_failures.py`, `tests/test_finalizer.py`,
`ci/native/finalizer.py` for confirmed fixes, `tests/finalizer_fixture.py` if needed.

## FI-09: Wire the coordinator's internal completion intent

Reach the finalizer from the owning coordinator without exposing a new CLI mode.

Acceptance:
- [ ] Validate/fix `completion_mode` before side effects and journal it before
  acquisition. Default qualification-only and dry-run responses remain unchanged;
  no old operation can be resumed or upgraded into finalization.
- [ ] After verified native completion, the same invocation passes its bound session
  to the finalizer before the old successful idle release. Claim, validation and
  release use the same execution context; original64 proof requests and artifact
  polling remain separately accounted from finalizer128.
- [ ] End-to-end local tests cover both completion paths. The shipped CLI retains
  existing `--apply` meaning and rejects any new finalize flag; read-only proof
  output remains `merge_authorized: false` and no workflow permission changes.

Verify: `test_finalizer_integration.py`, `test_coordinator.py` and existing
read-only entrypoint tests. Use actual argument parsing for the CLI refusal and
the complete local coordinator path for the positive trace.

Dependencies: FI-08. Scope: M.
Files: `ci/native/coordinator.py`, `ci/native/finalizer.py`,
`tests/test_coordinator.py`, `tests/test_finalizer_integration.py`,
`tests/finalizer_fixture.py`.

### Checkpoint C: Same-invocation integration

- [ ] Full existing/new suite, all mandatory mutation controls and compatibility
  checks pass on the same source revision, with no missing required test evidence.
- [ ] Independent review closes required findings for the complete path, including
  nested deadlines, sidecar durability and both types of lost acknowledgment.
- [ ] Confirm no CLI, workflow, permission, wire-schema, production or native-run
  expansion; record any remaining local-only evidence limitations.

## FI-10: Documentation and source acceptance

Close the source milestone without performing its later operational qualification.

Acceptance:
- [ ] Document the actual internal source capability, defaults, supported limits,
  outcome/recovery semantics and local-only qualification. Keep production enablement
  and any future live-test adapter/entrypoint behind their own decisions.
- [ ] Pass full tests, mutation controls, manifest/public patch validation,
  compilation, Ruff, whitespace, pinned workflow lint and REUSE on the exact
  candidate. Review the full diff and resolve all required findings.
- [ ] Preserve signed/DCO source, recipes, test/review evidence and a checksum-
  verified archive with restore instructions. If signing is unavailable, retain
  local work and report the block; do not publish an unsigned substitute or merge.

Verify: the full commands in [plan.md](plan.md), plus
`python3 -B tools/patchset.py check` and existing pinned Actionlint/REUSE recipes.
Do not install missing tools/services or start native qualification merely to
complete this checklist; report unmet prerequisites.

Dependencies: FI-09 and Checkpoint C. Scope: M.
Files: `docs/FINALIZER-INTEGRATION.md`, `docs/READ-ONLY-FINALIZER.md`,
`docs/MERGE-READINESS.md`, `tasks/finalizer-integration/plan.md`,
`tasks/finalizer-integration/todo.md`.

## Common task checks and completion evidence

Run focused tests named by each task after changes. Test filenames listed as
existing must be verified in the actual checkout and not silently skipped.
For changed Python/source files, also run:

```sh
ruff check --no-cache ci tools tests
git diff --check
```

At Checkpoints A/C and final source acceptance:

```sh
python3 -B -m unittest discover -s tests -v
python3 -B tools/patchset.py validate
python3 -B -m compileall -q ci tools tests
```

Record source SHA/tree, exact commands, exit results, test/mutation counts,
review disposition, external effects (expected none), pending validation and
restore paths. Check a box only after that evidence exists. A source-test pass
does not qualify GitHub enforcement, a new native run or production finalization.

## Entry gate

- [x] Owner has accepted this executable task breakdown before FI-01 begins.
- [x] Worktree/source identities and existing uncommitted work have been checked.
- [x] Existing minihost headless signing is verified before commit/publication;
  no key transfer or signing-security bypass is used.

Only verified entry conditions are checked above. FI-01 is the first source
task; later operational authorization is not inherited.
