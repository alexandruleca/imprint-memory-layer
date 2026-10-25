---
title: ADR 0014 — Main-only release packaging
description: Separate branch compile/test CI from main release artifacts and publication.
---

- Status: Amended
- Date: 2026-10-23
- Amends: [ADR 0002](0002-build-and-validate-before-publication.md) — removes automatic dev releases and requires main for every release build, manual dispatch included
- Amended by: [ADR 0066](0066-dev-prereleases-from-the-dev-branch.md) — dev pushes publish prereleases through the same pipeline again; release builds require main or dev
- Related: [ADR 0012](0012-native-package-runtime-and-silent-installs.md)
- Restructured: 2026-10-25

## Context

Development pushes and manual diagnostic runs were building full installers. These expensive runs compete for native runners and duplicate packaging before changes reach main. The owner requested compile/test checks on other branches and release artifacts only from main.

## Decision

Run compile, unit, dashboard lint/build and browser/runtime tests on branch pushes and pull requests. Cancel superseded CI runs on the same branch. Do not package Electron shells or build container images in branch CI. Keep the container smoke on main and move Electron archive-content checks into release packaging.

Remove automatic dev releases. Require the main ref before any reusable release build begins, including manual dispatch. Guard stable analysis and container publication as well; image source tags must be ancestors of main. Keep native installer/runtime gates before publication on main.

## Rejected alternatives

Packaging every branch gives earlier installer feedback but consumed substantial runner time during this incident. Building only on tags would change the existing automatic versioning/publication process. Main-only packaging preserves that process while making branch validation smaller.

## Consequences

Installer-specific defects are caught on main before publication. Feature branches receive native compile/test evidence but no downloadable release payloads. Historical dev releases remain available; no new branch prereleases are produced. A manual packaging request on another branch fails the policy check without building artifacts.

## Invalidation conditions

- If native runner capacity stops being the constraint, for example because dedicated runners make packaging cheap, revisit packaging on branches for earlier installer feedback.
- If releases move to tags instead of main, which was rejected because it changes automatic versioning and publication, the main-ref guard must be redesigned.

## Validation

Workflow lint validates job dependencies and conditions. Main packaging remains subject to native installer checks; record their executed results in platform validation. Branch CI must pass without Electron installer packaging or container jobs.
