<!-- SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com> -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
# Disposable App check lifecycle probe

Status: fresh-pair source implementation and regression tests are complete.
The three-run API sequence still needs separate approval and live validation.
This probe does not run KWin or qualify the native producer.

## Why checks are created rather than reset

The second controlled qualification stopped before native execution: updating
the existing activation check changed its external ID and run URL, but retained
the previous `completed` status, failure conclusion and completion timestamp.
The client's pending-state readback correctly rejected that result.

The later diagnostic run `41164646359` returned HTTP 422 for null `conclusion`
and `completed_at`. That reset request is invalid. The implementation now uses
fresh records and preserves earlier results. This diagnosis does not establish
the cause of the first native run's separate reporting failure, and no earlier
failure is relabelled successful.

## Scope and preconditions

`check-lifecycle-probe.yml` is a manual, main-only, eight-minute workflow using
the existing `native-gate-reporter` environment. Source merge and each approved
dispatch are separate operations. Its ordinary token has Contents-read and
Actions-read permissions; only the protected step receives the App key. The
probe never retrieves that key to the maintainer host or writes the authority ref.

Before running it:

- Keep the production operation held. Supply its freshly verified current SHA
  as `coordination_sha`; the probe never changes it or grants a worker.
- Create a signed disposable commit at
  `refs/heads/validation/check-lifecycle-contract`, distinct from main. Only its
  commit ID is used. No code from that ref is checked out or executed.
- Confirm the trusted v2 probe source is installed on main. A runtime check
  verifies the exact workflow path, repository, run attempt and current actor
  permission before the App token is created.

Every check write rereads main, held authority and disposable head. A changed
ref or lost permission stops the test. The two stable validation-only contexts
are `Validation / check lifecycle native` and
`Validation / check lifecycle activation`. They are not production gate names.

## Three actual runs, one pair per run

After explicit approval for the dispatch count, run the stages in order:

| Stage | `predecessor_run` | Pair's final conclusion |
| --- | --- | --- |
| A | Empty | Failure |
| B | Exact successful stage-A workflow run ID | Success |
| C | Exact successful stage-B workflow run ID | Failure |

Each workflow uses its actual run identity and creates just one pair. It does
not invent several production runs inside one dispatch. The preceding workflow
must have succeeded as a probe even when its test check conclusion was failure.
Its authenticated report carries the earlier history, which is checked again.

For example, **only after stage-A dispatch approval**:

```sh
gh workflow run check-lifecycle-probe.yml --ref main \
  -f coordination_sha="$EXPECTED_HELD_COORDINATION_SHA" -f stage=A
```

Stages B/C additionally require `-f predecessor_run="$PREVIOUS_PROBE_RUN_ID"`
and their respective `stage` value. A missing, ambiguous or failed predecessor
stops the sequence before App token creation. The verifier checks its repository,
launcher, workflow, attempt, successful job, artifact association/creation time,
ZIP digest and strict v2 report. All runs use the same pinned launcher and target.

Before every create or completion, a private one-shot journal records intent.
POST responses and exact-ID readbacks must agree before acknowledgment. There
are no reset PATCHes, retries, scan-and-adopt recovery or automatic check cleanup.
Both final checks and the earlier recorded results are verified before success.

Artifacts are separate:

- `check-lifecycle-<run-id>-1`: bounded v2 `report.json`.
- `check-lifecycle-journals-<run-id>-1`: `admit-journal.json` and
  `report-journal.json`, attempted with `always()`.

An incomplete report or missing journal is not proof that no write occurred.
The operator must stop and reconcile unknown outcomes. Downloaded report bytes
are capped while streaming, with a deadline and owned-process cleanup.

Failure reports distinguish HTTP rejection, transport failure, generic I/O,
invalid JSON, and response-field mismatch. HTTP diagnostics retain only the status and
allowlisted validation field/code pairs from a bounded 8 KiB error body.
Recognized null/type messages may supply field-name hints; raw messages, URLs,
headers, response values and credentials are never copied into diagnostics.
Mismatch diagnostics list field names only. Unknown causes remain unknown.
These fields describe the observed failure only; they do not establish a cause
for a different operation or turn a failed write into success.

A successful probe is evidence for the tested API lifecycle only. It does not
release the held production operation, authorize another native attempt, or
enable merge enforcement.

## References

- [Fresh-check contract and approval boundaries](FRESH-CHECK-LIFECYCLE.md)
- [GitHub check creation and update fields](https://docs.github.com/en/rest/checks/runs)
- [GitHub rerequest semantics](https://docs.github.com/en/rest/checks/runs#rerequest-a-check-run): rerequest resets the suite, not the check itself.
- [Main integration and cutover boundaries](MAIN-INTEGRATION.md)
