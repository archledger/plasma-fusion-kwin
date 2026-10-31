<!-- SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com> -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
# Specification: fresh checks for each trusted operation

Status: contracts and implementation plan approved by the owner on 2026-10-31;
implementation is in progress. No live operation is authorized by this document.
Baseline: `7714ca719b6a1a79c3e6348ffa13a601292cee35`.

## Objective and evidence

Replace completed-check reuse with fresh native and activation records for each
admitted operation. Preserve previous results, exact operation identity, refusal
of observed duplicates and replays, and the one-coordinator/one-worker model.

App-only run `41164646359` returned HTTP 422 for the null-valued `conclusion`
and `completed_at` reset request. That request is not a supported lifecycle.
This does not establish the cause of the first native run's reporting failure.

Source acceptance means the contracts below work in regression tests. Live API,
native qualification and merge enforcement are separate gates. Stock KWin,
packaging, installations and the finalizing main-write path are out of scope.

## Trust boundary and limitations

The existing signed coordination ref identifies the admitted operation. The
trusted main-only workflow is its sole cooperating check writer: one admission
job creates records, and its dependent report job completes them. The protected
App environment and permissions stay unchanged. No other job may write those
production contexts. An uncertain operation stays held; a new invocation is
not a recovery mechanism.

Neither a list-before-POST nor a GET-before-PATCH is atomic. This design does
not claim API idempotency, global uniqueness, conditional updates or protection
against a competing privileged writer. Establishing the single-writer boundary
is a cutover prerequisite. If that boundary is violated, stop and reconcile;
do not resolve the conflict by choosing the newest record.

Check lists are bounded observations, not a complete historical ledger. GitHub
limits the reference endpoint to 1000 recent suites and may delete older
same-name records within a suite. A short page cannot prove no hidden record
exists. Safety must therefore come from the admitted operation, exact check
IDs, trusted emitter and no-retry protocol, not absence from a list.

## Versioned evidence contract

Keep the signed coordination schema and the version-1 `identity`/QID formula.
Introduce an explicit version-2 outer envelope for new evidence:

| Artifact | Required changes |
| --- | --- |
| `admission.json` | Add `schema_version: 2` and `checks` with exactly `native_check_id` and `activation_check_id`; retain `identity`, `qualification_id` and `input_manifest_sha256`. |
| `qualification.json` | Add `schema_version: 2`; retain existing fields and add `activation_check_run_id` to `evidence`. Existing `check_run_id` remains the native ID. |
| Verified evidence in memory | Carry both check IDs, the verified operation nonce and native job ID into current-policy validation, alongside the existing verified identity. |

Both IDs are distinct positive integers and must match the admission artifact
exactly. Its authenticated repository, workflow, launcher, run, attempt, admit
job, digest and creation interval must match the operation. An artifact name,
App-written QID, run URL or caller-supplied ID alone is never authority.

The native job verifies the complete pair before claiming or executing. The
report job obtains its pair from authenticated admission evidence, not from
native output or check discovery. Historical qualification verifies both exact
IDs directly, including their App, names, commits, QID, canonical run URL,
success conclusions and completion times within the authenticated report job.
All existing source, runtime, cleanup and four-job provenance checks remain.

New production verification accepts only the explicit v2 envelope for its
pinned launcher. No missing-version fallback, mixed pair or v1 adoption is
allowed. Old artifacts and their pinned verifier source remain available for
historical inspection; they are not upgraded or relabelled successful.

## Creation, completion and partial failure

For each role, `begin` performs one POST and never PATCHes an existing record.
The payload names the exact commit, context, QID and canonical run URL, with
`status: in_progress`; it omits `conclusion` and `completed_at`. Verify the
returned ID and require pending status with null completion fields. Read that
exact ID back before admitting the complete pair. Never adopt an existing ID.

Completion accepts only the admitted ID. Verify its pending identity, fence
current operation ownership, issue one PATCH with the explicit outcome, then
verify that ID's terminal identity and outcome. Do not change its QID, name,
commit or URL. A terminal record is not eligible for another completion.
Recheck the complete pair after reporting. Any disagreement fails the job.
These are cooperating-writer rules, not an atomic server-side immutability claim.

Before every POST or completion PATCH, save a bounded local write-intent record
in the private runner workspace. Include the operation identity, role, action,
expected ID if known, and phase; record each validated acknowledgment before
the next mutation. A persistence failure prevents the request. Each role may
be created and completed at most once by this operation. Never repeat a request
after an error, timeout, interruption, mismatched response or missing journal.

The journal is diagnostic evidence, not admission authority. Limit it to 64 KiB
and a fixed schema; use the existing safe error classifications, never tokens,
raw HTTP messages or arbitrary response bodies. Admit and report jobs each
attempt an `always()` upload under separate run/attempt/role-specific names.
Runner loss can prevent upload: missing evidence means unknown, not no write.

