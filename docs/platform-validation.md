---
title: Platform validation
description: Executed checks, installer defects addressed and the remaining native platform validation matrix.
---

## Environment and limits

Windows CI follow-up, 2026-10-28: the native suite exposed open handles on
`project-associations.sqlite3` during temporary-source cleanup. SQLite's
transaction context committed/rolled back without closing the connection.
The association database now closes explicitly on success and exceptions;
a regression checks closure and rollback separately. Native Windows revalidation
is required before promotion to main.

Follow-up avatar assets, 2026-10-28, Linux/WSL: after the combined commit, the
additional iris-brightness/core-blink edits and regenerated GLBs passed generator
syntax/lint, the production dashboard build and all 32 Voice/setup browser tests.

Combined change-set validation before publication, 2026-10-28, Linux/WSL:
the full Python suite passed 681 tests (one real-model fixture skipped), all
137 dashboard Playwright scenarios passed, and all 59 Electron/extension/site
Node tests passed. Full dashboard lint, Go tests and vet, dependency consistency
and release-versioning regressions passed. The disposable runtime smoke verified
native audio imports, stdio MCP discovery, dashboard origin checks and a native
Qdrant download/startup/write/read. The production dashboard was rebuilt from
source. These local results do not establish native Windows/macOS behavior;
the branch CI and release jobs perform those checks on their native runners.

Progress-aware Chat research, 2026-10-28, Linux/WSL: 108 Python tests passed
across research budgets, local/remote chat protocols, section analysis, tool
formats, voice and ADR structure. Twenty-one Chat/Settings browser tests passed,
including all three research controls and the normal completion notice. The
production dashboard build, TypeScript and targeted ESLint checks passed.
Tests use fake model/provider responses and clocks to cover research beyond
12 rounds, alternating cycles, changing errors, worker token charging, time
overruns, skipped pending mutations and reserved synthesis. Native answer
quality and live provider billing were not measured. See
[ADR 0102](adr/0102-chat-research-follows-progress-and-resource-budgets.md).

Chat research completion, 2026-10-28, Linux/WSL: local/remote stub-provider,
section-analysis, tool-format and voice tests passed, with additional temporary
workspace tests covering the distinct conversation-inbox environment override.
Twenty-one Chat/Settings browser tests passed, including the research-completion
notice with a visible final answer and editing the numeric research budget.
The production dashboard build, TypeScript and targeted ESLint checks passed.
Native model answer quality and live cloud-provider calls were not tested for
this change. See [ADR 0101](adr/0101-chat-research-always-reserves-an-answer.md).

Project association validation, 2026-10-28, Linux/WSL: 93 targeted Python tests
passed across associations, canonical sources, moves, workspace ownership,
purge, queue dispatch/deduplication, audio ingestion and ADR structure. Nineteen
browser tests passed across association review, projects, settings and source
reading. The production dashboard build, targeted ESLint checks and Go command
tests passed. Local model responses were stubbed for evidence-validation tests;
native project-classification quality and Windows/macOS execution remain
unverified. See [ADR 0099](adr/0099-sources-can-belong-to-several-projects.md)
and [ADR 0100](adr/0100-explicit-workspace-inbox-for-unassigned-conversations.md).

Recorded 2026-10-23. Local host: Linux x64 under WSL2, Node 25.9.0; Go 1.25.0 and uv 0.5.11 were downloaded into a temporary tools directory for verification. A managed Python 3.12.15 environment was created separately. No release was published and no contributor client profile was used as a setup test.

The initial GitHub history showed successful recent release jobs and the expected `v0.8.0` assets. Historical failed-run logs were expired. Source review and executable tests below establish the findings; they do not reconstruct the user's exact historical macOS/Windows error.

## Executed locally

### Chrome preview ZIP and website — 2026-10-23

The follow-up preview keeps Gmail, PDFs, attachments and Google Docs/Sheets
marked **Coming soon**. The extension now offers page/selection capture only.
Three standard-library packaging regressions passed: exact runtime-only file set
(including exclusion of injected credential/dependency fixtures), deterministic
ZIP bytes across source mtimes, correct checksum, and preservation of an existing
artifact when a required source file is absent. Four extension transport/sender
checks and seven website evidence/download-selection/link tests passed.

The actual generated `dist/imprint-chrome-extension.zip` was extracted and loaded
in disposable Linux Chromium. Pairing, selected-text save/status and credential
isolation passed; Gmail and Google Docs were rejected with coming-soon messages.
The Astro production build passed. Desktop (1440px) and mobile (375px) screenshots
were inspected and the mobile page had no horizontal overflow. Workflow lint
passed for the updated release and CI workflows. This validates local Linux
packaging/browser behavior; interactive macOS/Windows Chrome installation remains
unverified. Hosted follow-up results are recorded below. See [ADR 0030](adr/0030-chrome-extension-release-zip.md).

The first hosted ZIP gate caught a settings startup race: a typed API address
could be overwritten when saved settings finished loading. Connection controls
now stay disabled until initialization completes. The browser regression holds
the settings response pending, verifies disabled inputs, releases initialization
and then connects to its fixture. A delayed-startup fixture reproduced the
original release failure locally before the fix. Archive/native packaging now
also waits for the Chrome ZIP gate, avoiding downstream work when it fails.

