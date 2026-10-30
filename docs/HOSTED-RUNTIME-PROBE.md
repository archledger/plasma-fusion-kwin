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
