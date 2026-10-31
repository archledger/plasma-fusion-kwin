<!-- SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com> -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
# Main integration and controlled cutover

Status: v2 source integration and operational gates, not authorization to merge,
change settings or run native qualification. The initial source stack was
integrated earlier; this change replaces completed-check reuse with fresh pairs,
versioned evidence and current signed-authority readiness checks.

## What is ready, and what is not

The manual four-job producer/coordinator and read-only verifier have local
regression coverage, including cross-boundary fixtures. The earlier hosted native
run qualified its recorded source/environment, not this new v2 contract.

The source-development baseline is main `7714ca7`. The recorded production
operation is held at `97e435f`, generation 8, under the old contract. Reverify
both before any operation. Do not reuse that grant with v2 or create a second
authority root. The null-reset diagnostic returned HTTP 422; it is not a reason
to repeat a native run, and does not explain the first reporting failure.

The coordinator's successful outcome is `qualified-only`. It does **not** merge
main. The read-only verifier also always returns `merge_authorized: false`.
Production use of the exact-result finalizing write path requires a separate
reviewed integration under the same held coordination operation. Do not enable
merge enforcement or replace that requirement with a GitHub-generated merge.

Stock KWin, end-user packaging, releases, desktop installation, physical GPUs
and self-hosted runner registration remain outside this plan.

## Approval boundaries

Request distinct approval for:

1. **Source integration:** merge the reviewed v2 source PR into main, without
   changing required checks or starting the native workflow.
2. **API qualification:** explicitly approve the bounded three-run disposable
   fresh-pair sequence. Source merge does not authorize dispatch.
3. **Old-operation reconciliation:** verify quiescence and append the reviewed
   retirement of the held old-contract operation. Never reset/delete its history.
4. **Native qualification:** approve a new operation, nonce, policy and budget
   after the v2 API contract is qualified.
5. **Enforcement:** separately qualify the exact protected-ref behavior and
   finalizing write path before enabling the required App/check mapping.

Approval of an earlier phase does not imply approval of a later one. Keep the
current branch/protection/environment settings as a recorded rollback baseline.
Never export, recover, replace or upload the App private key for these steps.

## Phase 1: install reviewed source only

- Verify the integration head and its complete main diff; require source tests,
  pinned patch application, workflow lint, licenses and signed/DCO history.
- Preserve the initial integrated history, including `54b7383` and `5aed169`,
  and retain original branches and experiment refs as recorded evidence.
- Use an ancestry-preserving merge commit for this integration, not squash or
  rebase-and-merge. Check both original commits remain reachable from main
  afterward. GitHub may mark stacked PRs as indirectly merged when their heads
  become reachable; verify each status rather than closing drafts in advance.
- With explicit source-integration approval, merge the v2 source PR. Capture
  the resulting main SHA and fresh workflow ID for `trusted-native.yml`; do not
  reuse a guessed workflow ID or the source branch's older SHA as the launcher.
- Confirm that the workflow remains manual, main-only, with a read-only ordinary
  token. Its private App key is scoped only to the protected admit/report steps.
- Leave required-native-check enforcement off. No native run starts as a side
  effect of this source integration.

## Phase 2: qualify fresh check pairs without a native build

After the v2 probe source is installed, follow the
[disposable lifecycle probe](CHECK-LIFECYCLE-PROBE.md). Approve the dispatch
count explicitly: A completes failure, B success, C failure, with one actual
run and fresh pair per stage. Each successor authenticates its predecessor.
Earlier records remain unchanged. Keep the old production authority held for
this sequence; no stage releases it.

Stop on an uncertain write, missing journal/artifact, changed ref, identity
mismatch or failed probe. Do not retry automatically. Source tests do not prove
live API behavior, and this sequence does not prove GitHub enforcement behavior.

## Phase 3: reconcile the existing authority domain

The production ref already exists. Before another native admission:

- Inventory current workflow IDs/states and all pending/running jobs. Disable
  the retired unsynchronized guest/native experiment entry points, with explicit
  approval and a record of their previous states. Inspect at least
  `hosted-native-preflight.yml`, `hosted-guest-boot.yml`,
  `hosted-kvm-container-probe.yml`, the validation worker/handoff workflows and
  historical status-writer/spoof workflows still registered from test branches.
  Distinguish read-only probes and image publishers from actual native/App
  writers; do not disable unrelated workflows as a blanket cleanup.
- Reconcile all old coordinators, jobs, native runtimes, App writers and pending
  updates. A cancelled job, dead PID or elapsed timeout alone is insufficient.
  Unknown activity blocks cutover.
