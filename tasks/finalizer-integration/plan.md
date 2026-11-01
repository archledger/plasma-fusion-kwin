<!-- SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com> -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
# Implementation plan: same-process finalizer integration

Status: **plan approved by the owner on 2026-11-01**. The
[specification](../../SPEC-finalizer-integration.md) is also approved. The task
breakdown remains a separate implementation gate; no live operation is authorized.
Scoped static review found no remaining required planning issue after the
deadline-propagation and preclaim-release corrections. Runtime validation is pending.

Use this module-specific directory, following the existing enforcement-module
layout. Do not overwrite the unrelated root `tasks/plan.md` or `tasks/todo.md`.
The executable task breakdown is [todo.md](todo.md). Its checkboxes record actual
implementation/verification, not approval of this plan.

## Baseline and scope

Baseline: `928ae15c2f6352ab250f9990dfa04cdd539d3f28`. The current coordinator
releases successful native work as `qualified-only`; its public CLI must continue
doing that. Current authority is already idle and must not be reopened.
R4 establishes the recorded disposable check-selection results, not a complete
finalizer or authorization for any new update.

The future internal callable is production-capable, not a sandbox. All tests
below must independently enforce local-only execution. This plan neither enables
a production CLI nor authorizes running the callable against GitHub. Preserve
the existing authority domain, schemas, App permissions and native workflow.

## Dependency graph and slice order

```text
FI-01 fixed phase budgets
  -> FI-02 bounded read/subprocess execution
  -> FI-03 deadline propagation through existing validators
  -> Checkpoint A: bounds and complete validator chain
  -> FI-04 durable coordinator journal
  -> FI-05 exact-update outcome evidence
  -> FI-06 local integration fixture
  -> Checkpoint B: real Git/strict-reader fixture
  -> FI-07 finalizer core
  -> FI-08 ambiguity and race coverage
  -> FI-09 coordinator integration, defaults preserved
  -> Checkpoint C: same-invocation integration
  -> FI-10 documentation and source qualification
  -> owner source-integration decision
  -> separately specified live qualification and production decisions
```

Use one sequential implementation stream. Read-only review may be independent;
parallel edits to the coordinator, finalizer or shared reader are not planned.
Every slice must leave the existing supported entrypoints working. No stub or
partially implemented public finalizing mode is exposed between slices.

## Technical decisions

### 1. Keep ownership in one coordinator invocation

Add a keyword-only `completion_mode` to the internal `coordinate()` API, default
`qualified-only`. Validate it before credential lookup or other side effects.
The only additional value is `finalize`; the shipped CLI never supplies it.
Read-only/default execution retains its current response and no-write behavior.

For apply mode, store completion intent in the new invocation's private journal
before acquisition. Do not add it to the signed coordination wire schema or QID.
Create a private finalization session only after the same invocation has verified
its native result while still owning `running`.

The session binds the exact operation, grant, signed merge, artifact identities,
source policy, current signed tip and journal. It exposes only current-state
observation and three narrow transitions: claim finalizing, release finalizing,
or fail the still-running operation before any claim or target attempt. There
is no generic `append(arbitrary_dict)` interface. Coordinator-owned closures
perform signing and CAS; `finalizer.py` does not import the coordinator or accept
resume data.
Private Python types express intent, not security against malicious trusted code.

### 2. Fixed budgets and bounded blocking work

Put the finalizer-specific budget/deadline object in
`ci/native/finalizer_limits.py`. Mint it once at finalizer entry. Its ordered,
one-shot phases are preclaim48, postclaim48 and outcome32, aggregate128. Failed
requests count; phases cannot restart, borrow or reset via a new reader. Reserve
the remaining phase allocations before crossing a mutation boundary.

Keep every `ProofAPI` reader's existing `max_calls <= 64` contract and defaults.
Each phase gets one reader/view backed by the same parent accounting object.
The coordinator's earlier64 proof/policy calls remain separate. Report existing
artifact polling/download requests separately rather than hiding them inside192.

Capacity is deliberately narrow. A static review estimates 41 + 2N requests for
the current single-page, signature-enabled readiness route, where N is the number
of reviewer permission candidates. One candidate fits 48; four would need 49. Treat
that as a test case to establish with actual accounting, not permission to lift
the cap. Exhaustion does not borrow another phase's allowance or release authority.

Use one monotonic300-second deadline before preclaim verification; retain its
audit timestamp. Checks before and after blocking work must prevent any new
mutation after expiry. Do not reset the deadline at claim, submission or release.

