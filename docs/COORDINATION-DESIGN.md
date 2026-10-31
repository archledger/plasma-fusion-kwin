<!-- SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com> -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
# Draft: durable coordination for activation and finalization

Status: three design-review cycles completed. The owner approved adding an
explicit, verified rejection outcome and proceeding to a local-only prototype.
The signed-state reader, local/live validation probes and manual qualification
producer are now implemented. Production cutover, operational qualification and
the finalizing main-write integration remain pending; see the
[integration plan](MAIN-INTEGRATION.md). No production authority ref or native
merge enforcement is enabled by this design.

## Contract

Owner-approved scope, 2026-10-30: preserve the exact tested commit/base and
serialize cooperating activations and finalization. PR state, reviews and
revocation are checked immediately before the push and remain subject to normal
GitHub protection enforcement; they are not one atomic snapshot with the Git
update. The owner explicitly accepted that remaining metadata race. This does
not authorize a protection bypass or an untested code/base combination.

Two cooperating operations must not activate or finalize different candidates
concurrently. No newer activation may occur between a finalizer's last proof
check and its conditional update of main. Signed history, required reviews and
protections remain mandatory. Writes stay disabled until this contract is
implemented and qualified.

The reporting App stays Checks-write/Metadata-read only. A maintainer-side
coordinator uses the existing signing and Git credentials. No CI job receives
Contents-write permission, and no signing key is uploaded to a runner.

## Authority ref

Use one repository-wide coordination branch, provisionally
`refs/heads/native-gate-coordination`. Production setup requires separate approval
and protection validation. The ref points to a signed commit whose tree contains
only a bounded regular `coordination.json` file. It is not a runnable checkout.
Every successor has exactly one raw parent: the previous coordination commit.
Only the pinned maintainer signing identity is accepted by protocol participants.

The UTF-8 JSON object has exactly these fields: `version`, `repository_id`,
`target_ref`, `epoch`, `cutover_digest`, `generation`, `state`, `operation`,
`grant`, and `completion`.
Version is integer 1; repository/target are pinned by operator policy; generation
is a nonnegative integer. Duplicate/unknown fields, non-finite numbers, invalid
types and oversized records are refused. The sole file is at most 64 KiB, mode
100644. State is exactly `idle`, `reserved`, `dispatching`, `granted`, `running`
or `finalizing`.

`epoch` is a fresh 32-hex bootstrap nonce and `cutover_digest` is the SHA-256 of
the signed maintainer cutover record. Both remain immutable for the entire ref
history. Initial idle is generation 0 with null operation, grant and completion.

Initialization requires explicit maintainer authorization, disabling every
nonparticipating activation/native/check/finalization entry point and reconciling
all pre-existing runs, queues, writers, pending updates and native runtimes. No
uncertain pre-existing operation may remain. The cutover record identifies the
retired entry points, trusted launcher and verification evidence. Creating an
idle ref alone is never proof of idleness. Production bootstrap/protection and
cutover qualification are separate from the local prototype.

`operation` is null in idle. Otherwise it fixes a fresh operation ID, the
authorized holder fingerprint, the complete existing qualification admission
object, workflow ID, launcher and pre-issued native nonce. The operation ID is
that nonce; it is not a credential. These fields never change within an operation.

Its exact keys are `id`, `holder_fingerprint`, `admission`, `workflow_id`,
`launcher`, and `nonce`; `id == nonce`. The `admission` schema is the exact one
documented in [QUALIFICATION-EVIDENCE.md](QUALIFICATION-EVIDENCE.md), not a second
loosely equivalent schema.

`grant` is null before `granted`. It then binds the exact workflow/run ID, run
number, attempt 1 and complete QID. The QID is recomputed from the existing
qualification identity, including the admission, launcher and pre-issued nonce.
`running` additionally binds one authenticated native job ID and a fresh
per-invocation worker nonce. `finalizing` adds one fresh finalizer nonce while
retaining the same grant and worker binding. Those nonces are generated inside
the trusted clients, never accepted as resume credentials from command-line data.

The grant's exact keys are `run_id`, `run_number`, `run_attempt`,
`qualification_id`, `native_job_id`, `worker_nonce`, and `finalizer_nonce`.
Attempt is always integer 1. The final three fields are null in `granted`;
`running` sets only the native job ID and worker nonce; `finalizing` additionally
sets the finalizer nonce. No existing grant field may otherwise change.

`completion` is null while held. On release it records the prior operation/QID,
terminal outcome and observed main result, or a pre-dispatch abort. The initial
idle record alone has null completion. Commit history retains the full prior
records; fields are not silently reset or reused.

