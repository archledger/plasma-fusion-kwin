<!-- SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com> -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
# Spec: disposable check-enforcement probe

Status: approved by the owner on 2026-11-01, with the maintainer-side protection
read split explicitly approved during planning. Implementation planning is in
`tasks/check-enforcement-probe/plan.md` and `todo.md`; those plans still need
approval. Source merge, live protection changes and dispatch remain separate gates.

Module: `check-enforcement-probe` in `CAPABILITY-MAP.md`.

## Objective

Establish how GitHub enforces the intended App-specific required check on an
actual protected disposable branch. Test server decisions, not just REST list
ordering or the local check selector. Record the exact rejection response and
remote ref before/after every attempted update.

This follows the successful native-v2 run `41278724261`; it does not repeat that
run, promote its proof to a new launcher, implement finalization or enable
production enforcement.

## Source installation and permissions

The owner selected a main-only source-installation path because the App secret
is restricted to the existing `native-gate-reporter` environment on `main`.
Prepare the probe in a reviewed PR. A separate source-only merge approval is
required before live use. Do not temporarily allow a validation branch into the
reporting environment.

After that merge, pin the actual new main SHA and workflow ID in a separate live
test package. Never invent these values before installation. The earlier native
proof remains evidence for its exact old launcher, not qualification of the new
source revision.

The new workflow is manual and main-only. Its ordinary token has only Contents,
Actions and Checks read permissions. Only its App-writing step receives the
existing environment secret. Reuse App `5544780`, with Checks-write and
Metadata-read only. No secret export, new App permission, CI Git push credential
or native execution is needed.

## Proposed files and reuse

| File | Responsibility |
|---|---|
| `.github/workflows/check-enforcement-probe.yml` | Manual, bounded, main-only App worker and evidence upload |
| `tools/probe_check_enforcement.py` | Strict stage/fixture validation, App operations and evidence records |
| `tests/test_check_enforcement_probe.py` | Offline identity, fencing, history and failure tests |
| `docs/CHECK-ENFORCEMENT-PROBE.md` | Maintainer procedure, live matrix and cleanup |

Reuse `CheckReporter.begin`/`complete`, `CheckJournal`, strict JSON/identity
helpers, bounded artifact retrieval and signed-commit verification. Do not
change the production coordinator, native workflow, qualification schema or
hard-main update primitive for this module.

Use the existing Python standard-library/unittest style, snake_case functions,
explicit validation and concise SPDX headers. For example, retain an explicit
guard rather than silently substituting a target:

```python
require(observed_ref == expected_ref, 'disposable target changed')
```

## Sandbox and write boundaries

The later live approval must name one experiment ID, source SHA, workflow ID,
fixture commits, exact ref names and protection JSON. Proposed namespace:

- `refs/heads/validation/check-enforcement-<experiment>-target`
- `refs/heads/validation/check-enforcement-<experiment>-case-a`
- `refs/heads/validation/check-enforcement-<experiment>-case-b`

All must be new, absent refs. Signed fixture commits carry a bounded,
experiment-specific marker; their objects must not equal main, the authority
tip, PR16's head or its signed candidate. Candidate contents are data only and
are never executed by the worker.

Only the maintainer may attempt Git updates, using an explicit allowlisted test
ref and exact expected tip. The worker has no Contents-write credential. Never
call the hard-main primitive against the production repository for this test.
Do not force-rewind or delete refs. Retain fixture/history refs as audit evidence.

Protect only the disposable target with the equivalent of:

```json
{
  "required_status_checks": {
    "strict": true,
    "contexts": [],
    "checks": [{"context": "Native KWin / trusted", "app_id": 5544780}]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "required_linear_history": false,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": false,
  "block_creations": false,
  "lock_branch": false,
  "allow_fork_syncing": false
}
```

The initial target must have no classic protection or inherited ruleset. Stop
on a conflicting existing rule; never weaken it. The exact request and effective
response must be saved. Administrator enforcement must be verified; no bypass
may be used to manufacture a positive result.

The context deliberately matches the intended native check, but only on the
new signed fixture objects. Reports and check summaries say **validation only**;
they must never be accepted as native qualification evidence.

## Live matrix

Use one maintainer coordinator, one local exclusive experiment journal and one
App worker at a time. No scheduler, heartbeat, failover or automatic recovery.
Each stage is a distinct run, attempt 1, completed and independently verified
before the next stage. No wall-clock race is used to catch a pending check.

| Step | Candidate/check state | Expected target update |
|---|---|---|
| Baseline | Case A has no required App check | Reject; tip unchanged |
| A1 | Create a fresh success on A; retain it as old success | No update attempted yet |
| A2 | Create a second, fresh failure on A; A1 remains unchanged | Reject despite A1 success |
| Identity control | Add an owner-created commit status named the same context, with success, while A2 fails | Reject; wrong provider must not satisfy the App requirement |
| A3 | Create a fresh current success on A, retaining A1/A2 | Accept exactly one fast-forward to A |
| B1 | On new candidate B based on accepted A, create old success | No update attempted yet |
| B2 | Create a fresh `in_progress` check on B and finish the worker after confirmed creation | Reject despite B1 success |
| B3 cleanup | Complete only B2's authenticated pending check as failure | Reject; tip stays A and no pending test check remains |

The live budget is **six App-only workflow dispatches**, at most six explicit
Git update attempts, and one wrong-provider status write. Native dispatches: 0.
Each worker has an eight-minute limit; the operator experiment has a one-hour
limit. These are maximum budgets, not permission to retry uncertain writes.

