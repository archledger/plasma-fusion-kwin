<!-- SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com> -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
# Implementation plan: disposable check-enforcement probe

Status: owner approved EP-01–EP-06 source implementation on 2026-11-01.
Review: no required planning issues remain after clarifying B3 identity and
maintainer-side protection reads. Source merge and live operations remain separate.
Specification: [approved module spec](../../SPEC-check-enforcement-probe.md).
Task list: [todo.md](todo.md). This module-specific location was selected by the
owner; the earlier root task files remain untouched.

## Objective and boundaries

Implement the source for the six-stage App-only experiment, then pass source
review before requesting its installation and a separately pinned live run.
Do not implement finalization, alter the native workflow or expand App access.
The planning baseline is `2f2d169`; reverify it before implementation. Existing
native evidence belongs to that exact launcher, not a future source revision.

## Dependency graph

```text
EP-01 strict experiment/fixture/report contracts
  → EP-02 fresh-check stages A1/A2/A3/B1/B2
    → EP-03 authenticated B3 completion
      → Checkpoint A: client and refusal paths
      → EP-04 manual workflow and bounded entrypoint
        → EP-05 operator procedure and matrix evidence
          → EP-06 full source validation/review/draft PR
            → Checkpoint B: source acceptance
            → separate source-only merge approval
              → EP-07 pin live package and preflight
                → separate live-operations approval
                  → EP-08 measured matrix, cleanup and closeout
```

Default: one sequential workstream. Reviews may be independently delegated;
this plan does not authorize parallel implementation or extra live runs.

## Implementation decisions

1. **Keep logic in the new probe.** Use the four source/test/docs paths listed
   in the specification. No change to production coordination, evidence schema,
   native workflow or `push_exact_merge`.
2. **Reuse real guards.** Use the existing check writer, journal, signature,
   strict-JSON and bounded API/artifact helpers. Network transport is replaced
   in tests, not the entire writer, so journal/fence behavior is exercised.
3. **Validation-only display.** A small probe-local reporter adapter may replace
   only the outgoing check `output` title/summary before delegating to the
   existing `_send`. It must not change endpoint/body identity, lifecycle or
   journal logic. Tests inspect the real outgoing body and prove production
   defaults remain unchanged. If this cannot be kept narrow, stop for an explicit
   shared-client API proposal rather than copying the writer.
4. **Separate writer and check origin.** A1/A2/A3/B1/B2 originate fresh checks.
   B3 is a new authenticated workflow execution completing exactly B2's known
   pending check. Preserve B2's QID, nonce, check ID and details URL; record B3's
   writer-run identity separately. Never search for a replacement ID.
5. **Fix the fixture graph.** Derive only the three approved namespace refs from
   a strict experiment ID. Case A descends from the initial disposable target;
   case B descends from A. Verify signed fixture objects, their bounded marker,
   expected parents and expected target tip before any write. Never execute them.
6. **Keep writes separated.** App operations occur only in the protected main
   workflow. Git update attempts, the single wrong-provider status and temporary
   target protection use the maintainer's existing credentials under the later
   explicit live package. Full protection reads also stay on that side, before
   and after every update attempt; matching approved snapshots are required for
   a counted result. The worker keeps its identity/source/ref fences and never
   receives a Git write or Administration credential. Do not broaden `ProofAPI`.
7. **Pin predecessor evidence.** Each stage authenticates its required previous
   run, completed workflow, job, artifact digest, experiment, source and report.
   Bound the complete chain to six stages. B3 requires B2's confirmed creation
   journal and a matching still-pending direct check read.
8. **Preserve failure evidence.** Create private journals once, save safe error
   reports, and attempt allowlisted artifact upload even on failure. Unknown
   writes stop the sequence. A failed test does not authorize broad cleanup.

## Verification checkpoints

Each source slice runs its focused tests, Ruff and changed-file whitespace
checks. Checkpoint A additionally runs the full suite, manifest validation,
compilation and targeted fence mutations. Checkpoint B adds pinned workflow and
license checks, public-source patch application and independent source review.

```sh
python3 -B -m unittest discover -s tests -p 'test_check_enforcement_probe.py' -v
python3 -B -m unittest discover -s tests -v
python3 tools/patchset.py validate
ruff check --no-cache ci tools tests
python3 -m compileall -q ci tools tests
git diff --check
```

Use the repository's existing pinned Actionlint and REUSE procedures. Verify
new untracked file contents too; an empty Git diff is not a check. No suppressions,
skips, weaker bounds, new dependencies or substitute tools to obtain a pass.

## Risks and handling

| Risk | Handling |
|---|---|
| Server selects an older success | Treat acceptance in a negative case as a failed experiment; stop, keep evidence and redesign before enforcement. |
| B3 completes the wrong or already-terminal check | Authenticate the exact B2 artifact/journal and direct check state; reject all other IDs/states before mutation. |
| Display adapter bypasses client security | Delegate through the existing transport, change only output text, and inspect actual request bodies in tests. |
| Partial or missing artifact | No guessed predecessor or retry; report the unresolved state. |
| Unexpected target or production ref change | Halt. No force-rewind, authority recovery or broader scope change. |
| Cleanup after an unexpected result | Only the specified, authenticated B3 completion and owned temporary protection restoration are eligible; otherwise report residual state. |
| New main source invalidates old launcher identity | Keep old native proof historical; do not relabel it as a qualification of the new source. |

## Separate operational gates

Source-plan approval allows EP-01–06 only after the owner approves this plan.
It does not approve a main merge. After source review, obtain explicit source-only
merge approval. Then EP-07 records the real installed SHA/workflow ID, immutable
fixtures, authority snapshot, exact protection request, byte digests and rollback.
That package requires its own approval before EP-08.

The live maximum remains six App-only dispatches, six target-update attempts,
one wrong-provider status and zero native runs. Only the expected positive control
may advance the disposable target. This plan does not approve production
enforcement, another native run, or `finalizer-integration` implementation.