Its exact keys are `operation_id`, `qualification_id`, `outcome`, and `main_sha`.
Outcome is `aborted-before-dispatch`, `failed`, `cancelled`, `qualified-only`, `rejected` or
`finalized`. The completion matrix below fixes QID and main-SHA values. SHA-1 object IDs are
nonzero lowercase 40-hex strings; SHA-256 values are nonzero lowercase 64-hex;
nonces are nonzero lowercase 32-hex; fingerprints are uppercase 40/64-hex. IDs
are positive integers below 2^63. Booleans are not accepted as integers. Unknown
nested fields or missing state-specific fields are invalid.

The coordination ref must retain append-only history. It is never deleted or
rewound during normal release. Production protection must reject deletion and
non-fast-forward updates; clients also validate raw parents and pinned signatures.
Initializing a missing production ref is not part of ordinary acquisition.

## Transitions

**Only the maintainer-side coordinator signs and pushes transitions.** Workers
submit requests and observe replies; they receive neither a signing key nor
Contents-write permission.

Every transition increments generation by exactly one, has exactly the current
coordination commit as its raw parent, and uses that exact current SHA as its
compare-and-swap expectation. Repository, target, version, epoch and cutover
digest never change.

| Transition | Preconditions and permitted change |
| --- | --- |
| idle → reserved | Allocate one fresh operation and clear the previous completion to null; grant is null. Only one acquisition from the observed idle tip can win. |
| reserved → idle | Normal abort only before any dispatch is permitted; record a pre-dispatch abort. |
| reserved → dispatching | Persist this transition **before** sending a workflow request. Same operation, no grant yet. |
| dispatching → granted | Authenticate the acknowledgement for this exact request, candidate, launcher and new run/attempt; attach its recomputed QID. |
| granted → running | The coordinator accepts one authenticated worker request and performs CAS. Bind its native job ID and fresh invocation nonce exactly once. |
| granted → idle | Only if the run is definitively terminal without native consumption and all possible writers are quiescent. Uncertain dispatch never qualifies. |
| running → idle | Qualification-only/failure release, after the worker and every writer are definitively terminal and no main update was initiated. |
| running → finalizing | First drain all workers/writers; then one finalizer claim wins by CAS and adds its fresh nonce. No further App/native work is permitted. |
| finalizing → idle | After the final proof check and known push outcome, release from the **exact finalizing tip**, recording the terminal result. An ambiguous push remains finalizing. |

Every release following a possible launch also requires verified termination of
the complete owned execution tree, including launcher children, daemon-managed
containers and VMs. A terminal job or exited client is insufficient. Failed or
missing cleanup evidence leaves coordination held.

All unlisted transitions are forbidden. Reserved abort racing dispatch, worker
claim racing a non-consuming release, and finalizer claim racing a
qualification-only release all use CAS from the same predecessor: only one may
win, and the loser must not perform its external action.

Every transition uses a single-ref compare-and-swap:
`--force-with-lease=<coordination-ref>:<expected-sha>`. Despite the option's name,
the proposed successor must be independently proven to be a fast-forward by its
raw parent. Never use an implicit tracking-branch lease or unconditional force.

### Completion matrix

All completion records identify the released operation. Before any release,
every possible writer and native runtime must be quiescent; the reserved
pre-dispatch case establishes this by prohibiting any dispatch or launch.

| Releasing state / outcome | QID | `main_sha` |
| --- | --- | --- |
| reserved / aborted-before-dispatch | null | null |
| granted / failed or cancelled | Exact grant QID | null; no native claim or main update occurred |
| running / failed, cancelled or qualified-only | Exact grant QID | null; no main update may have been initiated |
| finalizing / failed or cancelled before initiating an update | Exact grant QID | null |
| finalizing / rejected | Exact grant QID | Reconciled observed main SHA, different from the attempted signed merge; a definitive not-applied response is also required |
| finalizing / finalized | Exact grant QID | Exactly the qualified `admission.signed_merge`; successful update and readback must be acknowledged |

No other combination is valid. Null `main_sha` means no main-update result is
claimed, not that main is known to be unchanged. A rejected outcome requires a
conclusive not-applied result for this exact attempted target/base, reconciled
main, no outstanding update and normal writer/runtime quiescence. A nonzero exit
code, transport error or different readback alone is not that proof. If neither
success nor definite rejection is established, the record stays finalizing.

## Worker and finalizer obligations

- A workflow is admitted only for the current signed operation and grant,
  matching operation/nonce, full QID/admission, candidate, launcher, workflow/run
  ID and attempt. No caller-provided QID or replayed acknowledgement is authority.
- Before granting, authenticate the request acknowledgement against the exact
  newly dispatched run. A branch name or approximate start time is insufficient.
