<!-- SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com> -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
# Hosted native KWin qualification plan

Status: the owner approved this bounded plan, including the 75-minute hosted job
limit. Implementation is in progress; no hosted native workload has run yet.

## Goal and scope

Qualify the three selected native suites on GitHub-hosted infrastructure using
the proven offline KVM guest boundary. The prior local 43-entry result and hosted
boot result are prerequisites, not substitutes for fresh hosted native evidence.
Tasks are tracked in [todo.md](todo.md).

No host packages, kernel modules, permissions, desktop sessions or persistent
runners may change. No package release, merge or upstream submission is included.
This first native qualification runs on a dedicated trusted branch, not arbitrary
PR-controlled outer-hypervisor code.

## Architecture

- Reuse the published hypervisor image by digest and configuration ID. Keep the
  previous boot-only workflow intact.
- Boot the clean, checksum-verified Fedora cloud image. Never publish or reuse a
  private guest snapshot containing generated keys as a CI input.
- Transfer the original builder image with its manifest and configuration intact,
  using a digest-preserving directory transport. Verify each referenced blob and
  the complete 882-package inventory; do not substitute an OCI export that changes
  the selected image identity.
- Fetch the pinned upstream commit and apply the complete checked-in patch set.
  Archive the verified patched tree, not the unpatched `HEAD` commit. Bind the
  archive and all payload files by SHA-256.
- Use a read-only input ISO inside the guest. Load only the guest's verified vgem
  module and expose only that guest device to the unprivileged inner builder.
- Keep the outer UID1000 KVM container constrained as in the accepted boot probe:
  no network, host binds, GPU, privileged mode or forwarded credentials; one owned
  managed volume, dropped capabilities, enforcing AppArmor and built-in seccomp.
- Compile and run inside the guest's isolated builder, with two CPUs and bounded
  memory. No writable cross-job cache or host container-engine socket is exposed.
- Stage the three byte-verified frame QML files at the private runtime prefix's
  `share/kwin-wayland/frames/plasma`. Preserve and document the historical fixture
  mismatch; do not change KWin source or assertions to hide it.
- Export only bounded, allowlisted text evidence over serial. Independently verify
  test results on the host. Never upload guest disks, private keys or build binaries.

## Ordered increments

1. Implement strict native result validation and runtime-data staging, with positive
   and refusal tests. Require the exact CTest/QtTest suites, direct composition and
   effect-frame cases, no failures/skips/blacklists and actual llvmpipe evidence.
2. Implement verified source/image preparation and bounded text transport. Test
   digest drift, wrong source tree, unsafe archive members, unexpected files and
   oversized/compressed output before any guest execution.
3. Implement the guest and outer controllers. Prove isolation, guest-only DRM
   identity, image inventory, deadlines and owned cleanup with negative tests;
   perform a small hosted renderer preflight before the expensive native build.
4. Run one fresh hosted build/test qualification using the fixed inputs. Allow up
   to 55 minutes for the inner build/test process, 60 minutes for the guest and a
   75-minute job envelope with explicit acquisition/cleanup reserves. Stop on
   failure and retain diagnostics instead of automatically retrying.
5. Review the actual result, update the documentation/handoff and archive essential
   source/evidence with verified checksums. General PR-native CI remains a separate
   acceptance decision after this trusted qualification succeeds.

## Gates

- Every increment passes the repository's Python tests, Ruff, Actionlint/ShellCheck
  where applicable, REUSE and meaningful diff checks before a signed commit.
- Review the controller and trust boundaries before running a guest workload.
- A missing/disabled/repeated/not-run suite, any individual skip or blacklist,
  failed runtime-data identity, missing renderer proof, failed artifact export,
  unknown cleanup state or changed host device metadata prevents success.
- The full run must register and execute `kwin-testOffscreenQuickView`,
  `kwin-testInternalWindow` and `kwin-testTabBox` once each. Compare the observed
  case inventory to the pinned source rather than accepting arbitrary positive
  totals.

## Risks and open checks

- The clean cloud image previously contained Podman 5.8.1; verify its current
  digest-bound guest inventory again rather than copying private prepared disks.
- The larger read-only input and offline import need measured disk/time bounds.
- Result collection must retain useful failure context without granting the guest
  a host filesystem path or accepting fabricated summary fields.
- Check current version-specific Podman/Skopeo, QEMU, CMake/CTest and cloud-init
  behavior against official or installed documentation before implementation.
- No fresh unpatched hosted control has run. Decide whether a bounded control is
  practical after qualification; historical sensitivity is not a fresh control.
