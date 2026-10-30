<!-- SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com> -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
# Specification: first pinned patch set and CI

Status: approved for the fast-validator stage. Native workflow acceptance remains gated below.
This document does not authorize packages or installation.

## Objective

Make the first downstream correction inspectable and verifiable from GitHub: a pinned upstream
source, an ordered patch with provenance, fast repository checks, and one isolated Linux build
with targeted native regressions.

This milestone does not establish a supported end-user package or change normal Fusion installs.

## Proposed baseline and patch

- Upstream: https://invent.kde.org/plasma/kwin.git.
- Reference release: `v6.7.5`.
- Exact source commit: `ab7df7ccb7c6af20f4b279cd6220f7cd3d2267d7`.
- Initial correction: offscreen-view opacity, from the previously qualified stable patch commit
  `af822fbb8663f42e360bfa59a333bce3781b9a8e` in the retained local checkout of the upstream
  repository.
- Upstream tracking: https://invent.kde.org/plasma/kwin/-/merge_requests/10275.

This is a historical source candidate selected because the exact base and patch have prior
qualification. It is not asserted to be the current upstream release, a Fedora 44-supported KWin
target, or compatible with the new builder until the new lane passes. Earlier Qt 6.11.2 results
are retained historical evidence only, not qualification of this CI environment.

The patch commit is currently a local provenance reference, not an object that CI may assume is
fetchable from upstream. CI will consume the checked-in patch and its verified digest. Record its
source repository, base and derivation; retain authorship and applicable licenses. The related
public submission is commit `02b36adce62958bd036860a8f9607eb6b7deee0c` in
https://invent.kde.org/archledger/kwin. The MR is tracking information, not an assumption of merge.

The imported patch must change exactly these upstream paths:

- `src/effect/offscreenquickview.cpp`
- `autotests/integration/CMakeLists.txt`
- `autotests/integration/offscreenquickview_test.cpp`

Further patches and release lanes are outside this first increment.

Record the source commit and each patch's SHA-256 digest. Preserve original authorship and
licensing. Reject a different source revision, a changed patch digest, or failed patch application;
do not silently apply a patch to a different version or skip a patch that conflicts.

## Build environment

Proposed first lane: Linux x86-64, using a Fedora 44-based container where practical.
The earlier qualification used Qt 6.11.2; the new public lane must report its actual dependencies.

Before claiming a reproducible CI environment, verify unauthenticated retrieval of a
dependency-complete builder and pin its `image@sha256` identity and dependency inventory. Do not
install additional unpinned dependencies from moving repositories in the accepted workflow.
A pinned source plus a moving package repository is not enough. This milestone does not claim
bit-for-bit binary reproduction. Do not automatically publish a new builder image or introduce
credentials if no suitable public environment exists; report the gap first.

Use software rendering for hosted native tests if the selected environment supports it. Validate
that route before making it a required CI job. Do not turn missing renderer support into skipped
tests or a successful result. This lane does not replace physical-hardware qualification.

### Feasibility gates

1. After spec approval, the manifest, patch provenance and fast source/patch validators may be
   implemented and reviewed independently.
2. Perform a bounded public-builder check: retrieval, digest, dependency inventory, compatibility
   with the historical source candidate, and working software-rendered native tests.
3. Only after that succeeds, implement the native workflow against the selected environment.

Builder and renderer validation are prerequisites for the native workflow and milestone
acceptance. If either remains unresolved, the milestone stays open. Fast checks alone must not
be described as a completed or green native CI milestone, and native tests must not be optional
or skipped to conceal the missing environment.

## Repository structure and style

- `patches/`: the initial patch, preserving upstream context and licensing.
- A versioned manifest: exact source identity, ordered patch paths/digests and provenance links.
- `tools/`: small source/patch verification helpers, only where standard commands need coordination.
- `tests/`: standard-library Python tests for manifest and patch-handling behavior.
- `.github/workflows/`: fast checks and the one verified build/test lane.
- `docs/`: environment, commands, evidence limits and update instructions.

Use Python's standard library where sufficient, four-space indentation, descriptive names and
SPDX headers. Keep new tooling separate from the imported KWin patch. Follow the existing Markdown
style and keep changes focused. KWin's licenses apply to imported code; the existing documentation
license does not relicense it.

## Commands and verification

The implementation must document runnable commands and required dependencies. Planned commands:

```sh
python3 -m unittest discover -s tests -v
reuse lint
git diff --check "$BASE_COMMIT" "$HEAD_COMMIT"
cmake -S .work/kwin -B .work/build -G Ninja -DCMAKE_BUILD_TYPE=Debug -DBUILD_TESTING=ON
cmake --build .work/build --target testOffscreenQuickView testInternalWindow testTabBox --parallel 2
ctest --test-dir .work/build --show-only=json-v1
ctest --test-dir .work/build --output-on-failure --no-tests=error --parallel 1 --timeout 180 \
    -R '^kwin-(testOffscreenQuickView|testInternalWindow|testTabBox)$'
```

