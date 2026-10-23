---
title: ADR 0013 — Remediate security scan dependencies
description: Upgrade the website, remove vulnerable build dependencies, harden the relay and constrain optional LLM caching.
---

- Status: Accepted
- Date: 2026-10-23
- Related: ADR 0007, ADR 0008, ADR 0010, ADR 0011

## Context

The initial Trivy scan reports 60 dependency findings plus two relay Dockerfile findings. Most are in the website's older Astro/Vite/image pipeline. The dashboard's generator and Next lint preset introduce unfixed braces through glob tooling. The optional llama-cpp-python binding requires diskcache 5.6.3, whose pickle-based disk cache has no upstream patched release.

## Decision

Upgrade Astro, Starlight, MDX and Sharp together to compatible patched versions, regenerate the website lock and use Node 24 LTS for Pages. Remove the shadcn generator from installed dashboard dependencies; preserve its required static CSS as an attributed MIT asset. Replace the Next lint preset with explicit JavaScript/TypeScript, React Hooks/compiler, React and the existing accessibility checks. Keep production Next/React versions and generated components. Framework-specific lint rules from the removed preset are no longer included; TypeScript compilation, lint, production build and browser interaction checks remain required.

Run the relay container as an unprivileged user and add a health probe against its existing `/health` route. Validate non-root execution and HTTP health in hosted CI.

Explicitly disable llama.cpp state caching in the shared helper. Keep the unfixed diskcache advisory visible in Trivy rather than suppressing it or claiming an upstream fix. Imprint never creates a disk-backed llama cache; maintainers must revisit this constraint before adding one. The optional dependency remains installed through the binding. No ignore or VEX exception is introduced.

## Alternatives

Downgrading the Next lint preset to satisfy an automated audit fix changes framework policy and still avoids the actual dependency issue. Blanket overrides of glob libraries can silently change matching behavior. A private fork of diskcache or the native binding adds a maintenance/distribution burden for an unused code path. Hiding the medium advisory would make future tracking less reliable.

## Consequences

The website moves to Astro 7's supported integration versions. The small vendored stylesheet must retain its license and be reviewed if upstream component styles change. Dashboard lint dependencies are explicit and no longer pull the vulnerable glob chain. Optional native inference still carries one visible unfixed transitive advisory, with disk caching disabled. The high/critical security gate stays unchanged.

## Validation

Local checks include the website production build, dashboard lint/build and four Playwright scenarios, 58 Python tests, Go tests and a fresh Trivy scan. Trivy reports no high/critical findings and no Docker misconfiguration findings; only the known medium diskcache advisory remains. Hosted CI and native installer results are recorded separately in platform validation.
