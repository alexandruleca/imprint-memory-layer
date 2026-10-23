---
title: Platform validation
description: Executed checks, installer defects addressed and the remaining native platform validation matrix.
---

## Environment and limits

Recorded 2026-10-23. Local host: Linux x64 under WSL2, Node 25.9.0; Go 1.25.0 and uv 0.5.11 were downloaded into a temporary tools directory for verification. A managed Python 3.12.15 environment was created separately. No release was published and no contributor client profile was used as a setup test.

The initial GitHub history showed successful recent release jobs and the expected `v0.8.0` assets. Historical failed-run logs were expired. Source review and executable tests below establish the findings; they do not reconstruct the user's exact historical macOS/Windows error.

## Executed locally

| Check | Result | What it establishes |
| --- | --- | --- |
| `go test ./...`, `go vet ./...` | Passed | Existing tests plus hook quoting, configuration preservation/idempotence, checksum and Go/Python lock interoperability on Linux. |
| `make all` | Passed | Go CLI cross-compiles for Linux amd64/arm64, Darwin amd64/arm64, Windows amd64. This is not native execution. |
| Managed Python + base lock install, dependency check | Passed | A clean Python 3.12 CPU runtime resolves and imports. Qdrant client range was corrected after reproducing a compatibility warning. |
| Python unittest suite | Passed, 39 tests | Index replacement/fingerprints/deletions/empty and large files, stale caches, MCP durability/bounds/workspaces, doctor/startup locking, checksums, hooks, Host/Origin and process safety. Windows-specific execution awaits CI. |
| Electron navigation tests | Passed, 3 tests | Exact origin validation, loopback restriction and external protocol allowlist. |
| Dashboard production build | Passed after Next 16.4.2 update | Fresh static export and TypeScript compilation. |
| Dashboard lint | Passed, no errors/warnings | Resolved the previous 14 errors/four warnings; CI now gates lint. |
| Playwright Chromium | Passed, 4 scenarios | Mobile search/filters/modal/focus/shortcut, failure/retry, stale response cancellation and reconnect. API responses are mocked for interaction tests. |
| `make ui-dist-linux ARCH=amd64` and `ARCH=arm64` | Passed | Both Electron 45.0.0 AppImages build and are staged separately. ARM build is not ARM execution. |
| Electron ASAR inspection | Passed | `preload.js` and navigation module are in the packaged app. |
| Linux x64/ARM archive packaging and validator | Passed | Runtime payloads and matching executable architectures; staging remains available. |
| Packaged x64 CLI bootstrap with CPU/no-LLM | Passed | Bundled uv provisions managed Python/base dependencies and recognizes the static dashboard. Shell integration was disabled for the test with an unsupported test shell; real profiles were untouched. |
| MCP/API/storage smoke | Passed | Fourteen tools discovered with annotations/schema bounds, health and static HTML served, remote Origin rejected, native Qdrant downloaded/started and point written/read. |
| Default-model semantic store/search | Initially failed, then passed after fix | Reproduced ONNX external-weight cache-path failure. Real model provisioning and ONNX inference pass with regular local files. Follow-up native Qdrant tests pass semantic/hybrid search, exact identifiers, project/source filters, public IDs and workspace isolation. |
| Bash syntax, Python compilation, workflow actionlint, diff whitespace | Passed | Syntax/static checks, including the shared release workflow. |
| Full Linux x64 container | Passed | Fresh dashboard, Python 3.12 hashed CPU install, real entry-point HTTP/static/Host checks, Go doctor and stdio handshake; verified native Qdrant download/start/write/read. ARM container not executed. |
| Astro documentation site build | Passed | Engineering pages, remediation/UX/retrieval reviews and eight ADRs render through the docs symlink (36 pages). |

## Qwen model adoption follow-up

Executed on Linux x64/WSL2 with managed Python 3.12.15, pinned CPU dependencies plus `llama-cpp-python==0.3.36`, and temporary Go 1.25.3/uv 0.5.11. Tests used disposable data directories and loopback ports. Existing user memories and client profiles were not changed.

| Check | Result | Scope |
| --- | --- | --- |
| Python unittest suite | Passed, 58 tests | Adds Qwen templates/padding/empty decoder caches/output projection, finite vectors, profile mismatch, forced reindex, sync IDs, snapshot rejection, queue flags, helper sharing/schema options and context pruning that preserves the current request. |
| `go test ./...`, `go vet ./...` | Passed | Includes flag preservation and subprocess regression proving index failures exit nonzero. |
| Hashed base/LLM lock installation and dependency check | Passed | Builds the pinned optional binding on Linux CPU with two compiler jobs; checks compatibility of all 135 installed packages. |
| Real pinned Qwen q8 ONNX inference | Passed | Default 2048-token runtime cap, 1024-dimensional finite normalized query/doc vectors and a tiny relevant/unrelated document check. Exposed and fixed required decoder KV inputs. |
| Native Qdrant 1.17.1 + real embeddings | Passed | Semantic/hybrid store/search, persisted collection identity, equal-dimension mismatch rejection and same-profile sync into a new workspace. Sync exposed and fixed a note-versus-chunk-zero ID duplication. |
| `scripts/smoke-local-helper.py --model-path ...` | Passed | Real pinned Qwen3.5-4B Q4_K_M, CPU, 8K context; shared chat/tagger instance, closed thinking template, schema classification, structured summary, status tool/result/answer round trip. Tool storage response is mocked. Initial classification confusion led to clearer type definitions; this is one functional case, not a precision/recall score. |
| Astro site build and diff/syntax checks | Passed | 40 pages include model analysis, helper instructions, rebuild procedure and ADR 0010. |

