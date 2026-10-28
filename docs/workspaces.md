---
title: Workspaces & Projects
description: Imprint workspaces isolate memory collections, SQLite graphs, and config per-context. How projects are detected from manifest files and how to switch between them.
---
## Project Detection

When you run `imprint ingest ~/code`, it recursively finds real project roots by looking for manifest files — not just top-level directories:

```mermaid
graph LR
    DIR[~/code/myorg] --> SCAN[Recursive scan]
    SCAN --> PJ1[frontend<br/><small>package.json → my-web-app</small>]
    SCAN --> PJ2[backend<br/><small>go.mod → api-server</small>]
    SCAN --> PJ3[services/auth<br/><small>package.json → auth-service</small>]
    SCAN --> PJ4[ml-pipeline<br/><small>requirements.txt → ml-pipeline</small>]
    SCAN --> PJ5[platform<br/><small>composer.json → platform-core</small>]
    SCAN --> MORE[... more projects]

    style PJ1 fill:#1a1a3a,stroke:#60a5fa,color:#fff
    style PJ2 fill:#1a1a3a,stroke:#60a5fa,color:#fff
    style PJ3 fill:#1a1a3a,stroke:#60a5fa,color:#fff
    style PJ4 fill:#1a1a3a,stroke:#4ecdc4,color:#fff
    style PJ5 fill:#1a1a3a,stroke:#ff8c42,color:#fff
```

| Manifest | Type | Name extracted from |
|---|---|---|
| `package.json` | Node.js | `name` field |
| `go.mod` | Go | `module` path |
| `pyproject.toml` / `setup.py` | Python | `name` field or dir name |
| `requirements.txt` | Python | directory name |
| `composer.json` | PHP | `name` field |
| `Cargo.toml` | Rust | `name` field |
| `pom.xml` / `build.gradle` | Java | directory name |
| `Gemfile` | Ruby | directory name |

Projects are identified by **canonical name** from the manifest, not the file path. The same project at different paths on different machines gets the same identity — this makes sync work across machines.

Files outside any manifest go into a fallback project named after the ingested folder: ingesting `~/Desktop` creates a project `Desktop`.

## Associate one source with several projects

On **Projects → Review project associations**, inspect suggested project links
and the source passages supporting them. Accept a link to make the conversation
or file appear in that project's sources and retrieval results. Reject a
suggestion to keep it out. Select any source to add an existing or new project
manually, or remove an accepted link. Manual decisions survive re-ingestion.
The source's primary project, IDs, vectors and provenance stay unchanged.
Linked sources are readable in the related project; move them from their primary
project. Project counts can overlap, while the total memory count remains unique.

Name and registered-path suggestions run automatically after a full snapshot is
published. **Analyze existing sources** queues a retroactive pass, useful after
adding a project. Optionally enable **Use installed local helper** for ambiguous
passages. It runs locally, without tools or model downloads, and requires the
tagging helper to be installed. Follow the job in Queue, then refresh suggestions.
The same command is available on Commands, or with the installed Python runtime:
`python -m imprint.project_associations [--model]`.

These are whole-source links. Supporting passages explain the link; they do not
partition its membership. Topics stay independent shared themes. A model result
always needs review. Settings can opt into automatic exact registered-path
matches. Associations are local to a device and workspace, are not synced, and
never decide workspace ownership. Existing sources without snapshots must be
refreshed or learned before analysis. See [ADR 0099](adr/0099-sources-can-belong-to-several-projects.md).

### Conversations without a directory

In **Settings → Project associations → Unassigned workspace**, choose an explicit
inbox workspace to include directory-free client conversations on the next
learning run. The default is **Off**. This covers unresolved Cursor folders and
other known client transcripts with no cwd evidence. New imports enter the
`unassigned` primary project. Existing identity is preserved on refresh.
Conflicting or relative cwd evidence is still rejected. This setting does not
approve filesystem roots, run ingestion, move prior imports or follow the active
workspace. Changing the inbox changes future imports; it does not erase sources
already imported into the previous workspace. See [ADR 0100](adr/0100-explicit-workspace-inbox-for-unassigned-conversations.md).

