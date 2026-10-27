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

**Local models — llama.cpp:** every installation includes `llama-cpp-python` 0.3.36, llama.cpp's Python binding, which runs embeddings, the local tagger, summaries and chat ([ADR 0086](adr/0086-embeddings-run-on-llama-cpp-without-cuda.md)). Windows x64 and Linux x64/arm64 install upstream's prebuilt wheel, so no compiler is needed ([ADR 0035](adr/0035-prebuilt-llama-wheels-and-chat-provisioning.md)). **macOS builds it from source, which needs the Xcode Command Line Tools** (`xcode-select --install`), until Imprint pins prebuilt Metal wheels from its own release workflow (planned). ONNX Runtime's CPU build (about 15 MB) is installed for OCR and the opt-in privacy filter.

**Optional — GPU acceleration:** any NVIDIA, AMD or Intel GPU through its graphics driver; no CUDA or other toolkit is installed. On Windows x64 and Linux x64 the `gpu` and `auto` install profiles install llama.cpp's Vulkan build (a 40 MB wheel on Windows, 121 MB on Linux) and keep it only when it finds a GPU; macOS uses Metal ([ADR 0049](adr/0049-llama-gpu-offload-through-vulkan-wheels.md)). Embeddings and the local LLM then run on one GPU: on an RTX 4080 Laptop, embedding ran at 7,316 tokens/s against 438 on the CPU, and a tagging call took about 4 s against 38 s. Pick the profile with `--profile gpu` at install, or `imprint profile set gpu` later. `model.device` (default `auto`) decides whether embeddings use the GPU ([embeddings](./embeddings.md#gpu-embeddings)).

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

This downloads the pre-built imprint binary + bundled `uv`, picks the install profile (`install.sh` asks on a terminal, defaulting to GPU, and installs `cpu` under `curl | bash`; `install.ps1` uses `auto`), provisions a Python venv via `uv`, installs the selected dependencies, registers the MCP server, configures Claude Code hooks, and sets up shell aliases. One command, everything ready.

### Desktop startup and background operation

Open the installed Imprint shortcut/app, or run `imprint ui open`. The desktop shell shows a loading screen while its hidden local service starts and the dashboard loads. Startup errors offer **Try again** and **View startup log**; cancelling startup exits the app. Ready Windows installs launch the GUI directly without opening a CLI console.

The shortcut and `imprint ui open` tell the desktop app where its runtime is (installation, data directory and Python). When the operating system starts the app by itself, for example **Reopen** after a crash, a Dock or Spotlight launch on macOS, or a pinned taskbar icon, the app asks the `imprint` CLI that ships beside it (`imprint ui runtime`, which prints the same paths as JSON). The paths stay defined in one place, the CLI.

Closing the dashboard hides it and keeps the service running. Reopen it from the tray, macOS Dock or the Imprint shortcut. Choose **Quit Imprint** at the bottom of the sidebar or in the tray menu to exit and stop the app's service. On Linux desktops without a tray, close minimizes the window so you can reopen it from the taskbar.

If you previously ran `imprint ui start`, the app reuses that independent service and leaves it running on Quit. Whichever started it, the dashboard server registers itself in `imprint_ui.json` under the data directory: `imprint ui status` reports it, `imprint ui stop` stops it (the app then shows that its service stopped, with **Try again**), and `imprint ui restart` restarts it with the installed code without changing who owns it ([ADR 0050](adr/0050-registered-dashboard-server-and-supervised-restart.md)). Both write startup diagnostics to `imprint_ui.log` under the data directory (`imprint ui log` shows the location). Qdrant is shared with CLI/MCP clients and remains independent of the dashboard. Browser-only distributions continue to use the explicit daemon lifecycle.

See [ADR 0025](adr/0025-desktop-owned-background-service.md) and [platform validation](platform-validation.md) for ownership and native GUI limitations.

### Clients inside WSL

The Windows installer offers **Connect clients in WSL** after copying its files. Select the detected Claude Code, Codex, Cursor, GitHub Copilot and/or Cline profiles you want to connect. They share the Windows installation's memories and dashboard; a second Linux runtime is not installed. Discovery may start a distribution, skips Docker/Rancher utility distributions and inspects the distro's default Linux user. No choices are selected on a fresh install.

Repair and silent updates reuse saved WSL choices. A fresh silent install does not change WSL profiles. Selected registration failures appear in the setup log and fail setup. Unchecking a profile stops future add/repair work for that choice; use the removal command to disconnect an existing profile.

