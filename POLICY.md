<!-- SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com> -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
# Installation and downstream maintenance policy

## Non-negotiable default

Normal Plasma Fusion installation must never replace KWin automatically.
Patched KWin is a separate, explicit user opt-in.

Normal Fusion packages must not acquire a hard or weak dependency that silently selects this
downstream build. Enabling a package source alone must not make the package manager replace stock
KWin during an ordinary update.

## Explicit opt-in is a replacement decision

If a downstream build occupies the same paths as the distribution's KWin, the package manager
may need to remove, replace or conflict with stock packages. Packaging must handle that honestly;
it must not overwrite files behind the package manager's back.

Before the user confirms the separate opt-in operation, explain:

1. Which packages will change and which additional features the patches enable.
2. Which distribution, Plasma, KWin and Qt combinations are supported.
3. That the active compositor is being changed, including any required session restart.
4. How to restore the distribution packages and disable the downstream update source.

Do not preselect the patched build, bury the replacement in a normal Fusion install, or treat a
generic package-manager confirmation as the only opt-in step. Do not claim side-by-side support
unless an isolated development-session arrangement has actually been implemented and tested.

## Capability and support boundaries

- Stock KWin remains a supported baseline for features it can provide.
- Features requiring missing capabilities must stay disabled or use a tested fallback.
- Matching a KWin version string alone does not prove that a downstream patch is present.
- A supported package needs a precise source revision, patch set and dependency compatibility
  record. A successful patch-application check alone is not runtime qualification.
- Local tests, upstream CI, physical-device tests and release qualification are distinct evidence.
  State which were performed and retain failures rather than relabelling them.

## Release prerequisites

Before distributing installable packages, establish supported targets, reproducible source and
patch provenance, build and regression checks, package-manager transaction tests, an update policy,
and a tested rollback path. Security fixes from upstream need continuing review.

No package release, repository enablement or desktop deployment is part of the initial
documentation-only repository. Introducing any of those requires an explicit implementation
scope and review; this policy is not itself an installer or an enforcement mechanism.
