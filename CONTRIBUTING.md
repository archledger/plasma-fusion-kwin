<!-- SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com> -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
# Contributing

Keep the downstream delta small and the stock installation path unchanged.
Read [POLICY.md](POLICY.md) before proposing patches, packaging or integration changes.

## Patch proposals

Each proposal should identify the upstream repository and exact base commit, the defect or
capability addressed, the smallest coherent correction, the applicable licenses, and any upstream
issue or merge request. Record why the change still needs to be carried downstream.

Supply a regression that fails without the correction when practical. Record exact build and
test commands, versions and results. Do not describe patch application, compilation or simulated
input as evidence of physical hardware behavior.

Do not remove tests, widen tolerances or weaken checks merely to obtain a passing result.
Investigate failures and preserve the evidence. Revalidate when the upstream baseline or a
relevant dependency changes.

## Repository conventions

- Use descriptive, narrowly scoped commits and preserve upstream authorship and license notices.
- Sign commits and include exactly one author-owned `Signed-off-by` trailer.
- Give new files an appropriate SPDX license and copyright notice.
- Use plain English, explicit paths and concise explanations of the reason for a change.
- Keep generated packages, build caches, credentials and machine-specific state out of Git.
- Run `git diff --check` before committing documentation changes.

The initial repository contains no build or test runner. The first patch import must introduce
its verification procedure and pinned inputs rather than relying on undocumented local commands.

## Packaging and installation

Before implementing packaging, agree on target distributions, compatibility checks, explicit
opt-in transactions and rollback. A normal Fusion install must not select downstream KWin through
dependencies or package-source priority. Do not introduce an installer as an incidental part of a
patch import.

Do not publish security-sensitive findings in public issues. Arrange private reporting with the
maintainer before sharing exploit details.