You can enroll or remove profiles later in the dashboard: **Settings → WSL profiles** lists each distribution's default-user profile for every client a WSL profile can be registered for, with whether it was found and whether this installation enrolled it. **Enroll** and **Remove** (asked first) run `imprint wsl setup` / `imprint wsl remove` through the job queue and show its log. The list comes from `imprint wsl list --json`, which reads the CLI's client table, so a client added there appears without a dashboard change. Checking runs every distribution, which starts stopped ones, so the section checks only when you ask. Enrolling registers the installed Windows runtime and installs nothing ([ADR 0053](adr/0053-connect-clients-without-touching-the-runtime.md)). Profiles enrolled this way are not installer selections: Repair does not replay them, and they keep working because the installation path does not change. Other Linux users and custom homes remain CLI-only.

Or from PowerShell (`imprint wsl list` prints the same listing as a table; `--json` for scripts):

```powershell
$ImprintBin = Join-Path $env:LOCALAPPDATA 'Programs\Imprint\bin\imprint.exe'
& $ImprintBin wsl list
& $ImprintBin wsl setup --distribution Ubuntu --target claude-code
& $ImprintBin wsl setup --distribution Ubuntu --target codex
& $ImprintBin wsl setup --distribution Ubuntu --target cursor
& $ImprintBin wsl setup --distribution Ubuntu --target copilot
& $ImprintBin wsl setup --distribution Ubuntu --target cline
```

A Cursor selection is for learning only. With Remote-WSL, the Windows Cursor app runs the agent against the distribution and writes its conversations to the distribution's `~/.cursor`. Registering that profile lets `imprint learn` read them and writes nothing into it. Cursor's MCP server and hooks come from `imprint setup cursor` on Windows ([ADR 0052](adr/0052-learn-cursor-conversations-from-their-folder-names.md)).

Under Remote-WSL, VS Code's Copilot agent host runs inside the distribution, as the Copilot CLI does. A Copilot selection registers in the distribution's Copilot home (`COPILOT_HOME` or `~/.copilot`): the bridge launcher, the `imprint` entry in `mcp-config.json`, the managed block in `copilot-instructions.md` and `hooks/imprint.json` (SessionStart and Stop through the bridge). Learn reads its `session-state` ([ADR 0057](adr/0057-copilot-home-as-store-config-and-hook-root.md)).

Cline's VS Code extension runs inside the distribution under Remote-WSL, as the Cline CLI does, so a Cline selection registers there: the bridge launcher, the `imprint` entry in `<cline home>/data/settings/cline_mcp_settings.json` and the rule `<cline home>/rules/imprint.md`, where the Cline home is `CLINE_DIR` or `~/.cline`. Learn reads its sessions from `<cline home>/data/sessions`. Cline gets no hooks ([ADR 0056](adr/0056-learn-cline-sessions-from-their-manifests.md)).

For Claude Code, Codex and Copilot, restart the selected client, verify the Imprint MCP server and review/trust its hooks. For Cline, reload the extension or restart the CLI. WSL Windows-executable interoperability must be enabled. The wizard detects existing default-user profile directories; it does not install Claude Code, Codex or WSL. Settings exported only by your interactive shell are not sourced during discovery. For another Linux user or profile, pass `--user <name>`, `--claude-home /absolute/profile` or `--codex-home /absolute/profile`. An explicit `--python <Windows-path>` can select an existing Windows Imprint interpreter. A non-default user's profile must also be accessible through the Windows account's WSL filesystem provider; setup does not change Linux permissions or elevate privileges.

Setup does not approve or index project directories. To work with a WSL project through the Windows store, use the installed launcher from WSL (substitute `.claude` if that is your selected profile):

```bash
cd ~/code/my-project
"$HOME/.codex/imprint-wsl" cli workspace roots add "$PWD"
"$HOME/.codex/imprint-wsl" cli ingest "$PWD"
```

Existing workspace overlap, sharing and purge rules apply. Linux files are accessed through the distribution's Windows UNC namespace; mounted Windows drives use their real Windows paths, honoring custom mount locations. Windows paths returned by other tools can be translated with `wslpath -u`. Spaces, Unicode and apostrophes are supported; paths that cannot be represented through Windows filesystem APIs fail explicitly. Hook support follows the current host adapters; a configured entry alone does not establish host-side trust or transcript-version compatibility.

