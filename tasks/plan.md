<!-- SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com> -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
# Hosted native KWin qualification plan

The completed hosted qualification below is retained. The current source
work is [Fresh-check lifecycle implementation](#fresh-check-lifecycle-implementation).

Status: the owner approved this bounded plan, including the 75-minute hosted job
limit. Renderer and native qualification passed in run 41079594986. Evidence
review and checksum-verified archival are complete; general PR-native CI is separate.

## Goal and scope

Qualify the three selected native suites on GitHub-hosted infrastructure using
the proven offline KVM guest boundary. The prior local 43-entry result and hosted
boot result are prerequisites, not substitutes for fresh hosted native evidence.
Tasks are tracked in [todo.md](todo.md).

No host packages, kernel modules, permissions, desktop sessions or persistent
runners may change. No package release, merge or upstream submission is included.
This first native qualification runs on a dedicated trusted branch, not arbitrary
PR-controlled outer-hypervisor code.

## Architecture

- Reuse the published hypervisor image by digest and configuration ID. Keep the
  previous boot-only workflow intact.
- Boot the clean, checksum-verified Fedora cloud image. Never publish or reuse a
  private guest snapshot containing generated keys as a CI input.
- Transfer the original builder image with its manifest and configuration intact,
  using a digest-preserving directory transport. Verify each referenced blob and
  the complete 882-package inventory; do not substitute an OCI export that changes
  the selected image identity.
- Fetch the pinned upstream commit and apply the complete checked-in patch set.
  Archive the verified patched tree, not the unpatched `HEAD` commit. Bind the
  archive and all payload files by SHA-256.
- Use a read-only input ISO inside the guest. Load only the guest's verified vgem
  module and expose only that guest device to the unprivileged inner builder.
- Keep the outer UID1000 KVM container constrained as in the accepted boot probe:
  no network, host binds, GPU, privileged mode or forwarded credentials; one owned
  managed volume, dropped capabilities, enforcing AppArmor and built-in seccomp.
- Compile and run inside the guest's isolated builder, with two CPUs and bounded
  memory. No writable cross-job cache or host container-engine socket is exposed.
- Stage the three byte-verified frame QML files at the private runtime prefix's
  `share/kwin-wayland/frames/plasma`. Preserve and document the historical fixture
  mismatch; do not change KWin source or assertions to hide it.
- Export only bounded, allowlisted text evidence over serial. Independently verify
  test results on the host. Never upload guest disks, private keys or build binaries.

## Ordered increments

1. Implement strict native result validation and runtime-data staging, with positive
   and refusal tests. Require the exact CTest/QtTest suites, direct composition and
   effect-frame cases, no failures/skips/blacklists and actual llvmpipe evidence.
2. Implement verified source/image preparation and bounded text transport. Test
   digest drift, wrong source tree, unsafe archive members, unexpected files and
   oversized/compressed output before any guest execution.
3. Implement the guest and outer controllers. Prove isolation, guest-only DRM
   identity, image inventory, deadlines and owned cleanup with negative tests;
   perform a small hosted renderer preflight before the expensive native build.
4. Run one fresh hosted build/test qualification using the fixed inputs. Allow up
   to 55 minutes for the inner build/test process, 60 minutes for the guest and a
   75-minute job envelope with explicit acquisition/cleanup reserves. Stop on
   failure and retain diagnostics instead of automatically retrying.
5. Review the actual result, update the documentation/handoff and archive essential
   source/evidence with verified checksums. General PR-native CI remains a separate
   acceptance decision after this trusted qualification succeeds.

## Gates

- Every increment passes the repository's Python tests, Ruff, Actionlint/ShellCheck
  where applicable, REUSE and meaningful diff checks before a signed commit.
- Review the controller and trust boundaries before running a guest workload.
- A missing/disabled/repeated/not-run suite, any individual skip or blacklist,
  failed runtime-data identity, missing renderer proof, failed artifact export,
  unknown cleanup state or changed host device metadata prevents success.
- The full run must register and execute `kwin-testOffscreenQuickView`,
  `kwin-testInternalWindow` and `kwin-testTabBox` once each. Compare the observed
  case inventory to the pinned source rather than accepting arbitrary positive
  totals.

## Risks and open checks

- The clean cloud image previously contained Podman 5.8.1; verify its current
  digest-bound guest inventory again rather than copying private prepared disks.
- The larger read-only input and offline import need measured disk/time bounds.
- Result collection must retain useful failure context without granting the guest
  a host filesystem path or accepting fabricated summary fields.
- Check current version-specific Podman/Skopeo, QEMU, CMake/CTest and cloud-init
  behavior against official or installed documentation before implementation.
- No fresh unpatched hosted control has run. Decide whether a bounded control is
  practical after qualification; historical sensitivity is not a fresh control.

## Fresh-check lifecycle implementation

Status: implementation plan approved by the owner on 2026-10-31, following
approval of the [specification](../docs/FRESH-CHECK-LIFECYCLE.md).
Active checklist: [FC-01 through FC-10](todo.md#fresh-check-lifecycle-tasks).
The completed hosted qualification above is not repeated or reclassified.

FC-01 through FC-10 and source checkpoints A through C are complete. The signed
source series is in draft [PR #15](https://github.com/archledger/plasma-fusion-kwin/pull/15).
Review and any separately approved merge come next; no completed source slice
authorizes a live operation.

Historical checkpoint A covered only the three foundations: 456 tests and 11
targeted mutations passed before producer integration. It is not the current
resumption point. The active checklist records the later completed slices.

### Objective and boundaries

Replace the invalid completed-check reset with fresh per-operation check IDs,
authenticated v2 evidence and current signed-operation selection. Preserve
historical failures and the existing native-proof requirements. The baseline
is `7714ca719b6a1a79c3e6348ffa13a601292cee35`; working branch is
`fix/fresh-check-lifecycle`; the original documentation branch is retained.

These are local source increments, not independently deployable releases. No
partial series may be treated as a qualified producer. No App probe, native
run, main merge, held-operation reconciliation, protected-ref experiment or
enforcement is included. Each needs its own scoped approval after source review.

### Dependency graph

```text
FC-01 shared record validation ─┐
FC-02 bounded write journal ───┼── FC-04 create-only client and API-only probe
FC-03 v2 evidence contracts ───┘                    │
                                     FC-05 producer admission/report binding
                                                  │
                                     FC-06 completed qualification binding
                                                  │
FC-07 fixed authority transport/anchor ── FC-08 current-policy composition
                                                  │
                                     FC-09 cross-boundary failure tests
                                                  │
                                     FC-10 documentation and source closeout
```

FC-01, FC-02, FC-03 and FC-07 have independent foundations, but the default is
one sequential workstream in this tree. No parallel agents or repeated Kimi
calls are authorized by this plan. Existing pure helpers should be reused; new
small modules are justified only for shared validation or journal ownership.

### Ordered slices

| ID | Outcome | Dependencies | Main source/test scope |
| --- | --- | --- | --- |
| FC-01 | Exact record identity and visible-conflict rules | None | Proposed `ci/native/check_records.py`, `tests/test_check_records.py`. |
| FC-02 | One-shot write-intent journal with bounded failure evidence | None | Proposed `ci/native/check_journal.py`, `tests/test_check_journal.py`. |
| FC-03 | Strict v2 admission/result validators and verified binding data | None | `ci/native/qualification.py`, `tests/test_check_evidence.py`. |
| FC-04 | Create-only journaled client and real-run API-only probe | FC-01–03 | `ci/native/checks.py`, `tools/probe_check_lifecycle.py`, their two test files and `.github/workflows/check-lifecycle-probe.yml`. |
| FC-05 | Producer uses admitted IDs and exports partial-write evidence | FC-04 | `ci/native/producer.py`, `.github/workflows/trusted-native.yml`, `tests/test_producer.py`, `tests/test_workflow.py`. |
| FC-06 | Final qualification verifies both checks and coordinator uses v2 only | FC-03, FC-05 | `ci/native/qualification.py`, `ci/native/coordinator.py`, `tests/test_qualification.py`, `tests/test_coordinator.py`, `tests/test_check_evidence.py`. |
| FC-07 | Fixed authority read and trusted anchor loader | FC-03 | `ci/native/proof_api.py`, `ci/native/readiness.py`, `tests/test_proof_api.py`, `tests/test_readiness.py`. |
| FC-08 | Readiness binds the current signed operation and check pair | FC-01, FC-06–07 | `ci/native/current_policy.py`, `ci/native/readiness.py`, `tools/verify_native_proof.py`, `tests/test_current_policy.py`, `tests/test_readiness.py`. |
| FC-09 | Integrated partial-failure, replay and stale-success regressions | FC-05–08 | `tests/test_producer.py`, `tests/test_coordinator.py`, `tests/test_current_policy.py`, `tests/test_readiness.py`, `tests/test_check_lifecycle_probe.py`. |
| FC-10 | Accurate user-facing contracts and source-review evidence | FC-09 | `docs/CHECK-LIFECYCLE-PROBE.md`, `docs/QUALIFICATION-EVIDENCE.md`, `docs/READ-ONLY-FINALIZER.md`, `docs/MAIN-INTEGRATION.md`. |

Keep new validators independently testable before wiring callers. Activate v2
only through explicit new paths, then switch every production caller; never
add a permissive missing-version or v1 fallback to keep intermediate tests green.
If a switch cannot be made coherently within its slice, revise that slice rather
than leaving a placeholder, skipping a test or publishing a broken intermediate.

### Verification recipes

Use test-first positive and refusal cases, then the focused tests named in
[todo.md](todo.md). After each slice, run lint and whitespace checks for changed
files. At checkpoints A, B and C, run the complete suite and local manifest check:

```sh
python3 -B -m unittest discover -s tests -v
python3 tools/patchset.py validate
ruff check --no-cache ci tools tests
python3 -m compileall -q ci tools tests
git diff --check
```

For newly added, untracked files, also check their contents rather than accepting
an empty Git diff. Final source review additionally requires the existing pinned
Actionlint and REUSE checks and the fast workflow's exact public-source patch
application validation. Do not install new tooling to satisfy a missing check
without approval. Record unavailable tooling rather than claim it passed.

Retain red/green and mutation evidence for operation/check IDs, v2 version,
nonce, ownership fences, publication failure, duplicate detection and a new
signed operation invalidating an old success. API mocks and fixtures must be
labelled as such. No local test supplies real GitHub enforcement evidence.

### Checkpoints and review

- **A, after FC-01–03:** independently testable validators/journal; all existing
  tests still pass. No producer behavior is declared qualified.
- **B, after FC-04–06:** creation, admission, native input and reporting use one
  coherent v2 contract; no legacy reset or scan-and-adopt path remains reachable.
- **C, after FC-07–09:** read-only readiness requires the trusted anchor and
  exact pair; integrated negative tests and mutation checks pass.
- **Source closeout, after FC-10:** full validation, scoped review, resolved
  findings, signed source history and retained evidence. Any requested cross-model
  re-review needs a new explicit approval. Main merge is still separate.

Routine local slices can proceed under one approved plan. Stop for a failed
gate, a necessary contract change or work outside the approved scope; do not
ask for a new approval merely to run the next already-approved local test.

### Risks and mitigation

| Risk | Required handling |
| --- | --- |
| Incomplete API history or another privileged writer | No global absence/atomicity claim. Keep explicit single-writer preconditions and refuse visible conflicts. |
| Partial POST or missing upload | Persist intent, retain known IDs, halt further writes; no native work or automatic retry/release. |
| Schema change splits producers and readers | Land helpers first, wire consumers coherently, require v2 at every production entry point. |
| Legacy tests simulated unsupported reset | Preserve observed HTTP 422 and safety checks; replace only behavior assertions intentionally superseded by the approved contract. |
| Old success appears current | Anchor to signed current operation and exact admitted IDs; do not rank history by timestamps, IDs or run numbers. |
| Native GitHub required-check behavior differs | Keep enforcement off. A later protected-ref experiment must validate the actual intended rules/update path. |
| Current held operation belongs to old contract | Never consume it under v2. A separate approved reconciliation and fresh operation are prerequisites to production use. |

### Open operational gates

No unresolved design question is permission to improvise a live experiment.
The following remain outside implementation approval: main source merge;
reconciliation of `97e435f`; the explicitly counted three-run App-only sequence;
fresh native qualification; and protected-ref/finalizing-write/enforcement work.
Stop and obtain an updated decision if the approved single-writer assumption
cannot be established or an essential API behavior differs from the contract.