For deadline-enabled `ProofAPI` calls, use a narrow, short-lived **read-only HTTP
helper process** around the existing single-request transport. The parent debits
the request budget, enforces the remaining wall-clock wait and response cap, and
retains strict response parsing. The helper has no signing/coordination/update
protocol, accepts one already-allowlisted GET and performs no retries or redirects.
Keep tokens off argv/logs and use private pipes; this is trusted helper code, not
a security sandbox. Normal readers without a deadline retain their existing path.

Reuse the existing child-process timeout/cleanup pattern, not an asynchronous
signal exception as the sole deadline mechanism. A signal handler may run late
in C code or interrupt unrelated critical sections. Helper timeout/cap/interruption
must terminate and reap only its owned process group before returning failure.

Git/signing commands likewise need stdout/stderr capture capped at 64KiB each,
a timeout no larger than remaining allowance, owned process-group cleanup and no automatic
retry. Killing a local Git process does not prove a remote update was cancelled.
Treat that result as unknown. Do not claim an OS-level guarantee to interrupt an
uninterruptible filesystem stall; after a persistence call returns, recheck the
deadline before any external mutation and poison uncertain persistence.

Thread the same explicit bounded-execution context through the existing local
verification chain; do not use production monkeypatching or process-global state:

- `readiness.verify_readiness()` and `verify_coordination_anchor()` pass it to
  signed-merge and signed-coordination verification.
- `signed_merge.verify_signed_merge()` and `verify_signed_commit()` pass it to
  raw-object reads and the Git/GPG verification subprocess. The current fixed
  30-second timeout becomes `min(30, remaining)` only when a context is supplied.
- `coordination.read_signed_coordination()` passes it to signature, raw tree
  and raw blob reads.
- `merge_update.read_raw_object()`, `read_exact_merge()` and their Git runner
  pass it through every command, clamping the current60-second bound.
- Coordinator-owned claim/release closures use it for hash/tree/commit signing,
  CAS submission and authenticated readback, not merely the final main push.

These are optional internal parameters; omitted context retains existing
behavior and interfaces. Near-expiry tests must enter this actual validator
chain and prove signature descendants are terminated/reaped, no subsequent
claim/update/release starts, and no fresh deadline is created by a nested helper.

### 3. One durable journal, not a second authority

Introduce a small `CoordinatorJournal` in
`ci/native/coordinator_journal.py`, replacing the existing direct rewrite in
`coordinator.py`. Keep existing qualification-only fields and values compatible.
Add a versioned finalization section only for opted-in invocations.

Use exclusive creation in an owned private directory,64KiB total bound,
regular-file/inode/link/permission checks, complete writes, file fsync, atomic
replacement and parent-directory fsync. Borrow the established `CheckJournal`
persistence pattern, but do not reuse its admit/report schema or widen its API.
Any failure permanently poisons the in-memory writer; no reload/recovery method.

Record intended and acknowledged transition SHAs, immutable operation/merge
identity, phase budget use, the one target-update attempt, its bounded evidence,
and release state. Raw proof archives remain separate artifacts, not journal data.
Raw mutation stdout/stderr likewise use exclusive private sidecar files, with
their byte counts and SHA-256 digests in the journal; do not try to fit several
64KiB streams into the 64KiB journal. Persist these before accepting an outcome.
Missing/truncated/changed sidecars or a failed write poison the attempt, not a
reason to retry. Public reports contain validated/redacted metadata, not arbitrary
credential-bearing diagnostics.
Persist intent before every possible external mutation. A failed journal update
after an external effect never grants permission to repeat that effect.

### 4. Preserve the exact-update primitive's compatibility

Keep the existing public `push_exact_merge()` dry-run/apply contract intact.
Factor shared argument/environment and raw-parent checks only where necessary;
the finalizer's internal attempt path additionally retains bounded process
outcome and exact per-ref acknowledgment. Do not make the legacy wrapper silently
accept new failure cases or turn its dry run into a mutation.

For finalization, require one status row for exactly the requested source and
`refs/heads/main`. A successful ordinary fast-forward, successful process result
and exact merge readback are needed for `applied`. Forced/new/deleted/no-op rows,
extra rows, truncated output and contradictory process/status evidence fail closed.

For `not-applied`, require a definitive complete local-not-sent or remote-refusal
result, no outstanding process and reconciled target different from the merge.
`!` or nonzero exit alone is insufficient. Remote failure, lost output, timeout
and ambiguous parsing are `unknown`. Do not infer this attempt's acknowledgment
from a later successful read or confuse a custom hook's message with server proof.
The production source/configuration and configured hooks remain trusted inputs;
tests use a private controlled configuration and never disable a production hook.

### 5. Finalizer state machine and release semantics

The core receives typed verified inputs plus the coordinator-owned session,
never a readiness JSON as authority. Before claim: prove quiescence and perform
full readiness under the exact running anchor. Claim once, generating the
finalizer nonce internally. Authenticate signed claim/readback before proceeding.

