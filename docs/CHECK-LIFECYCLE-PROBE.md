<!-- SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com> -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
# Disposable App check lifecycle probe

Status: candidate reset implementation and source tests; live contract validation
is still required. This probe does not run KWin or qualify the native producer.

## Failure being addressed

The second controlled qualification stopped before native execution: updating
the existing activation check changed its external ID and run URL, but retained
the previous `completed` status, failure conclusion and completion timestamp.
The client's pending-state readback correctly rejected that result.

The candidate update sends explicit JSON nulls for `conclusion` and
`completed_at` when reusing a check. Pending responses must also have no
completion timestamp. Official API documentation lists these fields but does
not explicitly guarantee null-reset behavior; source tests alone cannot prove
the fix works against GitHub. The existing failed qualifications remain failed.

## Scope and preconditions

`check-lifecycle-probe.yml` is a manual, main-only, eight-minute workflow using
the existing `native-gate-reporter` environment. Installing it on main and
running it require explicit approval. It receives no Contents-write permission
and does not fetch, export or recover the App key locally.

Before running it:

- Keep the production operation held. Supply its exact current SHA as
  `coordination_sha`; the probe never changes that ref or grants a worker.
- Create a signed disposable commit at
  `refs/heads/validation/check-lifecycle-contract`, distinct from main. Only its
  commit ID is used. No code from that ref is checked out or executed.
- Confirm the trusted workflow source is installed on main. A runtime check
  verifies the exact workflow path, repository, run attempt and current actor
  permission before the App token is created.

Every check write rereads main, the held authority and disposable head. A
changed ref or lost permission stops the test. Check names are fixed under
`Validation / check lifecycle #<run-id>`, not a native gate or activation name.

After approval, the normal invocation is:

```sh
gh workflow run check-lifecycle-probe.yml --ref main \
  -f coordination_sha="$EXPECTED_HELD_COORDINATION_SHA"
```

## What the probe verifies

One disposable check is used for seven bounded mutations, with no retries:

1. Create a pending check, then complete it as failure.
2. Apply the old update without clearing fields. Confirm that the observed
   retained-completion failure is reproduced.
3. Apply the candidate update and require pending status, null conclusion and
   null completion time on the same check ID with the new external ID.
4. Complete it as success, then repeat the reset checks with another external ID.
5. End the disposable check as failure so no lasting successful gate is implied.

The artifact `check-lifecycle-<run-id>-1` contains a bounded JSON report with
phase, IDs, status, conclusion and timestamps, never credentials. A failure
preserves its phase and observations; an uncertain write is not retried or
silently repaired. The production native checks and authority remain untouched.
Write intent is saved before each mutation and cleared only after verified
readback, so interruption retains the pending phase and known identifiers.

Failure reports distinguish HTTP rejection, transport failure, generic I/O,
invalid JSON, and response-field mismatch. HTTP diagnostics retain only the status and
allowlisted validation field/code pairs from a bounded 8 KiB error body.
Recognized null/type messages may supply field-name hints; raw messages, URLs,
headers, response values and credentials are never copied into diagnostics.
Mismatch diagnostics list field names only. Unknown causes remain unknown.
These fields describe new failures; they do not retroactively identify the
earlier probe's unrecorded cause or turn a failed write into success.

A successful probe is evidence for the tested API lifecycle only. It does not
release the held production operation, authorize another native attempt, or
enable merge enforcement.

## References

- [GitHub update-check fields](https://docs.github.com/en/rest/checks/runs#update-a-check-run)
- [GitHub rerequest semantics](https://docs.github.com/en/rest/checks/runs#rerequest-a-check-run): rerequest resets the suite, not the check itself.
- [Main integration and cutover boundaries](MAIN-INTEGRATION.md)
