---
title: ADR 0108 — Installations opt in to dev builds in Settings
description: The update.channel setting chooses the releases an installation is offered, stable or dev; imprint update resolves it unless --stable or --dev is passed, so the dashboard and the terminal follow the same channel.
---

- Status: Accepted
- Date: 2026-10-28
- Amends: [ADR 0066](0066-dev-prereleases-from-the-dev-branch.md) — the dashboard offers dev builds to installations that opt in
- Related: [ADR 0023](0023-update-by-installation-type.md), [installation](../installation.md#updating-an-existing-install), [configuration](../configuration.md)

## Context

[ADR 0066](0066-dev-prereleases-from-the-dev-branch.md) publishes dev prereleases again, but only `imprint update --dev` and the install scripts' dev flags reach them. The dashboard's update notice runs `imprint update --check --json` without a flag and therefore never offers a prerelease. On 2026-10-28 the owner asked for "a channel for updates for dev updates in the update flow that users can opt in to at settings".

Three facts shape where the choice lives:

- The dashboard's Settings page is generated from `imprint/config_schema.py`: each setting appears under the group named by its key's prefix, with its choices. Precedence there is environment variable, then `config.json`, then the default.
- The CLI is the single implementation of release resolution ([ADR 0023](0023-update-by-installation-type.md)). The dashboard asks it for a report and starts it to update.
- On an installation running a dev build, `imprint update` without a flag installs the latest stable release after confirmation, even when it is older ([ADR 0066](0066-dev-prereleases-from-the-dev-branch.md), Consequences).

## Decision

**The setting.** `update.channel` (`IMPRINT_UPDATE_CHANNEL`) takes `stable`, the default, or `dev`. It appears in **Settings → Updates**.

**The CLI resolves it.** Without `--version`, `imprint update` takes the channel from `--stable` or `--dev` when one is passed, and otherwise from the setting. The setting is read through `config_schema.resolve` in the installation's Python, so `imprint update` sees exactly what `imprint config get update.channel` shows. A setting that cannot be read, or holds an unknown value, stops the command with a message naming the flags; it never falls back to a channel silently. `--check --json` reports the `channel` it used and whether the release is a `prerelease`.

**The dashboard passes no channel.** It still runs `imprint update --check --json` and `imprint update -y`. Its cached report is valid only for the channel currently set, so changing the setting checks again at once instead of after the six-hour cache. The update dialog marks a prerelease as a dev build.

The channels themselves are unchanged: stable is `/releases/latest`, dev the highest version among the 30 newest releases.

## Rejected alternatives

- **The dashboard passing `--dev` from its own reading of the setting, with the CLI defaulting to stable.** An opted-in installation would then be offered dev builds in the dashboard while `imprint update` in a terminal offered the latest stable release, older than the dev build installed, and installed it after confirmation. The two update paths would disagree about the same installation.
- **The CLI parsing `config.json` and the environment variable itself.** That is a second implementation of the precedence `config_schema` owns, and of its value parsing; the two could drift.
- **A state file of the CLI's own, like the install profile in `data/profile.json`.** The Settings page is generated from `config_schema`; a file outside it would need its own API and UI.
- **Clearing the cached report when the configuration endpoint saves this key.** It ties the generic configuration endpoint to one setting. Keying the cache by channel needs no event and also covers a change made with `imprint config set` or the environment.

## Consequences

- An installation set to dev is offered each dev build in the dashboard as it is published, and stable releases once they are newer.
- Switching back to stable offers nothing until a stable release is newer than the build installed. `imprint update --stable` still installs the latest stable release after confirmation, even when it is older.
- `imprint update` without a flag starts the installation's Python once to read the setting. When that Python cannot run, the command fails and asks for `--stable` or `--dev`, which skip the setting.
- Installing with `install.sh --dev`, `install.ps1 -Dev` or `IMPRINT_CHANNEL=dev` does not set the channel: the installation follows stable until the setting is changed.
- Installations on a release before this one ignore the setting, because their CLI does not read it. It takes effect after the next update.

## Invalidation conditions

- If `imprint update` must work without the installation's Python and without a flag, for example to repair a broken environment, the channel must move to a store the CLI reads itself, and the Settings page must reach it through the API.
- If another channel is published, the setting's choices and the CLI's accepted values must change together.
- If the dashboard stops asking the CLI for its update report ([ADR 0023](0023-update-by-installation-type.md)), the channel resolution and the cache key move with it.

## Validation

- **Go:** `cmd/update_test.go` covers the flags (none, `--dev`, `--stable`, `--version` with `--dev` refused) and the channel choice: a flag over the setting in both directions, a flag needing no readable setting, an unreadable setting failing with the flags named, and an unknown value refused. `go vet ./cmd/` and `go build ./...` pass.
- **Live:** a CLI built from this change, in an install tree whose Python imports the repository's `config_schema`, reported against the live releases API: `channel` stable by default, dev from `IMPRINT_UPDATE_CHANNEL=dev`, dev from `config.json`, stable with `--stable` over a dev setting, and refused `nightly`. Without a Python it failed naming the flags. The dev channel's latest was then the stable `v0.32.1`, as ADR 0066 expects when no prerelease is newer.
- **Python:** `tests/test_updates.py` checks that switching the setting to dev checks again and caches the dev report, and that the dashboard still runs `update --check --json` without a channel.
- **Browser:** `imprint/ui/tests/update.spec.ts` checks that a prerelease is marked as a dev build and a stable release is not, and the existing update and settings specs pass. They ran in `mcr.microsoft.com/playwright:v1.64.1-noble`: Chromium lacks system libraries in the WSL used.
- **Not verified:** an update from one dev build to the next on a real installation of each type.
