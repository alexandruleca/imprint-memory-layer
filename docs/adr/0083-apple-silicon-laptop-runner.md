---
title: ADR 0083 — Apple silicon laptop runner
description: Route trusted Apple silicon jobs to the developer laptop while retaining hosted PR and Intel checks.
---

- Status: Amended
- Date: 2026-10-26
- Scope: Repository Apple silicon workflows and the Outland laptop
- Amended by: [ADR 0106](0106-intel-mac-laptop-runner.md) — Intel jobs move to the owner's Intel laptop

## Context

The owner requested a runner on this Apple silicon laptop to reduce hosted macOS
costs. CI, native voice, bundled voice engines and release packaging currently use
`macos-latest`. The ARM64 package smoke installs into the real Applications folder,
which is unsuitable for the owner's development laptop.

## Decision

Register one repository runner outside the checkout, with the custom label
`imprint-apple-silicon`, and run it as a login LaunchAgent. Route trusted push and
manual Apple silicon jobs to its self-hosted/macOS/ARM64 labels. Keep all PR jobs
on hosted macOS and keep Intel hosted. On this laptop, validate the expanded pkg
payload instead of installing it. Full installation remains exercised on hosted
Intel macOS, with ARM64 installation requiring a disposable native machine.

## Rejected alternatives

Continuing all Apple silicon work on hosted macOS does not address the owner's
cost request. Installing release packages on this laptop would overwrite the
owner's application and potentially alter real user state, so it is excluded.

## Consequences

One job runs at a time and jobs wait while the laptop sleeps or is disconnected.
The service starts on login rather than before login. The public repository's
runner has the owner's local privileges; PR routing is not an isolation boundary
against modified workflow files. Untrusted workflow runs must not be approved.
Runner credentials remain outside git. ARM64 package installation is no longer
automatically tested by the laptop job.

## Invalidation conditions

If trusted workloads outgrow one laptop or require continuous availability, move
the labels to a dedicated native Mac. If untrusted contributions need access to
self-hosted execution, replace this arrangement with isolated disposable runners.

## Validation

Registered the official checksum-verified macOS ARM64 runner on macOS 26.6.2 and
started its LaunchAgent. Workflow lint, ADR checks and job execution evidence are
recorded in the change report. No native ARM64 installer was installed on the laptop.
