<!-- SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com> -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
# Dependency-only CI builder

This image is build infrastructure, not a patched KWin package or an installer.
Normal Plasma Fusion installation must not use it to replace the system compositor.

## Frozen environment

The builder starts from a digest-pinned Fedora 44 base. `ci/builder/packages.lock`
records the complete regular RPM inventory as exact name/epoch/version/release/architecture
values. Image construction enforces package signature checking for the main configuration and
each repository, disables weak dependencies and refuses an inventory mismatch.

Only RPM records whose package name is exactly `gpg-pubkey` are excluded from the lock.
Similarly prefixed ordinary package names remain part of the inventory. This does not disable
signature checks: the Fedora base supplies its repository keys and package verification stays
enabled. Package-provided copyright and license files are not removed.
The image also records source-RPM names and package license identifiers. Dependencies retain
their original licenses; the repository's documentation license does not relicense them.
The OCI license label is `NOASSERTION` rather than inheriting the base image's single-license
label for this mixed dependency collection. Consult the package-specific notices and inventory;
publishing this builder does not assert additional patent-license coverage.

The initial lock is derived from an isolated dependency-resolution prototype using Fedora's
KWin build requirements plus the explicit tools/runtime dependencies needed for testing.
That moving-repository experiment is not the accepted native CI environment. The final image
must build from the lock, be published, and be referenced by its verified digest.

The lock fixes installed package versions; it does not promise bit-identical image rebuilds.
If an exact RPM is no longer available, construction must fail rather than select a newer
package or regenerate the lock automatically. A dependency update is a reviewed change.

## Publication boundary

Only an explicit `ci-builder-YYYYMMDD.N` tag push in the owning repository triggers
`.github/workflows/publish-builder.yml`. Creating that tag is a publishing operation and
requires maintainer approval. Pull requests and ordinary branch pushes do not publish images.

The publisher builds before logging into GHCR. The registry token is injected only in the
publishing step, not passed into the image build. The image is linked to this repository with
OCI source/revision labels. No GitHub Release or installable KWin binary is created.

GHCR initially creates private packages. The new CI package must explicitly be made public,
then its exact digest must be retrieved without credentials before it is accepted as a public
builder. Repository access inheritance alone does not prove public visibility.

## Qualification boundary

Image construction and publication are not native test qualification. The published digest
must separately configure/build the pinned KWin source and pass the required software-rendered
suites in a copied-input container with no host mounts, devices, credentials or engine socket.
The builder runs as an unprivileged user. Logs and inventories are retained; native build outputs
are not published as end-user packages.

No native workflow may treat a missing dependency, renderer failure, absent test or skip as
success. Until qualification is recorded, this remains a candidate builder and the native gate
in SPEC.md stays open.

## Published candidate and qualification result

The dependency image was published from signed tag `ci-builder-20261030.1` at
`864718f2ebc52ef65c2f39c18f5b2037f55f459d`:

```text
ghcr.io/archledger/plasma-fusion-kwin-builder@sha256:4ea17e89d08d6d921e50cb2dabc3011a7061b3cfd1a2b22b026ce8842d0042eb
```

The [publication workflow](https://github.com/archledger/plasma-fusion-kwin/actions/runs/40988574921)
passed. Anonymous manifest retrieval and a full pull passed; the downloaded Linux amd64 image
runs as `builder`, and its actual 882-package inventory matches the lock.

The first native probe configured and compiled all three targets from the pinned patched source.
It did **not** pass the renderer gate:

| Suite | Result |
| --- | --- |
| InternalWindow | 18 QtTest entries passed; no failures or skips. |
| TabBox | 10 QtTest entries passed; no failures or skips. |
| OffscreenQuickView | Setup failed: QPainter selected instead of required OpenGL. |

The probe exposed no DRM devices. This KWin virtual backend only advertises OpenGL after opening
a DRM render device; setting llvmpipe environment variables did not satisfy that requirement.
The opacity/composition regression cases therefore did not execute. The image remains a
published dependency candidate, **not a qualified native CI environment**. No test was disabled,
and no host device access or additional KWin patch was introduced to bypass this failure.
