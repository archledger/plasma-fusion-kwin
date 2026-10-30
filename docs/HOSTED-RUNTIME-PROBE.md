<!-- SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com> -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
# Hosted native-runtime prerequisites

The local guest validation is not proof that a GitHub-hosted runner can run the
same environment. This narrow probe checks that prerequisite without installing
packages, loading modules, creating a VM or executing KWin.

`hosted-runtime-probe.yml` runs only on the dedicated `ci/hosted-runtime-probe`
branch in this repository. It uses read-only repository permissions, does not
persist checkout credentials, and uploads only the JSON capability report.
This is not a native test lane or a required merge check.

The probe opens `/dev/kvm` as the current user and queries only
`KVM_GET_API_VERSION`, defined by the Linux UAPI. It does not create a VM or vCPU.
It records the runner image identity, kernel, resources and the availability of
the tools used by the validated local recipe. It also checks for the versioned
`pc-q35-10.2` machine instead of silently substituting TCG or another machine.
The libvirt check imports its Python bindings in isolated mode; it does not open
a hypervisor connection. The XML validator must also be present.

The command exits nonzero when the current local recipe cannot run unchanged.
That result means the environment must be designed or supplied differently; it
is not a failed KWin regression and must not be hidden as a skipped native test.
A zero exit means only that these prerequisites are present. VM confinement,
boot, software rendering and the three native suites remain separate gates.

```sh
python3 -m unittest discover -s tests -p test_hosted_runtime.py -v
python3 tools/probe_hosted_runtime.py --output /path/to/new-report.json
```

The output path must not already exist. No secret environment variables or
machine-specific identifiers are collected. The documented runner software
inventory is supporting evidence, not a substitute for the live report:
https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2404-Readme.md

## Observed hosted result

The [2026-10-30 probe](https://github.com/archledger/plasma-fusion-kwin/actions/runs/41063494999)
ran on Ubuntu image `20261026.354.1`, kernel `7.0.0-1017-azure`, as UID 1001.
It correctly stopped with unmet prerequisites:

- Opening `/dev/kvm` returned `EACCES` for the runner user.
- `qemu-system-x86_64`, `qemu-img`, `virsh`, `virt-xml-validate` and `xorriso`
  were unavailable; Python could not import libvirt.
- Podman was present. The runner had four CPUs, about 16 GB of memory and enough
  free storage, so resource capacity was not the reported blocker.

No VM, installation, module load or permission change was attempted. The workflow
retained `hosted-native-prerequisites.json`; the separate fast checks passed.

The tested runner cannot use the local recipe unchanged. `EACCES` does not prove
the hardware cannot support KVM; it establishes the current user's access limit.
Any next design must explicitly supply pinned hypervisor tooling and appropriately
confined KVM access. Do not silently install packages, change device permissions,
fall back to TCG, or switch to a self-hosted runner to turn this probe green.

## Restricted container follow-up

`hosted-kvm-container-probe.yml` tests whether the existing Docker daemon can
provide narrow access without changing host permissions. It uses the published
digest-pinned dependency image solely as a Python helper, not to build KWin.

The helper runs as UID 1000 with the KVM node's numeric supplementary group. Its
root filesystem is read-only, capabilities are dropped, AppArmor and
no-new-privileges remain enabled, and networking, host mounts and shared host
namespaces are forbidden. Only `/dev/kvm` is assigned; no GPU device is exposed.
The effective configuration is checked before starting it.

The helper only queries `KVM_GET_API_VERSION`. It creates no VM or vCPU. The host
script checks that device ownership/mode did not change and removes only its own
identified container. Registry access uses an empty temporary Docker credential
configuration, and no host environment or socket is passed into the helper.

A successful result establishes only restricted KVM API access. Hypervisor
tooling, guest boot, isolation and native tests remain separate requirements.
If this route fails, a self-hosted option needs its own isolation review; do not
attach untrusted PR jobs to an existing personal runner or register a new runner
implicitly.

### Restricted-access result

The [2026-10-30 restricted probe](https://github.com/archledger/plasma-fusion-kwin/actions/runs/41067489592)
passed on the hosted runner. UID 1000, with supplementary group 993, opened the
mapped KVM device and received API version 12. The helper used the expected
digest-pinned image, read-only root filesystem, default AppArmor, built-in
seccomp, no-new-privileges, dropped capabilities, no mounts and no network.

The host device remained owned by UID 0/GID 993 with mode `0660`; no permission
change was needed. The helper was removed after completion. No VM or vCPU was
created, and no native suite ran in this probe.

This resolves the narrow access question without switching to a self-hosted
runner. Missing hypervisor tooling remains: a pinned, isolated tool environment
and an actual guest-boot test are still required before hosted native CI can be
accepted. API access alone is not guest or native-test qualification.
