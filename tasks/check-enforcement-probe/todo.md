<!-- SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com> -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
# Check-enforcement-probe tasks

Status: EP-01–EP-06 source implementation was approved on 2026-11-01.
Local implementation and review are complete; draft publication is next.
EP-07–EP-08 still need separate approvals.
See [plan.md](plan.md) and the [approved spec](../../SPEC-check-enforcement-probe.md).
No inherited checkbox is completion evidence for this module.

## EP-01: Strict fixture and report contracts

- [x] Derive only approved experiment refs; verify signed fixture marker,
  parent graph and expected target/source/authority identities.
- [x] Define strict stage reports with separate writer/check-origin identities;
  reject wrong scope, unknown fields, replay, malformed IDs and incomplete history.
- [x] Verify positive/refusal cases with real temporary Git fixtures and ephemeral
  test signing material; perform no network write.

Verification: focused `test_check_enforcement_probe.py` suite, Ruff and whitespace.
Dependencies: approved plan. Files: new probe module and its test file. Size: S.

EP-01: 9 focused tests and 578 total tests passed, including real disposable
signatures and wrong marker/parent/unsigned refusals. Ruff and formatting checks
passed. This is local contract validation, not live enforcement evidence.

## EP-02: Fresh-check stages and validation-only output

- [x] Implement A1/A2/A3/B1/B2 with real `CheckReporter` and `CheckJournal` behavior;
  new IDs per creation and exact known IDs for same-stage completion.
- [x] Add the probe-local output adapter without changing production defaults,
  identity fields, allowed endpoints or journal/fence ordering.
- [x] Prove pre-write fences, confirmed acknowledgments, preserved earlier checks
  and no retry after transport/persistence failure using fake transport boundaries.

Verification: focused tests and real request-body assertions; production
`test_checks.py` suite; Ruff/whitespace. Dependencies: EP-01. Same two files. Size: M.

EP-02: 15 focused tests, 21 original-client tests and 584 total tests passed.
Real client/journal paths used fake transport; pending and uncertain-write
results are not live GitHub evidence. Production display defaults stayed intact.

## EP-03: Authenticated B3 pending-check completion

- [x] Authenticate B2's completed run, job, artifact and confirmed creation journal;
  derive its ID/QID/nonce/URL rather than accepting an arbitrary ID.
- [x] Require the exact pending check; complete it as failure with a fresh report
  journal and reporter constructed with B2's origin run ID/QID/nonce, preserving
  its details URL while separately authenticating and recording the B3 writer.
- [x] Reject wrong source/experiment/predecessor, terminal checks, replay and
  unknown creation; do not create a replacement or bypass changed-target fences.

Verification: positive B3 and one-variable-at-a-time refusal tests, full real
writer/journal path with transport fakes. Dependencies: EP-02. Same two files. Size: M.

EP-03: authenticated ZIP/journal provenance and B3 completion tests passed.
The B3 client retains B2's check identity while reporting the new writer.

## Checkpoint A: Source mechanics

- [x] Full suite, manifest validation, compilation, Ruff and whitespace pass.
- [x] Targeted mutations removing fixture/source/authority/predecessor/check-ID
  guards are caught; restore exact source after each isolated experiment.
- [x] No production behavior changed and no simulated result is called live proof.

Checkpoint A: 591 full-suite tests and five targeted mutations passed. A first
mutation exposed a fixture-scope test confounded by a wrong parent; its fixture
was corrected so the scope guard is independently exercised. No live calls ran.

Checkpoint A review follow-up: fixed real API output metadata handling, optional
temporary-directory portability, and pre-I/O B3 writer validation. Realistic
response and zero-write regression tests pass; the old temporary-path assumption
fails under the retained simulation and the corrected tests pass. Final total:
594 tests and six caught mutations, including journal/check-ID binding. Scoped
core review found no remaining required code issue; runtime wiring is still next.

## EP-04: Main-only manual workflow

- [x] Add only the proposed manual workflow, strict inputs and bounded worker
  entrypoint; check repository/main/event/actor/attempt before token use.
- [x] Keep ordinary permissions read-only and the App key in the protected
  writing step only; no native command, Git write credential or shared cache.
