---
title: Devices and portable project paths
description: Shared memory origin, current-device context and local mappings across machines.
---

Memories, decisions and conversation chunks remain shared across devices. Device metadata describes origin; it does **not** filter retrieval or create another knowledge base. Both sides still need the same embedding profile for vector sync.

New memories record a stable device ID, hostname, OS and recorded time. Files also record source device and project-relative path. Graph facts retain origin. Stream/snapshot sync and workspace copies preserve it. Old records show **Unknown origin (legacy)** instead of being attributed to the receiver. Reindex local files to refresh provenance; metadata itself does not require re-embedding.

```bash
imprint device
# On device B, map the canonical stored project name to its local checkout:
imprint workspace roots add /code/project --workspace work
imprint workspace map project /code/project --workspace work
```

Normal file ingestion sets the project mapping automatically. A mapping changes only this device's workspace policy; root approval/sharing rules still apply. Maps are not synced.

A memory can reference `C:\Users\alice\project\src\app.py` from A while B uses `/code/project`. Imprint preserves the original as a reference and resolves `src/app.py` through B's approved map. Retrieval adds `path_context`: current device/cwd, original path, status and optional `local_source_path`. A hint requires an existing file inside both the approved root and current process cwd. Traversal/symlink escape produce no hint. Hashes describe indexed bytes; verify current files before edits.

Without a mapping, or outside cwd, shared content remains available. Original paths are not instructions to access local files. Conversation text and example commands are preserved, including historical paths. MCP advertises device/cwd on initialization and exposes `device_context`; its cwd comes from the coding host. Restart the connection from the project directory if needed. `/api/device` and dashboard memory/source detail expose the same context.

Imported paths are unverified locally even when their strings match local paths. Refresh/pruning do not delete foreign or unknown sources based on local filesystem state. Physical-path purge uses locally recorded provenance; use source-key purge for synced/legacy records. Snapshot restore refuses to bypass purge exclusions: use a fresh workspace or explicitly restore exclusions first.

`data/device_id.txt` is shared by Go/Python sync; existing IDs stay stable and concurrent startup provisions one ID. Hostname is a captured human-readable label. The ID is not cryptographic authentication. Bundles do not copy device identity or maps. Whole-data-directory clones require a distinct local identity before recording more data; prefer supported export/import or peer sync.

See [workspaces](workspaces.md), [MCP](mcp.md) and [ADR 0017](adr/0017-device-provenance-and-local-project-paths.md).
