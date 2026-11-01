<!-- SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com> -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
# Disposable required-check enforcement probe

This source implements an App-only worker, not native qualification or a merge
authorizer. It has not established GitHub's required-check selection behavior
until the separately approved live matrix has run.

## Approvals and identities

1. Review this source and obtain a separate source-only main merge approval.
2. Pin the installed main SHA and actual workflow ID. Prepare a reviewed live
   package containing one experiment ID, fixture commits, exact refs, production
   authority snapshot, target protection request, budgets and restoration plan.
3. Obtain approval for that complete package before creating live refs, changing
   protection, writing a test status or dispatching any stage.

The workflow runs only from main in the existing `native-gate-reporter`
environment. Ordinary workflow permissions are Contents/Actions/Checks read.
Only the App step receives the existing private key; its installation token is
restricted to this repository, Checks-write and Metadata-read. No Git write
credential, Administration permission, private-key export or native job is used.

Full protection reads belong to the maintainer. Read them immediately before
and after each target-update attempt and require the approved mapping on both
sides. The worker checks source/authority/fixture/ref identities before each
App write. These observations are not an atomic lock against arbitrary
privileged actors changing settings between reads.

## Fixtures

For a fresh 32-hex experiment ID, the only permitted refs are:

```text
refs/heads/validation/check-enforcement-<experiment>-target
refs/heads/validation/check-enforcement-<experiment>-case-a
refs/heads/validation/check-enforcement-<experiment>-case-b
```

Require all three refs to be absent before setup. The target starts at the
installed launcher commit. A is a signed, single-parent child of that commit;
B is a signed, single-parent child of A. Each changes only the regular root
file `check-enforcement-fixture.json`, with this strict marker shape:

```json
{
  "schema_version": 1,
  "scope": "disposable check-enforcement probe only; no native qualification",
  "experiment_id": "<actual 32 lowercase hex characters>",
  "case": "A",
  "parent": "<exact launcher commit>"
}
```

For B, use case `B` and A as the parent. The installed launcher must not already
contain the marker. Use the approved maintainer signature and sole owner DCO
footer. Preserve the base tree apart from the marker; never check out or execute
candidate code in the worker. The exact marker path has a CC0 annotation in
`REUSE.toml`, so fixture data does not require changing a second file.

The worker checks raw signed parents and marker content, not `git log` display.
Fixture objects cannot equal main or the production authority; the required
marker-only graph also excludes the old PR16 candidate objects.

## Disposable target protection

Use only the approved test target. Verify the branch exists, its baseline
protection is absent, and no inherited rule applies. The required configuration
is in the approved specification: strict required status checks, administrator
enforcement, and `Native KWin / trusted` from App `5544780` only. Do not fall back
to any-provider contexts or use an administrative bypass for the positive case.

A target-update attempt must use an explicit refspec and exact expected tip.
Validate the target against the derived experiment name before running Git:

```sh
test "$TARGET" = "refs/heads/validation/check-enforcement-$EXPERIMENT_ID-target" || exit 1
git push --porcelain --no-follow-tags --recurse-submodules=no \
  --force-with-lease="$TARGET:$EXPECTED_TIP" \
  origin "$APPROVED_CANDIDATE:$TARGET"
```

The lease is not permission to rewrite history. Verify the raw parent graph
first; the candidate must fast-forward the expected test tip. Never substitute
`refs/heads/main`, reset the target, delete refs or call the hard-main writer.

Retain exit status, stdout/stderr, exact refspec, protection snapshots and remote
tip before/after. A rejection caused by authentication, non-fast-forward, hooks,
reviews or signatures does not prove the required-check gate worked.

## Six-stage sequence

The maximum live budget is six App-only dispatches, six explicit target-update
attempts and one wrong-provider commit-status write. Native runs: zero.

| Step | Action and required observation |
|---|---|
| Baseline | Attempt A with no App check: required-check rejection, target unchanged. |
| A1 | Create/complete a fresh success on A; do not update target yet. |
| A2 | Create/complete a fresh failure on A; retain A1 success. Attempt A: reject. |
| Identity control | Create the single approved owner status named `Native KWin / trusted`, success, on A. Attempt A: reject despite that wrong-provider status. |
| A3 | Create/complete a fresh success on A. Attempt A: accept exactly that fast-forward. |
| B1 | Create/complete a fresh success on B; target remains A. |
| B2 | Create a fresh pending check on B and confirm its ID; do not complete it. Attempt B: reject despite B1 success. |
| B3 | Complete only B2's known pending check as failure. Attempt B: reject; no pending test check remains. |

Dispatch one stage only after its preceding workflow/artifact has completed and
been independently verified. Use the real approved values, for example:

```sh
gh workflow run check-enforcement-probe.yml \
  --repo archledger/plasma-fusion-kwin --ref main \
  --field experiment_id="$EXPERIMENT_ID" \
  --field stage=A2 --field authority_sha="$AUTHORITY_SHA" \
  --field expires_at="$EXPIRES_AT" \
  --field predecessor_run="$VERIFIED_A1_RUN"
```

A1 has an empty predecessor. Every other stage requires the immediately prior
run from the same experiment, launcher and actor. Each worker has an eight-minute
limit; the whole operator experiment has a one-hour limit. Do not blindly rerun.

Set `EXPIRES_AT` once from the approved operator start time, using canonical UTC
seconds (`YYYY-MM-DDTHH:MM:SSZ`), and pass the identical value to every stage.
An expired queued invocation refuses before App-token creation. The worker
rechecks expiry after network fences and immediately before each check request,
including B3 cleanup; it cannot extend the deadline to tidy a pending check.
The expiry cannot exceed one hour after run creation, and predecessor reports
must carry the same value. An in-flight request can still complete after expiry;
its outcome must be reconciled without retry or a deadline extension.

Probe reports now use schema version 2. Their expiry is included in each stage's
QID, and historical report validation does not require the expiry to remain in
the future. Version-1 reports are not accepted as current-stage predecessors.

## B3 is a completion, not a new origin

The worker authenticates B2's successful run, sole probe job, artifact digest,
report and confirmed creation journal before minting an App token. It derives
the pending check ID, QID, nonce and details URL from that evidence. B3's writer
run is separate; its reporter and new report journal use B2's origin identity.

B3 never creates a check, reopens a completed one, adopts an unknown write or
accepts an arbitrary check ID. A terminal or changed check causes refusal. The
result report updates only B2's check snapshot and retains every earlier terminal
record. Failure records are diagnostic data, not success evidence.

## Halt, cleanup and interpretation

Stop on changed source/authority/ref, unexpected acceptance, missing/ambiguous
artifacts, unknown write outcome or a failed fence. Do not force-rewind to keep
the matrix going. Keep prior records and report what happened.

The only allowed post-failure continuation is the explicitly approved B3
completion of a known, acknowledged pending check, while all identity/ref fences
still hold. Otherwise report cleanup as blocked and seek a new decision.

After all workers/writes are terminal, restore only the temporary protection
created for the exact target, and verify its original unprotected state. If
ownership or baseline differs, stop rather than remove someone else's setting.
Retain fixture refs and check history. Confirm no pending test check remains.

The final report must separate measured server outcomes from source tests. Only
the complete successful matrix qualifies this enforcement mechanism. It does
not enable production enforcement, complete a finalizing write, qualify a new
native launcher or authorize merging PR16. A failed matrix blocks those claims.