After claim: run the full verifier again with the finalizing anchor and fresh
phase reader. Confirm returned IDs against the held inputs, then recheck the exact
owner/deadline and persist the single update intent. Submit the exact signed
merge once through the internal attempt path.

Before claim, a conclusive policy refusal may use the narrow
`running -> idle / failed` release only if current ownership and native/writer
quiescence remain verified, no claim or target-update request was submitted,
and the budget/deadline are healthy. Seal the unused postclaim phase and enter
the existing outcome allocation; do not borrow its allowance. Malformed or
unavailable evidence, exhausted limits, uncertain quiescence and attempted or
uncertain claim leave the operation stopped without this cleanup transition.
Never claim `finalizing` just to make cleanup reachable.

After submission, do not rerun the old-base PR predicate against the changed
base. Validate the actual acknowledgment, target readback, current owner and
terminal writer evidence. Build only the fixed legal completion:

| Result | Completion/release action |
| --- | --- |
| Conclusive preclaim policy refusal, with no claim/update submitted | Narrow `running -> idle / failed`, exact QID, null main SHA; only with verified ownership/quiescence and healthy bounds. No successful qualification-only fallback. |
| Proven failure after finalizing claim but before target attempt | `finalizing -> idle / failed`, exact QID, null main SHA; release once only if still owned/quiescent and within limits. |
| Acknowledged update and exact merge readback | `finalized`, exact QID and merge SHA; release once. |
| Definitive not-applied and reconciled different target | `rejected`, exact QID and observed target; release once. |
| Unknown target update, failed persistence or lost ownership | No release; stop and retain available evidence. |
| Release sent but acknowledgment/readback uncertain | Stop as `release-unknown`; preserve known target outcome separately. Remote may already be idle or newly owned. Never retry/reacquire/rewind. |

Postclaim releases are CAS from the exact finalizing tip; the narrowly allowed
preclaim failure release is CAS from the exact running tip. Both preserve the
operation binding, epoch and history. Lost acknowledgment of either release has
the same `release-unknown` handling. An already-applied release is not repaired
by restoring a held record. No recovery, stealing, renewal or resume is added.

## Planned slices and verification

These are planning units, not an approved executable checklist. Create the
module's `todo.md` only after this plan is accepted. Each unit includes tests
before implementation and a focused review; no unit intentionally exposes an
unsafe partial public path.

| Slice | Deliverable and acceptance | Likely changed files, maximum5 | Focused verification |
| --- | --- | --- | --- |
| FI-01 | Fixed phase/aggregate accounting and immutable deadline; exact limits, failed calls, phase restart and late-entry refusals. | `finalizer_limits.py`, `test_finalizer_limits.py` | New focused limit tests with deterministic clock. |
| FI-02 | Deadline-enabled strict reads and bounded child execution; old reader defaults unchanged; slow connect/body, cap, timeout and interrupt leave no helper. | `proof_api.py`, a narrow internal read helper if needed, `finalizer_limits.py`, `test_proof_api.py`, focused helper tests | Real `ProofAPI` counter/parser with fake HTTP boundary; owned-process cleanup tests. |
| FI-03 | Existing raw-object, signature, coordination and readiness verification share the same deadline; defaults preserved. | `merge_update.py`, `signed_merge.py`, `coordination.py`, `readiness.py`, `test_finalizer_validation.py` | Near-expiry full validator chain, signature descendant cleanup, and no nested deadline reset. |
| FI-04 | Durable `CoordinatorJournal` replaces direct rewrites; no existing journal adoption or unpoisoning; default output remains compatible. | `coordinator_journal.py`, `coordinator.py`, `test_coordinator_journal.py`, `test_coordinator.py` | Short-write/fsync/replace/path/inode failures and existing coordinator tests. |
| FI-05 | Internal exact-update evidence distinguishes applied/not-applied/unknown without breaking legacy API. | `merge_update.py`, `test_finalizer_update.py`, `test_merge_update.py` | Real local bare Git, lease races, refusal and accepted-but-lost-ack injection; stream caps. |
| FI-06 | Local integration fixture joins real signatures/archives/strict reads and actual local Git mutations. | `finalizer_fixture.py`, `test_finalizer_fixture.py`, `lifecycle_fixture.py` only if narrow reuse is necessary | Unexpected subprocess/network/credential lookup must fail before effects; exact command/ref inventory. |
| FI-07 | Complete finalizer core performs a positive claim/revalidate/update/release trace with fixed legal terminal outcomes. | `finalizer.py`, `test_finalizer.py`, `finalizer_fixture.py` | Real signed local trace; preclaim refusal can only release failed from running, without claim/update/fallback; postclaim failure release is distinct. |
| FI-08 | Ambiguity, late changes and race matrix closes unsafe paths, including lost release acknowledgment followed by a new acquisition. | `test_finalizer_failures.py`, `test_finalizer.py`, `finalizer.py` only for confirmed corrections, fixture if required | One winning claim/update, no replay, new owner untouched, near-expiry claim/release, all boundary faults and mutation controls. |
| FI-09 | Wire the internal completion mode into the same coordinator before the old successful release; leave CLI/default path unchanged. | `coordinator.py`, `finalizer.py`, `test_coordinator.py`, `test_finalizer_integration.py`, `finalizer_fixture.py` | One invocation end to end, default qualified-only regression and CLI refusal of any new finalize option. |
| FI-10 | Document source capability and remaining operational gates; run exact-tree full source acceptance and review. | `docs/FINALIZER-INTEGRATION.md`, relevant existing boundary docs, module checklist/evidence references | Full suite, manifest/patch, compile, Ruff, REUSE, workflow lint and scoped independent review. |

