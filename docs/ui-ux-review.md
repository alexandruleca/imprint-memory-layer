---
title: UI and UX review
description: Dashboard interaction findings, implemented changes and the next product improvements.
---

## Product goal

A user should understand whether Imprint is connected, what was indexed, how fresh a result is, and what an action will change. The primary journey is connect → select sources → ingest → search/inspect → maintain/delete. A graph is useful exploration, but first successful retrieval is the more important onboarding outcome.

## Implemented improvements

| Finding | Result |
| --- | --- |
| Fixed backend port in dashboard helpers | Same-origin API access works with another local port; explicit build-time override remains. |
| Empty and failed searches looked alike | Idle, loading, no matches and server failure have distinct messages; users can retry. |
| Older async work could overwrite a new search | Superseded search/detail requests are cancelled and guarded. |
| Limited retrieval control | Project/type filters and hybrid/semantic mode; first result selected for inspection. |
| Similarity presented without its limits | Score explanation, match type and available source hash/mtime support source verification. |
| Full-memory ID lookup failed for public logical IDs | Shared lookup accepts public IDs and legacy UUIDs. |
| Memory/destructive dialogs lacked dependable focus behavior | Labelled modal dialogs (shadcn `Dialog`/`AlertDialog` on Base UI, ADR 0039; first built on the native modal, ADR 0008), Escape, focus trap/restoration and confirmation reset on reopen. |
| Connection failure was hidden | Visible service banner with retry and bounded health requests. |
| Navigation consumed narrow screens | Compact mobile sidebar, labels/current page, responsive search/stats and skip link. |
| Desktop startup waited without feedback and had unclear shutdown | The shell opens a splash before API startup, retains it until dashboard load, exposes retry/log access, and separates hide from explicit Quit in the sidebar/tray. See ADR 0025; native graphical verification remains pending. |
| In the desktop window, dialogs opened from the top bar (Manage workspaces, Update) could not be clicked; dragging anywhere moved the window | The frameless window's drag regions are hit-tested by Chromium ahead of the page, ignoring stacking, and `app-region` is inherited. The top bar was a drag region that contained its controls, so dialogs and popover internals rendered inside it inherited `drag`; only the bar's empty space and the sidebar header are handles now (`app-drag`). Marking overlays `no-drag` on top of a handle (5075bd2) did not help: v0.19.0's Update dialog still moved the window. While a dialog, alert dialog, sheet, popover, menu or select is open the handles are therefore not drag regions at all (`globals.css`, ADR 0039); tooltips leave them alone. A nested dialog's Escape closes only that dialog. Browser tests check the handles' computed `app-region` with each overlay type open and closed; Electron's own hit-testing on Windows, macOS and Linux still needs a native check. |
| Existing React lifecycle lint failures | Derived state, keyed request results and deliberate client hydration replace synchronous effect resets. Lint is a CI gate. |
| Container builds failed in Google's dynamic font import | Bundle the pinned, OFL-licensed Inter variable font (the website's face) through `next/font/local`; build and browser font loading use local assets. See ADR 0008. |
| The dashboard looked unlike the website | One light, black-and-white theme: near-white background, white cards, black primary actions, neutral greys for data, and status colours kept for state only. The graph uses neutral node shades on a light canvas. |
| The active workspace was only visible in Settings | A top bar on every page names the active workspace. Its menu switches (the dashboard reloads), creates and switches (**New workspace…** opens a naming dialog), and opens **Manage workspaces**: create, edit roots, wipe, copy/move and delete, with deletion typed to confirm and blocked for the active and default workspaces. |
| Updating meant finding the right installer or command | The top bar shows **Update to vX.Y.Z** when a newer release exists; its dialog explains the steps for this installation, starts the update, tails its log and reloads when the new version runs (ADR 0023, ADR 0050). The sidebar version reads this installation's CLI instead of showing `unknown`. |
| One document could only be ingested by typing a path into the directory action | **Ingest Document** sits next to Ingest URL. It browses the server's files and queues `imprint ingest <file>`, the same extractor path the CLI uses; **Ingest Directory** now picks folders only. |
| Graph labels were unreadable and controls covered the canvas | Labels sit below nodes and overlapping ones give way to bigger nodes; projects keep their colour, topics are rings, edges are tinted by the project they leave; the canvas matches the app. A toolbar (zoom, fit, re-run layout) and a settings popover replace the always-open physics panel, and the legend doubles as the node-kind filter. Small scopes show at natural size instead of being zoomed to fill. |
| Graph navigation was hard to follow | The scope lives in the URL, so back/forward and links work; breadcrumbs name each kind. Find highlights and cycles matches (`/` to focus, Enter, Escape). A details panel lists connections by kind, copies source paths and can limit the graph to a node's connections. Display toggles restyle without re-running the layout; a loading scope keeps the previous graph visible; empty and failed loads say so, with retry. On phones the details are a bottom sheet and the focused node stays above it. |
| Controls looked and behaved differently on each page | One kit: the vendored shadcn components (ADR 0039) for buttons, inputs, selects, checkboxes, radios, switches, menus, popovers, dialogs, progress, spinners, empty states, tables and hover hints. Clickable rows (topics, sources, chunks) are real buttons. |
| Actions finished silently | Toasts confirm actions that showed nothing (settings saved, a root approved, a workspace created, deleted or wiped, memories copied or moved, pairing paused or forgotten, a sync code copied) and report their failures. A queued job this tab saw is announced when it finishes, fails or is cancelled, whichever page is open. Async actions that showed nothing while running now show a spinner or skeleton. |
| An ingest outside the workspace's roots was queued and then failed with "outside workspace roots" | The server checks the path before queueing and answers 409 `root_not_approved` with the directory to approve. Every ingest and refresh entry point (quick actions, Commands) goes through one shared request, which asks "Approve <folder> as a root of workspace <name>?". The question names any other workspace that already has the folder, so approving confirms that sharing (ADR 0015). The request adds the root and submits the job again; declining queues nothing. |
| Pages showed two or three scrollbars at once (Queue: the document, the page and the job log) | `<main>` is the one page scroller and, now positioned, also the containing block of the page. The queue's screen-reader status labels are absolutely positioned; with no positioned ancestor they were laid out against the viewport and stretched the document into a second scroller behind the page. The sidebar fills the window frame instead of `100vh`, and Echo's surface fills the page area instead of `100vh` minus the top bar. Topics, Projects and Sources share one list-and-details layout (`MasterDetail`): the details flow with the page, and only the list is a capped pane that scrolls on its own. The panes stack below `lg` instead of squeezing three columns onto a phone. Search results flow with the page and the selected memory stays in view beside them, replacing a results box sized `65vh`. The remaining inner scrollers are deliberate: fixed-size panels (job log, command output, excerpts, export history), dialog bodies, and Chat's session list and conversation. The job log and command output follow new lines by scrolling only themselves; before, they pulled the page along on every line, and opening Commands on a phone jumped to the output panel. Scrolling a log up pauses the following until the reader returns to its bottom. `page-scroll.spec.ts` counts scroll containers on Queue, Topics, Search and Settings at 1440, 768 and 390 pixels wide; `log-follow.spec.ts` streams output into both logs and checks that the page stays put. |