- The trusted, pinned main-branch workflow emits an immutable request artifact
  containing the operation ID, current coordination SHA, complete candidate and
  run/attempt identity. The coordinator verifies the artifact's API digest,
  repository, workflow ID/path, main-branch launcher revision, dispatch event,
  authorized actor, attempt 1 and operation tuple. If multiple matching runs are
  present, grant at most one by CAS; all others must refuse a nonmatching grant.
- The pinned workflow must contain exactly one native job key, no native matrix
  or fan-out, and one synchronous, non-retrying launch path. Validate the actual
  job graph as well as its source definition.
- The worker generates a fresh invocation nonce and emits a request artifact for
  its exact granted run/attempt, native job ID/key and coordination SHA. The
  coordinator authenticates that artifact via API metadata and its pinned
  emitter code, verifies the actual unique running native job, and performs the
  single `granted → running` CAS. Timestamp matching alone is not uploader
  authentication; the trusted workflow must have no other untrusted uploader.
- The worker polls only for its bounded response and verifies the signed current
  running record contains its run, job and independently generated nonce before
  launch. A different or absent binding refuses execution. It then atomically
  creates a one-shot local launch marker; failed launch/retry cannot remove it.
  A new process cannot adopt a nonce from a record or command-line input. No CI
  participant signs or pushes a coordination transition.
- Old workflow reruns, duplicate dispatches and losing worker claims cannot
  activate checks, execute native work or finalize a candidate.
- Native execution remains globally one-at-a-time. The coordinator holds the
  operation through completion rather than using a replaceable pending slot as
  a queue. Further requests receive a busy refusal and may retry later.
- All check writes are awaited. Before claiming `finalizing`, require the complete
  granted run and expected job/writer set to be terminal, every write acknowledged,
  the complete owned native execution tree stopped/removed, and no dispatch/job
  capable of starting later. A previous matching grant read
  is not permission to perform delayed work. Uncertainty leaves coordination held.
- After winning the finalizer claim, bind the complete proof to that operation,
  QID/admission, launcher, workflow/run/attempt, worker job and exact signed
  `MergeCommit` target. Redo all read-only checks, then make at most one main
  update from the qualified base. Never reuse a stale finalizer capability.
- A new finalizer process cannot resume `finalizing` or borrow its nonce. It must
  stop for recovery. The owning process may release after a pre-push validation
  failure only if it never initiated an update; ambiguous writes stay held.
- Every automated gate activation and finalization must participate. Direct
  maintainer bypass is outside the supported procedure; no bypass is granted by
  this design. Concurrent unrelated main changes are still rejected by the
  exact-base lease.

## Failure and recovery

This lock does not expire. A timeout is an unknown outcome, not permission for
another operation to take over. A coordinator crash, uncertain dispatch, unknown
workflow shutdown, or ambiguous Git/check write leaves the record held.

There is intentionally no automatic recovery/steal operation. Recovery requires
explicit maintainer intervention and proof that old writers and pending updates
and native runtimes cannot still act or execute. Repeated reads, an elapsed timeout or a dead local PID alone
are not sufficient fencing. If that cannot be established, remain stopped.
Production availability/recovery is a separate acceptance gate, not a hidden
guarantee of this prototype.

## Required experiments before implementation approval

### Local probe scope

`tools/probe_coordination.py` is deliberately local-only: it creates both bare
repositories itself and accepts no remote address. Its commits are unsigned
fixtures and `ProbeEvidence` values simulate external proof. It tests state/CAS
mechanics, not production signatures, worker authentication, actual VM cleanup,
bootstrap cutover or recovery. It adds no queue, daemon, heartbeat, lease renewal
or automatic failover. None of its results authorizes production use.

### Signed-state reader

`ci/native/coordination.py:read_signed_coordination()` authenticates one local
snapshot. The caller supplies the exact commit, trusted `QualificationPolicy`,
public keyring, DCO signoff, expected parent, generation, epoch and cutover digest.
The reader reuses the existing OpenPGP verifier and the record schema shared with
the local probe. It accepts only a bounded commit with the exact raw parent and a
tree containing one regular `coordination.json` file of at most 64 KiB. Duplicate
JSON keys, invalid types, extra fields and mismatched identities are refused.

Only generation zero may have no parent. Accepting that record authenticates its
contents, not the claimed cutover or quiescence. Likewise, a pinned parent does
not validate the predecessor's history or authorize this transition. Expected
values must not be copied from the untrusted record to make it pass.

