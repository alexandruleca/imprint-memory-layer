---
title: ADR 0119 — Throwaway runners carry CI's dependencies, and the dashboard is built once
description: The throwaway Linux runners start from an image with the locked Python dependencies and Go installed, and CI builds the dashboard export in one job for every platform.
---

- Status: Accepted
- Date: 2026-10-30
- Scope: `ci.yml` and the throwaway runners on bedrock
- Amends: [ADR 0112](0112-ci-runs-on-the-owners-machines.md) — the throwaway runners use a locally built image instead of the upstream runner image
- Related: [docs/ci-runners.md](../ci-runners.md)

## Context

The emulated arm64 job was CI's slowest, at 40 minutes, and Release waits for CI. In run 41047899084 it spent 13.5 minutes installing the same locked wheels (5 downloading, 8 unpacking under qemu), 4 minutes setting up Python and Go, and 5 building the dashboard export. The throwaway runner is a fresh container per job, so nothing carries over. The job cannot use a `container:` built on the imprint base image instead, because these runners get no Docker socket: they run pull requests' code.

## Decision

- `.github/runners/Dockerfile.ci-runner` extends the pinned runner image with GitHub's Python 3.12 build and Go from `go.mod` in the tool cache, and a venv of that Python at `/opt/imprint-ci` holding the `base`, `cpu` and `llm` locks. `build-ci-runner.sh` builds it for amd64 and arm64 on the host; `install-host.sh` runs it, and `jit-runners.sh` falls back to the upstream image when it has not been built.
- `ci.yml`'s Linux native jobs use the venv when the runner has it, skipping setup-python; their pip install still runs and adds only what the locks changed since the image was built. A stale image is slower, never wrong.
- A `dashboard` job builds the static export once; every native job downloads it for the runtime smoke.

## Rejected alternatives

- **Ubuntu's `python3.12` for the venv.** The first image used it: under arm64 emulation the test step took 36 to 40 minutes instead of 9, and a plain Python benchmark ran 11 times slower than GitHub's build of the same version.

- **Running the jobs in the imprint base image.** It needs a Docker socket on the throwaway runners, which would hand pull requests root on the host.
- **Caching downloaded wheels.** It saves the 5 download minutes but not the 8 spent unpacking.

## Consequences

- The arm64 job should take about 20 minutes instead of 40 on a warm image, and the Release gate as much less.
- Native jobs start after the dashboard job, a couple of minutes on x64.
- The Linux native job no longer installs into a bare Python; the release's `smoke-linux` jobs still install the real package on a clean runner.
- After a lock or Go change, rerun `build-ci-runner.sh` to get the time back.

## Invalidation conditions

- If GitHub-hosted arm64 runners replace the emulated ones, then the image is unnecessary.

## Validation

- The first CI run on the new image, compared against run 41047899084's step timings.