- [x] Upload only bounded reports/journals, including failure evidence, and enforce
  the eight-minute worker limit without automatic retries.

Verification: workflow structure/entrypoint refusal tests and pinned Actionlint,
focused suite, Ruff/whitespace. Dependencies: EP-03 and Checkpoint A.
Files: workflow, probe module, test file. Size: M.

EP-04: 600 full-suite tests and all 12 workflows through the cached, pinned
Actionlint image passed. Runtime tests cover early refusal, pre-token predecessor
failure and B3's origin/writer split. No workflow was dispatched.

## EP-05: Operator procedure and matrix accounting

- [x] Document signed fixture creation, exact temporary target protection,
  serialized stages, six update attempts and the single wrong-provider status.
- [x] Require actual server rejection reasons and ref readback; specify stop rules,
  maintainer-side protection reads before/after every attempt, authenticated B3
  cleanup and owned protection restoration without ref deletion.
- [x] Keep source installation, live-package approval, native qualification and
  finalizer work explicitly separate; never claim server behavior from mocks.

Verification: compare each spec matrix row/budget against the documented flow;
check links, commands and unchanged production targets.
Dependencies: EP-04. Files: new probe guide and module docstrings if needed. Size: S.

EP-05: the guide records the six-stage matrix and explicit maintainer-only writes.
The exact fixture marker path is added to the existing CC0 data annotation so
future disposable commits stay REUSE-compliant without changing a second file.
Pinned REUSE passed for both the source tree and a tree containing the marker.

## EP-06: Source validation, review and draft PR

- [x] Run full tests, manifest/public patch checks, compilation, Ruff, whitespace,
  pinned Actionlint and REUSE on the exact candidate tree.
- [x] Resolve independent scoped review findings; preserve red/green, mutation,
  source-diff and artifact evidence without weakening existing checks.
- [ ] Prepare signed/DCO commits and a source-only draft PR, retaining old branches;
  do not merge or dispatch from source-plan approval.

Verification: exact-revision evidence and signed source history; PR scope excludes
native/authority/production enforcement changes.
Dependencies: EP-05. Source files: no additional runtime paths. Size: M closeout.

Final review required direct runtime authorization and pre-write drift tests.
Added a separate runtime test file without changing the worker or client.
All 607 tests pass. Four new in-memory guard mutations are caught by assertions;
the runtime source remains unchanged. Follow-up review closed the finding.
Source publication and hosted validation remain pending.

## Checkpoint B: Source acceptance

- [ ] EP-01–06 source gates passed on the exact submitted revision.
- [ ] No required review finding remains; limits and untested live behavior recorded.
- [ ] Obtain separate source-only main-merge approval. Do not treat this unchecked
  approval reminder as permission to merge.

## EP-07: Freeze the live package — after separately approved source merge

- [ ] Reverify installed launcher/workflow ID, idle production authority and
  unchanged environment/App permissions; sign and verify new disposable fixtures.
- [ ] Record exact namespace refs, protection request/baseline, artifact contracts,
  execution budget, one-shot journal and cleanup/rollback scope in the live package.
- [ ] Obtain explicit approval for that package before creating live test refs,
  applying protection, dispatching or writing the test status.

Verification: independent package review and read-only preflight; no live action
from this task alone. Dependencies: Checkpoint B plus merge approval.
Files: operational artifacts only, no product code. Size: M.

## EP-08: Approved matrix, cleanup and closeout — separate live approval required

- [ ] Execute only the approved matrix/budget; authenticate each actual stage and
  capture server response/ref readback without repeating uncertain operations.
- [ ] Verify earlier records, B3 cleanup, owned protection restoration and terminal
  workers; preserve any failure or residual state without broader cleanup.
- [ ] Independently review the measured verdict and archive source/evidence with
  remote checksum verification; leave production enforcement and finalizer untouched.

Verification: actual GitHub results, exact counts/IDs, before/after settings and
checks, retained failure evidence and verified archive.
Dependencies: EP-07 plus live approval. Files: operational artifacts only. Size: M.

Only after the measured result is reviewed may `finalizer-integration` be
specified. No checkbox here authorizes that module or a production rollout.
