---
title: ADR 0012 — Native package runtime and silent installs
description: Restore Intel macOS wheel compatibility and keep Windows silent installs out of interactive wizard flow.
---

- Status: Accepted
- Date: 2026-10-23
- Related: ADR 0002, ADR 0007

## Context

Release run 39910991520 installed the Intel macOS package successfully, then failed provisioning because ONNX Runtime 1.31.0 has no Intel macOS wheel. Apple Silicon and Linux smoke jobs passed. Windows compiled its installer, then remained inside silent installation for over an hour with no diagnostic stream. Cancelling revealed only Inno installer processes still alive. The installer created interactive custom pages during silent execution and the CI process wait was unbounded.

## Decision

Retain current ONNX Runtime on other targets and pin 1.23.2, the last published CPython 3.12 Intel macOS wheel, using platform markers in the hashed base lock. Regenerate constrained optional locks. Keep macOS 13 as the supported desktop floor. Native installer smoke tests, including real model inference, remain required.

Skip custom wizard creation/navigation in silent mode; perform silent provisioning exclusively through the existing [Run] setup helper. Bound Windows installer execution at 15 minutes, terminate its process tree on timeout, and retain installer/bootstrap logs. Bound native package jobs and runtime steps. Reuse the bundled Electron executable's icon through Windows APIs rather than installing ImageMagick just for icon conversion.

## Alternatives

Dropping Intel macOS would avoid the older runtime but changes supported hardware unnecessarily. Disabling hash enforcement or dynamically falling back to an arbitrary wheel obscures the actual compatibility contract. Merely increasing Windows timeout would retain the hidden-wizard hang. Publishing despite failed native installation is not accepted.

## Consequences

Intel macOS uses an older runtime line and requires separate compatibility/security review. Upstream wheel availability must be checked when changing that pin. Silent installs use deterministic CPU/no-LLM defaults; interactive profile pages remain available. Timeouts expose slow or failed provisioning and preserve evidence rather than consuming an unbounded runner.

## Validation

Source evidence comes from completed native job logs. PyPI wheel inventory confirms the CPython 3.12 macOS x86_64 1.23.2 artifact. Workflow syntax is checked with actionlint. Replacement native build/install/runtime checks are required before declaring the fixes validated; results will be recorded in platform validation.
