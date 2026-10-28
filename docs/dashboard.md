---
title: Dashboard
description: What each page of the local dashboard shows and does, in a browser and in the desktop app.
---

The dashboard is Imprint's local web interface. It shows what your AI tools remember and where it came from, adds new sources, and runs the same commands as the CLI through the [queue](queue.md). `imprint ui open` opens it in a browser; the desktop app shows the same pages in its own window. Every action goes through the [HTTP API](api.md), and every job runs in the active [workspace](workspaces.md).

## Layout

**Sidebar.** Dashboard, Graph, Search, Chat, Voice, Topics, Projects, Sources, Sync, Settings, Commands and Queue, with the installed version at the bottom. On narrow screens it shows icons only, each with a hint naming the page.

**Top bar**, on every page:

- While a job runs, a pill shows it, for example `Learn conversations · embedding · 3/112 conversations`, and links to Queue.
- While Echo is being set up, a pill shows its progress and links to Voice.
- **Update to vX.Y.Z** appears when a newer release exists. Its dialog explains the steps for this kind of installation, starts the update, shows the updater's log and reloads once the new version runs. Memories, workspaces and settings are kept, and running jobs finish first. An installation that cannot update itself says why ([ADR 0023](adr/0023-update-by-installation-type.md), [ADR 0050](adr/0050-registered-dashboard-server-and-supervised-restart.md)).
- The workspace switcher names the active workspace. Its menu switches to another one (the dashboard reloads), creates one with **New workspace…** and switches to it, and opens **Manage workspaces…**.

**Connection and job notices.** When the local service stops answering, a banner says so and offers **Retry connection**; the dashboard reconnects on its own. A job this tab saw waiting or running is announced when it finishes, fails or is cancelled, whichever page is open; a failure links to Queue.

## Pages

### Dashboard

- **Add to memory** indexes something new: a **Folder**, a **Document**, an **Audio** file, a **Web page** (with an optional project, fetch-again and link-following options) or **Conversations** (`imprint learn`). Each choice shows the command it runs. The job is queued; the card says whether it started now, waits at a position, or joined an identical job that was already waiting ([ADR 0068](adr/0068-merge-identical-waiting-jobs.md)). Refresh, retag and the rest live in Commands.
- The running job is shown with its phase, progress, time so far and left, and **Cancel**.
- Totals of memories, projects, languages and topics, and their breakdown by project, language, domain and type.
- An empty workspace shows the steps to build a first memory. A memory index built with a different embedding model says it needs rebuilding.

### Search

- A query box (Ctrl/⌘+K focuses it) with filters for project, memory type, and method: **Semantic + exact terms** (hybrid, the default) or **Semantic only**.
- Results show their match type, project and type; the first is selected. Its details show when the source was modified, its content fingerprint, the semantic similarity (a ranking signal, not confidence) and a warning when the source may be stale.
- **Open full memory** shows the whole memory, and **Read in context** opens its source at that point.

### Chat

- Chats are listed by date; each can be renamed or deleted (deleting asks first). The last open chat is reopened.
- Replies come from the configured chat model, local or a provider. Memory searches and reads it makes show as rows that expand to their results. The first message to a local model sets it up and shows the download and load progress.
- A notice says when the model chats without tools, with a link to the chat settings. A reply can be stopped, copied or retried.

### Topics

- Topics with their memory counts, and a filter. Choosing a topic lists the conversations and files it came from, named by a conversation's first question, with a project filter when the topic spans projects.
- **Read conversation** (or **Read source**) opens the source whole with the topic's passages marked; clicking an excerpt opens it at that passage.
- **Graph** shows the topic in the graph; **Migrate…** copies or moves its memories to another workspace. `?topic=NAME` links to a topic.

### Projects

- Projects with their memory counts. A project shows its topics (each opens in Topics), kinds of memory, domains and languages as proportional bars, and a table of its sources.
- **Move or merge…** moves the project, or selected sources, into another project after a review of what moves ([ADR 0036](adr/0036-move-memories-between-projects.md)). **Migrate…** copies or moves it to another workspace.
- **Review project associations** lists suggested links from sources to other projects; each can be accepted, rejected or removed, and **Analyze existing sources** queues a new analysis ([ADR 0099](adr/0099-sources-can-belong-to-several-projects.md)). A source linked from another project cannot be moved.

### Sources

- Every indexed source with its chunk count, and a filter by title or key. A source shows where it was recorded, its tags and topics, and its chunks; each chunk can be viewed or read in context.
- **Workspace roots and cleanup** edits the workspace's approved roots and purges indexed files or directories. A purge is previewed first and confirmed by typing the workspace name; purged paths stay excluded from ingestion until **Allow re-ingestion** ([ADR 0015](adr/0015-workspace-roots-and-source-purge.md)).

### Graph

- Projects, topics, sources and chunks as a graph. Click selects a node and shows its details; double-click opens it. Breadcrumbs lead back up, and the scope is in the URL (`?scope=project:X`, `topic:X`, `source:X` or `chunk:X`), so back, forward and links work.
- **Find in graph** (`/` focuses it) highlights matches; Enter and Shift+Enter step through them and Escape clears.
- The details list a node's connections by kind, copy a source's path, read a source in full and can limit the graph to a node's connections. On phones they are a bottom sheet.
- The legend hides or shows each kind of node. The toolbar zooms, fits, re-runs the layout and opens display and layout settings, which this browser remembers.

### Queue

Imprint runs one job at a time ([ADR 0064](adr/0064-single-slot-command-queue-and-shared-lock.md)).

