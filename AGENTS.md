<!-- SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com> -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
# Repository rules

This is a downstream patch-set repository, not an automatically installed KWin replacement.
Read README.md, POLICY.md and CONTRIBUTING.md before making changes.

- **Never replace KWin automatically during normal Plasma Fusion installation.** Patched KWin
  requires a separate, explicit opt-in with replacement and rollback information.
- Do not add dependencies or package-source settings that silently select patched KWin.
- Keep stock-compatible behavior and capability-gated enhancements distinct.
- Pin and verify upstream inputs. Preserve authorship, licenses and regression evidence.
- Do not weaken tests or fabricate build, hardware or compatibility results.
- Keep credentials and generated packages out of Git. Sign commits with the author's DCO only.
- The repository has fast source/patch validation, not a qualified native build or package release.
  Respect SPEC.md's builder gate; scope packaging and installation separately.
- Repository creation does not authorize releases, deployments, account changes or installation
  on the current desktop.
