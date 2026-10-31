<!-- SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com> -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
# Fast validation and its limits

This stage validates source identity and patch application. It does not build KWin, qualify a
renderer, produce packages or alter the installed desktop. The separate trusted-branch guest
qualification is recorded in [NATIVE-GUEST.md](NATIVE-GUEST.md). Operational qualification of the
assembled manual producer remains separate; see the [integration plan](MAIN-INTEGRATION.md).

## Inputs

`patchset.json` schema version 1 records the canonical upstream URL, historical release label,
exact source commit, expected patched Git tree, and an ordered list of patches. Each patch records
its SHA-256, expected source paths and provenance.

The first source candidate is KWin `v6.7.5` at
`ab7df7ccb7c6af20f4b279cd6220f7cd3d2267d7`. Its checked-in opacity patch was exported without
modification from qualification commit `af822fbb8663f42e360bfa59a333bce3781b9a8e`.
That commit is local provenance, not something CI must fetch from a personal fork. The related
[upstream submission](https://invent.kde.org/plasma/kwin/-/merge_requests/10275) is a tracking link,
not an assumption that KDE merged the correction.

The expected patched tree is `b33310c0a440ed968f82cd03d94359ad435eaeb1`. This is a Git source-tree
identity, not a binary hash or proof that the new CI environment can compile it.

## Commands

Requires Python 3.10 or newer and Git. No third-party Python package is required for the validator.

```sh
python3 -m unittest discover -s tests -v
python3 tools/patchset.py validate
python3 tools/patchset.py check
```

`validate` checks the local manifest, provenance fields, paths and patch digests. It does not
contact upstream or establish that the patch applies.

`check` initializes a disposable repository, fetches the exact public upstream commit, confirms
its identity and cleanliness, and applies the complete series once in order. It checks each
patch's target paths, rejects added-source whitespace errors and verifies the final Git tree.
It prints a receipt only on success and removes its temporary checkout before returning. It does
not leave a source directory for a caller to build.

The internal `apply_patchset()` helper is only for a trusted, fresh, full disposable checkout.
It does not sanitize arbitrary existing repository-local configuration or index flags. The CLI
never accepts an existing checkout. Source fetching disables inherited Git configuration and
credential prompting and never runs package managers or build commands.

The version-1 validator rejects unknown fields, duplicate JSON keys, duplicate patch IDs/files/
content, invalid hashes, paths with traversal or symlinks, binary/rename patches, unexpected target
paths and unsupported result modes. A conflict or other failure produces no successful receipt.
An invalid partially applied temporary tree is discarded, not handed to a later build stage.

## Workflow boundaries

The fast workflow runs on pushes and `pull_request` events on an ephemeral Ubuntu runner, with
read-only repository permissions and no persisted checkout credentials. Actions are pinned to
immutable revisions. It does not use `pull_request_target`, a self-hosted runner or a writable cache.

REUSE 6.2.0 runs from the public FSFE image pinned by digest. Source is copied into a disposable
network-disabled, capability-dropped container; no host filesystem or container socket is mounted
inside it. The checker container receives no token or other credential.

Repository whitespace checks compare the PR's base/head or push's before/after commit, with an
explicit empty-tree comparison for branch creation. Missing event commits are fetched by exact
identity, including previous tips lost from reachable history after a force-push. A failed fetch
fails the check; it never substitutes another base. Patch files are excluded from that text-level
check because unified-diff context uses trailing space markers. Their added source lines are
instead checked by `git apply --whitespace=error-all`; the regression suite covers that refusal.

Only validation logs and provenance receipts are uploaded. Their collection cannot convert a
failed preceding step to success. No installable KWin artifact is created or published.

## Native qualification is separate

The [recorded hosted guest run](NATIVE-GUEST.md) passed the targeted native suites with the pinned
builder. It qualifies that source and execution path, not the newly assembled manual producer.
The producer still requires a real end-to-end run, App lifecycle checks and verified cleanup
before enforcement. Do not interpret a green fast workflow as native or end-user qualification.
