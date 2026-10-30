<!-- SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com> -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
# Hosted native qualification

Status: implementation and review in progress. No hosted native test result is
claimed here. The earlier local 43-entry result and hosted boot qualification
remain separate evidence.

## Inputs and execution

The dedicated `ci/hosted-native-qualification` branch runs a renderer preflight before
the native job. It does not accept arbitrary PR-controlled hypervisor scripts.
General PR-native CI needs a separate acceptance decision after qualification.

Both jobs use the verified hypervisor image:

```text
ghcr.io/archledger/plasma-fusion-kwin-hypervisor@sha256:279f86086562a41840b597358c3bc302836e0dd03c627d2e22cc14dded4cab95
```

The inner dependency environment is unchanged:

```text
ghcr.io/archledger/plasma-fusion-kwin-builder@sha256:4ea17e89d08d6d921e50cb2dabc3011a7061b3cfd1a2b22b026ce8842d0042eb
```

The preparer fetches the manifest's exact KWin commit, checks the complete patch
series and exports the verified patched index tree with fixed timestamps. It
does not accidentally archive the unchanged upstream `HEAD`. It verifies the
original builder manifest, configuration, every referenced blob and all 882
installed RPM pins. Image transport must preserve the original digests.

Only declared input files enter the owned Docker volume. A read-only ISO carries
them into the fresh, pinned Fedora guest. The bootstrap validates its checksum
manifest before running Python and has an independent failure/shutdown trap.

## Isolation

| Layer | Boundary |
| --- | --- |
| Hosted hypervisor container | UID 1000, read-only root, dropped capabilities, no-new-privileges, enforcing AppArmor, built-in seccomp, private namespaces, no network or host binds, only `/dev/kvm`. |
| Guest setup | No NIC or physical GPU. Verify the pinned kernel/vgem module, load only guest vgem, wait for udev and recheck its identity before starting the builder. |
| Inner builder | UID 1000, no network or mounts, private namespaces, no-new-privileges, empty capabilities, seccomp, and only the guest vgem node with `rw` access. |

Podman 5.8.1 reports an empty device-permissions field in its inspection output.
That field is not treated as permission proof: after `podman init`, the runner
checks the generated OCI device rules, namespaces, user and capabilities.

No host KWin, package, kernel module, permission or desktop setting is changed.
Guest disks and generated private keys are never uploaded.

## Test contract

The native workload builds only `testOffscreenQuickView`, `testInternalWindow`
and `testTabBox`. Each runs through its CTest registration and D-Bus wrapper,
with the original working directory. The verifier requires all 43 expected
QtTest entries, including the data rows, no failures/skips/blacklists and actual
llvmpipe output from OffscreenQuickView. Contradictory non-passing records are
rejected even if a summary claims success.

The historical fixture uses a different QML path from the runtime install rule.
Only three hash-verified source QML files are staged under a fresh private
`share/kwin-wayland/frames/plasma` prefix. No KWin source or assertion changes.

The renderer preflight checks the guest vgem driver, llvmpipe, GBM allocation,
DMA-buf export/import and four pixel readbacks. Passing it is not a substitute
for executing the three native suites.

## Time and evidence limits

The renderer job has a 20-minute limit; the native job has a 75-minute limit.
Acquisition time is subtracted before the outer controller receives its budget.
The native QEMU deadline is 3,600 seconds; guest and inner-workload deadlines
are shorter and reserve time for export, shutdown and owned-resource cleanup.
Insufficient remaining time prevents starting a guest rather than borrowing
the cleanup allowance.

Evidence crosses serial as a nonce-bound compressed text envelope. File names
are allowlisted; symlinks, special files, unknown names and overlarge payloads
are rejected. Each file is limited to 5 MiB and the decoded set to 16 MiB.
The host independently recomputes test, image, source, runtime-data and renderer
checks. Missing evidence or unconfirmed container/volume removal fails the job.

## References

- [Skopeo 1.13.3 copy options](https://github.com/containers/skopeo/blob/v1.13.3/docs/skopeo-copy.1.md)
- [Ubuntu 24.04 runner image inventory](https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2404-Readme.md)
- [OCI runtime Linux device configuration](https://github.com/opencontainers/runtime-spec/blob/main/config-linux.md)
- [Hypervisor qualification and its limits](HYPERVISOR.md)
