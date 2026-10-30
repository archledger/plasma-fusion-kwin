<!-- SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com> -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
# CI-only hypervisor tools

This image supplies QEMU and a bounded boot probe. It contains no patched KWin,
build output or installer. It is separate from the native dependency builder.
Publishing it does not qualify the native CI lane or change the installed desktop.

## Locked inputs

- Base: `registry.fedoraproject.org/fedora@sha256:351d714a39c16fda7f3d266c0e41b4f34b51e8dfc62f12ae83b8169d7a3c5c64`.
- Complete inventory: `ci/hypervisor/packages.lock`, 193 regular RPMs.
- QEMU: `10.2.2-1.fc44`; SeaBIOS: `1.17.0-10.fc44`;
  xorriso: `1.5.8-2.fc44`.
- Guest: `Fedora-Cloud-Base-Generic-44-1.7.x86_64.qcow2`, SHA-256
  `28680fe5b371a5a82ebf43a31926e086a168e59949d03969c5093e7071f90b7f`.

The guest checksum was checked against the Fedora-signed release checksum file.
Only the clean public image may be used. Retained private test disks contain
generated keys and must not be published or substituted.

The installer requires signed RPMs and fails if the installed inventory differs
from the complete lock. Its metadata stays under
`/usr/share/plasma-fusion-kwin-builder/`, including in this tools-only image.
Exact package versions do not guarantee byte-identical rebuilds. If a pinned
package disappears, the build must fail rather than resolve a newer version.

## Build and publication

From the repository root:

```sh
bash ci/builder/install-locked.sh --check ci/hypervisor/packages.lock
podman build --format=docker --file ci/hypervisor/Containerfile \
  --tag localhost/pf-kwin-hypervisor:check ci
podman run --rm --network=none --read-only --cap-drop=all \
  --security-opt=no-new-privileges localhost/pf-kwin-hypervisor:check
```

The last command prints help. Booting requires an explicit `--probe` and a
separately reviewed launcher. The image defaults to UID/GID 1000, not root.

`publish-hypervisor.yml` runs only for an explicitly pushed
`ci-hypervisor-YYYYMMDD.N` tag in this repository. It validates the tag and
revision, runs the tests, builds before registry login, checks the image, then
publishes to `ghcr.io/archledger/plasma-fusion-kwin-hypervisor`. Checkout
credentials are not persisted. The upload contains metadata and logs, not VM
disks. Consumers must pin the published digest, not a moving tag.

## Boot boundary

The boot controller is limited to one clean Fedora guest, `pc-q35-10.2`, KVM,
two vCPUs, 6 GiB RAM, a 32 GiB disposable overlay and a 300-second QEMU deadline.
There is no TCG fallback, guest NIC, display, GPU, monitor, host directory share
or guest package installation. The guest only reports its kernel and the
availability and checksum of the expected vgem module; it does not load that
module or run KWin.

The hosted launcher checks the effective Docker configuration before starting:
read-only root, UID 1000, dropped capabilities, no-new-privileges,
default AppArmor and built-in seccomp, private namespaces, bounded resources,
and only `/dev/kvm`. Writable storage is one uniquely owned Docker-managed
volume at `/work`, not a bind mount. Only the declared public image is copied in.
The launcher must remove its owned container and volume and confirm unchanged
host device metadata before accepting success. Its subprocess budget is 720
seconds, with 90 seconds reserved for cleanup. It refuses to start the controller
without its full 390-second allowance plus that reserve. Download attempts are
bounded separately; the 20-minute job leaves time for cleanup and artifact upload.

The only accepted output is bounded metadata and logs. Guest overlays, generated
keys and cloud-init state must not be uploaded. A successful boot is not proof
of rendering, native regression execution or end-user compatibility.

## Verification so far

The initial locked local build matched all 193 RPM pins. UID 1000, QEMU 10.2.2,
readable SeaBIOS firmware and the default help command were verified. A fresh
Fedora advisory query reported no pending security updates for this inventory;
that is not a guarantee that no vulnerabilities exist.

The [publication workflow](https://github.com/archledger/plasma-fusion-kwin/actions/runs/41071028266)
built the image and a separate fresh runner pulled it anonymously, checked the
source revision and matched all 193 installed RPMs. Its immutable reference is:

```text
ghcr.io/archledger/plasma-fusion-kwin-hypervisor@sha256:f5375da7eecdfd255cd393be237ab5e2c655b87f2d6169a23a5facfe0fb2bc52
```

`hosted-guest-boot.yml` uses that digest and the matching image configuration ID.
It runs only for relevant changes on the dedicated `ci/hosted-guest-boot` branch,
not for arbitrary pull requests. Hosted guest boot remains pending. The earlier
restricted [KVM API probe](HOSTED-RUNTIME-PROBE.md) passed but created no VM.

The [first hosted guest attempt](https://github.com/archledger/plasma-fusion-kwin/actions/runs/41071772300)
did not qualify. QEMU exited normally and the retained log showed guest shutdown,
but the receipt parser failed and volume removal was not confirmed. The saved
serial tail omitted the earlier receipt area, so it did not establish the exact
framing failure. The original failed report is retained separately.

The corrective probe starts its receipt on a fresh console line and keeps the
complete bounded serial log on failure. Cleanup recognizes Docker's lowercase
`no such volume` response and records removal and follow-up inspection results.
These changes do not relax receipt validation or the isolation requirements.

## References

- [Docker volumes](https://docs.docker.com/engine/storage/volumes/): managed
  volume ownership, copy-up and lifecycle differ from host-directory binds.
- [Cloud-init NoCloud](https://cloudinit.readthedocs.io/en/latest/reference/datasources/nocloud.html):
  local `CIDATA` seed, `instance-id` and offline configuration.
- QEMU 10.2.2's installed `qemu(1)` manual: `-accel kvm`, `-nodefaults`,
  `-no-user-config` and the opt-in seccomp sandbox.
