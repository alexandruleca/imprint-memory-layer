---
title: Dashboard
description: What each page of the local dashboard shows and does, in a browser and in the desktop app.
---

The dashboard is Imprint's local web interface. It shows what your AI tools remember and where it came from, adds new sources, and runs the same commands as the CLI through the [queue](queue.md). `imprint ui open` opens it in a browser; the desktop app shows the same pages in its own window. Every action goes through the [HTTP API](api.md), and every job runs in the active [workspace](workspaces.md).

## Layout

**Sidebar.** Dashboard, Graph, Search, Chat, Voice, Recordings, Reminders, Topics, Projects, Sources, Sync, Settings, Commands and Queue, with the installed version at the bottom. On narrow screens it shows icons only, each with a hint naming the page.

**Top bar**, on every page:

- While a job runs, a pill shows it, for example `Learn conversations · embedding · 3/112 conversations`, and links to Queue.
- While Echo is being set up, a pill shows its progress and links to Voice.
- In the desktop app, while Imprint listens for “Hey Echo”, a pill says so, with **1 hour** (pause) and **Off** ([Echo voice](live-voice.md#hey-echo)).
- **Update to vX.Y.Z** appears when a newer release exists on the update channel: stable releases, or dev builds as well once **Settings → Updates** is set to dev, which the dialog marks as a dev build ([ADR 0108](adr/0108-installations-opt-in-to-dev-builds-in-settings.md)). Its dialog explains the steps for this kind of installation, starts the update, shows the updater's log and reloads once the new version runs. Memories, workspaces and settings are kept, and running jobs finish first. An installation that cannot update itself says why ([ADR 0023](adr/0023-update-by-installation-type.md), [ADR 0050](adr/0050-registered-dashboard-server-and-supervised-restart.md)).
- The theme menu chooses **Light**, **Dark** or **System** (follows the operating system as it changes). The choice is kept in this browser or desktop app and applied before the page first paints; every page open in the same browser or app follows a change at once ([ADR 0154](adr/0154-the-dashboard-and-desktop-app-follow-a-light-dark-or-system-theme.md)).
- The workspace switcher names the active workspace. Its menu switches to another one (the dashboard reloads), creates one with **New workspace…** and switches to it, and opens **Manage workspaces…**.

**Connection and job notices.** When the local service stops answering, a banner says so and offers **Retry connection**; the dashboard reconnects on its own. A job this tab saw waiting or running is announced when it finishes, fails or is cancelled, whichever page is open; a failure says why and offers **Retry** and **Open queue**.

## Pages

### Dashboard

- **Add to memory** indexes something new: a **Folder**, a **Document**, an **Audio** file, a **Web page** (with an optional project, fetch-again and link-following options) or **Conversations** (`imprint learn`). Each choice shows the command it runs. The job is queued; the card says whether it started now, waits at a position, or joined an identical job that was already waiting ([ADR 0068](adr/0068-merge-identical-waiting-jobs.md)). Refresh, retag and the rest live in Commands.
- The running job is shown with its phase, progress, time so far and left, and **Cancel**.
- **Memories**: the total, how many arrived in the last 7 days, and a column per calendar month for the last year (hover a column for its count; screen readers get the table). Beside it, the number of projects, sources, topics and languages; each opens its page.
- **Needs attention**: a job that failed in the last day, memories waiting for LLM tagging and what background tagging is doing about them, or "Nothing needs you right now". **Recently added** lists the sources of the newest memories, named by a conversation's first question, and opens their project.
- **What is in memory**: the largest projects, topics, types, languages, domains and layers, each with its count and share of all memories; a project opens on Projects.
- An empty workspace shows the steps to build a first memory. A memory index built with a different embedding model says it needs rebuilding.
- The page reads one summary that the service keeps per workspace and rebuilds in the background after changes, so it opens at once even in a large workspace. The header says how old it is, or **Updating…** while a newer one is prepared; the refresh button asks for an up-to-date one. A reload shows the last summary of the same workspace until the new one arrives ([ADR 0152](adr/0152-dashboard-summaries-are-served-stale-while-they-refresh.md)).

### Search

- A query box (Ctrl/⌘+K focuses it) with filters for project, memory type, and method: **Semantic + exact terms** (hybrid, the default) or **Semantic only**.
- Results show their match type, project and type; the first is selected. Its details show when the source was modified, its content fingerprint, the semantic similarity (a ranking signal, not confidence) and a warning when the source may be stale.
- **Open full memory** shows the whole memory, and **Read in context** opens its source at that point.
- **Open in graph** shows the results, with their sources, projects and topics, as a graph scope ([Graph](#graph)). A link to `/search/?q=…` (with `project`, `type` or `mode`) searches on arrival.

### Chat

- Chats are listed by date; each can be renamed or deleted (deleting asks first). The last open chat is reopened. A conversation with Echo is a chat too, marked with a sound-wave icon ([ADR 0146](adr/0146-echo-saves-its-conversations-in-chat-history-and-carries-chats-on.md)).
- **Continue with Echo**, above the open chat, opens Voice ready to carry that chat on by voice; what is said there is added to it.
- Replies come from the configured chat model, local or a provider. Memory searches and reads it makes show as rows that expand to their results. The first message to a local model sets it up and shows the download and load progress.
- A notice says when the model chats without tools, with a link to the chat settings. A reply can be stopped, copied or retried.
- A reply goes on when you open another chat, leave the page or lose the connection; the list marks a chat still answering, and opening it shows the reply so far and the rest as it comes. Only **Stop reply** stops it. Replies are written one at a time: a question in another chat waits, and says so ([ADR 0145](adr/0145-a-request-runs-until-it-is-answered-or-explicitly-stopped.md)).
- A reply can ask you a clarifying question and wait: it shows as a card in the reply, with a button per choice and, when allowed, a box for your own words; the card takes the focus and Enter sends. While it waits, the message box answers it too. Any window, including one opened later, can answer; after 30 minutes without an answer the reply goes on with its stated assumption. Chat history shows the question with its answer ([ADR 0159](adr/0159-a-reply-can-ask-the-user-and-wait-for-the-answer.md)).
- When a long chat no longer fits the model's context, its oldest exchanges are summarized ("Summarizing the earlier conversation…" shows meanwhile). A divider after the last summarized exchange, **Summary of earlier conversation**, opens the summary the model now reads in their place. The model can still look up anything said earlier in the chat; that step shows as "Searched earlier in this conversation for …" ([ADR 0141](adr/0141-chat-summarizes-what-no-longer-fits-and-can-look-back.md)).

### Topics

- Every topic with its memory count and largest projects, 50 a page, searched and sorted (by size or name) on the server; the count beside the title is the workspace's ([ADR 0150](adr/0150-topics-page-on-the-server-and-workspace-copies-are-previewed.md)). Choosing a topic lists the conversations and files it came from, named by a conversation's first question, 20 a page (10 or 50 by choice), with a search and a project filter when the topic spans projects.
- **Read** opens the source whole with the topic's passages marked; clicking an excerpt opens it at that passage. A source's menu moves it to another project, copies or moves it to another workspace, or shows it in the graph.
- Checkboxes select sources across pages: **Move to project…** moves those of one project together ([ADR 0036](adr/0036-move-memories-between-projects.md)), and **Copy to workspace…** copies or moves any of them.
- **Graph** shows the topic in the graph; **Copy to workspace…** copies or moves its memories to another workspace. `?topic=NAME` links to a topic.

### Core memories

Statements about you, in two layers ([ADR 0129](adr/0129-core-memories-are-user-approved-and-start-every-session.md), [ADR 0143](adr/0143-agents-receive-generalised-guidelines-core-memories-are-kept-as-records.md)). **Recorded** core memories are the specific ones agents (a stored `preference`) and Organize find; they are kept for reference and need no approval, and no session receives them. **Guidelines** generalise them: Organize drafts them from groups of alike records and you approve them, or you write one. Every agent session receives the active guidelines, through the MCP server's handshake instructions, the dashboard's chat and Echo. Both layers sync to paired devices.

- The strip under the title shows how many of a session's 3,000 estimated tokens the guidelines for every project take, and says when some no longer fit. Records never count.
- **Write a guideline** (`n`) writes one, active at once, for every project or one project.
- **Conflicts** (opens first while any wait) shows newer information that contradicts an older core memory, the earlier and newer text with the reason ([ADR 0144](adr/0144-newer-core-memories-that-contradict-older-ones-wait-for-the-user.md)). A newer record against an older one: **Use the newer** (retires the older, `a`), **Keep the earlier** (retires the newer, `r`) or **Keep both** (`b`). Organize's rewrite of a guideline you approved: **Overwrite** (`a`) or **Leave as is** (`r`).
- **Guidelines** lists the active ones; each says how many recorded core memories it generalises and lists them.
- **Proposed** lists Organize's drafts, with the records they generalise and how many tokens each would add. A draft that rewrites a guideline shows the one it **Replaces**; approving it retires that one. Each can be edited, given another kind or scope, approved or rejected.
- **Recorded** lists the records: edit or retire one, or **Make a guideline** from one or several selected (`g`). A record a guideline already covers says so, and one with an open conflict is marked.
- **Retired** lists retired and rejected guidelines and records, labelled by layer; restore any.
- The lists work like Organize's: search, filters, 20 rows a page, select several for bulk actions, and **Undo** in the toast or `z`. Undoing an approval or a rejection returns the guideline to the proposals ([ADR 0136](adr/0136-review-pages-use-paged-tabs-and-decisions-reopen.md)); undoing a settlement reopens the conflict and restores what it retired. `/core/#conflicts`, `#guidelines`, `#proposed`, `#recorded` and `#retired` link to a tab; `1`–`5` switch.

### Organize

**Run Organize** queues a job that writes a short profile of each project, finds a project for memories sitting in catch-alls (no project, Desktop, Code, unassigned, URLs, desktop conversations), records core memories and drafts guidelines from them, noting newer ones that contradict older ones ([ADR 0130](adr/0130-reorganization-is-proposed-by-a-background-job-and-reviewed.md), [ADR 0143](adr/0143-agents-receive-generalised-guidelines-core-memories-are-kept-as-records.md), [ADR 0144](adr/0144-newer-core-memories-that-contradict-older-ones-wait-for-the-user.md)); its menu runs it **without the local helper**, which keeps to the folder-path and project-name evidence. With `organize.background` on, Organize also runs by itself when the queue is idle, after background tagging, and looks only at what changed ([ADR 0132](adr/0132-organize-runs-in-the-idle-slot-after-background-tagging.md)).

The strip under the title says whether it runs by itself and when it last passed, whether strong matches move by themselves, the progress of a running pass, and what holds moves back. It also gives the last run and links to the guideline proposals and the conflicts waiting on Core memories. When a run ends, its proposals come in by themselves. The page reviews in four tabs ([ADR 0136](adr/0136-review-pages-use-paged-tabs-and-decisions-reopen.md)); `/organize/#review`, `#moved`, `#decided` and `#profiles` link to one.

- **To review** lists the proposals that wait, by target project: a rail of projects by count (a select in a narrow window), the largest open first. Each row shows the conversation's first question or the source, where it is from, the evidence (the project's folder path, its name, or a quote the local model found, with the project's name marked) and, with `organize.auto_move` off, **Strong evidence**. **Move** or **Keep here** decides one; **Move all** or **Keep all** the project's group; a finished group offers the next. Click a row or press Enter for its preview, the whole quote, the project's profile and **Read source**. A move is a placement rule, so it holds through refreshes.
- **Moved automatically** lists the sources Organize moved by itself because the evidence was strong: the source names exactly one project's folder, or one distinctive project name at least twice and no other, early in what it says ([ADR 0133](adr/0133-strong-placements-apply-by-themselves.md)). **New since you last looked** shows those after the last **Mark all as seen** in this browser. **Move back** moves one back and keeps it there; **Move back all** a project's group, after a confirmation. `organize.auto_move` off sends these to review instead.
- **Decided** lists your moves and keeps, and moves back, newest first; **Reopen** returns one to review, moving it back first if it was moved.
- **Profiles** shows what Organize thinks each project is, with links to the sources waiting to move there and those moved there by themselves; search and chat find the profiles too.
- Every list has a search, filters, 20 rows a page (10 or 50 are remembered in this browser) and checkboxes for deciding many rows at once. A decision shows at once with **Undo** in its toast; writes go to the server one request at a time, and one that fails comes back with its error and **Retry**. While a job holds the ingestion lock, **Move** and **Move back** wait; **Keep here** still works.
- Keyboard: `j`/`k` move between rows, `a` moves, `r` keeps, `u` moves back or reopens, `x` selects, Enter opens a row, `o` reads its source, `z` undoes, `[`/`]` change page, `/` searches, `1`–`4` switch tabs, and `?` lists them all, with a switch that turns single-key shortcuts off.
- Moves, proposals and decisions reach paired devices ([ADR 0134](adr/0134-placement-rules-and-organize-proposals-sync.md)).

### Projects

Projects and Sources work like Organize: tabs linked from the address, paged lists with a search, filters and checkboxes, and the same keys ([ADR 0139](adr/0139-projects-and-sources-browse-in-paged-tabs.md)).

- A rail lists the projects by memory count (a select in a narrow window), with a filter. The open project is in the address (`/projects/?project=NAME`); without one, the last project opened in this browser opens, else the largest. `J` and `K` step through them.
- The project's line gives its memories and sources, **Graph**, **Move or merge…**, which moves or merges the whole project into another after a review of what moves ([ADR 0036](adr/0036-move-memories-between-projects.md)), and **Copy to workspace…**, which copies or moves it to another workspace.
- **Overview** (`#overview`) shows what Organize's profile says the project is, its sources by kind (each opens them on the Sources tab), and its topics (each opens in Topics), kinds of memory, domains and languages as proportional bars.
- **Sources** (`#sources`) lists the project's sources as the Sources page does, with **Move N to…** for the selected ones. A source linked from another project shows where it belongs and cannot be moved from here.
- **Project associations…** lists suggested links from sources to other projects; each can be accepted, rejected or removed, and **Analyze existing sources** queues a new analysis ([ADR 0099](adr/0099-sources-can-belong-to-several-projects.md)). A source's menu opens them for just that source.

### Sources

Three tabs, linked as `/sources/#indexed`, `#excluded` and `#roots`; they act on the workspace the top bar shows.

- **Indexed** lists every source of the workspace, largest first, 20 a page (10 or 50 are remembered in this browser): a conversation by its first question, a file by its name and folder, with its project, kind, age and chunks. Search titles and paths; filter by kind (conversations, files, documents, web pages, pasted content, and memories without a snapshot) and project; sort by size, recency or name.
- **Read** (`o`) opens the source whole. A click or Enter opens its details: where it was recorded, its tags and topics, and its chunks a few at a time, each readable in place. Its menu shows it in the graph, moves it to another project, links it to another, copies or moves it to another workspace, or purges it.
- Select sources of one project to move them together. When an ingest, a move or sync changes the workspace while the list is open, a note offers **Refresh** instead of moving rows under the cursor.
- **Excluded** lists what purges keep out of ingestion, each with **Allow re-ingestion**. **Purge a path…**, or **Purge…** in a source's menu, previews what matches a source key or a file or directory path, and applies once the workspace's name is typed ([ADR 0015](adr/0015-workspace-roots-and-source-purge.md)).
- **Roots** edits the folders the workspace may ingest from.
- Keyboard: `j`/`k` move between rows, `x` selects, Enter opens a row, `o` reads it, `m` moves it, `a` allows an excluded path again, `[`/`]` change page, `/` searches, `1`–`3` switch tabs, and `?` lists them all.

### Graph

- Projects, topics, sources and chunks as a graph. Click selects a node and shows its details; double-click opens it. Breadcrumbs lead back up, and the scope is in the URL (`?scope=project:X`, `topic:X`, `source:X`, `chunk:X` or `search:X`), so back, forward and links work. Each scope shows the largest items of each kind; the legend says when there are more, and **Show more** raises "Items per group".
- **Find or search** (`/` focuses it): typing marks matching names in the graph, and Enter and Shift+Enter step through them; Escape clears. With no match on screen, or with Ctrl/⌘+Enter, Enter searches all memories with the Search page's hybrid search. The hits, their sources, projects and topics become a scope of their own with a ranked list beside it: a result focuses its node, a selected node marks its result, and double-clicking either reads the chunk. The list links to the Search page, whose **Open in graph** opens its query and filters here ([ADR 0149](adr/0149-graph-search-is-a-scope-of-its-own.md)).
- The details list a node's connections by kind, copy a source's path, read a source in full and can limit the graph to a node's connections. On phones they, and search results, are a bottom sheet.
- The legend hides or shows each kind of node. The toolbar switches between the **graph view** and the **star view**, zooms, fits, re-runs the layout and opens display and layout settings; this browser remembers them.
- The graph view draws on one canvas from a layout that runs a few milliseconds per frame, so it stays responsive at any workspace size; it follows the light or dark theme. Drag the background to pan, drag a node to move it, and scroll or pinch to zoom ([ADR 0148](adr/0148-the-graph-draws-itself-on-a-canvas-and-as-a-star-system.md)).
- The star view shows the same scope as a slowly turning star system on its own night sky: projects as suns, topics, sources and chunks as smaller stars, connections as faint filaments with electric impulses running along them, as around Echo. Dragging turns it, scrolling zooms, and hover, click, double-click and find work as in the graph. In a search, hits glow and the impulses run from the query to them. It loads only when chosen, pauses while the page is hidden, stands still with reduced motion, and without WebGL the page returns to the graph view.

### Queue

Imprint runs one job at a time ([ADR 0064](adr/0064-single-slot-command-queue-and-shared-lock.md)).

- **Running** shows the current job with its progress, newest output line, full output on request, **Details** and **Cancel**, which asks first. When nothing runs or waits, one line says the queue is idle, and **Run a command** opens Commands.
- **Up next** lists waiting jobs; each can be removed. A job that absorbed identical requests shows how many.
- **Background work** shows the state of LLM tagging and background Organize, which fill idle time: memories waiting for tagging, workspaces Organize has not seen as they are, and **Resume now** when it waits after a cancel or failure ([ADR 0067](adr/0067-background-llm-tagging-that-yields-to-every-job.md), [ADR 0132](adr/0132-organize-runs-in-the-idle-slot-after-background-tagging.md)).
- **History** lists finished jobs 25 a page, newest first, grouped as Today, Yesterday, This week, Last week, This month, Last month and Older in your time zone, each group with its count; **Newer** and **Older** page through it and **Failed** shows only failures. A failed job shows why and has **Retry**, which queues the same command, arguments and workspace again; when an identical job already waits or runs, the row says so instead ([ADR 0068](adr/0068-merge-identical-waiting-jobs.md)). Any job opens a panel with **Retry** or **Run again**, its error, parameters, workspace, times, process, command and output, which can be copied.

### Agents

Every AI client that uses Imprint starts its own MCP server, and each one is a session here ([ADR 0117](adr/0117-the-dashboard-shows-each-agent-session-and-its-calls.md)).

- **Clients** totals every recorded session by client: sessions, how many run now, calls, errors and last activity. Clicking a client shows only its sessions; clicking it again shows all. On a phone the clients scroll sideways.
- **Filter** narrows the sessions to a folder, client or workspace name; Escape clears it.
- **Running now** keeps connected sessions on top, with a pulsing dot, how long each has run and when it last called a tool.
- **History** lists finished sessions by when they were last active, under Today, Yesterday, This week, Last week, This month, Last month and then each earlier month, in this computer's time zone and week. Each heading counts its sessions and stays at the top while its sessions scroll. A row shows the folder, client, calls, errors and how long the session ran; times are short within a group and full on hover.
- The history loads 50 sessions at a time: the next page loads when the end of the list scrolls into view, or with **Load older sessions**. The page refreshes the newest sessions every 3 seconds without dropping pages already loaded or moving the list ([ADR 0151](adr/0151-agent-sessions-page-by-last-activity-with-a-cursor.md)).
- A session opens a panel with its totals, folder, workspace, times and process, and its tool calls, newest first, with shortened and redacted arguments, errors, duration and time; calls from more than one day are grouped the same way. A running session's calls update live, and older calls load 100 at a time.

### Commands

The CLI's commands as forms, grouped as the CLI lists them. The catalog comes from the server (`GET /api/commands`), so a command added to the CLI appears here without a dashboard change.

- **Index:** Find project associations, Learn conversations, Ingest directory (a folder or a file), Refresh files, Ingest URL, Refresh URLs.
- **Maintain:** Retag memories, Scrub secrets, Export snapshot.
- **Clients:** Connect clients and Disconnect clients, for one client or all, with each client's connection state. They only change client settings: connecting installs and starts nothing, and disconnecting stops neither the dashboard nor Qdrant ([ADR 0053](adr/0053-connect-clients-without-touching-the-runtime.md)). Disconnecting asks first.
- **Inspect:** Status, Diagnose and Show config.

**Run** queues the command, and its output streams into the panel beside it. Leaving the page does not cancel the job.

### Sync

- **Live sync** serves a workspace (two-way, send only or receive only, optionally through a relay) and shows a code to enter on the other device, or receives with such a code. The serving device approves each request once or trusts the device; a push asks before it sends ([sync](sync.md)).
- **Paired devices** lists devices paired for automatic sync, with their state and when each was last seen, and counts pairings, devices and those that need attention (removal pending, not seen for a week, or a failed connection). More than eight pairings are searched (device, workspace, relay), filtered by state and paged. Automatic sync can be paused and resumed; a device can be forgotten on both devices or removed from this one, each confirmed first ([ADR 0020](adr/0020-explicit-pairing-and-automatic-workspace-sync.md), [ADR 0073](adr/0073-remove-a-pairing-from-one-device.md)).
- **Snapshots** downloads a workspace snapshot or imports one. **Desktop App Exports** scans Downloads for Claude Desktop and ChatGPT Desktop exports and lists those indexed, ten a page.

### Settings

One section at a time, chosen from a list; `#settings-chat`, `#wsl` and the other section ids link to a section. The filter searches every section and counts the matches in each.

Each setting has a one-line description and, under **What is this for?**, what it controls, what changing it does (speed, memory, what leaves the machine, what has to be reindexed) and a worked example. The text lives in [`imprint/config_help.py`](../imprint/config_help.py), which a test keeps complete for every setting; the filter searches it too. Settings that currently have no effect say so.

- **Configuration** edits the same values as `imprint config`, in this device's `config.json` ([configuration](configuration.md)). A value set by an environment variable is read-only. Reset (Remove, for a secret) deletes the `config.json` override. Running servers apply changes when they restart.
- **This device:** **Workspaces** creates, switches, edits the roots of, wipes and deletes workspaces, and copies or moves memories between them; wiping and deleting are confirmed by typing, and the default and active workspaces cannot be deleted. **WSL profiles** appears only on a Windows installation; macOS and Linux omit it from navigation and settings search and ignore its deep link. On Windows it checks each distribution's clients (checking starts stopped distributions) and enrolls or removes them through the queue.

### Recordings

Records a call or meeting: your microphone and, if you choose, what this computer plays ([ADR 0131](adr/0131-meetings-are-recorded-by-channel-and-transcribed-locally.md)).

- **Start recording** needs a title and project (default `meetings`) and your confirmation that everyone on the call knows it is being recorded. The desktop app records what the computer plays directly, without a picker and without capturing the screen. A browser asks which tab or screen to share, with its audio; shared without audio, only the microphone is recorded, and the page says so.
- On macOS 14.2 and later, the desktop app needs the System Audio Recording permission. macOS asks the first time. If it was refused, starting says so: allow Imprint under System Settings → Privacy & Security → Screen & System Audio Recording, in System Audio Recording Only, or record the microphone only. The macOS app is signed ad hoc, so macOS may keep showing an older version as allowed after an update while refusing the new one: remove Imprint from that list with −, or run `tccutil reset AudioCapture com.alexandruleca.imprint` (the app the installer puts in Applications; `com.alexandruleca.imprint.desktop` for the standalone app), then start a recording and allow it when macOS asks. On Linux the computer's audio depends on PulseAudio or PipeWire's PulseAudio service, and is not yet confirmed.
- While recording, the top bar shows the time and **Stop** on every page. Stopping queues the transcription.
- In the desktop app, with the dashboard hidden, the floating Imprint button's **Record** satellite opens a small panel with the same title, project, computer-audio switch and confirmation. The dashboard records in the background, moving to Recordings first. While recording, the button shows the time and a red rim, and its actions offer **Stop** and the levels. Quitting during a recording asks first and saves what was recorded ([ADR 0135](adr/0135-the-floating-launcher-is-a-handle-with-its-actions-around-it.md)).
- Each recording lists its state, its transcript (who spoke, with times, a paragraph per turn; the microphone's echo of a call is left out, [ADR 0173](adr/0173-a-transcript-leaves-out-the-microphones-echo-of-the-call.md), and so are sentences the speech model likely invented, such as a "Thank you." in silence, [ADR 0181](adr/0181-a-recordings-transcript-leaves-out-what-whisper-likely-invented.md); the transcript's first lines count both), **Listen**, **Download** (the audio as a `.webm` file), **Transcribe again**, **Delete audio** (keeping the transcript) and delete. With **Settings → Device sync → Sync recordings** on here and on a paired device, recordings appear on both, and deleting one, or its audio, does so on both ([ADR 0177](adr/0177-recordings-travel-between-devices-that-both-turned-it-on.md)). A recording whose window closed shows **Finish and transcribe**.
- Voices are told apart within each channel ([ADR 0180](adr/0180-a-recordings-transcript-tells-voices-apart-within-each-channel.md)). On the microphone, the voice that spoke most is "You" and anyone else in the room is "In the room 1", "In the room 2"…, while a voice heard only when the computer's audio speaks is that audio through the speakers and is left out; in the computer's audio, one voice is "Others" and several are "Speaker 1", "Speaker 2"…, in the order they first speak. The transcript's **Speakers** fields rename them: the transcript and its memories are written again, without transcribing again. Give two speakers the same name to merge them. The first transcription downloads the two diarization models (about 32 MB); with `audio.diarize` off, or when they cannot run, the transcript says only "You" and "Others". A recording transcribed before this can be renamed after **Transcribe again**.
- The audio stays in `data/recordings/` on this device; the transcript is the source `recording/<id>` and syncs like other memories.

### Reminders

Reminders, timers, scheduled jobs and questions for Imprint ([ADR 0158](adr/0158-reminders-timers-and-scheduled-jobs-fire-from-the-api-process.md)). They can also be set in Chat or by voice with Echo: "remind me at 10 to call Dan", "set a timer for 10 minutes", "every weekday at 9 re-index my code folder", "tomorrow at 8 summarize what I worked on yesterday".

- **New** sets a reminder (a message), a timer (a duration), a scheduled job (re-index or index a folder, learn conversations, organize, refresh URLs, retag) or a question for Imprint, at a date and time or in a duration, once or repeating every day, every weekday, weekly on chosen days, or every so many minutes or hours. Entries made here keep the browser's time zone.
- **Now** lists what rang and waits for you: **Done**, **Snooze** 5, 10 or 30 minutes, or **Open chat** for an answered question.
- **Upcoming** groups entries by day (Today, Tomorrow, This week, Later); a timer counts down. Each can be edited or cancelled. **Past** lists what rang, was done, cancelled or missed, by period.
- Whichever page is open, a reminder or timer that rings shows a toast with the snooze choices and **Done** until you choose; a job reports it was queued and a question links to the new chat with its answer. In the desktop app, a reminder also shows a system notification while the dashboard is hidden or in the background; clicking it opens the reminder here.
- Entries stay on this device (`data/schedule.sqlite3`) and do not sync. They ring only while Imprint's service runs; one missed while it was stopped rings once, marked late, on the next start, and a job or question missed by more than 12 hours is not run late.

### Voice

Echo, the voice companion ([live voice](live-voice.md)).

- The first visit sets Echo up: **Download & set up Echo** fetches its runtime and models, with its progress.
- **Start conversation** starts talking, by push to talk or hands-free, and a thought can also be typed. Switching between them, speaking or typing during a reply never cancels it: the new question waits its turn, shown as waiting with **Remove**. **Stop reply**, beside the talk button, or "stop" said alone, is the one way to cancel a reply; **End conversation** forgets the conversation; what was saved stays in Chat history. A failed reply offers **Try again**, and a finished one **Play again** ([ADR 0145](adr/0145-a-request-runs-until-it-is-answered-or-explicitly-stopped.md)).
- **Earlier chats**, beside **Start conversation**, lists recent chats from Chat history; choosing one carries it on by voice ([saved conversations](live-voice.md#saved-conversations)).
- **Companion settings** picks the reasoning model (the chat model, or the small LFM voice model), the microphone and speaker, the microphone options, and whether conversations are saved in Chat history (on by default; text only, on this device); the picker chooses Echo's look and voice. The status line says "saved in Chat" or "not saved".
- **Float Echo** opens Echo in its own small window: the desktop companion window, or a browser popup. Hiding the page, floating Echo or closing it releases the microphone; the conversation goes on in the service, and the next window picks up a reply in progress or shows one that finished meanwhile, without turning the microphone on.

## Shared behavior

**Approving a root.** Ingest and refresh only read inside a workspace's approved roots. When a job from any page asks for a folder outside them, the dashboard asks "Approve <folder> as a root of workspace <name>?" and names any other workspace that already has it, so approving confirms that sharing. Approving adds the root and submits the job again; cancelling queues nothing ([ADR 0015](adr/0015-workspace-roots-and-source-purge.md)).

**Reading a whole source.** Topics, Projects, Sources, Graph and a memory's **Read in context** open a conversation or file whole from its stored snapshot. A conversation shows its messages in order, with tool calls and their output, thinking and injected context folded between them; opened for a topic, its passages are marked and stepped through ([ADR 0094](adr/0094-the-dashboard-reads-whole-sources-from-their-snapshots.md)).

**Lists and details.** Topics, Commands and Settings show a list beside the details; Projects shows its rail beside the open project. On wide screens both scroll on their own and the header stays put; below that they stack and the page scrolls.

**Picking a path.** Folder and file pickers browse the server's filesystem, so paths are the service's own: on a Windows installation, WSL folders are under `\\wsl.localhost\<distribution>`.

**Copying or moving between workspaces.** **Copy to workspace…** on Topics and Projects and for selected sources, **Copy or move to a workspace…** in a source's menu, and **Copy or move memories…** in Workspaces open one dialog: what (any number of projects, topics or sources, searched, from the workspace the dashboard shows), where to (another workspace or a new one, created by the run) and how (copy, the default, or move). A preview counts the memories and sources it takes, how many the target already holds (updated in place, not duplicated) and what a move removes, before anything runs. Each selection then runs as a queued `migrate` job with its own progress and result; after a move, **Move back…** previews the reverse ([ADR 0150](adr/0150-topics-page-on-the-server-and-workspace-copies-are-previewed.md)).

## Desktop app

- The window opens a splash while the local service starts, with the startup log and **Try again** when it fails ([ADR 0025](adr/0025-desktop-owned-background-service.md)).
- Closing hides the window to the tray and leaves Imprint running; minimizing leaves it in the taskbar. **Settings → Desktop app → Floating orb** (off by default) adds a small floating button while the dashboard is hidden, and then minimizing hides to the tray as well; the button also appears during a recording, whatever the setting, and goes when it ends ([ADR 0174](adr/0174-the-floating-orb-is-off-unless-the-user-turns-it-on.md)). Hidden, the dashboard leaves the taskbar, and on macOS the Dock; the floating button and Echo never show there. **Quit Imprint** (sidebar, tray, or the floating button's menu) stops the app and its background service.
- The tray offers what the floating button does, shown or not: **Open Dashboard**, **New recording…** (the floating button's panel when it is up, otherwise the Recordings page with its consent box) or **Stop recording**, **Float Echo**, **Turn on “Hey Echo”…** (the explanation first, on the Voice page), a **Floating button** checkbox (the `desktop.floating_orb` setting) and **Quit Imprint**. While “Hey Echo” is on, its first line says whether Imprint listens, with pause, resume and off. The top bar's **Floating Echo** switch shows or hides Echo's window.
- The app follows the dashboard's theme choice: its windows, the splash, the floating button's ring and the operating system's own menus and dialogs turn light or dark with it, and the next start opens in it ([ADR 0154](adr/0154-the-dashboard-and-desktop-app-follow-a-light-dark-or-system-theme.md)). Echo's floating window keeps its dark glass in both, since it sits over other windows.
- The window's empty top bar and the sidebar header move it. While a dialog, menu or popover is open they do not, so the overlay stays usable ([ADR 0039](adr/0039-shadcn-overlays-and-desktop-drag-handles.md)).

## Design and tests

**Caching reads.** Pages read through `useCached(key, fetcher, options)` in `imprint/ui/src/lib/use-cached.ts`: the last answer for a key shows at once (memory, then `sessionStorage` after a reload), scoped to the active workspace and never shown before the tab knows which workspace is active; it is fetched again on mount, focus, a job starting or ending, and optionally on an interval, on `/api/stream` updates or while the server marks the answer `stale`. `loading` means nothing to show yet, `refreshing` that a newer answer is on its way ([ADR 0152](adr/0152-dashboard-summaries-are-served-stale-while-they-refresh.md)). Projects, Sources, Topics, Agents, Queue history, Settings, Commands, Recordings, Chat's list and paired devices read this way, so returning to a page shows what it last showed; the project pickers on every page share one cached list (`useProjectNames`, `GET /api/projects`, every project rather than the 20 largest). A page shows **Updating…** beside its title while it shows an earlier answer and asks for a newer one; pages that poll every few seconds (Agents, Queue) do not. Lists a reader works through (Sources, a project's sources) are fetched again only when opened or on **Refresh**, and a data change offers **Refresh** instead of moving rows. Answers that hold settings or conversation titles stay in memory only. Core memories and Organize keep reading directly: they reload after every decision they send.

**Themes.** Colours are tokens in `imprint/ui/src/app/globals.css`, with dark values under `.dark` on `<html>`; Tailwind's `dark:` variant keys on that class. `src/lib/theme.ts` keeps the choice (`imprint.theme` in `localStorage`) and an inline script in `src/app/layout.tsx` applies it before first paint and again when another window changes it. Use tokens rather than fixed colours so both themes work: besides shadcn's, `success` and `warning` colour status text and icons (and their 10% tints), and `mark`, `mark-strong` and `mark-edge` mark passages in a source; each keeps text at WCAG AA (4.5:1) in both themes. The desktop app is told the choice through `window.electronAPI.setTheme` ([ADR 0154](adr/0154-the-dashboard-and-desktop-app-follow-a-light-dark-or-system-theme.md)).

The dashboard uses one component kit, vendored shadcn components on Base UI ([ADR 0039](adr/0039-shadcn-overlays-and-desktop-drag-handles.md)), with a black-and-white theme, light or dark, and the bundled Inter font ([ADR 0008](adr/0008-dashboard-retrieval-and-accessibility.md)). Playwright tests under `imprint/ui/tests` drive the pages against mocked API responses, at desktop, tablet and phone widths. They are not an accessibility certification or a native desktop check; see [platform validation](platform-validation.md) for what has run on each platform.
