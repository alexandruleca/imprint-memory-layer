---
title: Installation
description: Install Imprint on macOS, Linux, or Windows in 30 seconds — one-line installer, version pinning, update command, Docker alternative, and upgrade safety.
---
## Requirements

**Desktop platform target:** macOS 13+ for the Electron app/pkg; Windows x64 and Linux x64/ARM artifacts have separate native validation gates. Native Windows/macOS execution of the current fixes is pending; see [platform validation](platform-validation.md).

**Core:**
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code/overview)
- A working network connection on first run (so the bundled `uv` can fetch Python + wheels)

**Host Python is *not* required.** Every release archive now ships Astral's `uv` binary alongside `imprint`; on first run `uv` downloads a pinned Python 3.12 via python-build-standalone into the install dir's `.venv/`. Nothing is installed system-wide.

**Optional — GPU acceleration:**
- NVIDIA GPU whose driver supports CUDA 13 (R580 or newer) for ~20× faster embedding. The GPU profile installs the CUDA and cuDNN libraries itself (about 1.3 GB); no CUDA toolkit is needed (pick `--profile gpu` at install, or run `imprint profile set gpu` later — see [embeddings.md](./embeddings.md))

**Optional — local LLM tagger** — `llama-cpp-python` is skipped by default to keep the install footprint small. Opt in with `--with-llm` at install time or `imprint profile add-llm` afterward; the dashboard's first local chat also installs it. Windows x64 and Linux x64/arm64 get upstream's prebuilt CPU wheel, so no compiler is needed; macOS builds it from source with the Xcode Command Line Tools (ADR 0035).

**LLM topic tagging** (`IMPRINT_LLM_TAGS=1`) — `anthropic` and `openai` SDKs are installed automatically in the base profile. No extra steps needed for any provider.

**Optional — kreuzberg document extractor** (ELv2, opt-in) — Replaces the built-in PDF/DOCX/image extractors with a more capable alternative that also adds archive formats (.zip, .7z, .tar), Apple iWork files, ODF documents, and email/mbox. Install separately and opt in:

```bash
pip install kreuzberg[all]          # or: kreuzberg  (core, no OCR / image support)
imprint config set ingest.use_kreuzberg true
```