Browser checks exercise mobile overflow, empty/error/retry, filters, stale responses, full-memory modal and reconnection. They mock API responses to test interaction deterministically; backend/native-model smoke provides separate retrieval evidence. This is not a full accessibility certification or native desktop GUI test.

## Paired sync and presentation follow-up

The Sync page now offers explicit automatic pairing on both sides and a paired-device list with workspace mapping, relay, approved direction, last successful sync and errors. Pause/enable controls apply locally; forgetting requires an accessible confirmation naming both devices and all workspaces. Offline remote removal stays visibly pending. Browser checks cover opt-in defaults, pause/resume, cancellation of the forget dialog and confirmed revocation. Backend relay tests verify enforcement separately (ADR 0020).

The presentation homepage leads with persistent project memory, local retrieval, encrypted workspace sync and shared cross-device knowledge. Benchmark percentages are removed from the hero and the large homepage summary is removed; the footer links to detailed measured evidence with uncertainties. The other design session's theme, logo and graph redesign are preserved.

## Next improvements

| Priority | Improvement | Acceptance criterion |
| --- | --- | --- |
| P1 | Guided onboarding and integration health | New install can select one source, see a completed job, confirm a host MCP connection and run a known query. No invisible model download. |
| P1 | Freshness and queue progress | Show last successful source refresh, retained stale chunks after failure, extractor/model download phase, bytes/progress and actionable retry. |
| P1 | Workspace clarity | Warn when switching with queued jobs; show each job's captured workspace. |
| P1 | Destructive action review | Screen-reader/keyboard walkthrough for wipe, project/source deletion, migration and workspace removal; cancellation and error recovery retain context. |
| P1 | Accessible graph alternative | Searchable list/table exposing the same projects/topics/sources without relying on canvas, color or pointer interaction. Find, the legend and the details panel's connection list are keyboard-usable already; the canvas itself is not. |
| P2 | Search refinement | Project picker, source filter, result pagination/diversity and provenance-to-live-source comparison; preserve query/filter state in navigation. |
| P2 | Consistent async UX | Apply visible loading/empty/error/retry conventions to all pages; graph topic errors currently need richer distinction from no data. |
| P2 | Resource and privacy controls | Show selected paths/exclusions, model disk budget, optional remote-provider use and retention controls before work starts. |
| P2 | Desktop polish | Native first-launch, tray, resize, zoom, reduced motion, light/dark contrast and high-DPI checks on supported OS targets. |

Measure time to first result, ingest recovery success, error discoverability and keyboard task completion. Test with both first-time users and developers with large existing workspaces. See [ADR 0008](adr/0008-dashboard-retrieval-and-accessibility.md).