Final source `5fe4118` passed the complete [dev CI](https://github.com/alexandruleca/imprint-memory-layer/actions/runs/40003625421)
and [production CI](https://github.com/alexandruleca/imprint-memory-layer/actions/runs/40003630925),
including native Linux x64/ARM, macOS Intel/ARM and Windows backend checks. The
[v0.18.0 release](https://github.com/alexandruleca/imprint-memory-layer/releases/tag/v0.18.0)
passed [release validation](https://github.com/alexandruleca/imprint-memory-layer/actions/runs/40003631152)
and published the Chrome ZIP with the complete native installer/archive set.
The public 14,157-byte ZIP contains 12 runtime/guide/license files, matches the
hosted Chromium-tested artifact byte for byte, and verifies against the release's
`SHA256SUMS`. The [post-release Pages deployment](https://github.com/alexandruleca/imprint-memory-layer/actions/runs/40005752646)
passed; the public Chrome page's download points to that exact v0.18.0 asset and
shows the install guide and seven Coming soon labels. Native installer/backend
validation does not replace interactive Chrome validation on macOS/Windows.

### Chrome capture WIP — 2026-10-23

On `wip/chrome-extension`, Linux x64/WSL2 with Python 3.12.15 and Node 25.9.0:
14 browser API/worker/storage regressions passed, including token rotation,
workspace/job isolation, exact origins/preflights, peer/Host rejection, bounded
inputs, account-aware Gmail identities, revoked-queue failure via the actual
dispatcher/subprocess, and in-memory Qdrant source replacement/failure preservation
with deterministic embedding stubs. Existing HTTP boundary (10), indexing/retrieval
(21), queue lock (1) and locale/dispatcher (2) regressions also passed.
The final full Python discovery run passed all 215 tests in 31 seconds.

Four extension transport/sender Node checks and the two existing documentation
link checks passed. The headless Chromium smoke loaded the actual unpacked
extension in a disposable profile, paired it against a synthetic local API,
extracted rendered article/selection/Gmail fixtures, sent a capture and read
job status. Injection into a local fixture verified that content scripts cannot
read trusted credential storage or use the privileged message bridge. Locally
Chromium used the already extracted libraries at
`/tmp/imprint-browser-libs/root/usr/lib/x86_64-linux-gnu` through
`LD_LIBRARY_PATH`; no system package installation or real browser/client profile
was used. CI is configured for the same browser smoke after its normal
Playwright Chromium install; that CI run has not been observed yet.

These checks do not establish live Gmail selector compatibility, full-model
capture quality, Store approval, native Windows/macOS Chrome execution, real
toolbar gesture behavior, or PDF/attachment/editor-export support. See
[browser extension setup and limitations](browser-extension.md) and
[ADR 0029](adr/0029-paired-browser-capture.md).

### Existing subsystem checks

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

## Local service network boundaries — 2026-10-23

Validated on Linux x64/WSL2 with Python 3.12.15, disposable data directories and ephemeral ports; see [ADR 0024](adr/0024-enforce-local-service-boundaries.md). The full Python suite passed 156 tests; the final focused security run passed 14 tests after adding the public-launcher rejection case. Runtime smoke passed the 16-tool stdio MCP handshake, dashboard/static serving, Origin rejection and actual Qdrant write/read using the cached pinned native binary.

Native socket inspection confirmed that managed Qdrant HTTP/gRPC listened only on `127.0.0.1`, with no cluster listener even when inherited configuration requested one. Both ports refused connections through the machine's non-loopback interface; the HTTP response omitted wildcard CORS. The API's normal loopback listener answered local HTTP and refused non-loopback TCP. A deliberately public direct uvicorn listener with proxy headers disabled rejected non-loopback requests carrying forged local Host, Origin and X-Forwarded-For headers on API, static and documentation routes.

The separately managed local Ollama service was moved from its bridge address to `127.0.0.1:11434` through a persistent systemd user override. After restart, `/api/tags` returned 200 locally and the old address refused TCP connections. This verifies binding and availability, not model inference. The separate Naming AI native-client endpoint was updated; its stopped containerized API/worker still need a secured connection arrangement to reach a host loopback service.

The full Docker image rebuilt successfully from source. `scripts/smoke-container.sh imprint-loopback-check:local` passed in disposable containers, including actual host-loopback publication/NAT access, dashboard/static serving, remote Origin and Host rejection, doctor/MCP discovery and native Qdrant download/start/write/read. Container mode still requires trusted internal networking and loopback publication; these checks do not establish an authenticated remote mode.

Native Windows/macOS/ARM execution of these changes remains pending. Existing historical native release results below do not validate this follow-up.

## Desktop startup and service ownership — 2026-10-23

See [ADR 0025](adr/0025-desktop-owned-background-service.md). Linux x64/WSL2 validation used disposable data directories and separate build output; existing memories, client profiles, `bin/` and dashboard export output were not changed.

| Check | Result | Scope |
| --- | --- | --- |
| Go tests/vet | Passed | Includes a native executable fixture proving `ui open` launches the shell before provisioning an API daemon and passes the resolved writable Python path. |
| Electron Node tests | Passed, 14 tests | Navigation boundaries, hidden/logged spawn, lifetime-pipe shutdown, splash-before-probe ordering, packaged/development arguments, hide/reopen, explicit Quit, independent daemon reuse, startup cancellation, crash recovery and missing runtime. Window/process doubles do not establish native GUI behavior. |
| Python suite and lifetime tests | Passed, 160 tests in the full run | Actual loopback API stays up while the parent pipe is open and releases its port after EOF; a stalled process exits within its shutdown bound. A subsequent focused three-test release-validator run also passes, including the new required desktop entry point. |
| Dashboard lint and isolated production build | Passed | New desktop Quit action and hide label compile; generated output stays outside the repository. |
| Playwright Chromium | Passed, two desktop scenarios | Hide and Quit use distinct bridge calls; browser-only dashboards have neither desktop control. Preload bridge is mocked. |
| Splash/recovery rendering | Passed | Local startup page renders under its CSP without browser exceptions; loading and retry/log states inspected at 520×360. This is Chromium rendering, not a native Electron launch. |
| Linux x64 Electron directory package and ASAR inspection | Passed | Packaged app includes `service.js`, preload/navigation and all three startup assets; graphical launch was not exercised. |
| Native Windows PowerShell 5.1 launcher fixture | Passed via WSL interoperability | A disposable ready install directly launches a native GUI executable fixture with source/data/Python paths preserved, including spaces and an apostrophe. The fixture was cross-compiled, then actually executed on Windows; no real client setup ran. This does not exercise Electron or prove absence of console flashes. |
| Workflow validation and macOS launcher syntax | Passed | Native CI now includes these regressions on five targets and the Windows launcher fixture; release ASAR checks require startup assets. |

Before pushing, the change was rebased onto current main (`b92f0e8`), preserving the update dashboard and automatic-sync work. Combined validation passed 191 Python tests, Go tests/vet, 14 Electron tests, dashboard lint and an isolated production build, workflow lint, and six Chromium desktop/update scenarios. ADR numbers were advanced to 0024/0025 to preserve the intervening decisions.

Interactive Windows/macOS/Linux desktop execution of the updated shell, console visibility, tray/Dock reopening, quitting with active jobs, app-crash recovery and upgrades remains pending. Windows/macOS Python lifecycle execution awaits the native CI jobs. A pre-existing CLI daemon intentionally survives desktop Quit; shared Qdrant also remains independent. First-run bootstrap still uses the existing setup UI, and OS/portable-executable startup can precede the Electron splash.

### Native Windows finding — desktop service never started

The first native Windows CI run of the lifetime test timed out with an empty service log. A Windows-runner diagnostic dumped the stalled service: the parent-watch thread was blocked reading standard input while the main thread hung importing numpy's extension module. Without that reader the same service answered in 2.9 s. Windows serializes I/O on a pipe a thread is blocked reading, and a DLL whose C runtime queries the standard handles waits on it. The desktop service therefore never started on Windows; any later DLL load would have hung the same way.

The service now moves the lifetime pipe to a private descriptor and points standard input at the null device before reading it, which also stops child processes from inheriting the pipe. A regression test blocks a reader on the detached pipe while importing numpy. The same Windows runner diagnostic then confirmed the service answers and exits on pipe close.

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

macOS: corrected setup-helper path, initialized the Cocoa application, safely quoted Terminal fallback arguments, reused an existing vector logo for icon conversion and kept mutable runtime state outside the app. Still verify Finder/Launchpad launch, native progress UI, Terminal fallback, Dock/tray, Intel/ARM upgrades and uninstall on real desktops. The package is not Developer ID signed or notarized (the desktop app inside is signed ad hoc, ADR 0055); clearing quarantine is not signing/notarization.

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

Two complete public studies recorded 360 successful Claude trials and 920 retrieval searches in total. The follow-up's 180 trials, 90 pairs, 460 retrieval searches and 60 explicit boundary checks all passed. Published sanitized rows independently reproduce usage, latency and ranking aggregates; no answer-quality improvement is claimed. The presentation site builds 56 pages, passes five Node adapter/link tests and browser checks at 390/1280 pixels. Its methodology has a separate route and Markdown links resolve to actual documentation pages.

Release [39969601759](https://github.com/alexandruleca/imprint-memory-layer/actions/runs/39969601759) built all desktop payloads and passed Windows, Apple Silicon macOS and both Linux package/runtime gates, but failed the Intel macOS runtime store. A leftover embedded-era idle timer closed an HTTP client during cold embedding work. This is a real application race, not evidence that the package passed. The isolated regression reproduces the failure before the fix; ADR 0022 retains HTTP clients until cleanup and exercises slow single, batch and query operations. The corrected-source native rebuild passed as recorded below; the failed artifact was not published.

Manual graphical installation/upgrade/uninstall, two physical devices and public-relay reconnection were not newly executed for these changes; automated native runners and isolated relay profiles provide the recorded evidence. Client profiles and existing user memories were not used as test fixtures.

The corrected source passed [CI 39972913971](https://github.com/alexandruleca/imprint-memory-layer/actions/runs/39972913971) on all five native targets, including all 162 Python tests, the complete frontend checks and both container smokes. A separate Linux check used the real Qwen model and Qdrant, deliberately held embedding for 35 seconds, then durably stored and retrieved the note successfully. Source-level benchmark audits also verified that all 612 filesystem tool calls in the first study and 622 in the follow-up stayed within their frozen public corpus.

The [corrected release run 39972914308](https://github.com/alexandruleca/imprint-memory-layer/actions/runs/39972914308) passed every native package/runtime gate, including Intel macOS, and published [v0.13.0](https://github.com/alexandruleca/imprint-memory-layer/releases/tag/v0.13.0) on 2026-10-23 at 15:10 UTC. Its nine release assets include both macOS `.pkg` installers, the Windows setup installer, all five platform archives and `SHA256SUMS`. The successful rebuild validates the HTTP-client lifetime correction on native installed runtimes rather than merely retrying the old failure.

## Windows installer → WSL clients — 2026-10-23

[ADR 0026](adr/0026-windows-installer-wsl-client-bridge.md) adds explicit per-profile registration of WSL Claude Code/Codex against the Windows runtime. Checks used disposable Linux profiles and Windows data; an existing installed Windows Python 3.12.8 interpreter/dependencies were reused read-only. No real Claude/Codex profile was registered or edited, and no model inference/real memory store was used.

| Check | Result | Scope |
| --- | --- | --- |
| Go tests and vet on Linux | Passed | Enumeration decoding, explicit distro arguments, profile/settings/server/hook/rule preservation, idempotence, removal, quoting, malformed TOML and foreign launcher protection. |
| Native Windows Go WSL fixtures | Passed | Windows-compiled tests actually executed through WSL interoperability, including native Windows configuration-file replacement and backups. Cross-compilation was followed by execution. |
| Python unittest suite | Passed, 199 tests | Includes path mapping, custom drive mount roots, source-key preservation, unchanged workspace approval, metadata-only hook translation and preventing all Stop consumers for an unapproved transcript. Final release-validation fixtures additionally pass after asserting selection backups are excluded. |
| Real WSL → native Windows runtime | Passed | Both clients' disposable registrations run twice without losing other servers, then are removed. Actual stdio initialize/list-tools discovers 16 tools; device context retains the Linux working directory and reads the same Windows workspace-root store written through the CLI bridge. |
| Linux and Windows-drive project roots | Passed | Real handshakes from Linux files and a mounted Windows drive, root approval and round-trip path hints; a profile stored on a Windows drive also registers/removes. Spaces, Unicode and apostrophes are present in fixture paths. No assumption that drive mounts are exported through the WSL UNC provider. |
| Native Windows PowerShell 5.1 setup regression | Passed | Successful setup replays two saved distro/target choices with spaces; stderr remains progress and nonzero failures propagate. Test fixtures run from a native temporary directory and retain the process handle so ExitCode is dependable. |
| Native Inno Setup 6.7.3 compilation | Passed | Actual installer and new WSL selection page compile with a disposable release payload. The official compiler was run in portable mode from Windows temporary storage. This is compilation, not an interactive installer walkthrough. |
| Documentation website build | Passed, 61 pages | New ADR, setup/removal instructions and engineering links render with the normal generated benchmark/release data. |
| Electron regression suite/workflow lint/diff whitespace | Passed | Existing 14 desktop tests stay green; native CI discovers the new Go/Python tests and its existing setup fixture now covers saved WSL choices. |

The interop smoke is opt-in at `scripts/test-wsl-interop.py`; hosted CI does not assert that a WSL distribution is installed. Native GUI selection/progress, client reload/trust and full conversation ingestion against live Claude Code/Codex versions still need manual checks. Codex transcript coverage remains the current adapter's limitation. Custom users/profiles require explicit flags and must be accessible through the Windows account's WSL filesystem provider; registration does not change their permissions or elevate privileges. WSL interoperability disabled, inaccessible distributions or unreadable profiles fail selected registration rather than report success.


## Symmetric uninstall milestone (2026-10-23)

[ADR 0027](adr/0027-symmetric-uninstall-and-branch-sync.md) connects native uninstall to shared disable cleanup and preserves memories by default. Tests used private profiles/data and a copied CLI in a private Windows install tree. Existing installed Windows Python was reused read-only for WSL bridge probes; real client configurations and memory stores were not used as fixtures.

| Check | Result | Scope |
| --- | --- | --- |
| Linux Go tests and vet | Passed | Supported JSON/TOML registries, nested OpenClaw entries, Claude/Codex/Cursor hooks, permissions/rules, unrelated settings, repeated cleanup, malformed configuration, foreign runtimes, shared Cline rules, shell aliases/symlinks and legacy/custom WSL selection coexistence. |
| Native Windows Go cleanup fixtures | Passed | Actual Windows execution of the compiled cleanup/profile tests and durable config updates. The symlink portion is skipped because this Windows account lacks symlink privilege; alias assertions execute, and Linux exercises symlink ownership. |
| Native Windows PowerShell 5.1 helper | Passed | Successful stderr diagnostics, propagated exit 23 and missing CLI; the helper invokes disable and preserves fixture memories. |
| Native Windows + actual WSL cleanup | Passed | `scripts/test-wsl-uninstall.py` registers disposable custom Claude/Codex profiles from a copied private Windows CLI. Native disable removes both WSL bridges plus native entries/hooks/permissions, preserves other config and remains idempotent. Invalid native TOML blocks uninstall and preserves a runtime sentinel; retry succeeds and retains a memory sentinel with default flags. |
| Native Inno Setup 6.7.3 | Passed | Current installer compiles. An isolated installer with a unique application ID/name and executable stand-ins runs install, failed cleanup, then successful uninstall: failure keeps program files; success removes them and retains a data sentinel. PATH logic is redirected to a private fixture registry key; unrelated user PATH remains untouched. |
| Python suite | Passed, 201 tests | Includes release exclusion of journals/backups/logs and an actual Git fast-forward/divergence fixture for the dev synchronization script. |
| Documentation/workflow checks | Passed | Website builds 62 pages; actionlint, shell syntax and diff whitespace checks pass. |

Windows release CI now also uninstalls its disposable real installation and checks preserved data plus Codex registration/hook/rule removal. This new hosted release gate is implemented; the local stand-in installer and copied-CLI checks above are the executed evidence. Native macOS `.command`/package uninstall and interactive OS/client reload behavior remain unexecuted locally; macOS script syntax is checked without claiming native execution. Linux cleanup runs natively in WSL; an interactive AppImage desktop uninstall was not exercised. Old custom WSL profiles created before journaling require explicit removal with their original flags; custom native profiles require their profile environment. Configuration recovery backups remain inert.

The first hosted Windows uninstall regression completed its assertions but inherited exit 1 from its intentional missing-CLI case. The fixture now clears that asserted native status before returning to GitHub Actions; an Actions-style native PowerShell wrapper is used locally to verify the script returns success. This changes test-runner reporting, not cleanup behavior.

## ONNX Runtime provisioning and first-run log (2026-10-24)

Triggered by a Windows GPU install that aborted on a `first-run.log` sharing violation and later failed every ingest with `module 'onnxruntime' has no attribute 'InferenceSession'`. See [ADR 0032](adr/0032-one-onnx-runtime-per-environment.md).

| Check | Result | Notes |
| --- | --- | --- |
| First-run log sharing (native Windows PowerShell 5.1) | Passed | `Out-File -Append` fails against a share-read poller (Inno `LoadStringFromFile`, the original wizard) and never against a read/write-sharing one. `scripts/test-windows-setup.ps1` now polls concurrently and checks UTF-8 decoding and the absence of ANSI escapes; each new check fails on the defect it guards. |
| Wizard page compile (Inno Setup 6 ISCC) | Passed | The real `ProfilePage.iss`, `WSLPage.iss` and `SetupOutputPage.iss` compile in a minimal wrapper script. A full installer was not built. |
| `imprint bootstrap` end to end (Linux x64, uv 0.5.11, locked wheels) | Passed | Scratch install tree with an isolated HOME and a fake `nvidia-smi`. Cases: fresh CPU install, the damaged Windows state (GPU dist-info with only `capi/`), unchanged rerun (audit only), auto→CPU, CPU→GPU, both builds installed, auto without a GPU, damaged CPU build. Each ended with one complete provider and a loadable `InferenceSession`. |
| Lock split | Passed | ONNX Runtime and its exclusive dependencies moved from `base.lock.txt` to `cpu.lock.txt` with identical pins, markers and hashes; `llm.lock.txt` unchanged. pip 24.3 accepts the identical hashed pins repeated across base and overlay. |
| Go, Python, workflow | Passed | `go test ./...`, full Python suite (232 tests), actionlint. |

Not run: native Windows or macOS bootstrap with the new layout, and any real CUDA session. The locked `onnxruntime-gpu` 1.31.0 links CUDA 13, while setup's CUDA retry targets CUDA 12.

## GPU runtime with bundled CUDA 13 (2026-10-24)

See [ADR 0038](adr/0038-gpu-runtime-ships-cuda-libraries.md).

| Check | Result | Notes |
| --- | --- | --- |
| GPU lock on native Windows (RTX 4080 Laptop, driver 581.80) | Passed | `gpu.lock.txt` installs with `--require-hashes` into a fresh venv in 39 s (1.86 GB). The setup smoke script prints `OK`; a 512×512 MatMul runs on `CUDAExecutionProvider` (onnxruntime-gpu 1.31.0). |
| `imprint bootstrap` on Linux (WSL2, same GPU) | Passed | A pre-0038 "broken" verdict is retried and passes; a driver reporting CUDA 12.8 makes `auto` and `gpu` install the CPU runtime with the driver message; the real driver switches `auto` back to the GPU runtime. |
| First-start collection race (native Windows, scratch Qdrant 1.17.1) | Passed | 8 threads × 10 fresh collections: the previous code failed 48 of 80 calls with Qdrant's "0 of 0 read operations failed" 500; the locked creation succeeded on all 80, one create per collection. |

Not run: Linux aarch64 GPU hosts, and pre-R580 drivers on real hardware.

## CUDA only for GPU embedding (2026-10-25)

See [ADR 0062](adr/0062-cuda-only-for-gpu-embedding-on-a-supported-nvidia-gpu.md). Machine: RTX 4080 Laptop (CUDA 0, compute 8.9) and GTX 1060 (CUDA 1, compute 6.1), driver 581.80, Windows 11 25H2, plus WSL2 Ubuntu. Every Windows run used scratch trees and venvs under `%TEMP%`, with an isolated `USERPROFILE`; the installed runtime was only read.

| Check | Result | Notes |
| --- | --- | --- |
| Libraries the CUDA provider needs (native Windows) | Passed | Without cuFFT, cuRAND and nvJitLink, Qwen3-Embedding-0.6B int8 and BGE-M3 fp16 ran on `CUDAExecutionProvider` (batches 1 and 4, up to 1,240 tokens; BGE-M3 0.24 s for 4 on CUDA against 14.7 s on CPU). Without cuDNN the session started and the first attention node failed. |
| Libraries the CUDA provider needs (Linux, WSL2) | Passed | The same with cuRAND kept: `libonnxruntime_providers_cuda.so` links `libcurand.so.10`, and without it the provider falls back to CPU. |
| New CUDA check (native Windows) | Passed | `python -m imprint.gpu_runtime check` passed on the trimmed set; without cuDNN it failed on the Conv node, where the old Identity check would have passed. |
| Replacing a loaded runtime (native Windows) | Passed | uv uninstalled and reinstalled ONNX Runtime, and installed the GPU build over the CPU one, while another process had it loaded; that process kept working. Deleting a loaded DLL's last link (in uv's cache) fails with "Access is denied"; `uv cache clean` drops the entry first, and `uv cache prune` freed the files after the process exited. |
| Migration with the real CLI (native Windows) | Passed | A venv from ADR 0038's lock with `model.device=cpu` (venv 2,108 MB, cache 2,178 MB, shared links) moved to the CPU build in 40 s while a process held ONNX Runtime: venv 306 MB, cache 381 MB, 1,773 MB freed at once, 25 MB more at the next setup. Download: one 15 MB wheel. |
| Enabling GPU embedding (native Windows) | Passed | `imprint config set model.device gpu` named the GPU and the download. `imprint bootstrap` installed 7 packages (venv 1,753 MB, NVIDIA 1,239 MB) and passed the check. |
| Dashboard provisioning (native Windows, `imprint ui start`) | Passed | `DELETE` then `PUT` of `model.device` queued bootstrap jobs (10 s and 68 s); the server restarted with a new pid after each, and Settings then showed the GPU without the download note. |
| Migration and dashboard flow (Linux, WSL2) | Passed | 2.8 GB of venv and cache down to 485 MB; GPU and back through queued jobs with restarts; unusable configurations (`CUDA_VISIBLE_DEVICES=`, `model.gpu_device=1`) kept the CPU build with the reason in setup, `config set` and `doctor`. |

Not run: Windows 10, Linux arm64 GPU hosts, drivers older than R580, a real installer update from a release with the old runtime, and machines with only AMD or Intel GPUs.

## Learn from WSL and Codex stores (2026-10-24)

See [ADR 0037](adr/0037-learn-every-integrated-client-store.md). These checks ran on a native Windows installation with Claude Code and Codex registered for WSL Ubuntu. Before the change, its queued learn job printed "No Claude Code projects found" and "No transcripts found". None of the checks wrote to the installation or its data: ownership checks used a copy of the workspace policy in `%TEMP%`.

| Check | Result | Notes |
| --- | --- | --- |
| Store discovery (cross-compiled Go test binary, real `.wsl-registrations.json`) | Passed | Returned this computer's `C:\Users\<user>\.claude` and `.codex`, then one Ubuntu group holding both registered stores, with the drive mount table read through `wsl.exe` in 0.15 s. |
| Missing distribution | Passed | Reported as `WSL NoSuchDistro is unavailable (… There is no distribution with the supplied name. Error code: Wsl/Service/WSL_E_DISTRO_NOT_FOUND)`. The other groups were kept. Before the fix, the message after the exit status was empty, because `wsl.exe` writes its errors to stdout in UTF-16. |
| Path context handed to the installed interpreter | Passed | The Ubuntu group resolved `/home/<user>/.claude` to `\\wsl.localhost\Ubuntu\home\<user>\.claude` and `/mnt/c/…` to `C:\…`. This computer's group left paths unchanged. |
| Listing and identity through `\\wsl.localhost` | Passed | 114 Claude Code transcripts in 5.8 s and 32 Codex rollouts in 1.2 s, each with a distinct source key. Projects named from the recorded cwd matched the folder-derived names. Codex sessions got the same names for the same directories. |
| Ownership read of every WSL conversation | Passed after a fix | Resolving the cwd on every record cost about 20 ms per record: a 0.52 MB transcript took 2.1 s, of which 1.97 s was 98 resolutions of 2 distinct directories, and a full pass did not finish in 25 minutes. With one resolution per distinct cwd, the same transcript takes 0.1 s, and all 146 conversations (1.8 GB) take 407 s. All 146 belong to the default workspace, whose only root is a Windows folder. |

Not run: a complete `imprint learn` that embeds and stores into the installation's data directory, and the dashboard **Learn** action on Windows. Every Claude Code project folder on this machine has auto-memory files, and their ownership rule rereads every transcript beside them on each run: about 3 minutes for these 1.2 GB through `\\wsl.localhost`.

## Learn from Cursor stores (2026-10-25)

See [ADR 0052](adr/0052-learn-cursor-conversations-from-their-folder-names.md). These checks ran on a Windows installation whose Cursor app works on WSL Ubuntu projects through Remote-WSL. The conversations are in the distribution's `~/.cursor` and the Windows profile's `projects/` holds none. All checks were read-only: ownership used an empty workspace policy in a temporary data directory, and nothing was stored.

| Check | Result | Notes |
| --- | --- | --- |
| Folder resolution, natively in WSL | Passed | Of 21 project folders, the 4 named after existing directories resolved to them, the first in 52 ms and the rest in under 2 ms. The other 17 named no directory: numeric empty-window folders, `tmp-*` folders, a `.code-workspace` folder and a cleanup folder. |
| Conversations, natively in WSL | Passed | 4 conversations; subagent files skipped. Each was owned, keyed `conversation/<id prefix>` and named `arnia-gmv-reporting-service`, `airportlabs-challenge` or `arnia-shopware`, with 40, 21, 4 and 1 exchanges of the user's query and the assistant's text. |
| The same through the installed Windows interpreter | Passed | With the Ubuntu path context, the store resolved to `\\wsl.localhost\Ubuntu\home\<user>\.cursor`. Every folder resolved as natively, in 1 to 17 ms. Identity, parsing and ownership of the 4 conversations took 0.34 s. |
| Native Windows folder names | Passed | `C-Users-<user>-AppData-Local-Programs-Imprint` and its lower-case `…-imprint-bin` resolved to the installation folders; the Windows profile's `C-Users-…-Temp-<uuid>` folders and its copy of the WSL project folder named no directory. |
| Profile probe (cross-compiled CLI, `wsl list`) | Passed | The real Ubuntu profile reported `cursor_home` beside its Claude Code and Codex profiles. |
| Go learn, WSL and cleanup tests (cross-compiled Windows test binary) | Passed | |

Not run: registering Cursor in the installation (`imprint wsl setup --target cursor` writes its journal), the installer wizard with a Cursor selection, and a complete `imprint learn` that stores Cursor conversations.

## Cline setup and learn (2026-10-25)

See [ADR 0056](adr/0056-learn-cline-sessions-from-their-manifests.md). No Cline is installed on this machine and no Cline sessions exist, so sessions are fixtures built from the Cline SDK's schemas (cline/cline@40c5d99). Every check used a temporary installation directory in `%TEMP%`, a disposable WSL home in `/tmp` and the installed Windows interpreter read-only; nothing was written to the installation, its data or a real client profile.

| Check | Result | Notes |
| --- | --- | --- |
| Go Cline, WSL, learn, cleanup and client tests (cross-compiled Windows test binary) | Passed | Includes setup in a disposable home with `CLINE_DIR`, `CLINE_DATA_DIR` and `CLINE_MCP_SETTINGS_PATH`, disconnect of old and new registrations, and the Cline WSL layout. The jsonutil tests passed too. |
| Profile probe (cross-compiled CLI, `wsl list --json`) | Passed | The real Ubuntu profile listed the new `cline` target with no profile, since `~/.cline` does not exist, beside its Claude Code, Cursor and Codex profiles. |
| `imprint wsl setup --target cline` into a disposable home (`--home /tmp/…`, `--python` the installed interpreter) | Passed | Wrote `<home>/.cline/imprint-wsl` (mode 700), the `imprint` entry beside an existing server in `data/settings/cline_mcp_settings.json` with a backup, and `rules/imprint.md` beside a user rule. The launcher's probe ran the Windows interpreter from WSL. The journal recorded `cline_home`. |
| `imprint wsl remove --target cline` | Passed | Removed the entry, the rule and the launcher; kept the other server and the user rule; emptied the journal. |
| Unknown target | Passed | `imprint wsl setup --target vim` failed with `target must be claude-code, cursor, codex or cline` before probing. |
| WSL Cline store through the installed Windows interpreter | Passed | With the Ubuntu path context, the sessions directory resolved to `\\wsl.localhost\Ubuntu\tmp\…\.cline\data\sessions`. The session was found, keyed by its full id, named `imprint-memory-layer` from the manifest's Linux cwd, owned by the default workspace, and parsed into the two typed questions. |
| Windows PowerShell 5.1 setup regression (WSL interop) | Passed | Saved selections including `cline` were replayed to the CLI. |
| Installer wizard pages (portable Inno Setup compiler) | Passed | |

Not run: a real Cline on any surface loading the settings and rule, real Cline sessions, the installer wizard with a Cline selection, a complete `imprint learn` storing Cline sessions, and macOS.

## GitHub Copilot setup and learn (2026-10-25)

See [ADR 0057](adr/0057-copilot-home-as-store-config-and-hook-root.md). No Copilot conversations exist on this machine, and the Copilot CLI is not installed. Both Copilot homes hold only `config.json` and logs, so sessions are fixtures built from the Copilot SDK's event schema. Every check used a temporary installation directory in `%TEMP%`, a disposable WSL home in `/tmp` and the installed Windows interpreter read-only; the real `~/.copilot` was only probed.

| Check | Result | Notes |
| --- | --- | --- |
| VS Code 1.143.1 bundle (installed, read only) | Confirmed | `chat.mcp.discovery.enabled` defaults to `copilot: true`; that source reads `<COPILOT_HOME or ~/.copilot>/mcp-config.json`, the remote host's in a remote window. The Agent Host resolves the same file, and the customization migration moves user `mcp.json` servers into it. |
| Go client, WSL, learn and cleanup tests (cross-compiled Windows test binary) | Passed | Includes Copilot setup and disconnect in a disposable home and the Copilot WSL layout. |
| Copilot hook commands in Windows PowerShell (cross-compiled hooks test, installed interpreter) | Passed | Paths with spaces and apostrophes. `SessionStart` printed a top-level `additionalContext`; `Stop` printed `{}`. |
| Profile probe (cross-compiled CLI, `wsl list`) | Passed | The real Ubuntu profile reported `GitHub Copilot  not enrolled  /home/<user>/.copilot`. |
| `imprint wsl setup --target copilot` into a disposable home | Passed | Wrote the launcher, the `imprint` entry beside an existing server in `mcp-config.json`, the managed block after the user's text in `copilot-instructions.md`, and `hooks/imprint.json` beside a user hook file, with backups of the changed files. |
| Registered hooks run as Copilot runs them (`bash -c`, PascalCase payloads) | Passed | `SessionStart` returned `additionalContext` from the Windows interpreter through the bridge. `Stop`, with a transcript that records no context, was skipped by the bridge's ownership check and printed `{}`. |
| WSL Copilot home through the installed Windows interpreter | Passed | The home resolved to `\\wsl.localhost\Ubuntu\tmp\…\.copilot`. A session with a context was keyed by its id, named `imprint-memory-layer`, owned and parsed into the two typed questions; the session without one was not owned. |
| `imprint wsl remove --target copilot` | Passed | Removed the entry, the block, the hooks file and the launcher; kept the other server, the user's text and the user's hook file; emptied the journal. |

Needs a live, logged-in Copilot: that Copilot honors the SessionStart context and the instructions file, that `Stop` payloads carry `transcript_path` in VS Code's harness and the CLI, that the WSL Agent Host runs the distribution's hooks and MCP server, and real sessions. Not run: VS Code, the CLI or JetBrains loading `mcp-config.json`, the installer wizard with a Copilot selection, a complete `imprint learn` storing Copilot sessions, and macOS.

## PersonaPlex startup memory prototype (2026-10-24)

See [ADR 0041](adr/0041-personaplex-startup-memory-prototype.md) and the [prototype guide](personaplex-prototype.md). This validates an experimental adapter, not a supported voice/avatar product.

| Check | Result | Evidence and limits |
| --- | --- | --- |
| Adapter tests, Linux/WSL2 | Passed, 12 tests | Actual MCP discovery/calls and stdio child with disposable storage fixtures; workspace/project isolation, bounded evidence, exclusive exports, drift rejection, local browser boundaries, server-side context injection, binary WebSocket frames, pinned cache-only lookup and owned-child shutdown. Binary frames are fixtures, not decoded speech. |
| Full Python suite, isolated data directory | Passed, 287 tests | Existing behavior and release-validator regressions included. No real client profile was registered or used as a test fixture. |
| NVIDIA source contract | Passed | A temporary clean checkout at `3428dfd95309a7f3c84fd93259ded0f810d1ff91` passes revision, tracked-file cleanliness and server SHA256 checks; this does not import the model stack. |
| Test dependency distribution boundary | Passed | The actual rsync release exclusion filter retains runtime locks and excludes both PersonaPlex test-overlay files; validator fixtures reject either file if included. No release archive or installer was produced. |
| Documentation | Passed | Site production build, repository-link checks and whitespace checks. |
| Pinned model access | Unavailable | A metadata-only request using the available Imprint/Hugging Face credentials returned `GatedRepoError`; no weights were downloaded. |

Not run: real PersonaPlex model import/load, microphone capture, audible responses, speech grounding, Opus decoding, interruption/echo tests, GPU latency/VRAM benchmarks, native Windows/macOS/Linux desktop execution, floating-head rendering or installer/update behavior. CI now installs the hashed adapter-test overlay on its native matrix; those jobs have not been executed for this branch in this session.

## LFM2.5-Audio CPU experiment (2026-10-24)

The user selected LFM2.5-Audio-1.5B as primary after requesting a smaller option. See [ADR 0042](adr/0042-lfm-audio-cpu-experiment.md), the [experiment guide](lfm-audio-prototype.md) and [raw smoke evidence](evidence/2026-10-24-lfm-audio-cpu-smoke.json). Work continues on `feat/local-voice-avatar`. PersonaPlex model access was subsequently granted using the interactive shell's credentials, but its setup/downloads were stopped before model inference when the direction changed.

| Check | Result | Evidence and limits |
| --- | --- | --- |
| Native model/runtime installation | Passed, Linux x64 under WSL2 | Four Q4 artifact checksums/sizes and the official Ubuntu runner archive verified at `7d525f883a077e20afb782f2ff618edcae0e39e4`; unpacked native files reverified before execution. Embedded build: 7641 (`68d8edf2`). No PyTorch profile needed. |
| Real speech synthesis | Passed, two smoke runs | Produced non-silent 24 kHz mono float32 WAV for an English question. One run generated 2.30 seconds of audio in 1.83 seconds including native startup, excluding asset verification. No listening panel or pronunciation-quality score. |
| Real speech recognition | Passed, two smoke runs | The same model transcribed the synthesized question correctly. Synthetic speech is not representative of microphone/noise/accent coverage. |
| Memory-conditioned speech replies | Passed, two names per run | The same WAV question returned “Nimbus” or “Comet” according to the current MCP snapshot, omitting the distractor. MCP uses the real registry with disposable retrieval fixtures at the storage boundary; no embedding/retrieval-quality claim or real user-memory test. |
| Local CPU resource measurements | Observed | Four threads, no GPU: fresh native process/model loads took 2.95–4.52 seconds for complete reply generation across two runs; sampled peak RSS about 1754 MiB. Timings exclude asset verification and include startup. Filesystem caches/load were uncontrolled, and first-audio latency was not measured. |
| Regression suite | Passed, 295 Python tests | Includes 8 new LFM tests plus the earlier PersonaPlex adapter tests. Fixtures cover idempotent install/repair, archive paths/symlinks, tampering, float/PCM WAV, identity isolation, privacy, bounds and owned-child timeout. |

Not run: live microphone input, streaming playback, first-audio latency, barge-in, echo cancellation, learned full-duplex conversation, live MCP tool use/writes, voice persistence, head rendering, real GPU inference or native Windows/macOS/ARM execution. Native CI for this branch has not been run in this session. The result establishes a promising small CPU speech experiment, not the complete real-time avatar assistant.

## Echo live voice pilot (2026-10-24)

Follow-up to the offline experiment: [live voice guide](live-voice.md),
[ADR 0043](adr/0043-live-voice-and-echo-companion.md),
[real transport/model evidence](evidence/2026-10-24-echo-live-voice.json) and
[repeat-run evidence](evidence/2026-10-24-echo-live-voice-repeat.json), plus
[native WSLg evidence](evidence/2026-10-24-echo-electron-wslg.json).
The user chose the Echo holographic particle head with push-to-talk and optional
hands-free/VAD. The same-local-chat route is implemented alongside LFM reasoning.

| Check | Result | Evidence and limits |
| --- | --- | --- |
| Private live worker build | Passed, Linux x64 CPU/WSL2 | Built pinned `tdakhran/llama.cpp` source `ec9d1fdd9cc18643c5e161b65b8053f6f6f34a4b` with Imprint's loopback/auth/origin/cancellation patch. Source archive and weights verified; executable/patch fingerprints checked at launch. |
| Native listener boundary | Passed | Authenticated health succeeds; incorrect bearer or any browser Origin returns 403. No wildcard CORS. Browser traffic uses Imprint's separately checked HTTP/WebSocket boundary. |
| Real synthetic speech question | Passed, two runs | Native TTS generated a question, ASR recognized it, retrieval-conditioned LFM streaming returned Nimbus. First audio 1.911–2.197 s after submitting the completed utterance, whole generation 3.374–3.622 s, output audio 3.02 s. Warm worker; input recording/endpoint time and real retrieval embedding cost excluded. Storage retrieval is a disposable fixture. |
| Warm native streaming probe | Observed | Separate short TTS/interleaved native requests first emitted audio at approximately 0.259/0.319 s after request, respectively. These exclude ASR, retrieval and chat reasoning and are not user-perceived end-to-end latency. |
| Workspace ownership | Passed | A lease for `default` still retrieved `default` after the disposable dashboard workspace switched to `other`. |
| Mid-speech interruption | Passed over real WebSocket, two runs | Cancel acknowledgement 0.0183–0.0209 s; the owned native process had exited. Fresh response after cancellation worked, with no stale generation IDs. This does not measure human acoustic onset or speaker echo. |
| Same-chat route | Passed with fixture reasoning and real streamed TTS | Invokes the existing `chat.stream_reply` with captured workspace, cancellation and enforced read-only tools; produced native speech. Actual Qwen reasoning/total voice latency was not benchmarked. |
| Python regression suite | Passed, 307 tests | New ownership, capability expiry/replay/origin, cancellation, read-only tools, long spoken tails, WAV bounds and runtime integrity cases, plus existing full suite. |
| Audio/controller tests | Passed, 3 tests | Actual TypeScript VAD and WAV encoder exercised for silence/click rejection, onset/endpoint, speaking hysteresis, bounds and PCM metadata. |
| Dashboard browser tests | Passed, 51 tests | Includes 4 new voice cases: real GLB load, interrupted playback/stale chunks, compact layout and actual AudioWorklet capture from Chromium's synthetic microphone. No physical microphone accessed. |
| Electron policy/lifecycle | Passed with controlled doubles | Separate transparent sandboxed companion, scoped IPC, optional pin, destroy on close/quit and main-frame audio-only permission checks. |
| Native graphical companion | Passed, Electron 45.0.0 on Linux/WSLg | Disposable profile, actual rendered head, separate window, always-on-top, synthetic-microphone permission, Stop and Close. WSL test used `--no-sandbox` and software WebGL flags; this does not validate a packaged Linux sandbox, native Windows or macOS. [Preview](assets/voice/echo-companion-preview.png). |
| Build and documentation | Passed | UI static export, TypeScript/lint checks, documentation production build and whitespace checks. Generated UI build output is excluded from this change. |

Not run: physical microphone/acoustic echo/room noise, accent and intelligibility
ratings, robust hands-free speakerphone behavior, actual Qwen voice latency,
phoneme-aligned animation, learned full-duplex reasoning, native Windows/macOS/ARM
speech workers, GPU inference, packaged desktop sandbox/installer tests or CI.
No real user memory stores or client profiles were used as test fixtures.

## Native Echo and transparent desktop (2026-10-24)

Follow-up to the Linux pilot: [ADR 0044](adr/0044-native-desktop-voice-platforms.md)
and the [current setup guide](live-voice.md). The optional live worker now builds
natively for Windows x64, macOS Intel/Apple Silicon and Linux x64. Its model/cache
is external to distributions; no release or installer was published for this work.

Source `b21e0db` passed the complete
[native voice workflow](https://github.com/alexandruleca/imprint-memory-layer/actions/runs/40085535980).
[Raw platform evidence](evidence/2026-10-24-echo-native-platforms.json) preserves
the target/build fingerprints and separate speech/desktop summaries.
The [general CI run](https://github.com/alexandruleca/imprint-memory-layer/actions/runs/40085535959)
also passed its five native backend targets, frontend and workflow checks.

| Native runner | Speech and desktop | First audio | Whole speech turn | Cancel acknowledgement | Desktop renderer |
| --- | --- | --- | --- | --- | --- |
| Windows x64, Server 2025 | Passed | 2.562 s | 7.984 s | 109.0 ms | Software WebGL |
| Intel Mac, macOS 15.7.9 | Passed | 4.060 s | 12.679 s | 68.7 ms | Software 3D canvas |
| Apple Silicon, macOS 26.6.2 | Passed | 2.955 s | 5.415 s | 59.2 ms | Software 3D canvas |
| Linux x64, Ubuntu hosted runner | Passed | 4.340 s | 15.745 s | 19.0 ms | Software WebGL |

These are individual synthetic CPU runs with a warm speech worker. First audio
starts after submitting the completed utterance and includes ASR and fixture
retrieval; it excludes recording/endpointing and real chat-model inference.
Cancellation measures protocol acknowledgement and owned-process exit, not human
speech onset. Hosted load and caches were uncontrolled; these are executable
checks, not a comparative performance benchmark.

The floating face has transparent background pixels with no enclosing panel or
border. Individual controls remain readable against small backdrops. The
dashboard's **Floating Echo** switch opens/closes the window and follows manual
close events. Off destroys the window and releases the voice session; on never
starts microphone capture. WebGL is preferred, with an animated projection of
the same 3D particles/morphs onto transparent canvas when WebGL2 is unavailable.
The [native preview](assets/voice/echo-transparent-preview.png) retains its alpha
channel; a viewer may display transparent pixels against black or a checkerboard.

Native tests use disposable API data, synthetic questions, retrieval/chat
fixtures and isolated Electron profiles. They check real native TTS/ASR,
memory-conditioned streamed replies, authentication/origin denial, workspace
pinning, mid-speech cancellation, recovery and worker exit on parent-pipe EOF.
Graphical checks load the real GLB, verify transparent PNG pixels, pin the window,
start a local voice session and close it using the dashboard switch.

The portable build explicitly reads/writes source patches as UTF-8 (Windows'
default code page previously changed the patch fingerprint). Thread/batch limits
respect CPU allocation and polling is disabled: an oversubscribed three-CPU M1
runner previously needed 138 seconds for 2.3 seconds of TTS; the corrected policy
passed its native speech gate. Hosted Mac desktops lack WebGL2, which motivated
the actual software 3D fallback. Hosted audio tests use a silent WebAudio sink
so they exercise the graph without requiring physical speaker endpoints.

Local regressions passed: 310 Python tests, 53 browser scenarios and 19 Electron
policy/lifecycle tests. The final startup-budget regression brings native Linux
CI to 311 Python tests. The final audio startup change also passed the six voice
browser cases, lint and production UI build. Documentation build/link checks and
workflow lint passed. Generated UI output is excluded from the commits.

A Windows GUI test initially waited only for the 90-second native load budget
plus a small margin. Diagnostic logs showed 20.59 seconds of preceding full
asset verification, then successful native readiness after the test wait. The
test now allows the complete startup/cleanup budget; production startup is
bounded to 120 seconds including verification and reports an actionable timeout.
The final native matrix above passed with this correction.

Remaining manual gates: physical microphone/output devices, acoustic echo and
room noise, actual local-chat-model voice latency, intelligibility/accent ratings,
interactive macOS TCC consent, oldest supported OS versions and signed/notarized
packaged installers. macOS graphical automation uses typed sessions and does not
bypass TCC; Windows/Linux use Chromium's synthetic microphone. Linux GUI testing
uses `--no-sandbox`. These checks do not establish packaged sandbox behavior or
learned full-duplex speech reasoning. Windows ARM, Linux ARM voice and GPU speech
inference remain outside this pilot.

## Visible first-time Echo setup (2026-10-24)

[ADR 0046](adr/0046-visible-echo-runtime-setup.md) adds a prominent initial setup
card, actual model byte progress, separate engine preparation, top-bar status
across navigation and cancel/retry/tool guidance. Downloads require an explicit
action; completion never opens the microphone. The floating face stays
transparent with setup beneath it among the controls.

Executed locally on Linux/WSL2: 25 voice/setup Python checks, including a real
owner-pipe EOF test with an actual descendant process; four new first-run browser
scenarios and the six existing voice scenarios; lint and the production UI build;
documentation build and workflow lint. Full workspace browser discovery passed
58 tests, including concurrent settings work outside this change. No real
client profile or memory store was used.

The real disposable API/Electron smoke also passed. It invoked the managed
setup child against the already verified LFM cache, reached ready without new
downloads, then checked transparent head rendering, synthetic mic permission,
pinning and dashboard on/off session cleanup. Fresh byte downloads and cached
reuse use small hash-verified network fixtures in unit tests; first-run UI states
use API fixtures. This does not establish a fresh 1.07 GB UI-driven download or
fresh compiler installation on a user's machine. Native Windows/macOS execution
of this new setup/cancellation path passed on source `1980545`: the
[native setup/speech/desktop run](https://github.com/alexandruleca/imprint-memory-layer/actions/runs/40087296414)
passed Windows x64, Intel Mac, Apple Silicon and Linux x64, including 25 voice
checks and the real setup owner/descendant shutdown test. Each graphical job
also invoked the managed setup child against its verified native cache and
reached ready. The [general CI](https://github.com/alexandruleca/imprint-memory-layer/actions/runs/40087296430)
passed all five native backend targets, frontend and workflow checks. This
establishes cached setup and native process lifecycle, not a fresh full UI
download/build or real microphone/TCC validation.

Follow-up for the same-Chat route: Voice now forwards real chat install/download/
load progress without raw package logs, paths or repository metadata. A bounded
first-time setup budget is separate from normal reply generation. A regression
delays provisioning beyond the ordinary reply timeout, verifies actual byte
events and privacy filtering, then completes the spoken reply. A browser case
checks chat download percentage/bytes and rejection of late progress after
interruption. This path does not benchmark or freshly provision the actual chat
model; the existing chat provisioning policy remains in force.
The follow-up passed 26 voice/setup Python checks and 11 voice/setup browser
scenarios locally, plus the UI production build and lint.

Resource follow-up: the Windows virtual desktop intermittently reached the
model startup limit with two software-WebGL heads. Diagnostic verification took
23.03 seconds and native model warmup was still running at timeout. Recognized
software WebGL now uses a 10 fps cap to limit CPU rasterizer work; hardware WebGL
uses 30 fps and the canvas fallback 15 fps. The local real Electron smoke passed
with `frameRateLimit: 10`, cached managed setup and the active-session dashboard
shutdown check. Native graphical evidence records this cap and asserts it for
the test's forced software-WebGL backend.

Final source `5536d39` passed the complete
[native voice/setup/desktop matrix](https://github.com/alexandruleca/imprint-memory-layer/actions/runs/40088910339)
on all four targets. [Raw setup and desktop evidence](evidence/2026-10-24-echo-visible-setup.json)
records ready/cached state, owned session shutdown, transparent PNG checks and
renderer limits: 10 fps software WebGL on Windows/Linux and 15 fps canvas on both
Mac architectures. Each target passed 26 voice/setup regressions, including real
owner/descendant termination. The Windows graphical run completed with the new
cap; its verification stage took 3.12 seconds on that runner. This is an observed
run, not a controlled performance comparison.

The [general CI for `56ed4dd`](https://github.com/alexandruleca/imprint-memory-layer/actions/runs/40088136624)
also passed all five native backend targets, 321 Python tests and 58 browser
scenarios. The subsequent software-rendering fix passed the final frontend CI
and native graphical matrix above. No release or installer was published.

## Anime Echo and desktop refinement (2026-10-24)

Source `f4d157f` passed the complete [native bundled-engine, speech and desktop
matrix](https://github.com/alexandruleca/imprint-memory-layer/actions/runs/40117167378).
[General CI](https://github.com/alexandruleca/imprint-memory-layer/actions/runs/40117167402)
also passed on Linux x64/ARM64, Windows x64 and both Mac architectures. The
[evidence JSON](evidence/2026-10-24-echo-anime-refinement.json) retains engine
fingerprints, actual setup states, speech checks and desktop results.

| Voice target | Release engine | Real speech and cancellation | Fresh bundled setup, PATH empty | Native head/launcher and recall | Renderer in hosted GUI |
| --- | --- | --- | --- | --- | --- |
| Linux x64 | glibc 2.28 build | Passed | Passed | Passed | Software WebGL, 10 fps cap |
| Windows x64 | Native static MSVC build | Passed | Passed | Passed | Software WebGL, 10 fps cap |
| macOS ARM64 | Native macOS 13+ build | Passed | Passed | Passed | Textured canvas 3D, 15 fps cap |
| macOS x64 | Native macOS 13+ build | Passed | Passed | Passed | Textured canvas 3D, 15 fps cap |

Fresh GUI setup seeded only verified model weights, copied the shipped engine
with PATH empty and reached ready without discovering CMake or a compiler. Each
platform exercised real ASR/TTS, private worker authentication/browser-origin
rejection, owner-pipe exit, interruption followed by a fresh turn, workspace
binding and same-Chat routing with fixture reasoning and real speech synthesis.

Actual Electron windows passed whole-head dragging, saved bounds/recovery,
registered recall shortcut, pinning, transparent PNG checks, compact layout,
dashboard-toggle microphone/session teardown and the draggable launcher after
the dashboard's Minimize and Close controls. Mac microphone capture/TCC remains
manual; Windows/Linux used synthetic input and a silent WebAudio sink. The WSLg
local window manager did not emit a programmatic native minimize event, so the
smoke uses the app's real Minimize control; OS minimize-event routing is also
covered by Electron fixtures.

For one synthetic question in each hosted run, first reply audio arrived in
2.56–3.79 s; cancellation acknowledgement took 32–110 ms. These are fixture results
on CI machines, not a general latency or acoustic-quality guarantee. Physical
global keypresses, Wayland behavior, real microphones/speakers/room echo, human
intelligibility ratings and signed installer behavior remain manual gates.
Full release packaging remains a main-only gate; the actual local Linux engine
payload also passed disposable release-staging validation.

Local checks passed: 335 Python tests, 63 Playwright scenarios, 23 Electron
fixtures, production UI build, lint and workflow checks. Browser cases cover
setup/download/error/ready controls without scrolling at 400×640 and 320×420,
chat provisioning with visible cancellation, transparent GPU-less rendering and
launcher keyboard activation after dragging. [Actual native companion
preview](assets/voice/echo-anime-companion.png).

The implementation fixes three observed integration defects. Exported engine
payloads need 755 directories/executables and 644 data/notices so a container-built
or system-owned installation is readable by the app user; private runtime caches
retain their private modes. CPU projection must process only vertices used by each
glTF primitive rather than repeatedly transforming a shared whole-head buffer,
and must leave time after painting for input/audio. Finally, dashboard skip links,
connection banners and overlays must not mount in floating routes: a focused
“Skip to content” link intercepted clicks over the tiny launcher. Launcher tests
wait for React readiness and asynchronous native position changes.

The head is adapted from pixiv's explicitly CC0 beta Sendagaya Shino model, with
Imprint modifications under Apache-2.0, a reproducible head-only glTF pipeline,
mouth/blink expressions, holographic shading and fine animated wire contours.
It is an adapted anime model, not an original sculpt or a licensed Haruhi asset.

## Registered dashboard server and supervised restart (2026-10-24)

[ADR 0050](adr/0050-registered-dashboard-server-and-supervised-restart.md)
follows a native Windows installer update from v0.20.0 to v0.21.0. The install's
`update-setup.log` and `imprint_ui.log` show that Restart Manager shut down only
the portable executable's stub, while the extracted desktop app and its
unregistered service kept serving v0.20.0 until a manual quit. These logs were
read only. No update or command was run against that installation.

Executed locally on Linux/WSL2: the full Python suite (335 tests, including
the real desktop service registering, unregistering and exiting with 75 after a
restart request), Go tests and vet (with Windows and macOS cross-vet), 22
Electron unit tests, dashboard type-check/lint and the full Playwright suite (59
scenarios, including reloading after the installer handover). The real CLI and
Python server were also run end to end from a disposable tree. `imprint ui
start` → `restart` → `status` → `stop` replaced the server under the same
supervisor and left nothing behind. The real `DesktopService` was driven by
`imprint ui status/restart/stop`: it was reported, replaced without a failure,
then stopped with the app notified.

Executed natively on Windows through WSL interop: `scripts/test-windows-setup.ps1`
under Windows PowerShell 5.1, which fails against the previous setup script, and
the cross-compiled Go supervisor, restart and windowless-console tests.

Pending native runs: a real Inno Setup update between two releases, an
interactive installer upgrade over a running Imprint, the macOS `.pkg` handover,
and the packaged desktop app on each platform after `imprint ui restart` and
`imprint ui stop`.

## Desktop microphone, relaunch and companion fixes (2026-10-25)

**macOS: Enable microphone crashed the app.** The published v0.22.1
`imprint-darwin-arm64.tar.gz` was inspected without running anything (its
checksum matches `SHA256SUMS`). `bin/imprint-ui.app` and its four helper apps
have no `_CodeSignature/CodeResources`. The main executable's x86_64 slice has
no code signature, and its arm64 slice has only the linker's ad-hoc signature
(identifier `Electron`, no Info.plist or resources binding, no entitlements, no
hardened runtime). `NSMicrophoneUsageDescription` is in `Info.plist` but not
sealed to the code. `imprint ui open` starts the app through `open -a`, so the
app is its own TCC-responsible process. [ADR 0052](adr/0052-ad-hoc-signed-macos-desktop-bundle.md)
signs it ad hoc as a bundle, and release validation now rejects this artifact.
Pending on a Mac: a release built with the change, `codesign --verify --deep
--strict` of the installed app, the TCC prompt itself, and the user's crash
report (`Termination Reason` namespace) to confirm the diagnosis.

**Relaunch without arguments.** The desktop app asks the CLI beside it
(`imprint ui runtime`) when the OS starts it without `imprint ui open`'s
arguments. Covered by Go and Electron fixture tests. Pending native runs: macOS
Reopen after a crash and a Dock launch, a pinned Windows taskbar icon and an
AppImage started directly.

**Floating companion.** Typing before a conversation, drag handles limited to
the head, header and grip, recovery from a missed release, and pin state from
the main process are covered by Playwright specs that fail against the previous
component. `acceptFirstMouse` (macOS only) still needs a native check.

**Windows: "Microphone unavailable. Check permissions".** Read-only evidence
from the reporting Windows install: the running v0.22.1 dashboard server serves
`/audio/imprint-capture.js` as `text/javascript`, but its log has no request for
that file in any desktop session. The `ConsentStore\microphone\NonPackaged` key
has no entry for `Imprint.exe` (Chrome and Discord have one). So `getUserMedia`
rejected before any device opened, and not with `NotAllowedError` (a request
handler refusal, `PERMISSION_DENIED` in Electron). The running shell was an
earlier build, extracted before the update: the v0.22.1 shell had yielded to it
through the single-instance lock. Its permission handlers are the same, and a
fixture confirms that both handlers grant audio to the voice page and the
companion on win32. The microphone failure now names the failed step
(device, capture worklet, audio graph) and the browser's error name and message.
Only a refusal mentions permissions. The exact Windows cause still needs that
message from the user's machine.

Executed locally on Linux/WSL2: release-validation unit tests (9, plus a run
against the extracted v0.22.1 bundle), Go `cmd` and `internal` tests, 34
Electron unit tests, dashboard type-check/lint and the full Playwright suite
(83 scenarios).

## Secret redaction (2026-10-25)

[ADR 0060](adr/0060-redact-secrets-before-memories-are-stored.md); measurements in `docs/evidence/2026-10-25-secret-redaction-cpu.json`.

| Check | Result | Scope |
| --- | --- | --- |
| Python unittest suite | Passed, 436 tests (1 skipped: the real-model smoke without its data directory) | Adds `tests/test_redaction.py`: rule formats and a false-positive corpus, idempotence, linear matching, version recognition, the setting, Privacy Filter with a stand-in model, every write path against an in-memory Qdrant (ingest, learn, MCP `store`/`ingest_content`, summaries, facts, sync import with re-embedding), scrub propagation across three workspaces standing in for devices (including differing Privacy Filter settings and a looser device re-indexing), redacted prompts for all six tagger and summarizer providers through stand-ins, `imprint scrub` and snapshot import. |
| Version queries against a real Qdrant 1.17.1 | Passed | A private server on a spare port: three workspaces, one scrub, the result reaching the other two through the import buffer, an unscrubbed copy skipped, the old text written again storing nothing. Exercises the full-text and keyword filters that the in-memory client only approximates. |
| Real Privacy Filter smoke, Linux x86_64 | Passed | Pinned q4f16 export from `data/models/privacy-filter/`, offline, ONNX Runtime 1.31 CPU: names and email kept, password and IBAN redacted, idempotent. |
| Real Privacy Filter, native Windows x64 | Passed | The installed runtime's Python and onnxruntime-gpu 1.31 (CPU provider), from a temporary copy of `imprint/` and a temporary data directory: first-use download and SHA-256 verification, then redaction and per-chunk timing. The installation's config and data were not touched. |
| `go build`, `go vet ./cmd/`, `go test ./...` | Passed | `imprint scrub` and the import lock. In the first round, one `cmd` run failed transiently while other sessions held dashboard test ports; four reruns passed. |
| Dashboard `tsc --noEmit`, ESLint on the Settings page | Passed | The Redaction settings group. Not rendered in a browser. |

Not run: macOS (Intel macOS pins ONNX Runtime 1.23.2, whose support for the model's `QMoE` kernel is unverified), Linux ARM, physical devices syncing over a relay, real tagger/summarizer provider endpoints, and the Commands page in a browser.

## Voice port: microphone consent, grouped companion and per-look voices (2026-10-25)

Another session's uncommitted voice work was ported onto the integration branch
and merged with the desktop fixes above
([ADR 0069](adr/0069-echo-opens-the-microphone-only-on-the-users-intent.md),
[ADR 0070](adr/0070-companion-moves-with-the-launcher-and-keeps-options-in-settings.md),
[ADR 0071](adr/0071-each-look-speaks-in-its-own-voice.md)).

| Check | Result | Notes |
| --- | --- | --- |
| `node --test` in `ui-electron` (43 tests) | Passed | Consent answers per platform, scoped consent and settings IPC with the sender rechecked after consent, the grouped launcher and head, pin state while grouped, intended positions when native reports lag. |
| `imprint/ui/scripts/voice-audio.test.mjs` (6 tests) | Passed | Preferences recovery, consent answers, Echo's robotic effect across chunks. |
| Dashboard `tsc --noEmit`, ESLint, production build | Passed | |
| Full Playwright suite (112 scenarios) | Passed | Settings panel and drag handles, per-platform refusal and recovery, pending consent dropped on Stop or a change of mind, remembered microphone and mode, session restart on a new look or model. One earlier run under heavy load had three voice failures that did not recur in the next runs, including three repeats of every voice scenario. |
| Python suite (553 tests, 1 skipped) | Passed | Includes `tests/test_adr_records.py`, the release validator's microphone usage text check and the per-look voice tests. Run after rebasing onto the queue work (ADRs 0067 and 0068). |
| Go `vet` and `test ./...` | Passed | No Go changes. |
| `ui-electron/smoke-microphone.mjs`, real Electron 45 under WSLg | Passed | Disposable profile, synthetic devices, no fake permission prompt, `--no-sandbox`. |
| `ui-electron/smoke-companion-controls.mjs`, real Electron 45 under WSLg | Passed | Native select by keyboard, look picker, the real checkbox, a remembered synthetic capture on the next conversation, grouped drags from both windows. Software WebGL, `--no-sandbox`. |
| Headless Chromium screenshots | Taken | Voice page (light) at 1440 and 390 pixels wide, companion (dark glass) at 400×640 and 320×480, with and without settings open; no horizontal overflow and no companion page scrolling. |

Not run: the native voice workflow (it now runs both fixture smokes and the
real-model smoke without the fake permission prompt on Windows, macOS and Linux),
interactive macOS consent and recovery on Intel and Apple Silicon, Windows
privacy toggles and hosted-runner privacy defaults, physical microphones and
speakers, Linux portals and packaged sandboxing, grouped windows on native
Windows and macOS desktops, and listening tests of the three voices, speaker
consistency in the small model's interleaved replies and the robotic effect.


## Installed macOS microphone crash (2026-10-26)

Apple Silicon macOS 26.6.2: the installed v0.25.0 `.pkg` wrapper has no
`NSMicrophoneUsageDescription`. The 10:28 crash report identifies TCC termination
for that missing key, while its nested Electron shell contains the key and passes
`codesign --verify --deep --strict`. The installer template now declares usage in
the enclosing app too; see [ADR 0075](adr/0075-microphone-purpose-in-the-macos-installer-wrapper.md).
Template plist lint and the metadata regression/ADR tests passed locally.
Interactive consent with a corrected installed package, physical capture and
Intel macOS remain untested. The installed app and real permission state were
left unchanged.

## 2026-10-26 — Conversation ingestion and default OCR

On macOS arm64, reproduced the reported `learn` stop at conversation 1/167 from the local job log and traced the first 58,739-character exchange into full-model sentence-level ONNX inference during semantic chunking. The conversation path now chunks without model inference. With real cached Qwen embeddings and a disposable in-memory Qdrant, that exact transcript produced 11 chunks, completed in 113 seconds on the final CPU run, and published its completion watermark only after success. Chunk preparation took about 0.00014 seconds; the first persisted chunk appeared after about eight seconds. This is one transcript, not a throughput estimate for all 167.

Installed hashed base/CPU requirements in a disposable venv and read the expected text from a generated image and an image-only PDF using real RapidOCR/PDFium. Default OCR used wheel-bundled models and rendering libraries without system Tesseract or Poppler. The targeted Python suite passed 146 tests; an additional embedded-Qdrant interruption/resume regression passed separately. Go command/internal tests passed. Other release changes in this workspace have their own validation entries.

Native Windows, Linux and Intel macOS OCR execution, custom-language Tesseract execution and complete installers were not exercised. Wheel metadata checks required an Intel macOS OpenCV 4.11 pin to preserve the macOS 13 baseline. The installed application and real memory store were not patched or reindexed by these checks. See [ADR 0076](adr/0076-default-ocr-with-wheel-installed-models-and-renderer.md).

## Setup frees the runtime before replacing its files (2026-10-26)

See [ADR 0084](adr/0084-setup-frees-the-runtime-before-replacing-its-files.md). Machine: RTX 4080 Laptop, Windows 11, plus WSL2 Ubuntu. Windows runs used a scratch tree under `%TEMP%` with an isolated `USERPROFILE`; the installed runtime was only read, apart from one `uv cache prune` on its cache that removed nothing.

| Check | Result | Notes |
| --- | --- | --- |
| Field failure (installed Windows runtime) | Reproduced | Setup's `uv cache prune` had removed every archive the fresh venv was linked to (15,626 files, 438 MiB), leaving loaded DLLs with one link; switching to GPU from Settings then failed with "Access is denied". Prune on scratch caches with the same uv, locks and bootstrap kept every archive; the trigger was not reproduced. |
| Swap with a held, single-link runtime (native Windows) | Passed | The old install command failed exactly as in the field. `imprint bootstrap` then stopped the holding process, restarted an `imprint ui start` server, installed `onnxruntime-gpu` alone and passed the CUDA check in 12 s; the server came back under the same supervisor. |
| Settings switch back to CPU (native Windows) | Passed | `PUT /api/config/model.device` started bootstrap detached; it stopped a holding process, restarted the server that started it, installed the CPU build and removed the CUDA libraries, and the server came back with nothing pending. |
| Go tests natively on Windows | Passed | `cmd` (run with `-test.v`) and `queuelock`, including Python waiting while Go holds `runtime.lock`. |

Not run: the desktop app restarted by setup, the Windows installer, `imprint update`, macOS and an archive update.

## The imprint command in WSL homes (2026-10-27)

See [ADR 0091](adr/0091-wsl-homes-with-a-connected-client-get-the-imprint-command.md). Machine: Windows 11 with WSL2 Ubuntu. Windows runs used a cross-compiled `imprint.exe` and the checkout's `imprint/` in `%TEMP%`, the installed venv's Python reused read-only, disposable Linux homes and Windows data. No real client profile was registered.

| Check | Result | Notes |
| --- | --- | --- |
| Linux symlinks seen from Windows | Measured | Through `\\wsl.localhost`, `os.ReadFile` of a valid absolute or relative Linux symlink reports the path missing; `os.Lstat` finds it as a non-regular file. |
| Go tests (Linux, and natively on Windows) | Passed | `cmd` WSL and cleanup tests: the command's install, ownership, per-home release and removal on disconnect. Windows lacks the symlink privilege, so the symlink case logs and is skipped there. |
| Python bridge tests | Passed | The CLI refusal lists supported commands without bridge usage; the new test fails against the previous bridge. |
| Real WSL → native Windows smoke | Passed | `scripts/test-wsl-interop.py`: the command is installed, approves a Linux root and refuses `update` (exit 2). A Linux installation's link survives setup and removal. A Windows-drive home gets and loses the command, and the command stays until the home's last registration goes. MCP discovery found 19 tools. |
| Regression: ownership by reading the file | Reproduced | A build that checks with `os.ReadFile` alone replaced a Linux installation's `~/.local/bin/imprint` link with the bridge command; this change kept it and warned. |

Not run: an Inno Setup compile of the WSL page's new description, the GUI installer, dashboard enrollment, and distributions other than Ubuntu.


## Audio ingestion — 2026-10-28

Implemented local file transcription with faster-whisper 1.2.1, CTranslate2 4.8.4
CPU/int8 and native PyAV decoding. The default model is revision-pinned Large v3
Turbo; multilingual Small is an explicit lighter choice. See [audio ingestion](audio-ingestion.md)
and [ADR 0096](adr/0096-cpu-whisper-runtime-for-audio-imports.md).

Executed on Linux x64 / WSL with Python 3.12:

- Checksum-verified model download/load and real English speech transcription
  using whisper.cpp's public JFK WAV fixture.
- Real Romanian speech from Google FLEURS `ro_ro` test file
  `10014289053103326436.wav`, with automatic `ro` detection and segment timestamps.
- Both model choices executed. Large v3 Turbo improved the Romanian sentence,
  but still misrecognized a numeral. This is functional evidence, not an
  accuracy benchmark or a claim of exact transcription.
- A synthetic recording joining the English fixture and Romanian fixture across
  the 30-second window boundary retained both languages and relative timestamps,
  using `scripts/smoke-audio-ingest.py` and a disposable data directory.
- Native bounded WAV, MP3, M4A and FLAC codec tests, including stereo 48 kHz to
  mono 16 kHz resampling; fixture storage tests for
  snapshot caching, failed replacement, forced retranscription and workspace
  isolation. The profile-refresh regression reuses vectors while updating
  transcription metadata when two models produce identical text.
- 79 targeted Python checks, 20 Chromium checks, dashboard production build,
  type checking, lint, runtime smoke and Go setup/runtime tests passed.

The CPU overlay's CPython 3.12 wheel metadata covers existing Linux x64/arm64,
Windows x64 and Mac Intel/ARM targets. Native Windows/macOS/Linux ARM execution
and installer updates with this overlay have **not** been executed here. PyAV
15.1.0 retains a macOS 13 ARM wheel; that compatibility pin does not itself
establish native execution. Mixed speech within one window, room noise,
speaker diarization, multi-hour performance and video ingestion remain unvalidated
or unimplemented as described in the audio guide.
