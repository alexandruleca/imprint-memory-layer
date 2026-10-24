---
title: Building and releases
description: Build the CLI, dashboard, desktop shell and native installers, with validation before publication.
---

## Local builds

```bash
make build                         # current platform CLI
make all                           # five Go targets
make dashboard-build               # fresh Next.js static export (Node 22+)
make chrome-extension-zip           # allowlisted manual-install Chrome ZIP + checksum
make ui-dist-linux ARCH=amd64       # native shell; arm64 is a separate build
make ui-dist-mac                    # universal macOS app, on macOS
make ui-dist-win                    # Windows x64 portable app
make package-local PLAT=linux/amd64 # stage + archive one platform
make package                       # stage + archive all prebuilt targets
make installer-macos ARCH=arm64     # native pkgbuild/productbuild
make installer-windows              # Inno Setup 6.3+ on Windows
```

Packaging requires Bash, rsync, Python 3, downloaded uv and the selected CLI binaries. It keeps `dist/imprint-<os>-<arch>/` for native installers. The static dashboard must be present; rebuild it before packaging. Native installers have host-specific tools; Go cross-compilation does not replace them.

Normalized Electron files live in `build/electron/linux-{amd64,arm64}/imprint-ui`, `build/electron/imprint-ui.app` and `build/electron/imprint-ui.exe`. Packaging never substitutes one Linux architecture for the other. Set `REQUIRE_ELECTRON=1` to fail when a desktop shell is missing; without it a local archive may provide the browser dashboard only.

`scripts/validate-release.py` checks runtime files, dashboard JavaScript, CLI/uv architecture and Linux AppImage architecture, and excludes development/user state. Runtime Python dependencies use the hashed locks: `base.lock.txt` plus exactly one ONNX Runtime overlay, `cpu.lock.txt` or `gpu.lock.txt`, and `llm.lock.txt` when selected ([ADR 0032](adr/0032-one-onnx-runtime-per-environment.md)). Refresh them deliberately; uv keeps the pins already present in each output file:

```bash
uv pip compile requirements/base.txt --universal --python-version 3.12 --generate-hashes -o requirements/base.lock.txt
uv pip compile requirements/cpu.txt -c requirements/base.lock.txt --universal --python-version 3.12 --generate-hashes -o requirements/cpu.lock.txt
uv pip compile requirements/gpu.txt -c requirements/base.lock.txt --universal --python-version 3.12 --generate-hashes -o requirements/gpu.lock.txt
uv pip compile requirements/llm.txt -c requirements/base.lock.txt --universal --python-version 3.12 --generate-hashes -o requirements/llm.lock.txt
```

Every pin in `gpu.lock.txt` needs `win_amd64` and manylinux (x86_64, aarch64) wheels. Some NVIDIA patch releases ship Linux only; exclude them in `gpu.txt` ([ADR 0038](adr/0038-gpu-runtime-ships-cuda-libraries.md)).

The base interpreter is managed Python 3.12. Base/GPU/LLM profile locks enforce hashes; GPU repair and local source-build paths still require separate reproducibility work. uv and Qdrant archives are verified against `requirements/runtime-manifest.json`; unknown versions require a manifest update or an explicitly managed binary. Release install/update verifies `SHA256SUMS`. Model downloads are separate. An installer with bundled uv still needs a network for first-run downloads; it is not an offline distribution.

The experimental [PersonaPlex adapter](personaplex-prototype.md) uses a separately provisioned model runtime. Its `requirements/personaplex-test.lock.txt` overlay is for development/CI only, not a runtime profile. Release staging excludes both test requirement files and the archive validator rejects them if present (ADR 0041).

The smaller [LFM audio experiment](lfm-audio-prototype.md) uses explicitly downloaded native CPU artifacts, with model/runner SHA256 digests in `runtime-manifest.json` (ADR 0042). It adds no Python profile dependencies, implicit model download or supported installer target. Keep its runtime cache and private speech exports outside release sources.