`imprint learn` and the dashboard's **Learn** action also read each registered profile's Claude Code, Codex, Cursor, Copilot and Cline conversations through `\\wsl.localhost\<distro>\…`, comparing their working directories the way the bridge does. Reading a distribution's mount table can start it. A distribution that cannot be reached is skipped and listed under "Not learned" at the end of the run; the other stores are still learned.

To disconnect a selected profile manually:

```powershell
& $ImprintBin wsl remove --distribution Ubuntu --target claude-code
& $ImprintBin wsl remove --distribution Ubuntu --target codex
& $ImprintBin wsl remove --distribution Ubuntu --target cursor
& $ImprintBin wsl remove --distribution Ubuntu --target copilot
& $ImprintBin wsl remove --distribution Ubuntu --target cline
```

Use the same user/profile override flags when removing a custom registration. Removal preserves memories and other integrations; it refuses to remove an Imprint entry that now points to a different runtime. Windows disable/uninstall also disconnects the WSL profiles previously registered by this installation, including recorded custom users and homes. If a recorded distribution or profile cannot be reached, cleanup fails and uninstall keeps the runtime for a retry. See [ADR 0026](adr/0026-windows-installer-wsl-client-bridge.md) and [platform validation](platform-validation.md).

### Disable and uninstall

`imprint disable` disconnects this runtime from supported local clients and recorded WSL profiles, removes its hooks, Claude permission and managed instructions, and stops the explicit dashboard daemon and Qdrant. Runtime and memories remain available for `imprint enable`. Another installation's MCP registration and associated rules are preserved. Cursor/Cline standalone rules are removed only when their contents match the generated rule; edited files are retained and reported. Other settings, servers, hooks and user-authored text are preserved. Configuration backups remain as inert recovery files.

`imprint connect [client]` and `imprint disconnect [client]` (default: all clients) rehook and unhook clients without the rest. Connect registers the installed runtime as setup would, but installs and starts nothing, and fails with "Run imprint setup first" when the runtime is missing. Disconnect removes only the chosen clients' registrations, including the WSL profiles recorded for them, and stops nothing. ChatGPT Desktop registers through Codex's profile, so disconnecting either disconnects both; a WSL registration comes back with `imprint wsl setup`. `imprint clients` shows which clients are connected, and the dashboard's Commands page offers both commands ([ADR 0053](adr/0053-connect-clients-without-touching-the-runtime.md)).

Uninstalling removes Imprint completely ([ADR 0028](adr/0028-complete-uninstall.md)). After you confirm, it disconnects clients, then stops every process of this installation: the desktop app, its local service, MCP servers your clients started, running jobs and Qdrant. Then it deletes the runtime and your memories, workspaces and settings, together with the desktop app's profile (window positions and the dashboard's browser storage, in `~/Library/Application Support/imprint-ui` on macOS, `%APPDATA%\imprint-ui` on Windows, `~/.config/imprint-ui` on Linux). Every Imprint installation on your account shares that profile ([ADR 0074](adr/0074-uninstall-removes-the-shared-desktop-profile.md)). Processes Imprint only opened, such as a browser tab on the dashboard, and services you run yourself, such as Ollama, are left alone. To keep your data for a later reinstall, answer **No** when asked, or use the flags below. If a process cannot be stopped or a client cannot be disconnected, nothing is deleted; the log names the cause.

Use **Windows Settings → Apps → Imprint → Uninstall**, or the Start menu uninstaller. After Windows' confirmation it asks whether to delete your data too (Yes deletes it, No keeps `data\` in the install folder, Cancel stops). A silent uninstall (`unins000.exe /VERYSILENT`) deletes everything unless you add `/KEEPDATA`. It removes only this install's user PATH segment. A failure cancels file removal; inspect `uninstall-cleanup.log` in the install directory, correct the reported error and retry. WSL cleanup uses the concrete profiles recorded during setup, with compatibility for the older installer's saved distro/target selections. Registrations created manually by older versions with custom user/home flags were not recorded; disconnect those using the same flags before uninstalling.

On macOS, run **Uninstall Imprint.command**. It asks whether to delete your data in `~/.local/share/imprint` and the desktop profile, cleans up as above, then removes the app, its CLI symlink and `~/.local/share/imprint` once nothing is left in it, and stops on cleanup errors.