> **License note:** kreuzberg is published under the [Elastic License 2.0 (ELv2)](https://www.elastic.co/licensing/elastic-license). Internal and self-hosted use is permitted; offering Imprint as a hosted/SaaS service that exposes kreuzberg's extraction is not. Imprint does not install kreuzberg automatically — you accept ELv2 when you run `pip install kreuzberg`.

**Optional — obscura headless browser** (Apache 2.0, opt-in) — Provides JS-rendered HTML extraction for `imprint ingest-url`, making it possible to fully index JS-heavy SPAs and documentation sites that return blank pages to static fetchers. Install the binary and opt in:

```bash
# Download from https://github.com/h4ckf0r0day/obscura/releases and put in PATH
imprint config set ingest.use_obscura true
```

When `use_obscura=true`, `imprint ingest-url` pipes each URL through `obscura fetch` for JS execution before extraction. Falls back gracefully if the binary is not in PATH.

## Quick Install

**Linux / macOS:**
```bash
curl -fsSL https://raw.githubusercontent.com/alexandruleca/imprint-memory-layer/main/install.sh | bash
```

**Windows (PowerShell):**
```powershell
irm https://raw.githubusercontent.com/alexandruleca/imprint-memory-layer/main/install.ps1 | iex
```

This downloads the pre-built imprint binary + bundled `uv`, prompts for the install profile (CPU / GPU + optional local LLM tagger — auto-skipped in `curl | bash` mode), provisions a Python venv via `uv`, installs the selected dependencies, registers the MCP server, configures Claude Code hooks, and sets up shell aliases. One command, everything ready.

### Desktop startup and background operation

Open the installed Imprint shortcut/app, or run `imprint ui open`. The desktop shell shows a loading screen while its hidden local service starts and the dashboard loads. Startup errors offer **Try again** and **View startup log**; cancelling startup exits the app. Ready Windows installs launch the GUI directly without opening a CLI console.

Closing the dashboard hides it and keeps the service running. Reopen it from the tray, macOS Dock or the Imprint shortcut. Choose **Quit Imprint** at the bottom of the sidebar or in the tray menu to exit and stop the app's service. On Linux desktops without a tray, close minimizes the window so you can reopen it from the taskbar.

If you previously ran `imprint ui start`, the app reuses that independent service and leaves it running on Quit; stop it with `imprint ui stop`. `imprint ui status` describes that explicit daemon, not the app-owned service. Both write startup diagnostics to `imprint_ui.log` under the data directory (`imprint ui log` shows the location). Qdrant is shared with CLI/MCP clients and remains independent of the dashboard. Browser-only distributions continue to use the explicit daemon lifecycle.

See [ADR 0025](adr/0025-desktop-owned-background-service.md) and [platform validation](platform-validation.md) for ownership and native GUI limitations.

### Claude Code and Codex inside WSL

The Windows installer offers **Connect clients in WSL** after copying its files. Select the detected Claude Code and/or Codex profiles you want to connect. They share the Windows installation's memories and dashboard; a second Linux runtime is not installed. Discovery may start a distribution, skips Docker/Rancher utility distributions and inspects the distro's default Linux user. No choices are selected on a fresh install.

Repair and silent updates reuse saved WSL choices. A fresh silent install does not change WSL profiles. Selected registration failures appear in the setup log and fail setup. Unchecking a profile stops future add/repair work for that choice; use the removal command to disconnect an existing profile.

You can connect clients later from PowerShell:

```powershell
$ImprintBin = Join-Path $env:LOCALAPPDATA 'Programs\Imprint\bin\imprint.exe'
& $ImprintBin wsl list
& $ImprintBin wsl setup --distribution Ubuntu --target claude-code
& $ImprintBin wsl setup --distribution Ubuntu --target codex
```

Restart the selected client, verify the Imprint MCP server and review/trust its hooks. WSL Windows-executable interoperability must be enabled. The wizard detects existing default-user profile directories; it does not install Claude Code, Codex or WSL. Settings exported only by your interactive shell are not sourced during discovery. For another Linux user or profile, pass `--user <name>`, `--claude-home /absolute/profile` or `--codex-home /absolute/profile`. An explicit `--python <Windows-path>` can select an existing Windows Imprint interpreter. A non-default user's profile must also be accessible through the Windows account's WSL filesystem provider; setup does not change Linux permissions or elevate privileges.

Setup does not approve or index project directories. To work with a WSL project through the Windows store, use the installed launcher from WSL (substitute `.claude` if that is your selected profile):

```bash
cd ~/code/my-project
"$HOME/.codex/imprint-wsl" cli workspace roots add "$PWD"
"$HOME/.codex/imprint-wsl" cli ingest "$PWD"
```

Existing workspace overlap, sharing and purge rules apply. Linux files are accessed through the distribution's Windows UNC namespace; mounted Windows drives use their real Windows paths, honoring custom mount locations. Windows paths returned by other tools can be translated with `wslpath -u`. Spaces, Unicode and apostrophes are supported; paths that cannot be represented through Windows filesystem APIs fail explicitly. Hook support follows the current host adapters; a configured entry alone does not establish host-side trust or transcript-version compatibility.

`imprint learn` and the dashboard's **Learn** action also read each registered profile's Claude Code and Codex conversations through `\\wsl.localhost\<distro>\…`, comparing their working directories the way the bridge does. Reading a distribution's mount table can start it. A distribution that cannot be reached is skipped and listed under "Not learned" at the end of the run; the other stores are still learned.

To disconnect a selected profile manually:

```powershell
& $ImprintBin wsl remove --distribution Ubuntu --target claude-code
& $ImprintBin wsl remove --distribution Ubuntu --target codex
```

Use the same user/profile override flags when removing a custom registration. Removal preserves memories and other integrations; it refuses to remove an Imprint entry that now points to a different runtime. Windows disable/uninstall also disconnects the WSL profiles previously registered by this installation, including recorded custom users and homes. If a recorded distribution or profile cannot be reached, cleanup fails and uninstall keeps the runtime for a retry. See [ADR 0026](adr/0026-windows-installer-wsl-client-bridge.md) and [platform validation](platform-validation.md).

### Disable and uninstall

`imprint disable` disconnects this runtime from supported local clients and recorded WSL profiles, removes its hooks, Claude permission and managed instructions, and stops the explicit dashboard daemon and Qdrant. Runtime and memories remain available for `imprint enable`. Another installation's MCP registration and associated rules are preserved. Cursor/Cline standalone rules are removed only when their contents match the generated rule; edited files are retained and reported. Other settings, servers, hooks and user-authored text are preserved. Configuration backups remain as inert recovery files.

Uninstalling removes Imprint completely ([ADR 0028](adr/0028-complete-uninstall.md)). After you confirm, it disconnects clients, then stops every process of this installation: the desktop app, its local service, MCP servers your clients started, running jobs and Qdrant. Then it deletes the runtime and your memories, workspaces and settings. Processes Imprint only opened, such as a browser tab on the dashboard, and services you run yourself, such as Ollama, are left alone. To keep your data for a later reinstall, answer **No** when asked, or use the flags below. If a process cannot be stopped or a client cannot be disconnected, nothing is deleted; the log names the cause.

Use **Windows Settings → Apps → Imprint → Uninstall**, or the Start menu uninstaller. After Windows' confirmation it asks whether to delete your data too (Yes deletes it, No keeps `data\` in the install folder, Cancel stops). A silent uninstall (`unins000.exe /VERYSILENT`) deletes everything unless you add `/KEEPDATA`. It removes only this install's user PATH segment. A failure cancels file removal; inspect `uninstall-cleanup.log` in the install directory, correct the reported error and retry. WSL cleanup uses the concrete profiles recorded during setup, with compatibility for the older installer's saved distro/target selections. Registrations created manually by older versions with custom user/home flags were not recorded; disconnect those using the same flags before uninstalling.

On macOS, run **Uninstall Imprint.command**. It asks whether to delete your data in `~/.local/share/imprint`, cleans up as above, then removes the app and its CLI symlink, and stops on cleanup errors.

For archive/script installations, run `imprint uninstall [-y]`. It removes the whole installation including memories; `--keep-data` keeps memories and settings (`--delete-data` states the default explicitly). Cleanup errors stop runtime removal. Shell aliases and symlinks are removed only when they point into this runtime, and a source checkout never deletes a separate release installation. The command leaves the source/release tree when keeping data; native installer-owned files and OS shortcuts are removed through the native uninstaller. Hosted ChatGPT connectors and project-local configurations installed by hand are not managed local files and must be removed where they were configured. Custom native profiles require their setup environment (`CLAUDE_CONFIG_DIR` / `CODEX_HOME`) when running cleanup.

See [ADR 0027](adr/0027-symmetric-uninstall-and-branch-sync.md) for client ownership and retry behavior and [ADR 0028](adr/0028-complete-uninstall.md) for process and data removal.

### Profile & extras flags

Skip the interactive prompt with CLI flags:

```bash
# Force GPU + local LLM tagger, no prompts
curl -fsSL .../install.sh | bash -s -- --profile gpu --with-llm

# CPU-only, explicitly disable the LLM tagger (default when piped)
curl -fsSL .../install.sh | bash -s -- --profile cpu --no-llm

# Fully non-interactive: fail fast on any ambiguity
curl -fsSL .../install.sh | bash -s -- --non-interactive
```

Swap profile after the fact with `imprint profile set gpu`, add the LLM tagger with `imprint profile add-llm`, or drop it with `imprint profile drop-llm`. `set` and `drop-llm` re-run the installer's bootstrap step via `uv`. `add-llm` installs only the LLM overlay into the existing environment, so it is safe while the dashboard or an MCP server is running.

## Supported hosts

`imprint setup all` probes for each of the following and wires whichever it finds. Missing tools are skipped cleanly:

| Target | Config path | Notes |
|---|---|---|
| `claude-code` | `~/.claude/` | Claude Code CLI (stdio MCP + hooks). |
| `claude-desktop` | macOS `~/Library/Application Support/Claude/` · Windows `%APPDATA%\Claude\` or `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\` (MS Store install) | Anthropic's desktop app. Detects both the standalone and Microsoft Store (MSIX) install layouts. **WSL2-aware**: when run from a WSL shell, writes the config to the Windows-side `claude_desktop_config.json` and wires the command through `wsl.exe -d <distro> -- env <PYTHONPATH=...> <venv-python> -m imprint`, so the Windows app launches the Linux venv directly. No second Windows-side install needed. |
| `chatgpt-desktop` | — | Detection-only. ChatGPT Desktop wires MCP via in-app hosted connectors (SSE), not a local stdio config, so `setup` reports the install and points at the `supergateway` reverse-proxy pattern. |
| `cursor` | `~/.cursor/` | MCP + hooks + always-on rule. |
| `codex` | `~/.codex/` | Codex CLI (TOML config). |
| `copilot` | VS Code settings | Custom instructions + `mcp.json`. |
| `cline` | `~/.clinerules/` + Cline extension + CLI | Rules file + MCP entries in both the VS Code extension and the Cline CLI. |
| `openclaw` | `~/.openclaw/openclaw.json` | Also recognized as `clawdbot` / `moltbot`. Nested `mcp.servers` key. |

Target a specific host with `imprint setup <target>` (e.g. `imprint setup claude-desktop`).

### Auto-syncing past conversations from Claude Desktop / ChatGPT Desktop

Neither consumer app keeps conversation content in a stable on-disk format — both load chats from the vendor's server on demand. The supported path is the built-in **export**:

- **Claude Desktop / claude.ai** — Settings → Privacy → Export data. Emails a zip.
- **ChatGPT Desktop / chat.openai.com** — Settings → Data controls → Export. Emails a zip.

Once the zip lands in your Downloads folder, run:

```bash
imprint learn --desktop
```

What it does:

- Scans common Downloads locations (WSL-aware: both `~/Downloads` on Linux and `/mnt/c/Users/<user>/Downloads` on the Windows side).
- Detects Claude / ChatGPT exports by sniffing the zip contents (fingerprint files like `projects.json`, `message_feedback.json`), not just filenames.
- Runs each new zip through the existing JSON extractor — one searchable memory per conversation, split into chunks, tagged.
- Dedups by SHA-256 of the zip; re-runs are cheap and skip anything already indexed.
- Stores under projects `claude-desktop-convos` and `chatgpt-desktop-convos`.

Keep it running in the background while you request new exports:

```bash
imprint learn --desktop --watch            # polls every 30 s by default
imprint learn --desktop --interval 300     # every 5 min
imprint learn --desktop --path ~/Archive   # extra folder(s) in addition to Downloads
```

`imprint learn` on its own indexes Claude Code transcripts and auto-memory files and Codex sessions. It reads them from this computer's profiles (`CLAUDE_CONFIG_DIR` or `~/.claude`, `CODEX_HOME` or `~/.codex`) and from every Claude Code or Codex profile registered in WSL. `--desktop` is additive. Neither needs an approved workspace root: the Downloads folders are where exports land, and each conversation's recorded working directory decides which workspace it belongs to. Extra `--path` folders must be inside the workspace's approved roots. See [workspaces](workspaces.md#learning-needs-no-root).

## Install a specific version or channel

The default installer resolves `/releases/latest`, which GitHub maps to the most recent **stable** (non-prerelease) build. Two release channels are published:

| Channel | Tag pattern | Produced by |
|---|---|---|
| **stable** | `vX.Y.Z` | conventional-commit release on every merge to `main` |
| **dev** | `vX.Y.Z-dev.N` | prerelease on every push to `dev` (N = build number) |

Pin a specific release or switch channels via env var or CLI flag.

**Linux / macOS:**
```bash
# Pin to a specific tag (env var)
IMPRINT_VERSION=v0.2.0 curl -fsSL https://raw.githubusercontent.com/alexandruleca/imprint-memory-layer/main/install.sh | bash

# Pin via CLI arg
curl -fsSL https://raw.githubusercontent.com/alexandruleca/imprint-memory-layer/main/install.sh | bash -s -- --version v0.2.0

# Latest dev prerelease
IMPRINT_CHANNEL=dev curl -fsSL https://raw.githubusercontent.com/alexandruleca/imprint-memory-layer/main/install.sh | bash
curl -fsSL https://raw.githubusercontent.com/alexandruleca/imprint-memory-layer/main/install.sh | bash -s -- --dev
```

**Windows (PowerShell):**
```powershell
# Pin to a specific tag
$env:IMPRINT_VERSION = "v0.2.0"; irm https://raw.githubusercontent.com/alexandruleca/imprint-memory-layer/main/install.ps1 | iex

# Latest dev prerelease
$env:IMPRINT_CHANNEL = "dev"; irm https://raw.githubusercontent.com/alexandruleca/imprint-memory-layer/main/install.ps1 | iex

# Or download and pass args directly
irm https://raw.githubusercontent.com/alexandruleca/imprint-memory-layer/main/install.ps1 -OutFile install.ps1
.\install.ps1 -Version v0.2.0
.\install.ps1 -Dev
```

Precedence: CLI flag > `IMPRINT_VERSION` env > `IMPRINT_CHANNEL=dev` env > default stable. The installer downloads that release's archive and verifies it against `SHA256SUMS`.

## Updating an existing install

When a newer release exists, the dashboard shows **Update to vX.Y.Z** at the top right. It explains what will happen on this machine, runs the update and reconnects when the new version is running. The dashboard checks GitHub's releases API at most every six hours while it is open.

From a terminal:

```bash
imprint update                 # latest stable (asks for confirmation)
imprint update --dev           # latest prerelease
imprint update --version v0.3.1
imprint update --check         # prints current + latest and exits
imprint update --check --json  # the same, as JSON
imprint update -y              # skip confirmation, for scripts / CI
```

Every download is verified against the release's `SHA256SUMS` before anything is installed. An update refuses to start while an ingest or other queued job runs. How it installs depends on how Imprint was installed ([ADR 0023](adr/0023-update-by-installation-type.md)):

| Installed with | Update |
| --- | --- |
| `install.sh` / `install.ps1` | Replaces the install tree in place, re-runs `imprint setup` and restarts a dashboard server started by `imprint ui start`. The previous files in `bin/` are kept as `bin/<name>.prev` for a manual rollback. |
| Windows installer | Stops the dashboard server and runs the new `setup.exe` silently. Imprint closes, updates and reopens; your install profile (GPU, local LLM) is kept. The installer log is `data/update-setup.log`. |
| macOS `.pkg` | Opens the new `.pkg` in Installer, which asks for your password, then restarts the dashboard server. |
| Docker | Pull the new image and recreate the container; the `/data` volume is kept. |
| Source checkout | `git pull` and rebuild. |

**What's preserved:** `data/` (workspaces, Qdrant storage, SQLite graphs, `config.json`, `workspace.json`, `gpu_state.json`) and `.venv/` (Python virtual environment). An in-place update overwrites the release's files and leaves files that newer releases dropped. Installations from before ADR 0023 update once by the old route (installer, install script or `imprint update`) before the dashboard can update them.

Re-running `install.sh` against an existing install also still works, but it now requires confirmation:

```bash
# Interactive (TTY): prompts "Upgrade existing install? [y/N]"
bash install.sh

# Non-interactive (curl | bash): must opt in explicitly
IMPRINT_ASSUME_YES=1 curl -fsSL .../install.sh | bash
curl -fsSL .../install.sh | bash -s -- --yes
```

### Sticky GPU failure handling

The profile selects exactly one ONNX Runtime build: `cpu` installs `onnxruntime`, `gpu` installs `onnxruntime-gpu`, and `auto` installs `onnxruntime-gpu` when `nvidia-smi` reports a GPU and CUDA has not already failed for this host and GPU build. When the driver supports an older CUDA than the GPU build needs, `gpu` and `auto` both install `onnxruntime` and name the CUDA version to update the driver to. The two builds share one package directory, so setup removes the other build and reinstalls a build with missing files; rerunning `imprint bootstrap --non-interactive` repairs `module 'onnxruntime' has no attribute 'InferenceSession'`, and `imprint doctor` reports the damage. An explicit `gpu` profile keeps `onnxruntime-gpu` when CUDA fails, because that build also runs on CPU. See [ADR 0032](adr/0032-one-onnx-runtime-per-environment.md).

`imprint setup` checks CUDA every run. When the ONNX Runtime CUDA check or the `llama-cpp-python` CUDA build fails (for example: Blackwell `sm_120` with an older `nvcc`), the failure is recorded in `data/gpu_state.json`, keyed on the GPU, driver, Python and, for ONNX Runtime, the GPU build; llama builds also record `nvcc` and compute capability. `auto` then uses the CPU build, and the llama rebuild is skipped, so you don't see the same multi-minute warning on every invocation. A new driver or GPU build retries automatically.

After you upgrade your CUDA toolkit or driver, force a retry:

```bash
imprint setup --retry-gpu
```

The GPU build installs NVIDIA's CUDA runtime, cuBLAS, cuFFT, cuRAND, NVRTC and cuDNN wheels from the hashed GPU lock, and ONNX Runtime's `preload_dlls()` loads them on Windows and Linux. See [ADR 0038](adr/0038-gpu-runtime-ships-cuda-libraries.md).

## Run the relay server (Docker)

Prebuilt multi-arch images (`linux/amd64`, `linux/arm64`) are published to GHCR
after every release:

```bash
docker run -p 8430:8430 ghcr.io/alexandruleca/imprint-relay:latest
```

Full deployment guide — pinned tags, `docker compose`, TLS termination (Caddy /
Traefik / Nginx), health checks, and manual rebuilds — lives on the dedicated
[Docker page](./docker.md). See [sync.md](./sync.md) for how peer sync uses the
relay.

## Integrity and diagnostics

Install/update verifies release archives against `SHA256SUMS` before extraction. Bundled uv and auto-downloaded Qdrant use pinned SHA256 values; Python profiles use hash locks. This is not an offline installer or independently signed publisher verification. Models and managed Python still download on first use. Windows signatures/macOS notarization remain pending maintainer configuration.

Run `imprint doctor --json --probe-mcp` after installation to inspect runtime dependencies, dashboard, primary Codex registration, Qdrant readiness and a timed MCP initialize/list-tools handshake. It is read-only and does not provision models. Confirm client-side trust/tools after restarting the host.
