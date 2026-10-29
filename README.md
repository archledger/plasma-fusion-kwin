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

This is the documentation foundation for the downstream patch set. It does **not** currently
provide patches, packages, releases, a package repository or an installer. Nothing here changes
the installed desktop.

The existing investigation and upstream submissions remain separate from a downstream release.
In particular, [KWin !10275](https://invent.kde.org/plasma/kwin/-/merge_requests/10275) addresses
offscreen-view opacity. A submitted or locally tested patch is not automatically a supported
package, and its presence here must not be inferred from that link.

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

The documentation in this initial repository is licensed under
[CC-BY-SA-4.0](LICENSE). Any KWin source or patches imported later must retain their applicable
upstream licenses and copyright notices; the documentation license does not relicense that code.
