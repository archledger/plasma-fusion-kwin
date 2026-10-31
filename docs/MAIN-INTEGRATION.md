<!-- SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com> -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
# Main integration and controlled cutover

Status: review plan, not authorization to merge, change settings or run native
qualification. The integration branch preserves the source stack through
`54b7383fa45b7a9df073a93a8421fb781f3975ef` and the original signed documentation
commit `5aed1697bbba754f204642c0c187aba55acf80e4`. The conflicting builder history
is reconciled chronologically; neither result is promoted beyond its evidence.

## What is ready, and what is not

The manual four-job workflow and its producer/coordinator are source-validated.
The earlier hosted native run qualified its recorded source and environment,
not this newly assembled producer. Real preparation, App check reopening and a
complete producer proof still need operational qualification.

The coordinator's successful outcome is `qualified-only`. It does **not** merge
main. The read-only verifier also always returns `merge_authorized: false`.
Production use of the exact-result finalizing write path requires a separate
reviewed integration under the same held coordination operation. Do not enable
merge enforcement or replace that requirement with a GitHub-generated merge.

Stock KWin, end-user packaging, releases, desktop installation, physical GPUs
and self-hosted runner registration remain outside this plan.

## Approval boundaries

Request distinct approval for:

1. **Source integration:** merge the reviewed integration PR into main, without
   changing required checks or starting the native workflow.
2. **Controlled cutover and qualification:** retire nonparticipating execution
   paths, configure the append-only authority ref and run the bounded tests below.
3. **Enforcement:** only after qualification and the finalizing write-path gate
   pass, enable and verify the exact required App/check mapping.

Approval of an earlier phase does not imply approval of a later one. Keep the
current branch/protection/environment settings as a recorded rollback baseline.
Never export, recover, replace or upload the App private key for these steps.

## Phase 1: install reviewed source only

- Verify the integration head and its complete main diff; require source tests,
  pinned patch application, workflow lint, licenses and signed/DCO history.
- Confirm both `54b7383` and `5aed169` are ancestors. Preserve the original
  stacked PRs and experiment refs until their disposition is recorded.
- Use an ancestry-preserving merge commit for this integration, not squash or
  rebase-and-merge. Check both original commits remain reachable from main
  afterward. GitHub may mark stacked PRs as indirectly merged when their heads
  become reachable; verify each status rather than closing drafts in advance.
- With explicit source-integration approval, merge the integration PR. Capture
  the resulting main SHA and fresh workflow ID for `trusted-native.yml`; do not
  reuse a guessed workflow ID or the source branch's older SHA as the launcher.
- Confirm that the workflow remains manual, main-only, with a read-only ordinary
  token. Its private App key is scoped only to the protected admit/report steps.
- Leave required-native-check enforcement off. No native run starts as a side
  effect of this source integration.

## Phase 2: establish one authority domain

Before creating `refs/heads/native-gate-coordination`:

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
- Record the allowed launcher, retired entry points and quiescence evidence in
  a maintainer-signed cutover record. Its SHA-256 becomes `cutover_digest`.
- Create one signed generation-0 idle root with a fresh epoch, null operation,
  grant and completion. This is explicit bootstrap, not ordinary acquisition.
- Protect the authority ref against deletion and non-fast-forward updates and
  verify the actual server behavior. Do not impose an incompatible PR-only rule
  on its normal signed single-parent compare-and-swap updates.
- Independently fetch and verify the root, signer, epoch, digest and protection.
  Keep the validation refs as audit history; do not rename them into production.

The ordinary coordinator must continue refusing a missing, changed or held ref.
No expiry, lock stealing, heartbeat, renewal or automatic recovery is added.

## Phase 3: qualify the real producer before enforcement

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
   the current idle anchors. Run the coordinator's default preflight first.
3. Approve one bounded `coordinate --apply` operation with a new private journal.
   Native execution retains the 75-minute job envelope, offline guest, pinned
   images, guest-only vgem and explicit cleanup checks.
4. Verify actual isolated preparation, run-bound admission before App writes,
   one-shot native claim, all 43 required native entries without failures/skips,
   exact source/input/nonce/QID binding, and terminal runtime cleanup.
5. Download both admission and qualification artifacts by authenticated IDs and
   verify their ZIP digests. Use the coordinator's verified qualification and
   current-association result for this pre-enforcement phase. Also run the
   read-only finalizer against those bytes and current PR/review/protection
   state, recording its result. No check mark alone is proof.
6. Qualify the App lifecycle, including reopening an existing completed check
   on a fresh serialized operation. A retained conclusion, mismatched check,
   ambiguous response or newer operation must stop the test. Any repeat native
   run requires its own approved budget; do not silently retry.

With enforcement intentionally off, the full read-only verifier is expected to
refuse readiness because the required native App/check mapping is absent. This
does not invalidate a separately verified qualification proof. Record that
specific refusal; do not treat unrelated verification errors as expected or
weaken the verifier to obtain success while required protections are absent.

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
