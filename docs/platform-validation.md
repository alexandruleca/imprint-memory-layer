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

## Native release validation

Follow-up 2026-10-23: release run `39910991520` established successful Linux x64/ARM and macOS ARM native execution, then exposed the Intel ONNX wheel failure and silent Windows installer hang. The obsolete run was cancelled after logs were retained. [Replacement build 39923142177](https://github.com/alexandruleca/imprint-memory-layer/actions/runs/39923142177) is build-only: both macOS installers and both Linux runtime jobs pass, including real default-model inference. Its Windows job now terminates and preserves logs, exposing a separate Windows PowerShell 5.1 native-stderr handling error. That helper path is corrected and covered by `scripts/test-windows-setup.ps1`; subsequent native results are recorded below when complete. See ADR 0012. The following table is the original validation baseline, not the current run status.

The shared release workflow builds installers before publication. Following the main-only packaging decision (ADR 0014), further native installer validation runs on main; branch CI compiles and tests without packaging. [Branch CI 39926994202](https://github.com/alexandruleca/imprint-memory-layer/actions/runs/39926994202) passes on all five native targets, including Windows PowerShell stderr/exit-code regression, Go/Python tests and MCP/API/Qdrant smoke. The frontend/browser and workflow jobs pass too.

| Target | Native check | Current evidence |
| --- | --- | --- |
| Windows x64 | Inno silent install in a path containing spaces, final setup result, MCP/API/Qdrant and default-model store/search | Passed on main in release run 39930880913: bounded silent install, managed runtime and real store/search. |
| macOS Intel | Build/install `.pkg`, writable user runtime, MCP/API/Qdrant and default-model store/search | Passed in replacement builds 39923142177 and 39924894171, using the Intel wheel pin. |
| macOS ARM | Same native package/runtime checks on ARM | Passed in both replacement builds. |
| Linux x64 | Extract archive, bundled-uv bootstrap, MCP/API/Qdrant and default-model store/search | Passed locally and in both hosted replacement builds. |
| Linux ARM | Same archive/runtime checks on native ARM | Passed in both hosted replacement builds. |

Branch CI runs native Go/Python tests and a lighter MCP/API/Qdrant smoke across these targets. Use the manual **Build release artifacts** workflow on main to validate packaging without publishing. Non-main refs are rejected before artifact building. Workflow syntax alone does not establish native execution.

The first remediation CI run exposed two additional native differences: macOS `/var` provenance needed the same canonicalization as scanned roots for missing-source pruning, and the Windows hook execution fixture needed raw `cmd.exe` command-line syntax instead of Go's default CRT argument quoting. Pruning now normalizes both sides with a symlink-boundary regression; the Windows fixture invokes the generated hook using cmd's native quoting. The updated local Python suite passes 59 tests; native rerun results follow below.

Workspace/purge milestone: [CI 39930880817](https://github.com/alexandruleca/imprint-memory-layer/actions/runs/39930880817) passes across all five native targets, dashboard/browser and container checks. [Release 39930880913](https://github.com/alexandruleca/imprint-memory-layer/actions/runs/39930880913) passes all native installer/runtime gates and publishes v0.8.1. [Default-branch security scan 39930881366](https://github.com/alexandruleca/imprint-memory-layer/actions/runs/39930881366) passes with only the documented medium diskcache advisory. These supersede the pending/failure status in the historical narrative. Interactive GUI checks below still remain.

### Portable shared knowledge follow-up

[Main CI 39937355570](https://github.com/alexandruleca/imprint-memory-layer/actions/runs/39937355570), commit `2560116`, passes all five native Go/Python and MCP/API/Qdrant targets, dashboard/browser checks and both container checks. The isolated local suite passes 83 Python tests, Go tests/vet, dashboard lint/export and seven Chromium scenarios. Tests cover device A → B conversation/vector/fact transfer with searchable shared content and unchanged origin, legacy imports, concurrent Go/Python identity creation, approved local mappings and foreign-path pruning exclusion. Windows short paths and macOS `/var` aliases are canonicalized in expected paths.

[Release 39937355749](https://github.com/alexandruleca/imprint-memory-layer/actions/runs/39937355749) passes native installer/runtime gates on Windows x64, macOS Intel/ARM and Linux x64/ARM and publishes [v0.9.0](https://github.com/alexandruleca/imprint-memory-layer/releases/tag/v0.9.0). Each target verifies the 16-tool MCP handshake, API/static dashboard, native Qdrant and actual default-model semantic/hybrid retrieval with filters, IDs and workspace isolation. [Branch CI 39937355045](https://github.com/alexandruleca/imprint-memory-layer/actions/runs/39937355045) also passes while skipping release packaging. The updated documentation builds as 48 pages locally.

The first container run exposed a Next/Google Fonts import failure. Roboto is now a pinned local asset with its OFL license in the static export; the replacement container build and runtime smoke pass. [Security scan 39937924930](https://github.com/alexandruleca/imprint-memory-layer/actions/runs/39937924930) passes on this commit with zero high/critical findings and the same unsuppressed medium diskcache advisory. This establishes automated behavior, not interactive desktop or retrieval-quality certification.

## Installer corrections and remaining checks

macOS: corrected setup-helper path, initialized the Cocoa application, safely quoted Terminal fallback arguments, reused an existing vector logo for icon conversion and kept mutable runtime state outside the app. Still verify Finder/Launchpad launch, native progress UI, Terminal fallback, Dock/tray, Intel/ARM upgrades and uninstall on real desktops. The package remains unsigned; clearing quarantine is not signing/notarization.

Windows: corrected early completion signaling, added final result/error/timeout handling, explicit silent setup and no-LLM choice, preserved repair choices and added venv checks. Hooks no longer depend on unquoted POSIX snippets. Still verify interactive profile selection/progress, interrupted installs, cancellation of descendants, repair, uninstall and upgrade with existing memories. Silent installer completion must be checked using `.setup-result`, not just the installer process exit code.

Linux: archive/bootstrap/dashboard and actual semantic retrieval pass on x64 and hosted native ARM. Graphical Electron startup, tray, FUSE/AppImage and browser fallback still need desktop checks; no local GUI session was available. GPU and OCR profiles remain unverified; optional local helper inference is validated on Linux x64 CPU.

## Dependency audit snapshot

Before compatible updates, the dashboard audit reported 30 advisories (including two critical) and Electron tooling 20 (including one critical). The first audit follow-up still had nine high dashboard advisories through development tooling (`braces`/`micromatch`/`fast-glob`, shadcn/ts-morph and Next ESLint). The 2026-10-23 Trivy remediation removes those unused generator/glob dependency chains, preserves the attributed static styles, and retains explicit lint checks. Dashboard, website and Electron npm audits now each report **zero advisories**. Website dependencies move together to compatible Astro 7/Starlight/MDX/Sharp versions.

The [hosted Trivy remediation scan](https://github.com/alexandruleca/imprint-memory-layer/actions/runs/39924890504) passes with zero high/critical findings. Its branch's GitHub Security results retain only the medium `CVE-2025-69872` in optional diskcache 5.6.3, which has no patched upstream release; disk caching is explicitly disabled and the finding is not suppressed. See [security scanning](security-scanning.md) and ADR 0013. The relay's non-root user and health probe pass the hosted container check, alongside the full runtime container smoke.

Electron 45.0.0 and its locked builder dependencies report **zero advisories** in the current npm audit. Both Linux AppImages build. The desktop macOS floor is explicitly 13, consistent with [Electron's platform changes](https://www.electronjs.org/docs/latest/breaking-changes#removed-macos-12-support) and [ADR 0007](adr/0007-verified-runtime-and-desktop-baseline.md). This does not establish native GUI, Windows or macOS runtime success. Audits are a dated snapshot, not a guarantee against future advisories.

## Release acceptance beyond these smoke tests

Before calling a platform fully supported, retain logs from native jobs and manually verify first launch, client trust/registration, a real search, upgrade preserving memories, repair and uninstall. Signing/notarization, independently authenticated manifests/model revisions and true offline payloads remain open. Release/uv/Qdrant checksum checks and Python profile hash locks are implemented. The installers currently require internet for initial Python, wheels, model and Qdrant downloads.

## Automatic pairing, response budgets and native recovery

The [paired-sync native CI](https://github.com/alexandruleca/imprint-memory-layer/actions/runs/39968166828) passed Go vet/tests/build, all 161 Python tests and MCP/dashboard/native-Qdrant smoke on Linux x64/ARM, Windows and both macOS architectures. Frontend production build, lint and the full browser suite passed; seven local sync browser scenarios additionally cover explicit pairing, pause/resume and confirmed forget. Final control framing also passed pairing, reconnect, unchanged-data and mutual-forget checks through the native Go relay with separate temporary device profiles.

Two complete public studies recorded 360 successful Claude trials and 920 retrieval searches in total. The follow-up's 180 trials, 90 pairs, 460 retrieval searches and 60 explicit boundary checks all passed. Published sanitized rows independently reproduce usage, latency and ranking aggregates; no answer-quality improvement is claimed. The presentation site builds 55 pages, passes five Node adapter/link tests and browser checks at 390/1280 pixels. Its methodology has a separate route and Markdown links resolve to actual documentation pages.

Release [39969601759](https://github.com/alexandruleca/imprint-memory-layer/actions/runs/39969601759) built all desktop payloads and passed Windows, Apple Silicon macOS and both Linux package/runtime gates, but failed the Intel macOS runtime store. A leftover embedded-era idle timer closed an HTTP client during cold embedding work. This is a real application race, not evidence that the package passed. The isolated regression reproduces the failure before the fix; ADR 0022 retains HTTP clients until cleanup and exercises slow single, batch and query operations. Corrected-source native rebuild evidence is pending below; do not treat the previous failed artifact as validated.

Manual graphical installation/upgrade/uninstall, two physical devices and public-relay reconnection were not newly executed for these changes; automated native runners and isolated relay profiles provide the recorded evidence. Client profiles and existing user memories were not used as test fixtures.