These are intended implementation commands, not a claim that this repository currently has those
helpers, work directories or dependencies. Source acquisition and patch application must be
documented with the final manifest format and checked before configuring CMake.

Whitespace validation must use the actual change range: the PR base/head commits, or the push
before/after commits. Handle branch creation explicitly and fetch the required objects; do not
let a clean working tree turn `git diff --check` into an empty check.

REUSE is separate tooling, not part of Python's standard library. The locally checked tool is
FSFE REUSE 6.2.0. Verify and pin its public tool-image digest or equivalent immutable environment
before adding the workflow, and record the version actually used. Missing tooling fails the check.

Verify the CMake target names and CTest names against the selected builder and source candidate
before committing the workflow. InternalWindow and TabBox provide adjacent input/window and
effect-consumer regression coverage alongside the direct OffscreenQuickView test.

Require the exact expected set of three CTest names to be registered and each to execute exactly
once. A missing, disabled, not-run or skipped suite fails the lane. The imported opacity regression
must actually execute and pass; a skip is not a pass. For this initial lane, any individual
test-case skip also fails and must be investigated. Record pass/fail/skip totals and setup errors;
do not substitute earlier local results for results from the new CI lane.

## Testing strategy

Fast tests cover valid inputs and refusal cases: wrong source identity, changed or missing patches,
invalid manifests, unsafe paths, duplicate entries, conflicts and partial application. Use temporary
repositories for real patch-application tests; avoid relying only on mocked Git calls.

The integration check fetches from the canonical public upstream, verifies the exact full commit
and starts from a clean tree. It verifies every patch digest and applies each patch exactly once,
in manifest order. A fetch, identity, digest, conflict or application failure stops before CMake
and makes the partially patched tree ineligible for building or testing. This requires failure
containment, not a general transactional rollback framework.

The build check compiles the selected native test targets and runs them in an isolated environment.
Retain logs, source/patch/image identities and dependency versions as CI artifacts. Artifact
collection must not hide a failed build or test. Artifacts are limited to logs, test results and
provenance inventories; do not publish KWin packages or installable binary artifacts.

Perform a one-time unpatched control in the selected environment where practical to confirm the
imported regression remains sensitive. This need not become a second per-PR build lane and must
not be confused with a fresh control if only historical results are available.

## CI isolation

Use `pull_request`, not `pull_request_target`, with ephemeral GitHub-hosted runners. PR-controlled
tools and tests receive no secrets or write credentials. Pin Actions to immutable revisions and
do not leave checkout credentials available to tested code.

The build container must not receive host filesystem mounts, container-engine sockets, privileged
access, or host network/process namespaces. Transfer only declared inputs into its disposable
filesystem. Do not share writable caches with untrusted PR jobs. No workflow performs host
package-manager operations, KWin installation, desktop/session modification or deployment.

## Boundaries

- Always: preserve the stock-default policy, validate inputs, retain provenance and failure
  evidence, use read-only workflow permissions, and keep builds isolated from the host desktop.
- Ask first: additional patches or platforms, publishing builder images, adding secrets or broader
  workflow permissions, package distribution, installers, deployments and merges.
- Never: install or replace host KWin in this workflow, add a patched-KWin dependency to normal
  Fusion installation, publish packages automatically, or weaken tests to obtain green CI.

## Acceptance criteria

1. The initial patch has the exact expected three-path scope, documented repository/base/derivation,
   preserved authorship, compatible licensing and a checked-in digest.
2. CI fetches canonical upstream, verifies the exact source commit and clean starting tree, and
   verifies and applies the entire patch series exactly once in order. Any failure blocks build
   and test; no partial tree can be used.
3. Fast validation proves refusal of incorrect inputs and patch-application failures.
4. Public-builder and renderer feasibility gates pass before native workflow acceptance. The
   digest-pinned environment has a dependency inventory and adds no unpinned moving dependencies.
5. That environment builds the patched source, registers the exact expected three suites and
   executes each once, including the imported regression, with no disabled, not-run or skipped
   cases and complete results/logs.
6. PR and push checks use meaningful commit ranges, pinned available tooling, read-only credentials
   and the stated untrusted-code isolation. Failure is not hidden by artifact collection.
7. Documentation distinguishes historical evidence, new CI qualification and end-user support.
8. The change is reviewed and proposed separately; no package, installer, installable artifact or
   package publication is added.

## Open decisions and feasibility gates

- `v6.7.5` is approved as the first historical CI reference.
- The public builder/dependency pin remains to be verified. Fedora 44 is the preferred first lane;
  a different environment or a published custom image requires a stated decision, not an assumption.