This is functional CPU validation, not a retrieval-quality comparison, helper benchmark or peak-memory guarantee. Native Windows/macOS/ARM/GPU execution of these new model defaults remains pending. Existing release native gates will exercise default ONNX store/search when run; the optional helper smoke is opt-in and has not been added to every installer job. Multi-process model sharing and transactional model migration remain proposed work in ADR 0009.

## Native release gates added, not executed here

The shared release workflow now builds installers before publication and runs the following checks in disposable hosted runners:

| Target | Native check | Current evidence |
| --- | --- | --- |
| Windows x64 | Compile Inno installer, silent install in a path containing spaces, verify final setup result, initialize MCP/API/Qdrant and perform default-model store/search | Workflow implemented; Windows execution pending. |
| macOS Intel | Compile package/helper, install `.pkg`, provision writable user runtime, initialize MCP/API/Qdrant and default-model store/search | Workflow implemented; Intel macOS execution pending. |
| macOS ARM | Same native package/runtime checks on ARM | Workflow implemented; ARM macOS execution pending. |
| Linux x64 | Extract archive, bootstrap with bundled uv, initialize MCP/API/Qdrant and default-model store/search | Local equivalents passed; hosted workflow execution pending. |
| Linux ARM | Same archive/runtime checks on native ARM | Build/architecture checks passed locally; native execution pending. |

PR CI additionally runs native Go/Python tests and a lighter MCP/API/Qdrant smoke across these targets. Use the manual **Build release artifacts** workflow to validate the changed release pipeline without publishing once the branch is available on GitHub. Workflow files passing actionlint do not establish that their native jobs have run.

## Installer corrections and remaining checks

macOS: corrected setup-helper path, initialized the Cocoa application, safely quoted Terminal fallback arguments, reused an existing vector logo for icon conversion and kept mutable runtime state outside the app. Still verify Finder/Launchpad launch, native progress UI, Terminal fallback, Dock/tray, Intel/ARM upgrades and uninstall on real desktops. The package remains unsigned; clearing quarantine is not signing/notarization.

Windows: corrected early completion signaling, added final result/error/timeout handling, explicit silent setup and no-LLM choice, preserved repair choices and added venv checks. Hooks no longer depend on unquoted POSIX snippets. Still verify interactive profile selection/progress, interrupted installs, cancellation of descendants, repair, uninstall and upgrade with existing memories. Silent installer completion must be checked using `.setup-result`, not just the installer process exit code.

Linux: archive/bootstrap/dashboard and actual semantic retrieval work on the local x64 host. Test graphical Electron startup, tray behavior, FUSE/AppImage behavior and browser fallback on supported desktop distributions. No GUI session was available here. Native ARM, GPU and OCR profiles remain unverified; the Qwen follow-up above validates the local helper on Linux x64 CPU.

## Dependency audit snapshot

Before compatible updates, the dashboard audit reported 30 advisories (including two critical) and Electron tooling 20 (including one critical). The current dashboard lock reports **nine high advisories** through development tooling (`braces`/`micromatch`/`fast-glob`, shadcn/ts-morph and Next ESLint). The reported automatic fixes involve incompatible downgrades; no safe upstream `braces` release was selected. Do not suppress this evidence or claim zero risk for a static export.

Electron 45.0.0 and its locked builder dependencies report **zero advisories** in the current npm audit. Both Linux AppImages build. The desktop macOS floor is explicitly 13, consistent with [Electron's platform changes](https://www.electronjs.org/docs/latest/breaking-changes#removed-macos-12-support) and [ADR 0007](adr/0007-verified-runtime-and-desktop-baseline.md). This does not establish native GUI, Windows or macOS runtime success. Audits are a dated snapshot, not a guarantee against future advisories.

## Release acceptance beyond these smoke tests

Before calling a platform fully supported, retain logs from native jobs and manually verify first launch, client trust/registration, a real search, upgrade preserving memories, repair and uninstall. Signing/notarization, independently authenticated manifests/model revisions and true offline payloads remain open. Release/uv/Qdrant checksum checks and Python profile hash locks are implemented. The installers currently require internet for initial Python, wheels, model and Qdrant downloads.
