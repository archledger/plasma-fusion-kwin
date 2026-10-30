<!-- SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com> -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
# Historical qualification evidence contract

`ci/native/qualification.py` verifies admission and native evidence for one
completed trusted workflow. It is a read-only library, not an admission issuer,
merge command or enabled native PR workflow. Passing this check does not prove
that the PR, reviews or protection settings are still current.

The returned `ProducerAttestedInputs` explicitly distinguishes issuer assertions
from independently checked facts. In particular, the historical verifier does
not prove that a named PR/fork owned the head or synthetic-merge ref in the past.
Retrieving a commit by SHA proves its structure, not a historical ref association.

## Trust inputs

The caller supplies:

- A `ProofAPI` instance bound to the intended repository.
- A `QualificationPolicy` from trusted local configuration, not the artifact.
  It pins the numeric repository, workflow and App IDs, workflow path, launcher
  commit, primary signing-key fingerprint, upstream commit and package inventory.
- A `SignedMerge` returned by `verify_signed_merge()`, never deserialized from
  candidate JSON. It retains the exact commit, tree and ordered base/head parents.
- The selected result artifact ID, its downloaded ZIP bytes and the separately
  downloaded admission artifact referenced by the result receipt.

There are no API writes or private-key operations in this verifier. Its caller
must impose a whole-operation deadline; the API transport's socket timeout is
not that deadline.

## Artifact and receipt

The artifact name is `native-qualification-<run-id>-1`. It contains exactly two
regular JSON members: `qualification.json` and `report.json`. No extraction into
a filesystem occurs. The compressed archive limit is 32 MiB; receipt and report
limits are 64 KiB and 64 MiB respectively. Duplicates, unexpected paths, links,
encryption and unsupported compression are refused. The API artifact digest is
checked before the contents are parsed.

A separate `native-admission-<run-id>-1` artifact contains only `admission.json`.
Its API digest is checked independently, and its creation must precede the
native job within the admission job's interval. Its identity, qualification ID
and expected input-manifest digest must match the worker receipt exactly. The
native worker cannot replace that earlier admission with its own checksum claim.

The version-1 receipt has exactly three top-level fields:

| Field | Meaning |
| --- | --- |
| `identity` | Version, immutable admission and execution identity. |
| `qualification_id` | SHA-256 of the canonical `identity` JSON. |
| `evidence` | Concrete job/check IDs and executed input-manifest digest. |

Canonical JSON uses sorted keys and compact comma/colon separators. Its hash
binds fields; it is not an authorization signature. Authority comes from the
authenticated artifact/run provenance and the pinned trusted workflow code.

`identity.schema_version` is integer `1`. `identity.admission` records the
repository, PR and head-repository IDs; base/head/synthetic-merge/signed-merge/tree
identities; payload SHA-256; pinned upstream commit; prepared source tree, archive
SHA-256 and byte count; and approver ID. `identity.execution` records the trusted
workflow ID, run ID, run number, attempt, launcher commit and a pre-issued native
nonce. The controller's serial evidence must use that nonce; a worker cannot
relabel an older report by changing only its outer qualification ID.

`evidence` records prepare/admission/native/reporter job IDs, the separate
admission artifact ID, the App check ID and the input-manifest SHA-256. These
concrete IDs are outside the identity hash so a
producer can allocate jobs and checks without a circular identity calculation.
They are independently checked against authenticated API records.

## Provenance and native checks

The run must be a successful attempt-1 `workflow_dispatch` of the pinned workflow
and launcher on `main` in the intended repository. The actor and triggering actor
must match the recorded approver, whose current repository permission must be
write, maintain or admin.

This initial contract requires exactly four jobs named `prepare`, `admit`,
`native` and `report`, all successful and from that run/attempt/launcher. The API job count
must match the complete returned list; a truncated page is refused. Admission
must follow isolated preparation and finish before native execution, which must
finish before reporting. Result-artifact
creation must fall within the native job, and App-check completion within the
reporter job. The check must have the pinned App identity/name, final signed
commit, qualification ID and run URL. A final run read rejects a newly started
rerun.

The final signed merge and GitHub synthetic merge must have the same tree and
ordered parents. The prepared source, qualification ID and input-manifest digest
must agree with the native report. The verifier reuses the existing controller,
serial-envelope and native-result validators rather than trusting a success flag:
all 43 test entries, package/runtime identity, renderer checks, cleanup and
unchanged host-device evidence remain mandatory.

The report must also contain `runtime` and `volume` inspect evidence. The verifier
reruns the existing outer Docker policy against it: no host networking/binds,
private namespaces, dropped capabilities, fixed limits and image, the expected
launcher command, only `/dev/kvm`, AppArmor and owned local-volume configuration.
Its result must match the recorded outer summary. The producer retains only
required inspect fields, excluding container environment, unrelated labels and
host mount-source paths. Engine seccomp/AppArmor availability is recorded too.

## Current PR association is a separate gate

`ci/native/current_candidate.py` authenticates the issuer-recorded PR number,
head-repository ID and current head/base/merge refs through the API. It reads the
PR again after the refs and refuses changed, closed, draft or withdrawn requests.
This establishes the association at verification time, not historically and not
atomically with a future merge. A finalizer must require this check before using
the producer-attested PR/fork fields in a merge decision.

## Producer and integration obligations

The producer is not implemented yet. Its reviewed, pinned code must issue the
exact admission and nonce before native execution, independently verify the
prepared source against it, pass the
qualification/input identities into the worker, and produce fresh evidence.
It must never obtain the admission or its expected source checksum from guest
output. Checksums produced by an untrusted preparation step are not sufficient.

The remaining finalizer must compose the current-association gate with admission revocation,
attempt authority, reviews and protection settings immediately before its
conditional update. No successful historical proof grants permission to bypass
those checks. Existing qualification artifacts without this receipt/report
binding are historical evidence, not interchangeable version-1 proofs.

Tests use synthetic API records and native reports with real parsers. They do
not claim a new guest execution, real admission issuance or GitHub merge test.

## Native-stage assembly

The existing `.github/actions/native-guest` action accepts the three admission
inputs together: `qualification-id`, `native-nonce` and
`input-manifest-sha256`. The producer must supply them from its trusted admission,
not guest output. The runner packs the invocation into the checksum-covered
input medium, the controller uses that nonce rather than generating another,
and the runner refuses a different input manifest or returned invocation.

Calls without all three inputs are either rejected (partial input) or remain
the legacy standalone qualification mode (all absent). Standalone mode does not
produce a version-1 PR qualification merely because its tests pass. The outer
container/VM boundary and cleanup requirements are unchanged.

This connects the native execution component; it does not complete the trusted
four-job producer. Assembly should reuse these existing pieces:

| Job | Existing implementation to reuse | Remaining integration |
| --- | --- | --- |
| `prepare` | Candidate binding and `tools/native_inputs.py` | Authenticated candidate collection and isolated preparation for that exact admission. |
| `admit` | Admission schema and signed coordination reader | Trusted issuance of the nonce/QID/manifest binding and the independent in-progress App check. |
| `native` | Authenticated claim, one-shot guard and `native-guest` action | Connect the accepted running claim to the three action inputs; retain the resulting evidence. |
| `report` | Native parsers, qualification and current-policy checks | Collect same-attempt evidence and perform only the authorized App result update. |

Do not replace these missing joins with placeholder jobs or weaken the four-job,
main-branch, signature, admission or cleanup checks to make a draft workflow pass.
No production workflow, native run, App write or merge is enabled by this adapter.