The return value is data, not a worker or write capability. The reader does not
check the remote tip, fetch objects, push refs or launch anything. Signed history
validation, transition authorization, worker handoff and runtime cleanup remain
separate requirements. Missing local objects fail rather than trigger a lazy
fetch. Raw reads and signature status follow Git's
[cat-file](https://git-scm.com/docs/git-cat-file),
[verify-commit](https://git-scm.com/docs/git-verify-commit) and
[GIT_NO_LAZY_FETCH](https://git-scm.com/docs/git#Documentation/git.txt-GITNOLAZYFETCH)
interfaces; the installed Git 2.56.0 manuals were checked during implementation.

### Request-only handoff probe

`.github/workflows/handoff-probe.yml` is restricted to validation branches. Its
single job emits public request data and exits; it cannot launch native work,
write refs or access the reporting environment. A push registers the workflow;
an explicit dispatch produces the request. If GitHub refuses that dispatch,
stop without changing the default branch or repository settings.

`ci/native/handoff.py:verify_handoff_request()` checks the artifact's authenticated
digest and run association, exact pinned workflow source/branch, authorized
actor, attempt 1, sole completed request job, and signed dispatching-state
binding. It returns an unconsumed grant proposal without changing the state.
The caller must supply the previously authenticated state and reviewed emitter
source. An artifact's job timestamp alone is not uploader authentication.

The experiment signs that proposal on a separate validation ref and reads it
back with the signed-state reader. It tests request-to-grant binding, not a
native worker receiving or consuming a grant. A validation-branch result is not
production admission. The existing main-only qualification verifier remains
unchanged; live workers, cutover and runtime quiescence still need qualification.

### Local one-shot worker acceptance

`ci/native/worker_claim.py:WorkerClaim` creates a fresh invocation nonce and
accepts only a matching, already authenticated `running` snapshot. The run and
job IDs come from the trusted caller. The nonce cannot be supplied or restored
through the constructor. Acceptance creates an exclusive marker in a fixed,
caller-owned private workspace. Callers must not delete that marker to retry
duplicate calls or failed marker writes.

The local tests race signed successors of one grant through real Git, then check
that only the selected invocation accepts the result, once. They reuse temporary
signing keys and the signed-state reader. No live GitHub job identity or workload
is involved. The helper does not authenticate a requester, check the latest ref,
authorize a transition or launch anything. Those remain caller obligations and
separate production gates.

### Live no-op worker integration

`live-worker-probe.yml` runs one foreground worker on a validation branch. It
publishes a request while still running, verifies the signed grant, generates
and publishes its claim, then verifies the signed running state. The existing
one-shot guard accepts that state once; accepting the earlier, still-unclaimed
grant and accepting the running claim a second time must both fail.
The final acknowledgement records those results and `native_executed: false`.
The coordinator requires the successful terminal run before releasing the lock.
The expected initial generation is a required dispatch input, not a fixed first-
operation value or a value copied from the unverified record.

The worker reuses the pinned `actions/upload-artifact` entrypoint rather than
implementing another artifact protocol. A small local JavaScript action supplies
the normal artifact runtime environment while keeping one Python invocation
alive. The workflow retains `contents: read`, never accesses the reporting
environment and never receives a signing key. It downloads only the existing
public verification key, pinned by SHA-256 and fingerprint, into a temporary
keyring. The worker wait is deadline-bounded; failure leaves coordination held.

Active request/claim verification is separate from the original completed-only
request verifier. Neither is production admission. This integration still does
not qualify native execution, cutover, general recovery or branch protection.

### Acceptance cases

- Race two acquisitions of one idle record through real Git receive-pack.
- Refuse activation while held, grant replacement, wrong owner/operation, old
  generation, unsigned or malformed state and non-fast-forward successors.
- Race two consumers of the same grant, including duplicate job IDs with different
  invocation nonces; only the winning claimant may reach its one-shot launcher.
- Authenticate the worker-request/coordinator-response path with the actual
  permission boundary: the worker can emit artifacts/read state but cannot sign
  or push; only the coordinator mutates the ref.
- Exercise the full state path and exact finalizing-tip release, plus safe
  pre-dispatch abort and races between release and worker/finalizer claims.
- Hold coordination across a delayed main update; prove a second activation
  remains blocked until an acknowledged terminal release.
- Preserve a held record after a simulated crash or ambiguous update; never
  unlock from age/PID alone.
- Finish or cancel a worker while leaving a simulated owned native runtime alive;
  release and reacquisition must remain blocked until verified cleanup.
- Refuse bootstrap while any pre-cutover entry point, writer, pending update or
  native runtime may still operate.
- Exercise a conclusively rejected leased update: preserve the changed main tip,
  permit only the explicit rejected completion after reconciliation, and continue
  refusing release for a timeout or ambiguous outcome.
- Verify normal release appends history rather than deleting or resetting it.
- Validate the same ref-update and protection behavior on a disposable GitHub
  target without touching main or changing the reporting App's permissions.
- Before real workers, prove the acknowledgement/grant handshake and terminal
  writer quiescence. They are not established by local lock tests.

Use Git's documented explicit-value lease semantics as the primitive, then
verify the actual server behavior. Passing these experiments would qualify only
their measured scope, not an enabled production coordinator.
