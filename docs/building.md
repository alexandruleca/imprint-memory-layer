---
title: Building and releases
description: Build the CLI, dashboard, desktop shell and native installers, with validation before publication.
---

## Local builds

```bash
make build                         # current platform CLI
make all                           # five Go targets
make dashboard-build               # fresh Next.js static export (Node 22+)
make ui-dist-linux ARCH=amd64       # native shell; arm64 is a separate build
make ui-dist-mac                    # universal macOS app, on macOS
make ui-dist-win                    # Windows x64 portable app
make package-local PLAT=linux/amd64 # stage + archive one platform
make package                       # stage + archive all prebuilt targets
make installer-macos ARCH=arm64     # native pkgbuild/productbuild
make installer-windows              # Inno Setup 6 on Windows
```

Packaging requires Bash, rsync, Python 3, downloaded uv and the selected CLI binaries. It keeps `dist/imprint-<os>-<arch>/` for native installers. The static dashboard must be present; rebuild it before packaging. Native installers have host-specific tools; Go cross-compilation does not replace them.

Normalized Electron files live in `build/electron/linux-{amd64,arm64}/imprint-ui`, `build/electron/imprint-ui.app` and `build/electron/imprint-ui.exe`. Packaging never substitutes one Linux architecture for the other. Set `REQUIRE_ELECTRON=1` to fail when a desktop shell is missing; without it a local archive may provide the browser dashboard only.

`scripts/validate-release.py` checks runtime files, dashboard JavaScript, CLI/uv architecture and Linux AppImage architecture, and excludes development/user state. Runtime Python dependencies use `requirements/base.lock.txt` when available. Refresh it deliberately:

```bash
uv pip compile requirements/base.txt --universal --python-version 3.12 --generate-hashes -o requirements/base.lock.txt
uv pip compile requirements/gpu.txt -c requirements/base.lock.txt --universal --python-version 3.12 --generate-hashes -o requirements/gpu.lock.txt
uv pip compile requirements/llm.txt -c requirements/base.lock.txt --universal --python-version 3.12 --generate-hashes -o requirements/llm.lock.txt
```

The base interpreter is managed Python 3.12. Base/GPU/LLM profile locks enforce hashes; GPU repair and local source-build paths still require separate reproducibility work. uv and Qdrant archives are verified against `requirements/runtime-manifest.json`; unknown versions require a manifest update or an explicitly managed binary. Release install/update verifies `SHA256SUMS`. Model downloads are separate. An installer with bundled uv still needs a network for first-run downloads; it is not an offline distribution.

Intel macOS uses ONNX Runtime 1.23.2 through platform markers because newer releases no longer publish its CPython 3.12 wheel. Other CPU targets retain the current runtime. Windows silent installation skips interactive wizard pages and has a 15-minute CI deadline with process-tree cleanup and retained installer/bootstrap logs. Native package jobs and runtime checks have explicit timeouts; see [ADR 0012](adr/0012-native-package-runtime-and-silent-installs.md).

The Windows setup helper logs ordinary native stderr as progress and uses the global native exit code to detect failure; Windows PowerShell 5.1 must not turn bootstrap's stderr banner into a terminating error or shadow the updated exit code with a local variable. The Windows native CI job runs `scripts/test-windows-setup.ps1` against both successful stderr output and a real nonzero exit before runtime provisioning.

## Release workflow

Stable (`release.yml`) selects versions from main and calls `build-release.yml`. Conventional commits retain the existing bump rules: `feat` minor, `fix`/`perf` patch, breaking changes major; documentation-only changes do not release. Automatic dev releases have been removed. Historical dev assets remain available. See [ADR 0014](adr/0014-main-only-release-packaging.md).

The reusable workflow builds the dashboard and platform shells, creates validated archives, builds native `.pkg`/`.exe` installers, provisions/exercises native runtimes, and only then publishes a release and `SHA256SUMS` covering all archives and installers. macOS app bundles travel between runners as tar archives to preserve symlinks. Website/container follow-up workflows run after a successful parent release. Native build failures leave no newly published release from this pipeline.

Use the **Build release artifacts** manual workflow on main for a build-only validation run. It does not publish. A branch-policy job rejects any other source ref before packaging starts. Native checks run on hosted Linux x64/ARM, macOS Intel/ARM and Windows x64 runners; local verification results and outstanding platform checks are in [platform validation](platform-validation.md).

Branch push and PR CI run Go checks, Python dependency/unit/runtime checks on five native targets, dashboard lint/production build/Playwright, Electron navigation tests and workflow linting. Electron packaging/archive-content checks belong to main's release workflow; container builds/smoke run only on main. Superseded compile/test runs are cancelled per branch. Signing/notarization and interactive GUI/tray checks remain separate release requirements; see the [roadmap](quality-roadmap.md).

CI jobs have explicit deadlines (20 minutes for native/container, 15 for frontend and 10 for workflow lint). On hosted Ubuntu, Playwright downloads Chromium with a five-minute deadline and uses the runner's existing runtime libraries; it does not upgrade unrelated OS/font packages through apt. Browser tests still launch Chromium and fail if required runtime libraries are missing.

The separate [nightly Trivy security scan](security-scanning.md) checks source, dependency locks, configuration and secrets daily at 01:23 UTC, publishes reports and fails on high/critical findings. It also has a manual trigger.

Version injection uses `main.version`; override with `VERSION=vX.Y.Z`. Avoid committing local regenerated binary/static-export output as an incidental change.

## Docker relay

```bash
docker build -t imprint-relay .
docker run -p 8430:8430 imprint-relay
```

The relay Dockerfile accepts `--build-arg VERSION=vX.Y.Z`. See [Docker](docker.md) and `docker-compose.relay.yml` for deployment configuration.

## Desktop compatibility

Electron is pinned to 45.0.0. The macOS desktop app/pkg now requires macOS 13, recorded in [ADR 0007](adr/0007-verified-runtime-and-desktop-baseline.md). This is an explicit compatibility change from the old installer metadata. Native signing/notarization and OS execution remain required before declaring a release validated.

## Full runtime container

`docker build -f Dockerfile.imprint -t imprint-local .` builds the CLI, fresh dashboard and a Python 3.12 venv from the hashed CPU lock. `.dockerignore` excludes local state and build caches. Runtime requirements include the checksum manifest; Go/Python share `IMPRINT_DATA_DIR`. Publish `-p 127.0.0.1:8420:8420`, mount `/data`, and use `docker exec <container> imprint doctor --json --probe-mcp` to diagnose it. Container ARM and GPU execution remain separate validation targets.
