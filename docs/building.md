---
title: Building and releases
description: Build the CLI, dashboard, desktop shell and native installers, with validation before publication.
---

## Local builds

```bash
make build                         # current platform CLI
make all                           # four Go targets (no Intel macOS, ADR 0111)
make dashboard-build               # Next.js static export into imprint/ui/out (Node 22+; not tracked, CI and releases build it)
make chrome-extension-zip           # allowlisted manual-install Chrome ZIP + checksum
make ui-dist-linux ARCH=amd64       # native shell; arm64 is a separate build
make ui-dist-mac                    # Apple silicon macOS app, on macOS
make ui-dist-win                    # Windows x64 portable app
make package-local PLAT=linux/amd64 # stage + archive one platform
make package                       # stage + archive all prebuilt targets
make installer-macos ARCH=arm64     # native pkgbuild/productbuild
make installer-windows              # Inno Setup 6.3+ on Windows
```

Packaging requires Bash, rsync, Python 3, downloaded uv and the selected CLI binaries. It keeps `dist/imprint-<os>-<arch>/` for native installers. The static dashboard must be present; rebuild it before packaging. Native installers have host-specific tools; Go cross-compilation does not replace them.

Normalized Electron files live in `build/electron/linux-{amd64,arm64}/imprint-ui`, `build/electron/imprint-ui.app` and `build/electron/imprint-ui.exe`. Packaging never substitutes one Linux architecture for the other. Set `REQUIRE_ELECTRON=1` to fail when a desktop shell is missing; without it a local archive may provide the browser dashboard only.