- **Running** shows the current job with its progress, newest output line, full output on request and **Cancel**.
- **Up next** lists waiting jobs; each can be removed. A job that absorbed identical requests shows how many.
- **Background tagging** shows the LLM tagging backlog's state in one line, with **Resume now** when it waits ([ADR 0067](adr/0067-background-llm-tagging-that-yields-to-every-job.md)).
- **History** lists finished jobs. Any job opens a panel with its parameters, workspace, times, process, command and output.

### Commands

The CLI's commands as forms, grouped as the CLI lists them. The catalog comes from the server (`GET /api/commands`), so a command added to the CLI appears here without a dashboard change.

- **Index:** Find project associations, Learn conversations, Ingest directory (a folder or a file), Refresh files, Ingest URL, Refresh URLs.
- **Maintain:** Retag memories, Scrub secrets, Export snapshot.
- **Clients:** Connect clients and Disconnect clients, for one client or all, with each client's connection state. They only change client settings: connecting installs and starts nothing, and disconnecting stops neither the dashboard nor Qdrant ([ADR 0053](adr/0053-connect-clients-without-touching-the-runtime.md)). Disconnecting asks first.
- **Inspect:** Status, Diagnose and Show config.

**Run** queues the command, and its output streams into the panel beside it. Leaving the page does not cancel the job.

### Sync

- **Live sync** serves a workspace (two-way, send only or receive only, optionally through a relay) and shows a code to enter on the other device, or receives with such a code. The serving device approves each request once or trusts the device; a push asks before it sends ([sync](sync.md)).
- **Paired devices** lists devices paired for automatic sync, with their state and when each was last seen. Automatic sync can be paused and resumed; a device can be forgotten on both devices or removed from this one, each confirmed first ([ADR 0020](adr/0020-explicit-pairing-and-automatic-workspace-sync.md), [ADR 0073](adr/0073-remove-a-pairing-from-one-device.md)).
- **Snapshots** downloads a workspace snapshot or imports one. **Desktop App Exports** scans Downloads for Claude Desktop and ChatGPT Desktop exports and lists those indexed.

### Settings

One section at a time, chosen from a list; `#settings-chat`, `#wsl` and the other section ids link to a section. The filter searches every section and counts the matches in each.

- **Configuration** edits the same values as `imprint config`, in this device's `config.json` ([configuration](configuration.md)). A value set by an environment variable is read-only. Reset (Remove, for a secret) deletes the `config.json` override. Running servers apply changes when they restart.
- **This device:** **Workspaces** creates, switches, edits the roots of, wipes and deletes workspaces, and copies or moves memories between them; wiping and deleting are confirmed by typing, and the default and active workspaces cannot be deleted. **WSL profiles**, on a Windows installation, checks each distribution's clients (checking starts stopped distributions) and enrolls or removes them through the queue.

### Voice

Echo, the voice companion ([live voice](live-voice.md)).

- The first visit sets Echo up: **Download & set up Echo** fetches its runtime and models, with its progress.
- **Start conversation** starts talking, by push to talk or hands-free; a reply can be interrupted, and a thought can also be typed.
- **Companion settings** picks the reasoning model (the chat model, or the small LFM voice model) and the microphone options; the picker chooses Echo's look and voice.
- **Float Echo** opens Echo in its own small window: the desktop companion window, or a browser popup. Voice stops when the page is hidden.

## Shared behavior

**Approving a root.** Ingest and refresh only read inside a workspace's approved roots. When a job from any page asks for a folder outside them, the dashboard asks "Approve <folder> as a root of workspace <name>?" and names any other workspace that already has it, so approving confirms that sharing. Approving adds the root and submits the job again; cancelling queues nothing ([ADR 0015](adr/0015-workspace-roots-and-source-purge.md)).

**Reading a whole source.** Topics, Projects, Sources, Graph and a memory's **Read in context** open a conversation or file whole from its stored snapshot. A conversation shows its messages in order, with tool calls and their output, thinking and injected context folded between them; opened for a topic, its passages are marked and stepped through ([ADR 0094](adr/0094-the-dashboard-reads-whole-sources-from-their-snapshots.md)).

**Lists and details.** Topics, Projects, Sources, Commands and Settings show a list beside the details. On wide screens both scroll on their own and the header stays put; below that they stack and the page scrolls.

**Picking a path.** Folder and file pickers browse the server's filesystem, so paths are the service's own: on a Windows installation, WSL folders are under `\\wsl.localhost\<distribution>`.

**Copying or moving between workspaces.** **Migrate…** on Topics, Projects and Sources, and **Copy / move memories…** in Workspaces, copy (the default) or move memories by project, topic or source, with a dry run first.

## Desktop app

- The window opens a splash while the local service starts, with the startup log and **Try again** when it fails ([ADR 0025](adr/0025-desktop-owned-background-service.md)).
- Closing or minimizing hides the window and leaves Imprint running, with a small floating button to open it again. **Quit Imprint** (sidebar, tray, or the floating button's menu) stops the app and its background service.
- The tray opens the dashboard, floats Echo or quits. The top bar's **Floating Echo** switch shows or hides Echo's window.
- The window's empty top bar and the sidebar header move it. While a dialog, menu or popover is open they do not, so the overlay stays usable ([ADR 0039](adr/0039-shadcn-overlays-and-desktop-drag-handles.md)).

## Design and tests

The dashboard uses one component kit, vendored shadcn components on Base UI ([ADR 0039](adr/0039-shadcn-overlays-and-desktop-drag-handles.md)), with a light, black-and-white theme and the bundled Inter font ([ADR 0008](adr/0008-dashboard-retrieval-and-accessibility.md)). Playwright tests under `imprint/ui/tests` drive the pages against mocked API responses, at desktop, tablet and phone widths. They are not an accessibility certification or a native desktop check; see [platform validation](platform-validation.md) for what has run on each platform.