The [live Echo companion](live-voice.md) has a separate explicit source-build
command for Windows x64, macOS Intel/Apple Silicon (13+) and Linux x64. It shares
portable GGUF weights and verifies the patched native worker, without downloading
the historical Linux CLI on other operating systems. Windows builds need CMake
and Visual Studio C++ Build Tools; Macs need CMake and Xcode Command Line Tools.
The `Native voice` workflow runs real speech/transport/lifetime checks and native
Electron transparency/dashboard-toggle checks with disposable data and profiles.
macOS interactive TCC consent, signing/notarization and acoustic validation remain
separate from automated fake-mic/typed tests. Entitlement and microphone usage text
are included in the desktop build configuration. Developer caches are not bundled
into installers. See [ADR 0044](adr/0044-native-desktop-voice-platforms.md).

Intel macOS uses ONNX Runtime 1.23.2 through platform markers because newer releases no longer publish its CPython 3.12 wheel. Other CPU targets retain the current runtime. Windows silent installation skips interactive wizard pages and has a 15-minute CI deadline with process-tree cleanup and retained installer/bootstrap logs. Native package jobs and runtime checks have explicit timeouts; see [ADR 0012](adr/0012-native-package-runtime-and-silent-installs.md).

The Windows setup helper logs ordinary native stderr as progress and uses the global native exit code to detect failure; Windows PowerShell 5.1 must not turn bootstrap's stderr banner into a terminating error or shadow the updated exit code with a local variable. The helper decodes native output as UTF-8 and sets `NO_COLOR`, so `first-run.log` is plain UTF-8 text. It reopens the log for every line, so the installer wizard polls it with `LoadStringsFromLockedFile` (read/write sharing); a share-read poll makes an append fail and aborts setup. The Windows native CI job runs `scripts/test-windows-setup.ps1` against both successful stderr output and a real nonzero exit before runtime provisioning, with a concurrent poller using the wizard's sharing mode and checks for UTF-8 decoding and absent ANSI escapes.

## Release workflow

Stable (`release.yml`) selects versions from main and calls `build-release.yml`. Conventional commits retain the existing bump rules: `feat` minor, `fix`/`perf` patch, breaking changes major; documentation-only changes do not release. Automatic dev releases have been removed. Historical dev assets remain available. See [ADR 0014](adr/0014-main-only-release-packaging.md).

Analysis reads every commit since the last stable tag with Git's NUL record delimiter. A later fix or documentation commit cannot hide an earlier feature/breaking change. `scripts/test-release-versioning.py` exercises the actual workflow script against disposable Git histories, including precedence, a forced bump and the first release.

The reusable workflow builds the dashboard and platform shells, creates validated archives, builds native `.pkg`/`.exe` installers, provisions/exercises native runtimes, and only then publishes a release and `SHA256SUMS` covering all archives and installers. macOS app bundles travel between runners as tar archives to preserve symlinks. Website/container follow-up workflows run after a successful parent release. Native build failures leave no newly published release from this pipeline.

The same main-only workflow builds `imprint-chrome-extension.zip`, validates its
contents and loads that exact ZIP after extraction in disposable Chromium. This
job is required by publication, and the extension ZIP is required in the complete
asset set and covered by `SHA256SUMS`. Branch CI exercises packaging in temporary
test directories; it does not publish extension releases. The local packager also
writes a `.zip.sha256` sidecar. See [ADR 0030](adr/0030-chrome-extension-release-zip.md).

Use the **Build release artifacts** manual workflow on main for a build-only validation run. It does not publish. A branch-policy job rejects any other source ref before packaging starts. Native checks run on hosted Linux x64/ARM, macOS Intel/ARM and Windows x64 runners; local verification results and outstanding platform checks are in [platform validation](platform-validation.md).

Branch push and PR CI run Go checks, Python dependency/unit/runtime checks on five native targets, dashboard lint/production build/Playwright, Electron navigation/service/lifecycle tests and workflow linting. Electron packaging/archive-content checks belong to main's release workflow; container builds/smoke run only on main. Superseded compile/test runs are cancelled per branch. Signing/notarization and interactive GUI/tray checks remain separate release requirements; see the [roadmap](quality-roadmap.md).

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

## Website deployment setting

GitHub Pages must use **GitHub Actions** (`build_type=workflow`), matching `pages.yml`. The old branch-root/Jekyll mode ran a second failing `pages-build-deployment` job alongside successful Astro deployments. The repository setting was corrected on 2026-10-23; the existing custom domain was retained.