| Failure point | Required disposition |
| --- | --- |
| Before an intent can be persisted | No request; fail the job. |
| First create confirmed; second fails or is uncertain | Retain known ID and pending phase; no complete admission, native work or automatic completion. Keep authority held. |
| Both creates confirmed; admission publication fails | Do not run native work. IDs remain evidence only until the exact admission artifact is authenticated. Keep authority held. |
| Native execution fails after valid admission | Report failure only against that admitted pending pair, under the existing ownership fence. Native proof remains failed. |
| Either completion is uncertain or mismatched | Stop further writes. A successful response for the other role cannot make the operation successful. Keep authority held. |
| Journal/artifact unavailable after interruption | Treat the affected writes as unknown. No scan-and-adopt, retry, inferred success or automatic release. |

The normal admission artifact remains success-only. A failure journal must not
be substituted for it. Preserve current native/report dependency gates; adding
diagnostic uploads must not make failed admission eligible for execution.

## Selection and observed conflicts

Use the exact pair from authenticated admission evidence. For each role, direct
GET must match its expected ID, App, commit, name, QID and canonical run URL.
The production contexts remain `Native KWin / trusted` and
`Native KWin / activation #<PR-number>`.

Retain bounded `filter=all` inventory checks before creation, before native
execution and during current-policy observations. Preserve the current page,
response-size, count-consistency and duplicate-ID checks; exhaustion fails.
For the matching App/context, refuse:

- A pre-existing current QID or current run during creation.
- A second ID for the selected QID/run, a QID mapped to different runs, or one
  run mapped to multiple QIDs. Apply these conflict checks to visible history
  as well as the selected record.
- A malformed matching record, wrong commit or another nonterminal record.
- A changed expected ID or selected record between required observations.

During selection, only the exact expected ID is exempt from conflict counting;
sharing its QID or run is not an exemption. Validate its expected pending or
terminal state separately. Other well-formed terminal records remain inert
history, whether successful or failed. They cannot replace, authorize or revoke
the signed active operation. Do not authenticate their provenance merely by
following their App-written run URL.

Do not sort or select authority using check IDs, timestamps, workflow run
numbers, `filter=latest` or the latest successful result. Missing/pruned expected
evidence fails closed. Do not extend these historical-record allowances to
unrelated required check providers; their existing strict policy is unchanged.

## Current signed authority and read-only readiness

Add a required operator-supplied `--coordination-anchor` input to the read-only
proof command. Its strict fields are the existing anchor fields: `commit`,
`parent`, `generation`, `epoch`, `cutover_digest`. It is a trusted anchor from
the coordinator or a reviewed reconciliation, not data discovered in a proof
artifact. Require its signed Git objects locally; do not fetch or choose anchors
automatically. Apply the existing bounded regular-file and signature checks.

Verify that anchor's exact ref tip before and after readiness observations,
using only the fixed production coordination ref. The signer, repository,
target, parent, generation, epoch and cutover digest must all match. Signature
and schema validation alone do not prove a valid transition or quiescence;
the operator's independently established protocol anchor remains a trust input.

The signed state must satisfy one of these exact predicates:

| State | Required binding to fully verified v2 evidence |
| --- | --- |
| `running` or `finalizing` | Operation ID and nonce equal the verified execution nonce; the recomputed QID, workflow, launcher, admission, run ID/number/attempt and native job ID agree with the signed operation/grant. |
| `idle` | Completion outcome is `qualified-only`, QID equals the evidence QID, completion operation ID equals its execution nonce, and `main_sha` is null. |
| Any other state or binding | Refuse current readiness, including a new reserved/dispatching/granted operation or an idle failed/cancelled completion. |

Continue all current PR, base/head, protection, review and revocation checks.
Recheck selected IDs and current authority within the observation budget.
Always return `merge_authorized: false`. Two reads do not create a lock or close
cross-system races. Implementing the final lock-held main write is still separate.

## Cutover and external qualification gates

The held `97e435fe4439d125621a9ee3d3d2cd7fe80dc8d0` operation is not reusable
under v2. Its existing check records and failed evidence remain untouched.
Source merge, reconciliation and execution are separate approvals:

1. After source acceptance, obtain approval to merge the reviewed v2 source.
   A source merge itself does not authorize any check or authority write.
2. Before new production admission, separately verify old writers are quiescent
   and reconcile the old operation through an approved signed append-only
   transition. Unknown writes remain held. Never reset or delete the ref.
3. Pin the new launcher and v2 verifier; use a fresh nonce/QID and new admission.
   An old launcher, old receipt or in-flight operation cannot cross the boundary.