- Independently verify the held tip, signer, raw parent, generation, epoch,
  cutover digest and current ref protection. Preserve existing failure evidence.
- Obtain approval for the exact signed append-only retirement of the old
  operation, after its unknown writes and runtime cleanup are reconciled.
- Keep the existing authority domain and history. Do not bootstrap a replacement
  root, reset its generation or rename a validation ref into production.
- Pin the new launcher/verifier and retain the independent trusted idle anchor
  for a later new operation. Never select an anchor from a proof artifact or by
  newest timestamp/check ID.

The ordinary coordinator must continue refusing a missing, changed or held ref.
No expiry, lock stealing, heartbeat, renewal or automatic recovery is added.

## Phase 4: qualify a new native operation before enforcement

Use one explicitly approved, harmless PR against the installed main source.
For the initial test, a manifest-only change that leaves the KWin payload
unchanged avoids testing a new compositor patch at the same time. It must still
be a real open, non-draft, requested PR with exact live refs and a signed final
two-parent candidate; a label or historical success is not admission.

1. Independently establish the candidate's manifest/patch digest and expected
   prepared source bytes. Record a complete operator-approved admission; never
   take its expected source checksum from guest output or an unchecked artifact.
2. Build the trusted policy from the new main launcher, actual workflow ID,
   repository/App IDs, pinned signing key and exact package inventory. Preserve
   the reconciled idle anchors. Run the coordinator's default preflight first.
3. Approve one bounded `coordinate --apply` operation with a new private journal.
   Native execution retains the 75-minute job envelope, offline guest, pinned
   images, guest-only vgem and explicit cleanup checks.
4. Verify actual isolated preparation, run-bound admission before App writes,
   both fresh admitted check IDs, one-shot native claim, all 43 required native
   entries without failures/skips, exact source/input/nonce/QID binding and
   terminal runtime cleanup. Both check completions must be verified.
5. Download both admission and qualification artifacts by authenticated IDs and
   verify their ZIP digests. Use the coordinator's verified qualification and
   current-association result for this pre-enforcement phase. Also run the
   read-only finalizer against those bytes and current PR/review/protection
   state, recording its result. No check mark alone is proof.
6. Any repeat native run needs its own approved budget and fresh operation.
   Never reopen a completed check or adopt an existing ID. Missing or mismatched
   v2 evidence, partial publication or completion, and a newer signed operation
   must stop qualification and keep authority held.

With enforcement intentionally off, the full read-only verifier is expected to
refuse readiness because the required native App/check mapping is absent. This
does not invalidate a separately verified qualification proof. Record that
specific refusal; do not treat unrelated verification errors as expected or
weaken the verifier to obtain success while required protections are absent.

## Phase 5: qualify enforcement separately

Before enabling required checks, approve a disposable protected-ref experiment
matching the intended rules and update path. Retained old success must not admit
a current pending or failed attempt; current success must pass the positive
control. Record the actual server rejection reason. REST ordering and the local
selector do not prove GitHub's own required-check selection behavior.

The lock-held finalizing write also needs its own reviewed qualification. If
either gate is unproven, enforcement remains disabled; there is no bypass here.

## Halt and rollback

- Before cutover: stop; main and existing settings remain unchanged. Close or
  amend the integration PR only through normal review, not a history reset.
- After source integration but before a run: leave the manual workflow unused
  or disable it with approval. If a source correction is needed, use a reviewed
  forward fix or revert commit; do not force-rewind main.
- During or after a failed/ambiguous operation: stop admission, retain the held
  signed state and all failure evidence. Do not release based on time or job
  conclusion alone. Reconcile old writers and native resources before any
  separately approved append-only retirement.
- Restore recorded workflow or protection settings only after confirming this
  cannot revive an old writer or allow overlapping operations. Do not delete the
  authority ref, lower checks to manufacture success or recover the App key.
- Keep source bundles, exact policies without credentials, settings snapshots,
  signed state history, journals and bounded evidence in the shared handoff and
  checksum-verified project archive.

No phase in this plan changes the installed desktop or makes patched KWin a
dependency of ordinary Plasma Fusion installation.

## References

- [Producer contract and coordinator usage](QUALIFICATION-EVIDENCE.md)
- [Coordination ownership and failure boundaries](COORDINATION-DESIGN.md)
- [Recorded hosted qualification](NATIVE-GUEST.md)
- [GitHub merge strategies and indirect merges](https://docs.github.com/en/pull-requests/reference/pull-request-merges)
- [GitHub manual workflow prerequisites](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)