`scripts/validate-release.py` checks runtime files, dashboard JavaScript, CLI/uv architecture and Linux AppImage architecture, and excludes development/user state. Runtime Python dependencies use the hashed locks: `base.lock.txt`, `cpu.lock.txt` (ONNX Runtime's CPU build, the only ONNX Runtime) and one llama.cpp build, `llm.lock.txt` or its GPU build `llm-gpu.lock.txt` ([ADR 0032](adr/0032-one-onnx-runtime-per-environment.md), [ADR 0049](adr/0049-llama-gpu-offload-through-vulkan-wheels.md), [ADR 0086](adr/0086-embeddings-run-on-llama-cpp-without-cuda.md)). Refresh them deliberately; uv keeps the pins already present in each output file:

```bash
uv pip compile requirements/base.txt --universal --python-version 3.12 --generate-hashes -o requirements/base.lock.txt
uv pip compile requirements/cpu.txt -c requirements/base.lock.txt --universal --python-version 3.12 --generate-hashes -o requirements/cpu.lock.txt
uv pip compile requirements/llm.txt -c requirements/base.lock.txt --universal --python-version 3.12 --generate-hashes -o requirements/llm.lock.txt
uv pip compile requirements/llm-gpu.txt -c requirements/base.lock.txt --universal --python-version 3.12 --generate-hashes -o requirements/llm-gpu.lock.txt
```

The base interpreter is managed Python 3.12. Base/CPU/LLM locks enforce hashes; local source-build paths (platforms without a prebuilt llama.cpp wheel) still require separate reproducibility work and a C/C++ toolchain at install time. macOS installs the wheels the `Build llama.cpp wheels for macOS` workflow builds from the locked source and publishes as the prerelease `llama-cpp-python-v<version>` ([ADR 0090](adr/0090-macos-installs-llama-cpp-wheels-built-here.md)). To change the binding's version, update `llm.txt` and `llm-gpu.txt`, lock the new source, run that workflow with the version, then pin its wheels and lock again. uv and Qdrant archives are verified against `requirements/runtime-manifest.json`; unknown versions require a manifest update or an explicitly managed binary. Release install/update verifies `SHA256SUMS`. Model downloads are separate. An installer with bundled uv still needs a network for first-run downloads; it is not an offline distribution.

The experimental [PersonaPlex adapter](personaplex-prototype.md) uses a separately provisioned model runtime. Its `requirements/personaplex-test.lock.txt` overlay is for development/CI only, not a runtime profile. Release staging excludes both test requirement files and the archive validator rejects them if present (ADR 0041).

The smaller [LFM audio experiment](lfm-audio-prototype.md) uses explicitly downloaded native CPU artifacts, with model/runner SHA256 digests in `runtime-manifest.json` (ADR 0042). It adds no Python profile dependencies, implicit model download or supported installer target. Keep its runtime cache and private speech exports outside release sources.

The [live Echo companion](live-voice.md) has a separate explicit source-build
command for Windows x64, macOS on Apple silicon (13+) and Linux x64. It shares
portable GGUF weights and verifies the patched native worker, without downloading
the historical Linux CLI on other operating systems. Windows builds need CMake
and Visual Studio C++ Build Tools; Macs need CMake and Xcode Command Line Tools.
The `Native voice` workflow runs real speech/transport/lifetime checks and native
Electron transparency/dashboard-toggle checks with disposable data and profiles.
macOS interactive TCC consent, Developer ID signing/notarization and acoustic
validation remain separate from automated fake-mic/typed tests. The smokes use
synthetic devices (`--use-fake-device-for-media-stream`) but never the fake
permission prompt, so the production permission handlers and OS consent check
run; on macOS they skip capture, because TCC is a person answering a dialog.
`node ui-electron/smoke-microphone.mjs` checks those handlers in real Electron
with a disposable profile: Voice and companion main frames capture audio, while
other pages, subframes, camera and mixed audio/video are refused, and the consent
IPC answers only voice pages
([ADR 0069](adr/0069-echo-opens-the-microphone-only-on-the-users-intent.md)).
Developer caches are not bundled into installers. See
[ADR 0044](adr/0044-native-desktop-voice-platforms.md).
`node ui-electron/smoke-companion-controls.mjs [dashboard-out] [screenshot-dir]`
runs real Electron against the built dashboard and a disposable fixture API, with
no voice model download: it opens Companion settings, picks the reasoning model
in the native select with the keyboard, picks a look, then hides the dashboard
and drags the docked launcher and head, checking they move together
([ADR 0070](adr/0070-companion-moves-with-the-launcher-and-keeps-options-in-settings.md)).
Except on macOS, it also enables the synthetic microphone, stops, and checks that
the next conversation turns it on again without another click.
The native voice workflow runs both smokes on every target before the real-model smoke.

electron-builder signs the macOS desktop app ad hoc as a bundle (`mac.identity: "-"`,
no hardened runtime), sealing its `Info.plist` with the microphone usage text.
macOS privacy consent reads that text through the signature. An unsigned bundle,
like the one v0.22.1 shipped, crashes on **Enable microphone**. `make package` runs
`scripts/validate-release.py`, which rejects a darwin tree whose `imprint-ui.app`
or helper apps are not signed that way, or whose app declares no
`NSMicrophoneUsageDescription`. The check runs on Linux, so it does not
replace `codesign --verify` on a Mac. See
[ADR 0055](adr/0055-ad-hoc-signed-macos-desktop-bundle.md).
The `.pkg` enclosing `Imprint.app` also declares microphone usage: verifying only
the nested Electron signature missed a TCC crash in an installed v0.25.0 bundle.
See [ADR 0075](adr/0075-microphone-purpose-in-the-macos-installer-wrapper.md);
interactive consent with the corrected installer still needs validation.

Intel Macs are not supported ([ADR 0111](adr/0111-intel-macs-are-not-supported.md)): no release target, package, wheel or dependency pin exists for darwin/amd64, and every target installs the current ONNX Runtime. Windows silent installation skips interactive wizard pages and has a 60-minute CI deadline with process-tree cleanup and retained installer/bootstrap logs. Native package jobs and runtime checks have explicit timeouts; see [ADR 0012](adr/0012-native-package-runtime-and-silent-installs.md).

The Windows setup helper logs ordinary native stderr as progress and uses the global native exit code to detect failure; Windows PowerShell 5.1 must not turn bootstrap's stderr banner into a terminating error or shadow the updated exit code with a local variable. The helper decodes native output as UTF-8 and sets `NO_COLOR`, so `first-run.log` is plain UTF-8 text. Each line also goes to the console, so the Start menu's **Repair Imprint** shortcut shows setup's progress in its window. The helper reopens the log for every line, so the installer wizard polls it with `LoadStringsFromLockedFile` (read/write sharing); a share-read poll makes an append fail and aborts setup. The Windows native CI job runs `scripts/test-windows-setup.ps1` against both successful stderr output and a real nonzero exit before runtime provisioning, with a concurrent poller using the wizard's sharing mode and checks for UTF-8 decoding and absent ANSI escapes.

The bundled installer is **uv 0.13.6**, pinned in the Makefile, both fetchers and
`requirements/runtime-manifest.json`. Verify fresh bootstrap with this binary;
uv 0.5.11 can accept an already-populated environment while rejecting the same
conditional hashed audio/ONNX requirements in an empty one. Hash enforcement and
platform-specific package pins remain required. See
[ADR 0104](adr/0104-bundle-uv-with-conditional-hash-lock-support.md).

## Release workflow

`release.yml` runs on pushes to `main` and `dev`. It selects a version and calls `build-release.yml`, the same build for both branches. Conventional commits retain the existing bump rules: `feat` minor, `fix`/`perf` patch, breaking changes major; documentation-only changes do not release. A manual run can force a bump.

- **main** publishes the stable release `vX.Y.Z`.
- **dev** publishes the prerelease `vX.Y.Z-dev.N`: the version main would release from the same commits, numbered from 1 for each new `vX.Y.Z`. A dev head that main already holds is not built again. That covers the fast-forward after a main release and main pushed first. A newer dev push cancels a dev build in progress; a stable release always finishes. See [ADR 0066](adr/0066-dev-prereleases-from-the-dev-branch.md), which amends [ADR 0014](adr/0014-main-only-release-packaging.md).

Pushing one commit to `dev` and then straight away to `main` builds it twice. Push `main` once the dev prerelease has been tried.

Analysis reads every commit since the last stable tag with Git's NUL record delimiter. A later fix or documentation commit cannot hide an earlier feature/breaking change. `scripts/test-release-versioning.py` exercises the actual workflow script against disposable Git histories: precedence, a forced bump, the first release, dev prerelease numbering, the dev skip rules, and the branch policy that admits only `main` and `dev` to packaging.

The reusable workflow builds the dashboard and platform shells, creates validated archives, builds native `.pkg`/`.exe` installers, provisions/exercises native runtimes, and only then publishes a release and `SHA256SUMS` covering all archives and installers, with GitHub-generated notes; dev builds are marked as prereleases. macOS app bundles travel between runners as tar archives to preserve symlinks. The website redeploys after every successful release. Container images are built only after a stable release from main. Native build failures leave no newly published release from this pipeline.

The same release workflow builds `imprint-chrome-extension.zip`, validates its
contents and loads that exact ZIP after extraction in disposable Chromium. This
job is required by publication, and the extension ZIP is required in the complete
asset set and covered by `SHA256SUMS`. Branch CI exercises packaging in temporary
test directories; it does not publish extension releases. The local packager also
writes a `.zip.sha256` sidecar. See [ADR 0030](adr/0030-chrome-extension-release-zip.md).

Use the **Build release artifacts** manual workflow on main or dev for a build-only validation run. It does not publish. A branch-policy job rejects any other source ref before packaging starts. Native checks run on the owner's runners ([CI runners](ci-runners.md)): Linux x64 and emulated ARM64 and the Windows VMs on bedrock, and Apple silicon jobs on the laptop; local verification results and outstanding platform checks are in [platform validation](platform-validation.md).

Branch push and PR CI run Go checks, Python dependency/unit/runtime checks on five native targets, dashboard lint/production build/Playwright, Electron navigation/service/lifecycle tests and workflow linting. Electron packaging/archive-content checks belong to the release workflow on main and dev; container builds/smoke run only on main. Superseded compile/test runs are cancelled per branch. Signing/notarization and interactive GUI/tray checks remain separate release requirements; see [installer limitations](../installers/README.md).

CI jobs have explicit deadlines: 60 minutes each, and 90 for jobs emulating arm64 (see [CI runners](ci-runners.md)). On the Linux runners, Playwright installs Chromium and its system libraries with `npx playwright install --with-deps chromium`. Browser tests still launch Chromium and fail if required runtime libraries are missing.

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

`Dockerfile.base` holds the Python 3.12 image (pinned by digest), system packages and a venv from the hashed base, CPU and `llm` (llama.cpp CPU build) locks; `Dockerfile.imprint` adds the CLI and a fresh dashboard on top of it (`BASE_IMAGE`, by default `imprint-base:local`). Build them in that order: `docker build -f Dockerfile.base -t imprint-base:local .`, then `docker build -f Dockerfile.imprint -t imprint-local .`. The Image workflow publishes the base as `ghcr.io/alexandruleca/imprint-base:<hash>`, tagged by [`scripts/base-image-tag.sh`](../scripts/base-image-tag.sh) from `Dockerfile.base` and the three lock files, and builds it only when that tag is new: the arm64 dependency install runs under emulation for about 35 minutes, while a release on an existing base needs minutes. CI's `container` job uses the published base or builds this commit's. `.dockerignore` excludes local state and build caches. Runtime requirements include the checksum manifest; Go/Python share `IMPRINT_DATA_DIR`. Publish `-p 127.0.0.1:8420:8420`, mount `/data`, and use `docker exec <container> imprint doctor --json --probe-mcp` to diagnose it. Container ARM and GPU execution remain separate validation targets.

## Website deployment setting

GitHub Pages must use **GitHub Actions** (`build_type=workflow`), matching `pages.yml`. The old branch-root/Jekyll mode ran a second failing `pages-build-deployment` job alongside successful Astro deployments. The repository setting was corrected on 2026-10-23; the existing custom domain was retained.

The native desktop-launch fixture publishes its captured arguments by a same-directory rename, so polling never reads partially written JSON (including on Windows).

Desktop service ownership is defined in [ADR 0025](adr/0025-desktop-owned-background-service.md). `node --test ui-electron/*.test.js` exercises navigation, hidden spawning, splash ordering, hide/reopen, retry and explicit Quit. Native Python tests start the actual API with a lifetime pipe and verify shutdown. Windows CI additionally runs `scripts/test-windows-launcher.ps1` against a disposable ready installation; the fixture checks the direct GUI target and argument quoting without touching client profiles. Release ASAR checks require the startup assets and service module. These automated checks do not replace interactive console/tray/Dock validation.

WSL client registration is covered by Go profile-preservation fixtures and Python path/hook policy tests on the native CI matrix. The Windows setup regression also verifies replay of saved WSL selections. For a real interoperability check, run `scripts/test-wsl-interop.py --windows-cli <WSL-path-to-imprint.exe> --windows-python <Windows-venv-python-path>` with the base Python dependencies inside WSL. It creates disposable Claude/Codex homes and Windows data, exercises setup/idempotence, hooks, the `imprint` command (root approval, refusal, a Linux installation's link kept), actual MCP discovery/context and removal, then cleans its successful fixture. It reuses the supplied interpreter read-only; it never provisions or edits real client profiles. Windows CI does not imply that a WSL distribution is available. See [ADR 0026](adr/0026-windows-installer-wsl-client-bridge.md).

Uninstall cleanup is shared with `imprint disable` and, per client, `imprint disconnect` (ADR 0053). Native CI runs configuration-preservation, retry-journal and foreign-runtime fixtures on every Go target; Windows also runs `scripts/test-windows-uninstall.ps1` with successful stderr, nonzero exit and missing CLI cases. Native Inno uninstall must complete cleanup before files are removed. The Windows release job also uninstalls its disposable real installation and verifies that a process running from its venv is stopped, the install folder is gone, and MCP/hook/rule cleanup ran. Process ownership and stopping are covered by Go tests on every target. Integration journals and their backups are private installation state and are rejected by release validation. See [ADR 0027](adr/0027-symmetric-uninstall-and-branch-sync.md) and [ADR 0028](adr/0028-complete-uninstall.md).

Pull requests target `dev`; `main` only fast-forwards to a tested `dev` commit, so `dev` already holds everything `main` releases and nothing synchronizes it back ([ADR 0085](adr/0085-main-only-fast-forwards-to-dev.md)). Never force a push; a fast-forward of `main` that fails means `main` took a commit outside `dev`, which `dev` must take first.

`scripts/test-wsl-uninstall.py --windows-cli <WSL-path-to-imprint.exe> --windows-python <Windows-venv-python-path>` copies the CLI and bridge to a private Windows tree, connects disposable WSL profiles, and exercises actual native disable/uninstall, failure retry, `--keep-data` and default data removal. It never disables the supplied installation or edits real client profiles.


## Audio transcription runtime

The hashed CPU overlay also installs faster-whisper/CTranslate2 and PyAV for
[audio ingestion](audio-ingestion.md). It keeps the existing single CPU ONNX
build for VAD. Transcription explicitly uses CPU/int8; no NVIDIA libraries,
PyTorch or external FFmpeg executable are required. PyAV 15.1.0 is pinned on
Apple Silicon macOS because its wheel retains the macOS 13 baseline; other
targets use 19.0.2. See [ADR 0096](adr/0096-cpu-whisper-runtime-for-audio-imports.md).

Model files are not distribution artifacts: an uncached audio import downloads
and verifies the chosen revision/size/SHA256-pinned assets into the data
model cache. Runtime installation, ordinary text ingestion and opening Settings
never download speech weights. The default download is about 1.51 GiB; the Small
option is about 464 MiB. `scripts/smoke-runtime.py` checks native package imports
and CPU int8 availability without downloading weights. Native multilingual
execution uses `scripts/smoke-audio-ingest.py` with disposable data and supplied
fixtures; run it on each target before declaring platform execution validated.

## Bundled Echo speech engine

Release packaging requires native `voice-engine-*` artifacts from the reusable
`voice-engine.yml` workflow for Linux x64, Windows x64 and Apple silicon macOS.
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

## macOS laptop runners

Trusted push/manual macOS jobs run on the owner's Apple silicon laptop instead of
hosted macOS: native CI, bundled voice engines, native voice checks, macOS installer
packaging, Electron packaging and the llama.cpp wheel. Each job runs in its own
disposable Tart VM ([ADR 0115](adr/0115-apple-silicon-jobs-run-in-disposable-tart-vms.md)).
Pull requests run no macOS jobs ([ADR 0112](adr/0112-ci-runs-on-the-owners-machines.md));
the other runners are described in [CI runners](ci-runners.md). There are no Intel
macOS jobs ([ADR 0111](adr/0111-intel-macs-are-not-supported.md)).

| Runner | Labels | Supervisor |
|--------|--------|-----------|
| `Outland-vm-<timestamp>`, one per job | `[self-hosted, macOS, ARM64, imprint-apple-silicon]` | `~/.local/share/github-runners/imprint-vm` |

The LaunchAgent `tech.imprint.vm-runner` runs
[`.github/runners/macos/supervise.sh`](../.github/runners/macos/supervise.sh). Every
30 seconds it asks GitHub for queued jobs labelled `imprint-apple-silicon`. For
each, it clones the golden VM `imprint-runner-golden`, boots it headless, mints a
just-in-time runner configuration on the host and hands it to the guest over
`tart exec` stdin. The guest runner takes exactly one job; the supervisor then
deletes the VM and the registration. A VM whose runner gets no job within ten
minutes is discarded. One VM (6 CPUs, 8 GB) runs at a time; the supervisor keeps
the laptop from idle-sleeping only while a job runs. Jobs queue while the laptop
sleeps or is offline; there is no automatic paid hosted fallback.

Host prerequisites: Tart (installed from its notarized GitHub release into
`~/.local/opt`, linked as `~/.local/bin/tart`; the Homebrew tap's formula fails on
current Homebrew), `gh` logged in with `repo` scope, and `jq`. The supervisor uses
a fine-grained token with *Administration: write* on this repository when one is
stored in the keychain item `imprint-vm-runner`
(`security add-generic-password -s imprint-vm-runner -a "$USER" -w`), else the `gh` login.

- Bake or refresh the golden VM: `.github/runners/macos/prepare-image.sh`. It
  clones the base image pinned by digest in
  [`common.sh`](../.github/runners/macos/common.sh), grows its disk to 100 GB,
  installs Homebrew `cmake`, `librsvg` and `python@3.12`, and installs the latest
  Actions runner checked against the SHA256 in its release notes. Re-run it when
  GitHub deprecates the baked runner version, and bump the digest to move the
  guest to a newer macOS.
- Install or update the supervisor: `.github/runners/macos/install.sh` (copies the
  scripts to `~/.local/share/github-runners/imprint-vm`; `--uninstall` removes it).
  Its log is `~/Library/Logs/tech.imprint.vm-runner/supervise.log`.

Jobs create a fresh Python virtual environment from the image's `python3.12` under
runner temporary storage; workflow setup actions supply Go and Node 22.
`actions/setup-python`'s prebuilt macOS interpreter requires `/Users/runner`, so
macOS jobs do not use it. The macOS app artifact preserves symlinks while omitting
AppleDouble and extended attribute metadata so Linux packaging receives only
bundle files.

The package job validates the expanded payload and compiled helper, then installs
the pkg with `sudo installer`, bootstraps it and smokes the installed runtime. The
install step refuses to run on a self-hosted runner whose environment lacks
`IMPRINT_DISPOSABLE_RUNNER=1`, which only the VM image sets. If it refuses on a
VM runner, the golden VM lacks the runner's `.env`; re-run `prepare-image.sh`.

Guests run on Tart's default NAT network and can reach the host and LAN. Tart's
`softnet` isolation would block that but needs a one-time privileged setup and is
not enabled, so the VMs are not a boundary for untrusted code (see
[CI runners](ci-runners.md#security)). Do not approve untrusted workflow runs
targeting these runners; restrict repository write access and review workflow
changes. GitHub recommends private repositories for self-hosted runners:
[runner security guidance](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/add-runners).