## Move memories between projects

A catch-all project such as `Desktop` can be sorted afterwards. On the dashboard's **Projects** page, pick a project, select sources and choose **Move selected…**. Use **Move or merge…** to move the whole project; moving into an existing project merges into it. Either way you review what moves before confirming. From the CLI:

```bash
imprint move Desktop branding --source "Desktop/Naming Questionnaire.pdf"           # preview
imprint move Desktop branding --source "Desktop/Naming Questionnaire.pdf" --apply
imprint move imprint-memory-layer imprint --apply                                   # merge a whole project
```

The project a memory's writer named (ingestion from the folder, learning from the conversation's directory, an agent from its argument) is its *origin project*. A move records a rule in this workspace's policy: the origin project, or one of its sources, goes to the target. Every write applies these rules, so refresh, learning and sync keep moved memories where you put them. Merging a whole project also sends memories saved later under its old name to the target.

- Source keys do not change. A moved file keeps `Desktop/<file>`, which records where it was indexed. Graph facts, purge and `file_chunks` keep working. The file stays where it is, so refresh the folder it lives in to pick up edits.
- Vectors are reused, but logical IDs change, because they include the project.
- Rules belong to this device's workspace, like roots and exclusions, and are not synced. A paired device keeps its own layout. Copies it sends back are placed by your rules, so they do not undo the move. Make the same move there for the same layout.
- To undo, move the sources back. For a merge, move back the sources whose keys start with the old project's name.

Moves preview by default, share the ingestion lock, and apply only to the workspace that was previewed. See [ADR 0036](adr/0036-move-memories-between-projects.md).

## Workspaces

Workspaces provide isolated memory environments. Each workspace gets its own Qdrant collection, SQLite knowledge graph, and write-ahead log — complete data isolation on the same Qdrant server.

```mermaid
graph LR
    subgraph "Qdrant Server (shared)"
        C1[(memories<br/><small>default workspace</small>)]
        C2[(memories_research<br/><small>research workspace</small>)]
        C3[(memories_staging<br/><small>staging workspace</small>)]
    end

    subgraph "SQLite (per workspace)"
        S1[(imprint_graph.sqlite3<br/><small>default</small>)]
        S2[(imprint_graph_research.sqlite3)]
        S3[(imprint_graph_staging.sqlite3)]
    end

    MCP[MCP Server] -->|active workspace| C1
    MCP --> S1

    style C1 fill:#1a1a3a,stroke:#60a5fa,color:#fff
    style C2 fill:#1a1a3a,stroke:#60a5fa,color:#fff,stroke-dasharray: 5 5
    style C3 fill:#1a1a3a,stroke:#60a5fa,color:#fff,stroke-dasharray: 5 5
    style S1 fill:#1a1a3a,stroke:#4ecdc4,color:#fff
    style S2 fill:#1a1a3a,stroke:#4ecdc4,color:#fff,stroke-dasharray: 5 5
    style S3 fill:#1a1a3a,stroke:#4ecdc4,color:#fff,stroke-dasharray: 5 5
```

**Default workspace** (`default`) uses the same file and collection names as before — zero migration. Existing data is automatically in the default workspace.

```bash
# List workspaces
imprint workspace
#   default (active)

# Create and switch to a new workspace
imprint workspace switch research
#   [+] created and switched to workspace: research

# Ingest into the active workspace — data goes to `memories_research` collection
imprint ingest ~/code/research-project

# Switch back
imprint workspace switch default

# Delete a workspace (must not be active)
imprint workspace delete research

# Make the project in this directory use a workspace (see "Workspace per project")
imprint workspace use research
```

In the dashboard, the workspace menu at the top right of every page shows the active workspace, switches to another one (the dashboard reloads) and creates one. **Manage workspaces** in that menu lists every workspace with its roots and lets you create, switch, edit roots, wipe, copy/move memories and delete. Deleting requires typing the workspace name and is unavailable for `default` and for the active workspace.

