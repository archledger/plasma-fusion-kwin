<!-- SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com> -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
# Spec: same-process finalizer integration

Status: **scope approved by the owner on 2026-11-01**. Technical planning is now
authorized. Implementation, source integration and live operations remain
separate approval gates; this document does not authorize executing a finalizer.

Module: `finalizer-integration` in [CAPABILITY-MAP.md](CAPABILITY-MAP.md).

## Objective

Join authenticated native qualification, current policy, exclusive finalizing
ownership and one exact-result update in the maintainer coordinator that owns
the operation. Validate that composition using real disposable local Git
repositories, with explicit simulated GitHub/native boundaries.

The proposed implementation is **production-capable source**: its internal
callable can ultimately update production when invoked with real credentials
and the fixed production endpoint. Calling the work source-only does not make
that callable a sandbox. This scope permits designing that capability, not
executing it. Implementation approval, source integration, remote qualification
and production enablement remain separate decisions.

## Verified baseline and limits

- Source baseline: `928ae15c2f6352ab250f9990dfa04cdd539d3f28`.
- R4 completed on 2026-11-01: five expected protected-ref rejections and one
  accepted fast-forward; five fresh checks across six App stages. Temporary
  protection was restored and the experiment is terminal.
- R4 runs: [A1](https://github.com/archledger/plasma-fusion-kwin/actions/runs/41329378176),
  [A2](https://github.com/archledger/plasma-fusion-kwin/actions/runs/41329453993),
  [A3](https://github.com/archledger/plasma-fusion-kwin/actions/runs/41329636644),
  [B1](https://github.com/archledger/plasma-fusion-kwin/actions/runs/41329755465),
  [B2](https://github.com/archledger/plasma-fusion-kwin/actions/runs/41329838799),
  [B3](https://github.com/archledger/plasma-fusion-kwin/actions/runs/41329911794).
- R4 did not exercise the complete finalizer, a two-parent merge under all PR
  policy, or an expired live worker. Its owner-status control proves only that
  the status did not override A2's failure, not universal provider binding.
- Native run `41278724261` qualified its recorded older launcher. Current
  authority `00bbf8a4d1c22d53c546d49576fa70400984914a` is already idle after
  `qualified-only`; it is not an available grant for a later finalizer.
- Production enforcement remains off. Installation-wide App inventory was
  unavailable with the existing credential; no credential expansion follows.

## Scope and compatibility

Add an explicit internal completion intent to a **new** coordinator invocation,
selected before acquisition and retained in its private journal. The existing
qualification-only behavior remains the default. The finalizing path is an
additive programmatic capability, not a standalone command that adopts a
running/finalizing record supplied by a file.

Keep `tools/trusted_native.py`'s shipped CLI qualification-only in this module.
Do not expose a new production-finalization flag or change what its existing
`--apply` means. Keep `tools/verify_native_proof.py` read-only and its
`merge_authorized: false` result unchanged. Preserve existing public
`push_exact_merge` defaults and legacy result/error behavior.

Retain coordination schema 1, native evidence version 2, QID/admission meaning,
the fixed authority domain and the exact production target. No generic
repository/ref option, alternate trust root, new App permission, workflow,
scheduler, lease renewal or recovery command belongs in this module.

## Required ownership and evidence contract

1. Finalization is reachable only from the same coordinator invocation that
   acquired and advanced this operation to `running`. Its selected completion
   intent cannot change after acquisition. Neither a successful readiness JSON
   nor an old signed grant is a transferable write capability.
2. Authenticate the complete v2 proof, published admission, exact signed merge,
   current operation/QID/run/attempt/native job and worker nonce. Require every
   owned workflow/job/writer to be terminal, all check writes acknowledged and
   trusted cleanup evidence for the complete owned execution tree. A green job
   or local exited PID alone is insufficient.
3. Evaluate full current readiness while still owning `running`. Refuse missing
   or unsupported protection, wrong App/check IDs, altered PR/fork association,
   withdrawn admission, changed review decisions, reruns and incomplete lists.
   Do not accept R4 validation-only reports as native evidence.
4. Claim `running -> finalizing` once by a signed append with exact predecessor
   SHA/generation and a new in-process finalizer nonce. Preserve all operation
   and grant fields except that nonce. Authenticate the new signed record and
   exact remote readback before proceeding. A losing/uncertain claim cannot push.
5. Under that exact finalizing anchor, rerun the complete proof/current-policy
   evaluation immediately before submission. Bind its result to the same
   admission, signed merge, QID and operation. Recheck ownership and the deadline
   after slow reads and immediately before the update.
6. Submit at most one exact update: qualified base to its signed two-parent
   merge, with the exact validated tree and ordered base/head parents. Require
   explicit expected-base lease, no implicit tracking lease, no extra refs/tags,
   no force-rewind and no substituted GitHub-generated merge.
7. Authenticate the outcome and read back the target and ownership. Only a
   known outcome permits one signed `finalizing -> idle` completion from the
   exact owned tip. Never recreate, reset or delete authority history.

The existing reader authenticates state but does not authorize transitions.
The finalizer must enforce the permitted transition and immutable-field rules;
schema validity alone is insufficient. Process-local objects are not a security
boundary against compromised trusted maintainer code.

## Durable attempt and outcome contract

Use one private, exclusive, bounded journal. Persist and fsync intent before
claim, target submission and release; use atomic replacement plus directory
fsync for later snapshots. Refuse changed ownership/path, symlinks, existing
journals and partial persistence. Any persistence failure poisons the invocation.
Never reuse markers or nonces to retry after a restart.

Preserve complete bounded per-ref push status and the exact command/target
identity without credentials. A nonzero exit code, `!` alone, transport error,
timeout or a different remote tip alone does not establish rejection.

| Observation | Required disposition |
| --- | --- |
| Failure before any target-update attempt | Release `failed`/null only while ownership, quiescence and no-send evidence remain conclusive; otherwise stop held. Do not silently fall back to a successful qualification-only outcome. |
| Positive acknowledgment for the exact fast-forward and exact merge readback | Record `finalized`, exact QID and signed merge SHA; attempt one owned release. |
| Definitive not-applied per-ref result, reconciled target different from the merge, and no outstanding attempt | Record `rejected`, exact QID and observed target; attempt one owned release. |
| Main-update acknowledgment lost, malformed/partial result, ambiguous readback or persistence failure | Do not submit a release. Stop with the unresolved attempt retained. Finding the merge SHA later is not a replacement for its missing acknowledgment. |
| Release acknowledgment lost after known main outcome | Stop locally as `release-unknown`. Remote authority may still be finalizing, may already be idle, or may have a new owner. Preserve the known main outcome separately; never retry release, recreate the old held tip or claim the lock is still held. |
| Ownership changes at any stage | Stop without further mutations; do not release another owner's operation. |

After an accepted update, old-base PR/readiness predicates naturally change.
Post-update outcome verification must check the intended target, acknowledgment,
ownership and terminal writers, not rerun a pre-update base predicate and
misclassify every successful update as failure.

## Bounded execution

Start one **300-second finalization deadline** before preclaim verification.
Every transitive API read, full response body, Git operation and persistence
boundary must respect the remaining allowance. Never restart the clock after
claim. A timeout does not expire the durable lock or permit another writer;
in-flight operations can finish and require explicit later reconciliation.

Allocate finalizer proof/policy requests once:

| Phase | Maximum requests |
| --- | ---: |
| Preclaim verification | 48 |
| Complete postclaim revalidation immediately before push | 48 |
| Outcome/ownership/release reads | 32 |
| Aggregate finalizer allowance | 128 |

Each phase is non-restartable and cannot borrow unused allowance from another.
One shared counter prevents new reader instances from resetting the aggregate.
Reserve the full remaining phase allocations before claim/submission. Exhaustion
fails closed, including after claim; it never triggers an automatic release.

The coordinator's existing proof-reader allowance remains64, so combined
proof/policy allowance is192. Existing bounded artifact polling/downloads are a
separate category and must be reported separately, not claimed inside192.
Preserve `ProofAPI`'s legacy `max_calls <= 64` contract for each reader. Deadline
propagation is proposed work: its current fixed20-second timeout does not yet
satisfy this contract. No new endpoint or more permissive JSON parsing is needed.

## Project structure and style

| Area | Intended responsibility |
| --- | --- |
| `ci/native/finalizer.py` (new) | Same-process ownership, one-shot update/outcome and release composition |
| `ci/native/coordinator.py` | Select immutable completion intent and reach finalizer before qualification-only release |
| `ci/native/merge_update.py` | Reuse exact raw merge/lease checks; expose sufficient internal outcome evidence without breaking the legacy wrapper |
| `ci/native/proof_api.py` | Only bounded, backward-compatible request/deadline support required by the composition |
| `tests/` | Finalizer unit tests and real-Git, real-signature cross-boundary regressions |
| `docs/FINALIZER-INTEGRATION.md` (new) | Supported source capability, operator boundaries, evidence interpretation and deferred live gates |

Prefer Python's standard library and existing typed proof/state objects. Use
snake_case, four-space indentation, explicit validation and SPDX headers. For
example, retain checks rather than coercing mismatched identities:

```python
require(current.commit == expected_finalizing_sha, 'finalizing ownership changed')
require(evidence.signed_merge == signed, 'qualified merge differs')
```

Do not generalize the workflow or create a second proof parser, state schema or
generic orchestration framework. Factor only the existing behavior needed here.

## Testing strategy and acceptance

Use real local bare Git refs named `main` and `native-gate-coordination`, ephemeral
test signing keys, real serialized v2 archives and production validators. Fake
only the external GitHub/native boundary. Test harnesses must reject network
endpoints, credential lookup, unexpected commands and unsafe inherited Git
configuration before an operation; never point a test at the production remote.

Acceptance requires:

- One integrated positive trace from a newly owned operation through actual
  signed claim, exact local update and signed release. Default qualification-only
  and read-only CLI behavior remain covered unchanged.
- Exactly one winning claim/update under races; stale owner/base and raw
  parent/tree/signature mismatch cannot update. Missing proof, pending or changed
  checks, changed reviews/protection/PR association and failed cleanup block it.
- Crash/short-write/fsync failures at each intent boundary cannot permit a
  repeated update. Wrong or replayed operation/nonce/journal is refused.
- Main update applied with lost acknowledgment remains unresolved without
  release or retry. Definite rejection is distinguished from ambiguous failure.
- Release applied with lost acknowledgment followed by a new acquisition leaves
  the new owner untouched and reports `release-unknown`, not a fictional hold.
- Real `ProofAPI` accounting through a fake HTTP boundary exercises the complete
  nominal signature/review path, pagination, every phase/aggregate limit and
  response-body deadlines. `SimpleNamespace` fixtures alone are insufficient.
- Targeted guard mutations are caught for ownership, exact merge, freshness,
  no-replay, acknowledgment classification and each deadline/budget boundary.
- Full existing suite and source checks pass without removed/skipped/weakened
  tests. Local success is not called remote enforcement/native qualification.

## Commands

Existing source checks, run from the repository root:

```sh
python3 -B -m unittest discover -s tests -v
python3 -B -m unittest discover -s tests -p 'test_merge_update.py' -v
python3 -B -m unittest discover -s tests -p 'test_lifecycle_integration.py' -v
python3 -B tools/patchset.py validate
ruff check .
git diff --check
```

New focused test filenames and implementation ordering belong in the subsequent
technical plan. Use the pinned REUSE workflow in
[`.github/workflows/fast-validation.yml`](.github/workflows/fast-validation.yml)
for source license validation. No native or live finalization command is supplied.

## Boundaries and open approval

- **Always:** preserve current default behavior, signed history, exact identities,
  terminal journals, separate evidence levels and fail-closed uncertainty.
- **Separate approval:** implementation plan; source integration; any executable
  production-finalization CLI; sandbox adapter/ref/protection changes; new native
  operation; remote finalizer qualification; production enablement or recovery.
- **Never:** reopen `qualified-only`, reuse R4's spent journal or budget, select a
  newest successful check as authority, bypass protection, export the App key,
  widen App permissions, mutate production during local validation, silently
  retry a write, or deploy patched KWin.

The proposed source capability and local-only validation scope above are the
owner's approval decision. There is no unresolved factual dependency blocking
its technical plan. Remote end-to-end proof, approved operator entrypoint and
production rollout remain expressly unqualified and outside this source scope.

Sources: existing [coordination contract](docs/COORDINATION-DESIGN.md),
[read-only verifier](docs/READ-ONLY-FINALIZER.md),
[current-policy contract](docs/MERGE-READINESS.md), and
[Git push manual](https://git-scm.com/docs/git-push) for explicit leases and
per-ref acknowledgment semantics. Git/protection/metadata operations are not a
single distributed transaction. Fresh reads and the held authority serialize
cooperating writers, not arbitrary privileged third-party mutations.
