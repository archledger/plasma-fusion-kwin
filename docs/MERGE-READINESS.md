<!-- SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com> -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
# Read-only merge-readiness observations

`ci/native/current_policy.py` combines verified v2 evidence and current signed
authority with PR association, classic branch protection, checks and reviews. It
returns a `ReadinessSnapshot`, not permission to push. The separate manual
producer, fenced activation writer and read-only finalizer CLI are implemented;
their operational qualification and finalizing main-write integration remain
pending. This module does not enable them.

## Supported initial lane

The current policy must require the native context from the expected App, strict
up-to-date checks and administrator enforcement. Every required context must
have one unambiguous positive App ID. The native pair is selected by its exact
admitted IDs; unrelated required providers still need exactly one matching
successful check on the signed merge. Legacy/status-only mappings, missing
mappings, conflicting contexts and skipped/neutral results are refused.

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

The native ID and the distinct activation ID come from authenticated v2
admission and qualification. Each direct read must match its expected App,
context, commit, QID, canonical run URL and successful terminal state. Native
targets the signed merge; `Native KWin / activation #<PR number>` targets the PR
head. Neither ID can be replaced by a scan result, newest record or old success.

Bounded inventories reject observed duplicate IDs, conflicting QID/run mappings,
malformed matching records and other nonterminal records. Older well-formed
terminal records under the native contexts remain inert history. This allowance
does not apply to unrelated required providers and does not prove global history
completeness. Missing or pruned admitted evidence fails closed.

The [read-only command](READ-ONLY-FINALIZER.md) requires an independent operator
anchor and authenticates the fixed coordination tip before and after evaluation.
Running/finalizing state must match the verified nonce, QID, run/number, native
job and policy-bound operation. Idle state must be the same qualified-only
completion with no main result. A new reserved/dispatching/granted operation or
a failed/cancelled completion blocks old successful evidence.

The evaluator compares both complete check snapshots, protection, reviews and PR association,
and confirms the qualification has not become a rerun. These reads reduce stale
observations but are not a transaction or lock.

## Unresolved write boundary

The manual producer and fenced App client implement fresh paired admission,
confirmation before native work, and journaled exact-ID completion. They stop on
ambiguous outcomes rather than retrying or letting an older reporter overwrite
a new operation. The fresh-pair API lifecycle and complete producer still need
separately approved live qualification. Completed checks are never reopened.

Finalization must also serialize against activation. A local Git base lease does
not prevent the pointer changing after its last read. Therefore this read-only
module cannot enable production merging. No CI write permission, new coordinator
or weaker race guarantee is implicitly authorized; the final write mechanism
must be resolved and explicitly approved before activation.

The caller must enforce a whole-operation wall-clock deadline and rerun the
complete evaluation, including reviews, immediately before a permitted update.
