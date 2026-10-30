<!-- SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com> -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
# Read-only merge-readiness observations

`ci/native/current_policy.py` combines current PR association, classic branch
protection, required checks, reviews and activation-pointer observations. It
returns a `ReadinessSnapshot`, not permission to push. The producer, activation
writer and finalizer CLI are not implemented or enabled by this module.

## Supported initial lane

The current policy must require the native context from the expected App, strict
up-to-date checks and administrator enforcement. Every required context must
have one unambiguous positive App ID and one successful check on the exact signed
merge. Legacy/status-only mappings, missing mappings, conflicting contexts,
skipped/neutral results and duplicate matching App checks are refused.

The evaluator refuses force pushes, deletion, a locked target, linear-history
requirements, conversation-resolution requirements, push restrictions, active
effective rulesets and unrecognized classic protection fields. Code-owner,
last-push and review-bypass requirements also need separate implementation and
are refused, not ignored. This intentionally supports a narrow configuration;
it does not alter unsupported settings.

Rule subobjects have explicit field allowlists too. If status checks include
`enforcement_level`, it must be `everyone`; `non_admins`, unknown values and
unknown nested enforcement fields are refused. Legacy responses without that
field still require administrator enforcement, strictness and the App-specific
mapping. Boolean rules must have an actual boolean `enabled` value. Informational
API URLs are validated as metadata and are never followed. Bypass allowances
must enumerate empty users, teams and apps lists. The initial lane supports
branch-creation and fork-sync flags only when disabled.
Dismissal actor lists describe who may dismiss reviews; this read-only checker
does not grant or exercise that permission and uses the API's current review states.

When signed-commit protection is enabled, the complete current PR commit list
must have valid GitHub signature verification, match the PR's declared count
and end at its qualified head. The final merge's pinned local signature is
verified separately by the signed-merge layer.

## Review reduction

Review IDs are deduplicated. Conflicting representations of one ID cause refusal.
`DISMISSED` removes only that review; it does not suppress earlier reviews by the
same person. `COMMENTED` and unsubmitted `PENDING` entries cannot erase decisions.
The latest remaining `APPROVED` or `CHANGES_REQUESTED` state is selected per user.

An unresolved changes request blocks even if that reviewer later lost permission.
Approval credit requires current write/maintain/admin permission, the exact PR
head and a reviewer other than the PR author. The configured approval count must
be met. Reviews and reviewer permissions are read again near completion.

## Check consistency and current activation

Check, review and commit lists are explicitly paginated with bounded page sizes
and counts. A full final allowed page is not proof of completion. Object lists
must retain a consistent total count and contain every unique record.

The native check ID must equal the ID retained from historical qualification,
and its App, context, signed commit, QID and run URL must match that proof.

A distinct App-owned check on the PR head supplies current activation:
`Native KWin / activation #<PR number>`. Exactly one matching App/context pair
must be successful, on that head, with the verified QID and exact trusted run URL.
A missing, duplicate, pending, failed or different-QID pointer blocks the old
native result, including when a new signed candidate uses the same PR head/base.
The native check and activation pointer are both required; neither substitutes
for the other.

The evaluator rereads check/pointer state, protection, reviews and PR association,
and confirms the qualification has not become a rerun. These reads reduce stale
observations but are not a transaction or lock.

## Unresolved write boundary

An activation writer must authenticate fresh admission, confirm the pending
pointer transition before starting work, reconcile ambiguous API outcomes, and
serialize every pointer/native-result transition. It must never let an older
reporter overwrite newer activation. These behaviors still need implementation
and bounded API experiments, including completed-check reopening.

Finalization must also serialize against activation. A local Git base lease does
not prevent the pointer changing after its last read. Therefore this read-only
module cannot enable production merging. No CI write permission, new coordinator
or weaker race guarantee is implicitly authorized; the final write mechanism
must be resolved and explicitly approved before activation.

The caller must enforce a whole-operation wall-clock deadline and rerun the
complete evaluation, including reviews, immediately before a permitted update.