For archive/script installations, run `imprint uninstall [-y]`. It removes the whole installation including memories; `--keep-data` keeps memories and settings (`--delete-data` states the default explicitly). Cleanup errors stop runtime removal. Shell aliases and symlinks are removed only when they point into this runtime, and a source checkout never deletes a separate release installation. The command leaves the source/release tree when keeping data; native installer-owned files and OS shortcuts are removed through the native uninstaller. Hosted ChatGPT connectors and project-local configurations installed by hand are not managed local files and must be removed where they were configured. Custom native profiles require their setup environment (`CLAUDE_CONFIG_DIR` / `CODEX_HOME`) when running cleanup.

See [ADR 0027](adr/0027-symmetric-uninstall-and-branch-sync.md) for client ownership and retry behavior, [ADR 0028](adr/0028-complete-uninstall.md) for process and data removal and [ADR 0074](adr/0074-uninstall-removes-the-shared-desktop-profile.md) for the desktop profile.

### Profile flags

Skip the interactive prompt with CLI flags:

```bash
# GPU build when llama.cpp finds a GPU, no prompts
curl -fsSL .../install.sh | bash -s -- --profile gpu

# CPU build only (the default when piped)
curl -fsSL .../install.sh | bash -s -- --profile cpu

# Fully non-interactive: fail fast on any ambiguity
curl -fsSL .../install.sh | bash -s -- --non-interactive
```

The profile selects llama.cpp's build, which runs embeddings and the local LLM: `cpu` installs the CPU build, and `gpu` and `auto` (the default of `imprint setup` and `imprint bootstrap`) install the GPU build and keep it when llama.cpp finds a GPU, so the two behave the same ([ADR 0049](adr/0049-llama-gpu-offload-through-vulkan-wheels.md), [ADR 0086](adr/0086-embeddings-run-on-llama-cpp-without-cuda.md)). `data/profile.json` records it. Swap it later with `imprint profile set cpu|gpu|auto`, which re-runs the installer's bootstrap step via `uv`. Whether embeddings use the GPU is `model.device`, which installs nothing.

## Supported hosts

`imprint setup all` probes for each of the following and wires whichever it finds. Missing tools are skipped cleanly:

| Target | Config path | Notes |
|---|---|---|
| `claude-code` | `~/.claude/` | Claude Code CLI (stdio MCP + hooks). |
| `claude-desktop` | macOS `~/Library/Application Support/Claude/` · Windows `%APPDATA%\Claude\` or `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\` (MS Store install) | Anthropic's desktop app. Detects both the standalone and Microsoft Store (MSIX) install layouts. **WSL2-aware**: when run from a WSL shell, writes the config to the Windows-side `claude_desktop_config.json` and wires the command through `wsl.exe -d <distro> -- env <PYTHONPATH=...> <venv-python> -m imprint`, so the Windows app launches the Linux venv directly. No second Windows-side install needed. |
| `chatgpt-desktop` | — | Detection-only. ChatGPT Desktop wires MCP via in-app hosted connectors (SSE), not a local stdio config, so `setup` reports the install and points at the `supergateway` reverse-proxy pattern. |
| `cursor` | `~/.cursor/` | MCP + hooks + always-on rule. |
| `codex` | `~/.codex/` | Codex CLI (TOML config). |
| `copilot` | `~/.copilot/` (`COPILOT_HOME`) | GitHub Copilot on VS Code 1.143+, the Copilot CLI and JetBrains: `mcp-config.json` entry, managed block in `copilot-instructions.md`, and `hooks/imprint.json` (SessionStart, Stop). Detected from the Copilot home, `copilot` on `PATH` or a VS Code install. `imprint learn` reads its sessions. |
| `cline` | `~/.cline/` (`CLINE_DIR`; data in `CLINE_DATA_DIR`) | Cline 4 on every surface (VS Code extension and forks, CLI, JetBrains, desktop): MCP entry in `<data>/settings/cline_mcp_settings.json` (or `CLINE_MCP_SETTINGS_PATH`) and the rule `<CLINE_DIR>/rules/imprint.md`. No hooks; `imprint learn` reads its sessions. |
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

`imprint learn` on its own indexes Claude Code transcripts and auto-memory files, Codex sessions, Cursor agent conversations, GitHub Copilot sessions and Cline sessions. It reads them from this computer's profiles (`CLAUDE_CONFIG_DIR` or `~/.claude`, `CODEX_HOME` or `~/.codex`, `~/.cursor`, `COPILOT_HOME` or `~/.copilot`, and Cline's sessions directory: `CLINE_SESSION_DATA_DIR`, else `<CLINE_DATA_DIR, else CLINE_DIR/data, else ~/.cline/data>/sessions`) and from every client profile registered in WSL. `--desktop` is additive. Neither needs an approved workspace root: the Downloads folders are where exports land, and each conversation's recorded working directory decides which workspace it belongs to. Extra `--path` folders must be inside the workspace's approved roots. See [workspaces](workspaces.md#learning-needs-no-root).

## Install a specific version or channel

The default installer resolves `/releases/latest`, which GitHub maps to the most recent **stable** (non-prerelease) build. Two release channels are published ([ADR 0066](adr/0066-dev-prereleases-from-the-dev-branch.md)):

| Channel | Tag pattern | Produced by |
|---|---|---|
| **stable** | `vX.Y.Z` | conventional-commit release on every merge to `main` |
| **dev** | `vX.Y.Z-dev.N` | prerelease of the next stable version on each push to `dev` that has a `feat`, `fix`, `perf` or breaking change; `N` counts the builds of that version |

Dev builds go through the same packaging and native checks as stable releases. A dev build ranks below the stable release it leads to: `v0.25.0-dev.3` comes after `v0.24.1` and before `v0.25.0`. The dev channel installs the highest version published, prerelease or stable. Once `dev` has been released as stable, that stable release is also the dev channel's latest build, because no prerelease is built for a commit that `main` holds.

Pin a specific release or switch channels via env var or CLI flag.

**Linux / macOS:**
```bash
# Pin to a specific tag (env var)
IMPRINT_VERSION=v0.2.0 curl -fsSL https://raw.githubusercontent.com/alexandruleca/imprint-memory-layer/main/install.sh | bash

