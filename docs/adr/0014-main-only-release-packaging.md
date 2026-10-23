---
title: ADR 0014 — Main-only release packaging
description: Separate branch compile/test CI from main release artifacts and publication.
---

- Status: Accepted
- Date: 2026-10-23
- Related: ADR 0002, ADR 0012

## Context

Development pushes and manual diagnostic runs were building full installers. These expensive runs compete for native runners and duplicate packaging before changes reach main. The owner requested compile/test checks on other branches and release artifacts only from main.

## Decision

Run compile, unit, dashboard lint/build and browser/runtime tests on branch pushes and pull requests. Cancel superseded CI runs on the same branch. Do not package Electron shells or build container images in branch CI. Keep the container smoke on main and move Electron archive-content checks into release packaging.

Remove automatic dev releases. Require the main ref before any reusable release build begins, including manual dispatch. Guard stable analysis and container publication as well; image source tags must be ancestors of main. Keep native installer/runtime gates before publication on main.

## Alternatives

Packaging every branch gives earlier installer feedback but consumed substantial runner time during this incident. Building only on tags would change the existing automatic versioning/publication process. Main-only packaging preserves that process while making branch validation smaller.

## Consequences

Installer-specific defects are caught on main before publication. Feature branches receive native compile/test evidence but no downloadable release payloads. Historical dev releases remain available; no new branch prereleases are produced. A manual packaging request on another branch fails the policy check without building artifacts.

## Validation

Workflow lint validates job dependencies and conditions. Main packaging remains subject to native installer checks; record their executed results in platform validation. Branch CI must pass without Electron installer packaging or container jobs.
