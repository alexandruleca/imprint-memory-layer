---
title: ADR 0036 — Move memories between projects
description: Projects are placements of memories that writers named. Workspace rules, applied wherever memories are written, place them, so a move outlives refresh, learning and sync.
---

- Status: Accepted
- Date: 2026-10-24
- Related: [ADR 0005](0005-source-lifecycle-and-hybrid-retrieval.md) (logical IDs), [ADR 0015](0015-workspace-roots-and-source-purge.md) (exclusions enforced at the same write boundary), [ADR 0016](0016-sync-direction-and-workspace-sharing.md), [ADR 0017](0017-device-provenance-and-local-project-paths.md) (provenance, project mappings), [ADR 0020](0020-explicit-pairing-and-automatic-workspace-sync.md) (merge sync)
- Restructured: 2026-10-25

## Context

The program that writes a memory names its project. Ingestion and refresh name it from manifest files (`imprint/projects.py` `find_projects`). Files outside any manifest go into a fallback project named after the ingested folder. Ingesting `C:\Users\alexa\Desktop` therefore produced a project `Desktop` holding unrelated PDFs and Word documents. Learning names a conversation's project from its working directory. MCP, URL and inline ingestion take the name from their argument, and sync takes it from the peer. The name appears in three places. It is the point payload's `project`. It is hashed into the logical ID (`vectorstore._make_id`: project, source, content, position). And file source keys are `<project>/<relative path>`. Graph facts reference source keys. Project mappings (ADR 0017) map a project name to this device's checkout. `imprint migrate` moves memories between workspaces, not between projects.

The owner wants to move memories out of a catch-all project such as `Desktop` into the project they belong to. Relabelling the stored points alone does not last:

- Refresh derives `Desktop` again and indexes the file there a second time.
- Learning re-adds a growing conversation's exchanges under the name it derives.
- A paired peer sends its old copies back. Sync is a merge that never deletes (ADR 0020), so the move would be undone.
- The sync importer rejects a record whose ID does not match its project, source and content. A relabel that keeps the old ID breaks sync.

## Decision

**Origin and placement.** A memory's `origin_project` (a payload field, part of device provenance) is the project its writer named. Its `project` is where it is placed. Memories without the field have the same origin and placement.

**Placement rules.** Each workspace has rules in the local policy database (`workspace-policy.sqlite3`, table `placements`). A rule maps an origin project, either one source key of it or all of it (`source = ''`), to a target project. A memory is placed by the rule for its origin and source, else by the rule for its whole origin, else in its origin. Placement always starts from the origin. Placing a memory twice gives the same result, which matters both for nested writes and for a memory that comes back from a peer. The rule function lives in `imprint/projects.py` (`place`).

**Applied at every write.** `vectorstore.store`, `store_batch`, `replace_source` and `store_batch_precomputed` place records before they compute IDs. Purge exclusions are enforced at the same boundary (ADR 0015). Every writer is covered: ingestion, refresh, learning, MCP, URL and inline ingestion, and sync import. The importer first checks the sender's IDs against the sender's labels, then places the records and gives each its ID under the placed project.

**Moving.** `projects.move` is reached through `POST /api/projects/move`, `imprint move <project> <target> [--source KEY]... [--apply]` and the dashboard Projects page. It moves either the selected source keys of a project or the whole project. Moving into an existing project merges into it. Like purge, it previews by default, applying needs the previewed workspace, and it holds the ingestion lock. Applying writes the rules first, then rewrites each memory. The vector is reused (nothing is re-embedded). Source key, provenance and origin stay the same. The project changes, the memory gets the new logical ID of the same kind (note or positioned chunk), and the old point is deleted. A whole-project move also redirects every rule that pointed at the project, and adds a rule for the project's own name. Memories later written under that name follow it, which makes a merge or rename durable. Rules that only restate the default are dropped.

**Source keys do not change.** A moved file keeps the key `Desktop/<file>`, which records where it was indexed. Graph facts, source-key purge and exclusions, `file_chunks` and refresh's fingerprint lookup keep working without rewriting anything. Project-relative paths and project mappings resolve under the origin project (`device.origin_project`), so a moved file's local path hint still points at the file on the Desktop.