# Pin via CLI arg
curl -fsSL https://raw.githubusercontent.com/alexandruleca/imprint-memory-layer/main/install.sh | bash -s -- --version v0.2.0

# Latest dev build
IMPRINT_CHANNEL=dev curl -fsSL https://raw.githubusercontent.com/alexandruleca/imprint-memory-layer/main/install.sh | bash
curl -fsSL https://raw.githubusercontent.com/alexandruleca/imprint-memory-layer/main/install.sh | bash -s -- --dev
```

**Windows (PowerShell):**
```powershell
# Pin to a specific tag
$env:IMPRINT_VERSION = "v0.2.0"; irm https://raw.githubusercontent.com/alexandruleca/imprint-memory-layer/main/install.ps1 | iex

# Latest dev build
$env:IMPRINT_CHANNEL = "dev"; irm https://raw.githubusercontent.com/alexandruleca/imprint-memory-layer/main/install.ps1 | iex

# Or download and pass args directly
irm https://raw.githubusercontent.com/alexandruleca/imprint-memory-layer/main/install.ps1 -OutFile install.ps1
.\install.ps1 -Version v0.2.0
.\install.ps1 -Dev
```

Precedence: CLI flag > `IMPRINT_VERSION` env > `IMPRINT_CHANNEL=dev` env > default stable. The installer downloads that release's archive and verifies it against `SHA256SUMS`.

## Updating an existing install

When a newer stable release exists, the dashboard shows **Update to vX.Y.Z** at the top right. It explains what will happen on this machine, runs the update and reloads the dashboard once the new version is running. The dashboard checks GitHub's releases API at most every six hours while it is open. It never offers prereleases: an installation running a dev build is offered the stable release that follows it, and `imprint update --dev` moves it to newer dev builds.

From a terminal:

```bash
imprint update                 # latest stable (asks for confirmation)
imprint update --dev           # latest dev build (a dev prerelease, or stable once it is newer)
imprint update --version v0.3.1
imprint update --check         # prints current + latest and exits
imprint update --check --json  # the same, as JSON
imprint update -y              # skip confirmation, for scripts / CI
```

Every download is verified against the release's `SHA256SUMS` before anything is installed. An update refuses to start while an ingest or other queued job runs. How it installs depends on how Imprint was installed ([ADR 0023](adr/0023-update-by-installation-type.md)):

| Installed with | Update |
| --- | --- |
| `install.sh` / `install.ps1` | Replaces the install tree in place, re-runs `imprint setup` and restarts the running dashboard server. The previous files in `bin/` are kept as `bin/<name>.prev` for a manual rollback. |
| Windows installer | Runs the new `setup.exe` silently while Imprint keeps running. After provisioning, it restarts the running dashboard server and reopens Imprint; your install profile (llama.cpp's build) and settings are kept. When the release replaces installed packages, setup first stops Imprint's MCP servers, so AI clients reconnect Imprint ([ADR 0084](adr/0084-setup-frees-the-runtime-before-replacing-its-files.md)). The installer log is `data/update-setup.log`. |
| macOS `.pkg` | Opens the new `.pkg` in Installer, which asks for your password, then restarts the running dashboard server. |
| Docker | Pull the new image and recreate the container; the `/data` volume is kept. |
| Source checkout | `git pull` and rebuild. |

**What's preserved:** `data/` (workspaces, Qdrant storage, SQLite graphs, `config.json`, `workspace.json`, `profile.json`) and `.venv/` (Python virtual environment). An in-place update overwrites the release's files and leaves files that newer releases dropped. Installations from before ADR 0023 update once by the old route (installer, install script or `imprint update`) before the dashboard can update them.

The dashboard server is restarted under whoever started it: the desktop app or `imprint ui start`. The desktop app's own window code updates the next time you quit and reopen Imprint. Releases before [ADR 0050](adr/0050-registered-dashboard-server-and-supervised-restart.md) cannot restart their server this way, so after the first update from one of them, reopen Imprint once.

Re-running `install.sh` against an existing install also still works, but it now requires confirmation:

```bash
# Interactive (TTY): prompts "Upgrade existing install? [y/N]"
bash install.sh

