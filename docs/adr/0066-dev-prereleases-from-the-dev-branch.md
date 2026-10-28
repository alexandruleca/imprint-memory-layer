---
title: ADR 0066 — Dev prereleases from the dev branch
description: Pushes to dev publish prereleases of the next stable version through the same release pipeline, skipping commits main already holds; the dev channel installs the highest release version.
---

- Status: Amended
- Date: 2026-10-25
- Amends:
  - [ADR 0014](0014-main-only-release-packaging.md) — restores automatic dev releases; release builds require main or dev
  - [ADR 0027](0027-symmetric-uninstall-and-branch-sync.md) — releases are no longer main-only: dev publishes prereleases
- Amended by: [ADR 0108](0108-installations-opt-in-to-dev-builds-in-settings.md) — the dashboard offers dev builds to installations that opt in
- Related: [ADR 0002](0002-build-and-validate-before-publication.md), [ADR 0023](0023-update-by-installation-type.md), [building and releases](../building.md#release-workflow), [installation](../installation.md#install-a-specific-version-or-channel)

## Context

[ADR 0014](0014-main-only-release-packaging.md) stopped packaging on every branch because development pushes and manual diagnostic runs were building full installers and competing for native runners. It also removed automatic dev releases. On 2026-10-25 the owner said that removal was a misunderstanding: "we should bring those back for dev". Branch compile/test CI stays as ADR 0014 decided.

The dev channel's consumers were left in place and now point at stale builds. `imprint update --dev`, `install.sh --dev` and `install.ps1 -Dev` take the first prerelease among the 30 newest releases. On 2026-10-25 that is `v0.8.0-dev.76`, built on 2026-05-27, sixteen minor versions behind the stable `v0.24.0`.

The removed `dev-release.yml` (deleted in `6c5bcfe`) was a second workflow that called the shared build. It versioned builds `<last stable>-dev.<run number>`, so `v0.7.1-dev.75` was built after `v0.7.1` but ranks below it by semantic version precedence. `v0.7.0-dev.70` was created after `v0.7.0` too.

The release process is already set: changes go to `dev` first and `main` fast-forwards to the same tested commit. `sync-dev.yml` fast-forwards `dev` after every push to `main` ([ADR 0027](0027-symmetric-uninstall-and-branch-sync.md)). `release.yml` picks the stable version from the conventional commits since the last stable tag.

## Decision

**One pipeline.** `release.yml` runs on pushes to `main` and `dev`, and on manual dispatch from either. Both branches use the same commit analysis and call the same `build-release.yml` with every packaging step and native gate; `dev` passes `prerelease: true`. The branch policy in `build-release.yml` accepts `main` and `dev`; any other ref fails before packaging starts. Every other branch still gets compile/test CI only.

**Version.** A dev build is `vX.Y.Z-dev.N`:

- `vX.Y.Z` is the version `main` would release from the same analysis: `feat` minor, `fix`/`perf` patch, a breaking change major, counted from the last stable tag.
- `N` is one more than the highest existing `vX.Y.Z-dev.*` tag.

By semantic version precedence, dev builds rank above the previous stable release and below `vX.Y.Z`, and each build ranks above the earlier ones.

**What a dev push skips.**

- A dev head that `main` already holds (`git merge-base --is-ancestor HEAD origin/main`) is not built: `main` releases it, or found nothing to release in it. This covers `dev` at or behind the latest stable tag, the fast-forward after a main push, and `main` pushed before `dev`. A forced bump does not override it.
- If the analysis finds nothing releasable, nothing is built, as on `main`. A manual dispatch can still force a bump.

**Runner cost.** Each branch has its own concurrency group. A newer dev push cancels a dev build in progress; a stable release is never cancelled.

**Release notes.** Prereleases are created by the same `gh release create --generate-notes` step, with `--prerelease`.

**Channels.**

- *Stable stays as it is.* `imprint update`, the dashboard's update check (`imprint update --check --json`), the default installers and the website read GitHub's `/releases/latest`, or the first release that is not a prerelease. GitHub never points `/releases/latest` at a prerelease.
- *Dev is the highest release version among the 30 newest releases, prerelease or stable.* This applies to `imprint update --dev`, `install.sh --dev` and `install.ps1 -Dev`. A skipped build means the newest build of `dev` can be a stable release: after `dev` is fast-forwarded to a release, the newest prerelease is older than that release.
- *Version comparison follows the same precedence.* A dev install sees the stable release that follows it as an update. The dashboard therefore offers stable releases to dev installs and never offers prereleases.

**Container images stay stable-only.** `image.yml` runs only after a `main` Release run. Pages redeploys after every successful Release run, so the download page lists new prereleases.

## Rejected alternatives

- **Restoring the separate `dev-release.yml`.** A second workflow file is a second pipeline to keep aligned. Stable and dev implementations drifting is what [ADR 0002](0002-build-and-validate-before-publication.md) rejected, and that workflow already computed versions its own way.
- **`<last stable>-dev.<run number>` versions, the old scheme.** A dev build ranks below the stable release it follows, so no version comparison can tell that it is ahead. Run numbers also jump across unrelated runs.
- **Building every dev push.** The fast-forward after a release and documentation-only pushes would package the same release again: the runner cost ADR 0014 removed.
- **Skipping only dev heads at or behind the latest stable tag.** A main release in progress has no tag until it publishes, about an hour of native builds later. A dev push of that commit in the meantime would build it again. `main`'s history holds the commit from the moment it is pushed, and that check also covers the tag case.
- **The dev channel as the newest prerelease, as before.** After `dev` is fast-forwarded to a stable release, `--dev` would step back from `vX.Y.Z` to an older `vX.Y.Z-dev.N`. Today it resolves to a five-month-old build.
- **The dev channel as the newest release by creation date.** This equals the highest version only while `main` receives commits solely through `dev`. A hotfix released from `main` while `dev` is ahead would make `--dev` step back from dev's own build.
- **Publishing dev container images (`:dev`), as before ADR 0014.** The container smoke runs only on `main`. Images build on the single self-hosted runner, and image source tags must be ancestors of `main`. A container cannot update itself either way.
- **Letting `main`'s release cancel a dev build of the same commit.** One shared concurrency group cannot do it without letting a dev push cancel a stable release in progress. An explicit cancel step would need Actions write permission and another moving part.

## Consequences

- **Cost.** Every releasable dev push runs the full native pipeline, about as much runner time as a stable release. Superseded dev builds stop early. Fast-forwards and pushes with nothing to release build nothing. Pushing one commit to `dev` and then straight away to `main` builds it twice, because the dev build starts before `main` holds the commit. Promoting to `main` after trying the prerelease avoids that.
- **No partial prereleases.** `gh release create` uploads assets to a draft and publishes it after the last upload; on an upload failure it deletes the draft. A cancelled publication can leave an unpublished draft but never a public partial prerelease.
- **The workflow token.** The fast-forward is pushed with the workflow token, which does not start workflows. The ancestry check keeps a token with that permission from causing duplicate builds.
- **Dev installs.** They are offered the next stable release in the dashboard once it is out, and are never offered prereleases. On a dev install, `imprint update` installs the stable channel's latest release after confirmation, even when it is older.
- **Old dev releases.** Dev builds from before ADR 0014 rank below their stable releases, so they never become the dev channel's latest.
- **Website.** The release list grows. The site now reads the 100 newest releases instead of 30, so the stable history and the latest Chrome extension ZIP stay inside its window.
- **Installer versions.** Windows and macOS installers carry the prerelease version, for example `0.25.0-dev.3`, in their app version fields. Dev releases from before ADR 0014 shipped `.exe` and `.pkg` assets versioned this way.

## Invalidation conditions

- If `dev` stops receiving every commit `main` releases, because `sync-dev.yml` is removed or `main` takes commits that never pass through `dev`, two rules must be derived again: that a commit `main` holds is `main`'s to release, and that the highest version is dev's newest build.
- If native runner time becomes the constraint again, as in ADR 0014, restrict dev prereleases rather than their native gates: for example, when dev builds hold up stable releases or exhaust the Actions budget, publish them only on manual dispatch.
- If the GitHub releases API stops listing the newest releases first, the dev channel and the website must page through releases instead of reading fixed windows of 30 and 100.
- If more than 100 releases are published between two stable releases, the website loses the latest stable release from its window and must read `/releases/latest` itself.
- If releases move to tags, the branch-based choice of channel must be redesigned. ADR 0014 rejected that move because it changes automatic versioning and publication.

## Validation

- **Release analysis.** `scripts/test-release-versioning.py`, run by CI's `workflows` job, executes the actual analysis script from `release.yml` against disposable Git histories. On `main` it covers the existing cases, plus dev tags never counting as the last stable release and a queued run whose commit `main` has moved past. On `dev` it covers:
  - minor, patch, major and first-release prerelease versions;
  - `N` counted per version, ignoring other versions' tags and malformed ones;
  - documentation-only pushes skipped, and building after a forced bump;
  - commits `main` holds skipped (the fast-forward, `main` pushed first, `dev` at the latest stable tag), even with a forced bump;
  - a dev head that holds older `main` commits still building.

  It also runs the branch policy from `build-release.yml`: `main` and `dev` pass; another branch, a lookalike branch name, a tag and a pull-request ref fail. Mutations confirmed the checks bite: disabling the ancestry skip, an off-by-one in `N`, and a policy admitting every branch each fail.
- **Workflows.** `actionlint -shellcheck=''` at version 1.7.12, the version CI uses, passes.
- **Go.** `internal/release` tests cover precedence: dev below stable, numeric build order, and `git describe` suffixes on both kinds of version. They also cover dev channel selection: stable catching up with `dev`, old dev releases, creation order differing from version order, and tags outside the scheme. `go vet ./...` and `go test ./...` pass on Linux.
- **Installers.** `tests/test_install_dev_channel.py` runs `install.sh`'s resolver (bash with a stub `curl`) and `install.ps1`'s selection against the same cases. Locally, `install.ps1` ran under Windows PowerShell 5.1 through WSL interop. CI runs these tests on every native target that has bash or PowerShell. `tests/test_updates.py` already checks that the dashboard runs `update --check --json` without `--dev`. The full Python suite passes on Linux.
- **Website.** Normalizing 40 prereleases newer than `v0.24.0` still gives `latestStable` `v0.24.0`, and so does the live API.
- **Not verified, needs a real dev push:**
  - the Actions run on `dev` and the published prerelease;
  - Windows and macOS packaging with a prerelease version on today's tooling;
  - cancelling a superseded dev build;
  - the `image.yml` branch filter and the Pages redeploy after a dev run;
  - `imprint update --dev` and the dashboard against a real prerelease.
