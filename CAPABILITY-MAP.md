<!-- SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com> -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
# Capability map: enforcement and finalizing-write validation

Status: module boundaries and order approved on 2026-11-01. The owner selected
the main-only source-installation path for specification. Actual source merge,
live-test configuration and execution still require their separate approvals.

| Module ID | Responsibility | Depends on |
|---|---|---|
| `check-enforcement-probe` | On disposable GitHub refs, establish whether the required App/context rejects absent checks and stale success paired with a current pending/failure state, while accepting the current-success control. Preserve the actual server rejection reason. | Existing fresh-check client and journals; exact sandbox/ref/rule approval |
| `finalizer-integration` | Join verified native evidence, current policy, exclusive finalizing ownership and the exact-result update. Validate success, stale ownership/base, rejection and unknown-outcome handling without changing production `main`. | `check-enforcement-probe` result; existing signed-state/proof/update primitives |

Build order: **check-enforcement-probe → finalizer-integration**.

## Existing gaps

- The current A/B/C probe validates check creation/completion and history, not
  GitHub's protected-ref selection behavior or a controlled pending state.
- `push_exact_merge` is a low-level primitive hard-wired to `refs/heads/main`.
  It is not a complete finalizer or safe to point at the production repository
  merely to demonstrate a test.
- The current coordinator ends at `qualified-only`. Its completed operation
  must not be reconstructed as a held grant for a later write.
- Local coordination tests use simulated runtime/authority evidence. They do
  not establish a production finalizing path.

## Boundaries

- Use disposable refs and explicit test configuration; retain failure evidence.
- Never use production `main` as the test-update target. A reviewed source-only
  probe installation on `main` requires separate merge approval. Keep production
  enforcement, coordination authority, reporting-environment restrictions, App
  permissions and the installed desktop unchanged.
- No extra native run, production merge or enforcement rollout is included.
- Exact live-test refs, temporary protection changes, App-only dispatch budget,
  isolation method and rollback will be specified and reviewed before use.
- Do not export the App private key or invent a bypass to use the existing
  protected reporting environment from unapproved code.
- The second module's specification must use the first module's observed server
  behavior; a surprising result is a blocker, not a reason to weaken checks.

The first module's specification and source implementation plan are approved.
Implement `tasks/check-enforcement-probe/todo.md` EP-01–EP-06 first. Source merges
and GitHub settings/live operations remain separately gated.