Desktop service ownership is defined in [ADR 0025](adr/0025-desktop-owned-background-service.md). `node --test ui-electron/*.test.js` exercises navigation, hidden spawning, splash ordering, hide/reopen, retry and explicit Quit. Native Python tests start the actual API with a lifetime pipe and verify shutdown. Windows CI additionally runs `scripts/test-windows-launcher.ps1` against a disposable ready installation; the fixture checks the direct GUI target and argument quoting without touching client profiles. Release ASAR checks require the startup assets and service module. These automated checks do not replace interactive console/tray/Dock validation.

WSL client registration is covered by Go profile-preservation fixtures and Python path/hook policy tests on the native CI matrix. The Windows setup regression also verifies replay of saved WSL selections. For a real interoperability check, run `scripts/test-wsl-interop.py --windows-cli <WSL-path-to-imprint.exe> --windows-python <Windows-venv-python-path>` with the base Python dependencies inside WSL. It creates disposable Claude/Codex homes and Windows data, exercises setup/idempotence, hooks, CLI root approval, actual MCP discovery/context and removal, then cleans its successful fixture. It reuses the supplied interpreter read-only; it never provisions or edits real client profiles. Hosted Windows CI does not imply that a WSL distribution is available. See [ADR 0026](adr/0026-windows-installer-wsl-client-bridge.md).

Uninstall cleanup is shared with `imprint disable`. Native CI runs configuration-preservation, retry-journal and foreign-runtime fixtures on every Go target; Windows also runs `scripts/test-windows-uninstall.ps1` with successful stderr, nonzero exit and missing CLI cases. Native Inno uninstall must complete cleanup before files are removed. The Windows release job also uninstalls its disposable real installation and verifies that a process running from its venv is stopped, the install folder is gone, and MCP/hook/rule cleanup ran. Process ownership and stopping are covered by Go tests on every target. Integration journals and their backups are private installation state and are rejected by release validation. See [ADR 0027](adr/0027-symmetric-uninstall-and-branch-sync.md) and [ADR 0028](adr/0028-complete-uninstall.md).

Publish changes to `dev` first, then fast-forward `main` to the same tested commit. `sync-dev.yml` also fast-forwards `dev` on future pushes to `main`, fetching the current branch heads rather than replaying a stale event commit. It never forces a push or discards dev-only commits; divergence fails and requires an explicit reconciliation. Branch protection must permit the workflow token's fast-forward for automatic synchronization. Release packaging remains main-only.

`scripts/test-wsl-uninstall.py --windows-cli <WSL-path-to-imprint.exe> --windows-python <Windows-venv-python-path>` copies the CLI and bridge to a private Windows tree, connects disposable WSL profiles, and exercises actual native disable/uninstall, failure retry, `--keep-data` and default data removal. It never disables the supplied installation or edits real client profiles.


## Bundled Echo speech engine

Release packaging requires native `voice-engine-*` artifacts from the reusable
`voice-engine.yml` workflow for Linux x64, Windows x64 and both Mac architectures.
Linux builds run in manylinux_2_28 and link the C++/GCC runtime statically. Model
weights are excluded; engine source, integrity metadata and notices are included.
Archive staging verifies target architecture, fingerprints and executability.
Linux ARM archives omit the unsupported speech engine. `REQUIRE_VOICE_ENGINE=1`
makes a missing supported-target bundle fatal, as set by release CI.

For a developer bundle, run the native Python with filelock installed:

```bash
python -m imprint.voice_bundle --build-root /tmp/imprint-engine-build --output build/voice/voice-engine-linux-x64 --jobs 4
```

Select the output target matching the current OS; this builder is native, not a
cross-compiler. It needs CMake and a C++ toolchain but downloads no speech weights.
To exercise release-style setup in a checkout, copy that target directory to
`imprint/voice-engine` (gitignored). `scripts/smoke-voice-desktop.py --bundled-setup`
uses a disposable cache, seeds only verified model weights and clears PATH to
prove managed setup does not discover developer tools. See
[ADR 0047](adr/0047-bundled-private-voice-engine.md).
