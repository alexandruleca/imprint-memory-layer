---
title: ADR 0085 — Main only fast-forwards to dev
description: Every change reaches main by fast-forwarding main to a dev commit, so the workflow that fast-forwarded dev after pushes to main is removed.
---

- Status: Accepted
- Date: 2026-10-27
- Amends: [ADR 0027](0027-symmetric-uninstall-and-branch-sync.md) — no workflow fast-forwards dev after main changes
- Related: [ADR 0066](0066-dev-prereleases-from-the-dev-branch.md)

## Context

ADR 0027 added `sync-dev.yml`: after every push to `main` it fast-forwards `dev` to `main`. The release process (ADR 0066) already moves the branches the other way. Pull requests merge into `dev`, as #33, #34 and #35 did on 2026-10-26, and `main` then fast-forwards to the tested `dev` commit. After such a push `dev` already is `main`, so the workflow's push to `dev` changes nothing.

The workflow changes `dev` only when `main` takes a commit that skipped `dev`, as #31 (`feat/echo-desktop-refinement` into `main`, 2026-10-24) did. On 2026-10-27 a workflow-only pull request was opened against `main` and the sync came up while checking what its merge would trigger; the owner asked to remove it.

ADR 0066 names this removal in its invalidation conditions: if `dev` stops receiving every commit `main` releases, its rules that a commit `main` holds is `main`'s to release, and that the highest version is `dev`'s newest build, must be derived again.

## Decision

`sync-dev.yml`, `scripts/sync-dev.sh` and its test are removed. Every change reaches `main` by fast-forwarding `main` to a `dev` commit, and pull requests target `dev`.

ADR 0066's rules, derived again: every commit `main` holds came from `dev`, so `dev` still receives every commit `main` releases. A `dev` run that finds its commit already on `main` still leaves the release to `main`, and `dev`'s newest build is still the highest version, because `main` releases nothing `dev` has not built.

## Rejected alternatives

- **Keep the workflow as a safety net.** It runs only to repair a commit that reached `main` without passing through `dev`, which the process rules out. When it does run, it moves `dev` onto that untested commit, so the next `dev` build starts from it unnoticed; without it, the next fast-forward of `main` fails and shows the divergence.

## Consequences

- A push to `main` starts one workflow fewer.
- A pull request merged into `main` directly leaves `dev` behind. The next fast-forward of `main` to `dev` fails, and `dev` must take `main`'s commit first.
- ADR 0027's other branch rules stay: no force pushes, and divergence is reconciled explicitly.

## Invalidation conditions

- If changes must land on `main` without passing through `dev`, for example urgent fixes, restore an automatic fast-forward of `dev` or make merging `main` into `dev` part of that process.

## Validation

- Pull request history (`gh pr list --state merged`): #33, #34 and #35 merged into `dev`; #31 is the most recent merge into `main` that skipped `dev`.
- `scripts/sync-dev.sh` as removed: it pushes `origin/main` to `dev` after checking that `dev` has no commits outside `main`.
- `tests/test_adr_records.py` passes with this record indexed.