# Non-interactive (curl | bash): must opt in explicitly
IMPRINT_ASSUME_YES=1 curl -fsSL .../install.sh | bash
curl -fsSL .../install.sh | bash -s -- --yes
```

### llama.cpp and ONNX Runtime

Setup installs two native runtimes into the venv ([ADR 0086](adr/0086-embeddings-run-on-llama-cpp-without-cuda.md)):

- **llama.cpp's binding** runs embeddings, the local tagger, summaries and chat. The install profile picks its build: the GPU build from `requirements/llm-gpu.txt`, kept when llama.cpp finds a GPU (`llama_supports_gpu_offload()`), otherwise the CPU build from `requirements/llm.txt`. On those profiles setup checks the GPU build on every run, so after installing or updating a graphics driver another `imprint setup` picks it up. Setup fails when the binding does not load. A binding that stops loading later is a damaged installation: chat, the tagger and `imprint doctor` say to run `imprint bootstrap --non-interactive`.
- **ONNX Runtime's CPU build** (`requirements/cpu.txt`) runs OCR and the opt-in privacy filter. ONNX Runtime's builds share one package directory, so setup keeps exactly one: it removes any other build and reinstalls a build with missing files. Rerunning `imprint bootstrap --non-interactive` repairs `module 'onnxruntime' has no attribute 'InferenceSession'`, and `imprint doctor` reports the damage ([ADR 0032](adr/0032-one-onnx-runtime-per-environment.md)).

An installation from a release that embedded through CUDA loses `onnxruntime-gpu` and the NVIDIA libraries only it brought in on its next update or `imprint setup` (`python -m imprint.onnx_runtime unused` names them), and setup clears them from the install's `uv` cache, whose files the environment shares, so their disk space is freed. `onnx` and `ml-dtypes`, which the old CUDA check installed (about 20 MB), stay. Before it replaces or removes packages, setup restarts the dashboard server and, on Windows, stops Imprint's MCP servers; AI clients reconnect Imprint ([ADR 0084](adr/0084-setup-frees-the-runtime-before-replacing-its-files.md)). The new embedding model also changes the embedding profile: reindex into a new workspace ([embeddings](embeddings.md#rebuild-after-upgrading)).

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

## OCR runtime

OCR defaults on for images and scanned PDFs. Bootstrap and update install RapidOCR with its bundled small Chinese/English models, Pillow and PDFium through the hashed base requirements. No separate Tesseract or Poppler install is needed for the default `eng`/`auto` path. Existing explicit OCR opt-outs are preserved. Other `ingest.ocr_lang` values select the Tesseract compatibility backend and still require that system tool and its language data. Run `imprint bootstrap --non-interactive` to repair an older runtime. See [ADR 0076](adr/0076-default-ocr-with-wheel-installed-models-and-renderer.md).