`ci/native/` and `tests/` prefixes are omitted in the table for readability.
If a unit would exceed5files or a focused session, split it at a tested internal
boundary before implementation; do not combine unrelated cleanup into it.

## Checkpoints

- **A, after FI-01–03:** budget/deadline tests cover the complete reader and
  existing signature/raw-object validator chain; legacy defaults remain intact.
- **B, after FI-04–06:** durable intent, real local updates and strict accounting
  are exercised together; simulation boundaries and signed fixtures are explicit.
- **C, after FI-07–09:** one complete same-process path plus ambiguity/race
  controls pass; no new CLI or native/GitHub dispatch. Review every lost-ack case.
- **Source acceptance, FI-10:** review the complete diff and evidence; only then
  propose source integration. No checkpoint authorizes live qualification.

Existing verification commands, from the repository root:

```sh
python3 -B -m unittest discover -s tests -v
python3 -B -m unittest discover -s tests -p 'test_proof_api.py' -v
python3 -B -m unittest discover -s tests -p 'test_coordinator.py' -v
python3 -B -m unittest discover -s tests -p 'test_merge_update.py' -v
python3 -B -m unittest discover -s tests -p 'test_lifecycle_integration.py' -v
python3 -B tools/patchset.py validate
ruff check --no-cache ci tools tests
python3 -B -m compileall -q ci tools tests
git diff --check
```

New `test_finalizer*.py` commands become meaningful only after those files exist.
Use the existing pinned Actionlint/REUSE procedures and public-source patch check
for final source acceptance. Keep compiler/cache output local and evidence shared.
Do not rerun unchanged expensive checks merely to repeat an old result.

## Risks and stopping conditions

| Risk | Response |
| --- | --- |
| Nominal full verification exceeds its phase cap | Demonstrate with real counting before enabling integration; stop and revise the approved scope rather than silently raising limits. |
| Deadline helper accidentally becomes a second writer or leaks credentials | Fixed read-only protocol, private pipes, no argv token, no inherited signing request; inspect allowed effects and test refusals. It is not a sandbox against compromised source. |
| Multiple observers are mistaken for a transaction | Preserve the approved cooperating-writer guarantee only; record metadata/noncooperating privileged-writer races without claiming atomicity. |
| Journal, process exit and remote state disagree | Keep explicit unknown state; no retry, fabricated acknowledgment or automatic recovery. |
| Source tests are mistaken for live qualification | Keep all fake HTTP/native boundaries labelled; R4 and older native evidence retain their original scope. |
| Existing signing remains unavailable | Preserve staged work and stop before publication; do not bypass signing or create unsigned substitutes. |

No new owner scope choice is currently needed beyond accepting this technical
plan. Any implementation discovery that changes the approved budget, supported
platform, trust boundary, schema or live scope must be surfaced before proceeding.

## Sources

- [Approved spec](../../SPEC-finalizer-integration.md), existing
  [coordination contract](../../docs/COORDINATION-DESIGN.md),
  [read-only verifier](../../docs/READ-ONLY-FINALIZER.md) and
  [current-policy rules](../../docs/MERGE-READINESS.md).
- [Git push](https://git-scm.com/docs/git-push): explicit leases and full-ref
  porcelain results; `remote failure` is not proof of refusal.
- [Python signal](https://docs.python.org/3/library/signal.html): signal handlers
  run in the main thread, can be delayed in C code and can interrupt critical work.
- [Python subprocess](https://docs.python.org/3/library/subprocess.html): a
  `Popen.communicate()` timeout does not itself kill/reap the child. Explicit owned
  process cleanup is part of the implementation, not an assumed library behavior.