**Rules are local to the device and workspace.** Like roots, exclusions and mappings, rules are not synced, exported or snapshotted. A peer places what it receives by its own rules, starting from the `origin_project` that travels with each memory. A peer without rules therefore shows a moved memory in its origin project. A peer that already held the memory dedupes the incoming copy with its own. Old copies a peer sends back are placed by the mover's rules, so they dedupe and do not undo the move. Peers that predate this ADR do not send an origin, so their label is taken as the origin. To show the same layout on another device, make the same move there once.

**Why IDs change, and why dedup still converges.** The logical ID hashes the project, and the importer requires IDs to match project, source and content. A moved memory therefore takes the ID it would have had if it had been written under its target. Each workspace computes IDs only after placing a record by its own rules, starting from the origin. Two devices with the same placement compute the same ID for the same memory. A device with a different placement maps an incoming copy to its own placement and finds it there. Each workspace keeps one copy of each memory.

**Undoing a move.** Move the same sources back. The rule that sent them away now restates the default, so it is dropped, and the workspace is back where it started. To undo a merge, move the sources that came from the merged project back to it. File sources' keys start with that project's name. Moving the whole merged project back moves everything it now holds, including its own memories, and leaves a rule mapping its name to the old project.

## Rejected alternatives

- **Relabel stored points only.** Refresh, learning and peers undo it, and the unchanged ID breaks sync's ID check.
- **Re-key sources to `<target>/<relative path>`.** The key would no longer say where the file lives. Graph facts, exclusions and mappings would need rewriting. Two origins with the same relative path (`README.md`) would collide in the target. Refresh would still derive the old key.
- **Apply rules only in ingestion and the sync importer.** Learning and agents would keep writing under the old name, so a merged project would reappear with the next conversation.
- **Keep the project already stored for a source key (no rules).** Source keys are not unique across projects (Claude Code's `memory/MEMORY.md` exists per project). A merge would not cover memories written later under the merged name.
- **Sync the rules, or make placement last-writer-wins in the payload, so moves propagate.** Imports would then rewrite and delete existing memories, which ADR 0016 and ADR 0020 rule out ("sync never deletes"). It would be a separate change-log protocol.
- **Drop the project from the logical ID.** That changes the identity of every stored memory and of every record from peers on older versions.

## Consequences

- Moved file sources keep their `Desktop/…` keys in their new project. The files stay where they are: no new root is needed, and changes are picked up by refreshing the folder they live in.
- A whole-project move also sends memories saved later under the old name, by refresh, learning or agents, to the target.
- Moves change logical IDs. Bookmarked memory IDs of moved memories stop resolving. Graph facts are unaffected.
- An interrupted apply leaves duplicates (new copy written, old not yet deleted), never losses. Applying again finishes it.
- Paired devices keep their own layout until the same move is made there.
- `imprint migrate` (between workspaces) and snapshot restore copy payloads as stored, without placement. They already bypass purge exclusions (ADR 0016). A project migration copies the graph facts of every migrated memory's source key, not only facts whose source starts with the project's name, so facts about files moved into the project go along.
- A move appends to the workspace WAL, so dashboard views and the Projects page refresh (`/api/stream`).

## Invalidation conditions

- If sync gains a change-log protocol that may rewrite and delete, which ADR 0016 and ADR 0020 rule out, placement rules could sync so moves propagate.
- If the logical ID stops hashing the project, moves no longer need new IDs.

## Validation

- `tests/test_projects.py` covers, against an in-memory Qdrant:
  - Previews change nothing.
  - A moved source stays through refresh without re-embedding, and an edit replaces it in place.
  - Graph facts keep their source keys.
  - Sync round trips go through `stream_export_lines` and `process_import_buffer`. The peer keeps its layout, the mover is not undone, and the same move on the peer yields identical IDs.
  - A merge keeps note IDs and places later writes under the old name.
  - Moving back leaves no rule.
  - A source moved out of a merged project follows its origin.
  - HTTP apply requires the previewed workspace and strict booleans.
  - A moved file's path hint resolves under its origin's mapping (`get_source_summary` and `get_chunks_by_source`).
- `imprint/ui/tests/projects.spec.ts` (Playwright, mocked API) covers the source selection, review and confirmation flow, a whole-project merge, and a project from a finished job appearing without a reload.
- The full Python suite, `go build ./...`, `go vet ./cmd/`, TypeScript and lint passed on Linux.
- Not run: moves against the managed Qdrant server, a live paired relay session, and the dashboard on Windows/macOS.
