<!-- SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com> -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
# Plasma Fusion KWin

An optional downstream KWin patch set for
[Plasma Fusion](https://github.com/archledger/plasma-fusion).
This repository is intended to track a small set of changes against pinned upstream KWin sources,
not maintain an unrelated compositor or duplicate the full upstream source tree.

## Stock KWin is the default

**A normal Plasma Fusion installation must never replace the distribution's KWin packages.**

| User choice | Intended behavior |
| --- | --- |
| Install Plasma Fusion normally | Keep the distribution's KWin. Use only supported capabilities. |
| Explicitly choose compatible patched KWin | Enable the capabilities supplied by its patches. |

A patched build that installs the same files will normally conflict with or replace the stock
packages. Optional does **not** mean that both builds can be installed as the normal compositor
at once. That replacement must be a separate, informed user choice, with compatibility checks
and documented rollback. A package-manager confirmation prompt alone is not sufficient consent.

Enabling a package source or updating a normal Fusion installation must not silently switch the
user to patched KWin. See [POLICY.md](POLICY.md) for the installation and maintenance requirements.

## Current status

This repository carries one pinned opacity patch and fast source/patch validation. It does
**not** provide packages, releases, a package repository or an installer. Nothing here changes
the installed desktop. A [trusted-branch native guest qualification](docs/NATIVE-GUEST.md)
passed. The manual trusted-native producer is source-validated, not yet
production-qualified or enforced; see the [main integration and cutover plan](docs/MAIN-INTEGRATION.md).
An automatic pull-request lane and end-user packages remain out of scope.

The first historical source candidate is KWin `v6.7.5`, pinned in `patchset.json` with the patch's
digest, provenance and expected result tree. See [validation details](docs/VALIDATION.md) and the
[approved scope](SPEC.md). This is not a claim of current distribution support.

[KWin !10275](https://invent.kde.org/plasma/kwin/-/merge_requests/10275) tracks the related upstream
submission. A submitted or locally tested patch is not automatically a supported downstream
package, and upstream merge status is not assumed.

## Maintenance model

- Pin the exact upstream source used for each patch set.
- Keep patches small, preserve their licenses and authorship, and track their upstream status.
- Validate patch application, builds and relevant regressions for each supported target.
- Remove downstream patches when the chosen upstream baseline includes the correction.
- Detect required capabilities rather than assuming that a version string proves a patch exists.
- Leave unsupported features disabled or use a separately tested fallback on stock KWin.

Upstream KWin remains at [invent.kde.org/plasma/kwin](https://invent.kde.org/plasma/kwin).
Public upstream sources can be fetched without an Invent account. Day-to-day downstream issues
and maintenance can live on GitHub; upstream contributions still follow KDE's own process.

Read [CONTRIBUTING.md](CONTRIBUTING.md) before proposing the first patch or build workflow.

## License

Documentation is licensed under [CC-BY-SA-4.0](LICENSE). Validator code and the imported patch are
GPL-2.0-or-later; manifest data is CC0-1.0. See `LICENSES/` and the SPDX notices. The documentation
license does not relicense KWin code or replace its copyright notices.