**Naming rules:** lowercase alphanumeric + hyphens, max 40 characters, must start with letter or digit.

**Data per workspace:**

| Workspace | Collection | SQLite | WAL |
|---|---|---|---|
| `default` | `memories` | `imprint_graph.sqlite3` | `wal.jsonl` |
| `research` | `memories_research` | `imprint_graph_research.sqlite3` | `wal_research.jsonl` |

**Wipe behavior:**

```bash
imprint wipe                       # wipe active workspace only (via API — no server restart)
imprint wipe --workspace research  # wipe a specific workspace
imprint wipe --all                 # stop Qdrant, delete all data, restart fresh
```

`workspace switch` and the dashboard menu set the *active* workspace, stored in `data/workspace.json`. Agent sessions and directory commands follow the project they run in instead, when the project has one; see [Workspace per project](#workspace-per-project). Each MCP process pins its workspace at startup, so another window's `workspace switch` cannot redirect it. CLI ingestion captures its workspace before waiting/queueing.

## Workspace per project

A project chooses its workspace with a file in the project, `.imprint/workspace.json`:

```json
{"workspace": "client-acme"}
```

Your device decides whether to honour it: the file counts only when the directory that holds it is inside an [approved root](#approved-roots-and-sharing) of the workspace it names. A cloned repository can therefore propose a workspace but never move your sessions into one, such as a client's, on its own. Imprint writes the file and leaves git alone; committing it, or ignoring it, is your choice.

### Assign a project

```bash
cd ~/code/acme-web
imprint workspace use client-acme     # create if new, approve the project folder, write the file
imprint workspace use                 # on a terminal: pick from a list or name a new workspace
imprint workspace use client-acme --allow-overlap   # when another workspace's roots cover the folder
```

The file goes in the project folder: the nearest folder, from the current directory up, that holds a `.imprint/workspace.json` or is a git repository's root, else the current directory. A home directory or a filesystem root is refused, since a file there would assign everything beneath it. `imprint workspace use` changes neither the active workspace nor any running session; sessions started in the project afterwards use it.

Agents do the same through the `use_workspace` MCP tool. When a session starts in a project no workspace is assigned to, or whose file names a workspace this device has not approved, the session-start hook (Claude Code, Codex, GitHub Copilot) and the first Imprint tool result tell the agent to ask you once: pick an existing workspace, confirm the proposed one or name a new one. The agent then calls `use_workspace`, which creates the workspace if needed, approves the project folder as a root, writes the file and switches the running session without a restart. Until you answer, the session keeps its default, so nothing breaks.

### Which workspace a directory uses

Every consumer asks the same question, in this order:

1. A tool call's explicit `workspace` argument (that call only).
2. `IMPRINT_WORKSPACE` in the environment, for example in a client's MCP server entry.
3. The nearest `.imprint/workspace.json` above the directory, when this device approved it. The search stops below your home directory and below the filesystem root; an unreadable or invalid file is ignored with a visible note.
4. The workspace whose approved root containing the directory is the most specific. When roots of several workspaces tie, nothing is assigned and agents ask.
5. The default: the active workspace for CLI commands; for agent sessions the `workspace.unassigned` setting, which follows the active workspace unless you set it.

`imprint ingest` and `imprint refresh` resolve the directory they index and print the result, for example `Workspace: client-acme (project file …/.imprint/workspace.json)`. `learn`, `move`, `purge` and the roots commands keep the active workspace or `--workspace`. Dashboard jobs carry the dashboard's workspace, as before.

A session has a project folder only inside a git repository or under a `.imprint/workspace.json`. Elsewhere, including a plain folder, your home directory, an application's own folder or `/`, the session uses the default and is not asked; run `imprint workspace use` in a plain folder to make it a project.

### Desktop apps

Claude Desktop starts one Imprint server for the whole app, with no project folder, and every chat shares it; the server cannot tell chats apart. Its chats use **Settings → Workspace selection → Unassigned** (`workspace.unassigned`), the default for every session outside an assigned project. To use another workspace in one chat, name it: the agent passes `workspace` on every Imprint call for the rest of that conversation. `use_workspace` there creates the workspace if needed but does not switch the shared server, which would move the other chats too.

The closest thing to a project file is a Project's instructions: in a Claude or ChatGPT Project, add a line such as

```text
Use Imprint workspace `client-acme` for every Imprint call.
```

Every chat in that Project then passes the workspace. ChatGPT Desktop runs local MCP servers through the Codex configuration it shares; a Codex thread with a project folder follows that folder like any coding session.

Which clients start their server in your project folder is listed in [ADR 0072](adr/0072-projects-name-their-workspace-and-each-device-approves-it.md): Claude Code, Codex and VS Code with Copilot do; Claude Desktop does not; Cursor's is undocumented and Cline's is its extension host's, so their sessions may count as having no project.

## Copy, move and share a workspace

`imprint migrate` transfers memories between workspaces by project, topic or exact source, keeping their vectors, so both workspaces need the same embedding profile. It moves by default. `--copy` leaves the source untouched, which is how you curate a workspace to share without taking anything out of your own:

```bash
imprint migrate --from default --to team --project payments-api --copy --dry-run
imprint migrate --from default --to team --project payments-api --copy
imprint migrate --from default --to team --topic auth --copy
```

A real run registers a new target workspace without switching to it; a dry run changes nothing, not even the target. Project and source transfers also copy the knowledge-graph facts about the transferred memories' sources, including files moved into the project from another one (topic transfers copy none); facts are never removed from the source and facts the target already holds are skipped, so re-running a copy refreshes the curated workspace without duplicates. A copy does not delete: memories you later remove from the source stay in the target until you remove them there. The dashboard's **Copy / move memories…** dialog (workspace menu → **Manage workspaces**, or **Settings → Workspaces**) offers the same choice and defaults to **Copy**.

Live sync works on one workspace, end-to-end encrypted, and never switches the active one. `imprint sync serve --workspace team --send-only` shares only `team`, read-only, and prints a share code; the joining machine runs `imprint sync <code>` and picks its own destination with `--workspace` (created when the data arrives). Joining only receives unless it adds `--two-way` or `--send-only`, and then it is shown the serving device and asked before anything is sent. See [sync](sync.md#share-part-of-your-memory) and [ADR 0016](adr/0016-sync-direction-and-workspace-sharing.md).

## Approved roots and sharing

The first ingest binds a directory (a single file binds its parent). Later ingest/refresh targets must stay inside approved roots. Additional roots require explicit approval:

```bash
imprint workspace roots
imprint workspace roots add /code/my-project --workspace research
imprint workspace roots add /code/shared --workspace research --allow-overlap
imprint workspace roots remove /code/my-project --workspace research
```

Roots resolve symlinks and use path-component boundaries. An overlap error identifies the other workspaces; `--allow-overlap` explicitly confirms sharing. The dashboard offers the same action with confirmation under **Sources → Workspace roots and cleanup** for the active workspace, and under **Manage workspaces → Edit** for any workspace. Removing a root retains indexed data. Policy is stored separately in `data/workspace-policy.sqlite3`.

An ingest or refresh started from the dashboard (quick actions, Commands) that the roots would refuse is checked before it is queued. Instead of a job that fails later, the dashboard asks whether to approve the folder as a root of the active workspace and names any other workspace that already has it, so approving confirms that sharing. Once approved, the job is submitted again. Declining queues nothing.

**Pick folder** browses the machine the Imprint service runs on, with breadcrumbs in that machine's path syntax. On a Windows installation it lists each WSL distribution as a root (for example **WSL Ubuntu** at `\\wsl.localhost\Ubuntu\`), so Linux folders can be picked and approved. These are the same `\\wsl.localhost\<distro>\…` paths that WSL clients connected to the Windows runtime use (ADR 0026). A root typed as `\\wsl$\<distro>\…` is a different path for the comparison and does not cover them. Linux-style paths such as `/home/me` are not guessed on Windows.

Existing data is retained; old provenance does not automatically establish ownership. Approve intended roots and purge mistakes. Inline/URL content and deliberate migrations use the selected workspace directly.

### Learning needs no root

`imprint learn` reads the stores of the clients Imprint is integrated with, so it needs no approved root. These are Claude Code's transcripts and auto-memory files, Codex's sessions, Cursor's agent conversations, GitHub Copilot's sessions and Cline's sessions on this computer, the same from every client profile registered in WSL, and the Downloads folders where desktop exports land. Roots, and project files this device approved, decide which workspace each conversation belongs to. A working directory under an approved `.imprint/workspace.json` belongs to the workspace it names; any other belongs to every workspace whose roots contain it, or to none. A conversation is learned when every working directory it recorded belongs to this workspace or to none. Claude Code records a working directory with each entry, Codex records one per session and turn, and Copilot records one when a session starts, resumes or changes context. Cursor records none; its conversation folder's name counts as the working directory when exactly one existing directory has that name. Cline records a session's working directory and workspace root in the session's manifest. A WSL conversation's directories are compared in the `\\wsl.localhost\<distro>\…` and drive-path form the WSL bridge uses. Conversations about another workspace's projects stay there, and conversations with no recorded working directory are skipped. Auto-memory files and the Stop hook follow the same rule; the Stop hook learns in the session's workspace ([Workspace per project](#workspace-per-project)). Desktop exports are tracked per workspace. Extra export folders passed with `--path` must be inside approved roots. See [ADR 0034](adr/0034-learn-from-client-stores-by-conversation-ownership.md), [ADR 0072](adr/0072-projects-name-their-workspace-and-each-device-approves-it.md), [ADR 0037](adr/0037-learn-every-integrated-client-store.md), [ADR 0052](adr/0052-learn-cursor-conversations-from-their-folder-names.md), [ADR 0056](adr/0056-learn-cline-sessions-from-their-manifests.md) and [ADR 0057](adr/0057-copilot-home-as-store-config-and-hook-root.md).

## Purge mistaken files or directories

Preview the scope/count, then apply to the same workspace:

```bash
imprint purge /code/wrong-project --workspace research
imprint purge /code/wrong-project --workspace research --apply
# Use --recursive for a deleted directory whose type cannot be detected.
imprint purge /code/deleted --recursive --workspace research --apply
# Logical source keys also work for legacy or foreign-machine provenance.
imprint purge project/private-dir --source --recursive --workspace research
imprint purge project/private-dir --source --recursive --workspace research --apply
imprint workspace exclusions --workspace research
imprint workspace exclusions restore project/private-dir --source --workspace research
```

Purge removes indexed chunks and source-linked graph facts from that workspace. Original files and other workspaces stay intact. Directory matching preserves similarly named siblings. Apply records an exclusion so refresh and sync/import cannot silently restore those sources. **Allow re-ingestion** in the dashboard, or `workspace exclusions restore`, removes that exact exclusion; reindex afterward.

The Sources panel supports path/source selection, recursion, count/sample preview and typed workspace confirmation. MCP `purge_sources` defaults to preview; agents must obtain user confirmation and name the reviewed workspace for apply. Cleanup does not load embeddings and works on old-model collections. Partial failures remain visible and preserve the protective exclusion for retry. Backups, exported bundles and logs are not erased. See [ADR 0015](adr/0015-workspace-roots-and-source-purge.md).

## Device-aware project paths

Shared conversation/project memories remain available on every device. `imprint workspace map <project> <local-directory>` maps portable project-relative references to this device’s approved checkout; file ingestion sets the map automatically. Original paths stay labelled as references, and foreign paths are never pruned by probing this machine. See [devices and portable project paths](devices.md).