Live App testing uses a fixed disposable ref and validation-only names. To test
failure to new pending, success to new pending and immutable older records, use
three actual workflow dispatches: A completes failure, B completes success,
C completes failure. Reuse the same two validation contexts, but each dispatch
creates only its own pair and uses its real run identity. Require explicit
approval for the bounded dispatch count; never retry an attempt automatically.
Each successor consumes its predecessor's authenticated validation evidence;
a missing or uncertain predecessor stops the sequence.

Its active validation pair is explicit test evidence, never production
authority. Previous successful validation records remain visible; C's failure
does not delete or revoke B. Keep all records and interruptions. Local fixtures
cover replay and conflict cases that cannot be safely induced live. This probe
tests the API lifecycle, not native execution or production readiness.

GitHub's own required-check selection is a separate unresolved external gate.
Do not infer it from the custom selector or REST list ordering. Before enabling
enforcement, require a separately reviewed and approved disposable protected-ref
experiment matching the intended rules and update path: retained old success
must not admit current pending/failure, and current success must pass the
corresponding positive control. Record the actual rejection reason. If the gate
cannot be demonstrated, enforcement stays disabled; this spec supplies no
workaround or policy bypass. Full native qualification and finalizing-write
qualification are also prerequisites, not implied by successful API probes.

## Implementation surface and style

| Area | Expected files |
| --- | --- |
| Check client and bounded journal | `ci/native/checks.py` and focused check tests. |
| Trusted pair propagation and failure uploads | `ci/native/producer.py`, `qualification.py`, `coordinator.py`, `.github/workflows/trusted-native.yml` and their tests. |
| Current authority observation | `ci/native/current_policy.py`, `readiness.py`, `proof_api.py`, `tools/verify_native_proof.py` and their tests. |
| API-only contract experiment and documentation | `tools/probe_check_lifecycle.py`, its workflow/tests and the lifecycle, qualification, readiness and integration docs. |

Keep standard-library Python, existing four-space style and SPDX headers. Use
explicit validation rather than assertions for security or identity checks:

```python
require(actual_id == expected_id, 'check identity differs')
```

No new dependency, scheduler, daemon, heartbeat, renewal, failover, credential
store or persistent authority is needed. Preserve pinned tooling and permissions.

## Regression acceptance and commands

Require passing cases and negative controls for:

- Fresh POST after prior failure and success, exact pair readback and no reset
  PATCH. Preserve the observed HTTP 422 regression; remove no safety assertion
  merely to pass a fixture that simulated unsupported reset behavior.
- Every identity field, duplicate/replay vector, missing or changed ID, visible
  conflicting history, bounded-list exhaustion and malformed API response.
- Lost create response, failed second create, failed publication, missing journal,
  partial completion, declined ownership fence and no automatic subsequent write.
- Authenticated admission/result pair equality, v1/mixed-envelope refusal,
  wrong job/run/attempt/launcher, and unchanged native/cleanup requirements.
- Current running/finalizing and qualified-only idle predicates, all other state
  refusals, nonce/job mismatch, changed ref, and old success after a new operation.
- Production one-run refusal versus real multi-run validation evidence. No test
  may manufacture live workflow identities or claim global API uniqueness.

Use mutation tests for removed ID/QID/nonce checks, skipped fences, adopted
existing records, ignored partial failures and missing version checks. Review
findings are not closed until the relevant code and negative tests exist.

Existing local commands, with no native run implied:

```sh
python3 -B -m unittest discover -s tests -v
python3 tools/patchset.py validate
ruff check --no-cache ci tools tests
python3 -m compileall -q ci tools tests
git diff --check
```

Also run the pinned Actionlint and REUSE checks and the existing fast workflow's
exact public-source patch application validation. Match evidence to the tested
revision. A documentation-only change need not rerun unchanged native tests.

## Approval and next gate

The owner approved these contracts and the cooperative single-writer boundary
on 2026-10-31. The implementation plan was subsequently approved. This is
not approval for merge, App dispatch, held-state reconciliation, native execution,
settings changes or enforcement. Those remain explicit later gates. The revised
text has not received a new independent or cross-model review.

## Sources

- [GitHub Checks REST API, version 2022-11-28](https://docs.github.com/en/rest/checks/runs?apiVersion=2022-11-28): creation, update and listing contracts. The retained retrieval includes the suite and history limits.
- [Existing lifecycle probe](CHECK-LIFECYCLE-PROBE.md): historical reset experiment, superseded by the recorded HTTP 422 result for that request.
- [Qualification evidence](QUALIFICATION-EVIDENCE.md), [read-only finalizer](READ-ONLY-FINALIZER.md), and [integration boundaries](MAIN-INTEGRATION.md): existing contracts that implementation must update without weakening unrelated gates.