B2's worker success means only that the expected pending check was created and
acknowledged. It is not a passing required check. B3 must derive the exact B2
check ID, QID, nonce, original run URL and target from its authenticated artifact;
no caller-supplied arbitrary check ID is accepted. B3 records its own writer-run
identity separately and preserves B2's check identity. It never creates another
check, reopens a completed one or adopts an unknown write.

Every create/complete operation has a new phase journal. Unknown outcomes poison
that write and stop the sequence. Cleanup of a known acknowledged B2 check is
the only allowed B3 continuation after a failed test assertion, and only while
all identity/source/scope guards still pass. Otherwise report cleanup as blocked;
do not bypass a guard to tidy up.

## Fences, records and failure handling

Before each App write, the worker verifies:

- Exact installed launcher, main ref, workflow/run/actor/attempt and experiment.
- Exact production authority snapshot, which must remain unchanged throughout.
- Signed, new fixture objects and allowed ref names; no production target.
- Current expected test tip.
- Complete bounded predecessor artifacts, API digests and check history.

The maintainer verifies those identities plus the complete protection/App mapping
immediately before and after each test-target update attempt. A result counts
only if the protection snapshots match the approved configuration and the ref
readback establishes the outcome. The worker does not read full protection or
receive an Administration credential: GitHub requires Administration:read for
that endpoint. Use the maintainer's existing credentials for these observations;
do not expand the shared API reader or the App's permissions.

Use unique SHA-256 experiment/stage identities and fresh check IDs. Preserve all
earlier terminal records byte-for-field. Lists must be bounded, paginated and
complete; ambiguous IDs, changed totals, conflicting records or missing evidence
stop the test. Report scope must be unambiguously distinct from native receipts.

A rejected push counts only if the actual server response demonstrates the
intended required-check gate. Authentication, non-fast-forward, hook, review or
signature failures are not substitute evidence. Confirm the remote tip after
every attempt. An ambiguous push outcome requires reconciliation, not retry.

If any expected denial is accepted, stop. Do not force-rewind or continue a
matrix whose baseline changed. Preserve the failure and require a new design or
approval; never enable production enforcement on that result.

## Testing and verification commands

Existing development commands:

```sh
python3 -m unittest discover -s tests -v
ruff check .
git diff --check
```

Commands for the proposed files, once implemented:

```sh
python3 -m unittest discover -s tests -p 'test_check_enforcement_probe.py' -v
actionlint .github/workflows/check-enforcement-probe.yml
```

Use the existing digest-pinned REUSE copy-in container check from
`.github/workflows/fast-validation.yml`; no host bind mount or tool installation.
If a required validator is unavailable, report the gap rather than skipping it.

The later live runbook will use the documented `gh workflow run` interface,
for example the following **proposed, not-yet-implemented** workflow inputs:

```sh
gh workflow run check-enforcement-probe.yml \
  --repo archledger/plasma-fusion-kwin --ref main \
  --field experiment_id="$APPROVED_EXPERIMENT_ID" \
  --field stage=A1 \
  --field authority_sha="$APPROVED_AUTHORITY_SHA" \
  --field predecessor_run=""
```

No live command runs until its actual source revision, refs, fixture identities,
configuration and budget have been separately approved.

Offline tests must cover wrong targets/refs, source or authority drift, wrong
actor/App/workflow/attempt, malformed or incomplete predecessor evidence,
duplicate IDs, unacknowledged writes, B3 attempts to reopen terminal checks,
and stale expected tips. Negative cases must show no forbidden write occurred.
Use real temporary Git repositories for ref/parent behavior and test doubles
only at the App/network boundary. Targeted mutations of critical fences must
be caught. Preserve all existing tests and validation gates.

## Acceptance and cleanup

Source acceptance: offline tests, full existing suite, Ruff, workflow validation
and license checks pass; scoped independent review has no required findings.

Live acceptance: the complete matrix matches actual GitHub outcomes, all raw
responses/ref observations/check IDs and artifact digests are retained, only A3
advances the disposable target, prior records remain unchanged, and no pending
test check or running worker remains. Zero native runs or production mutations.

Restore only the temporary protection created for the exact test target after
all workers/writes are terminal and ownership/baseline checks still match.
Verify the original unprotected response. Keep audit refs; do not delete or
rewind them. If rollback cannot be established, stop and report the residual
setting instead of changing a wider scope.

## Approval gates and non-goals

1. Review this specification; no implementation before approval.
2. Review the source change and explicitly approve its source-only main merge.
3. Approve the fully pinned live package, six-stage budget, wrong-provider status
   write, exact disposable protection and its restoration.
4. Review measured results before specifying `finalizer-integration`.

Production `main` is changed only by the separately approved source installation,
never as the experiment's update target. Production enforcement, authority,
environment restrictions, App permissions, native workflow, installed desktop
and finalizing writes are outside this module. A new native qualification for a
future integrated launcher requires its own approval.

## Sources and remaining uncertainty

- Existing `tools/probe_check_lifecycle.py`, `ci/native/checks.py`,
  `ci/native/check_journal.py`, and `docs/MAIN-INTEGRATION.md` at `2f2d169`.
- [GitHub workflow dispatch](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_dispatch): default-branch registration and selected-ref execution.
- [Manual workflow execution](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow): `--ref` and input handling.
- [Branch protection API](https://docs.github.com/en/rest/branches/branch-protection): explicit App mapping, strictness and administrator enforcement.

Those APIs do not establish the same-name historical-check selection behavior
being tested. That remains deliberately unverified until the live matrix runs.
This specification makes no prediction a substitute for the measured result.
