---
title: ADR 0168 — Skills and personas are kept once and synced to paired devices
description: The user's skills (Agent Skills folders) and personas (subagent files) live in a registry outside every workspace, stored verbatim as text files, and travel to paired devices as their own dataset, where the newest change of each wins and the replaced version stays in that device's history.
---

- Status: Accepted
- Date: 2026-11-09
- Scope: the user's own paired devices. Sharing items with other people is not decided here.
- Related: [ADR 0129](0129-core-memories-are-user-approved-and-start-every-session.md) (core memories: the user-level dataset this follows), [ADR 0116](0116-paired-devices-send-only-what-the-other-lacks-in-stored-chunks.md) (the paired exchange), [ADR 0060](0060-redact-secrets-before-memories-are-stored.md) (redaction), [ADR 0169](0169-skills-and-personas-are-written-into-the-folders-clients-read.md) (how they reach the clients), [skills](../skills.md)

## Context

On 2026-11-09 the owner asked whether "a persona and skill registry" would make sense for Imprint, "synced across devices/workspaces", so skills and personas could be created and "kept updated across your devices", and then asked for it to be built.

- Coding clients read user-level skills from their own folders on each computer: one folder per skill with a `SKILL.md` (YAML front matter with `name` and `description`, then instructions) and optional files. Claude Code reads `~/.claude/skills`; Codex, Cursor, Copilot and OpenCode read `~/.agents/skills` and folders of their own ([ADR 0169](0169-skills-and-personas-are-written-into-the-folders-clients-read.md) lists them with their sources). Claude Code reads subagents, roles it hands work to, from `~/.claude/agents/<name>.md`, in the same front-matter format.
- Nothing keeps these folders the same across clients or computers. The user copies them by hand or keeps them in a dotfiles repository.
- Imprint already keeps one kind of user-level record outside every workspace and syncs it to paired devices: core memories ([ADR 0129](0129-core-memories-are-user-approved-and-start-every-session.md)), an extra dataset that a peer receives only when its handshake offers it.
- Cursor and Codex require a skill's `name` to be lower-case letters, digits and hyphens, at most 64 characters, and equal to its folder's name (their documentation, read on 2026-11-09).

## Decision

**What an item is.** A *skill* is a set of text files with a `SKILL.md`. A *persona* is one file, kept as `PERSONA.md`, with the same front matter followed by the role's prompt. Each item is stored as its files, verbatim (line endings included), with the SHA-256 of its kind and files as its content hash. Its `name` and `description` come from the front matter: the name in the clients' format, the description 1 to 1,024 characters. Limits: 256 KiB a file, 1 MiB and 64 files an item. Only text (UTF-8) files are kept, and paths stay inside the item.

**Where it lives.** In `data/registry.sqlite3`, outside every workspace: an item belongs to the user, not to a project. A name is held by one item of each kind that is not removed. An item is active, disabled (kept, but given to no client) or removed (`retired`). Removed items stay as records, so a removal travels to the other devices.

**Credentials are refused.** A file that the redaction rules ([ADR 0060](0060-redact-secrets-before-memories-are-stored.md)) would change is refused, with the file and the kind of secret named, instead of being stored redacted. The same check runs on items received from a paired device, which are skipped when it fails.

**Sync.** Items travel between paired devices as the extra dataset `registry`, sent only to a peer whose handshake offers it, in streams of at most 4 MiB of files. The newest change of each item wins. The version it replaces goes to the receiving device's history, which keeps the last 10 versions of each item and is never synced. When two devices made an item of the same kind and name before they synced, both are kept, and every device gives the clients the older one (then the smaller id); the other is marked as shadowed for the user to rename or remove.

## Rejected alternatives

- **Items as memories in a workspace.** They would be searchable with the rest, but workspaces are isolated and a skill belongs to the user, not to a project. Core memories were kept outside workspaces for the same reason ([ADR 0129](0129-core-memories-are-user-approved-and-start-every-session.md)).
- **Redacting secrets as every other write does.** A redacted script no longer runs, and [ADR 0169](0169-skills-and-personas-are-written-into-the-folders-clients-read.md) writes the stored version back into the client's folder, so the user's own file would silently change. Refusing names the file, and nothing changes until the user removes the secret.
- **Holding both versions of an item edited on two devices between syncs, for the user to choose.** Each device would have to sync the held conflict too, or only the device that received the second edit would see it. Edits to one item on two devices within one sync interval are rare for one user, and the losing version stays in the receiving device's history.
- **A git repository of skills.** The owner could version and review skills there, and a project's own skills belong in its repository. It reaches only the clients and computers where the user installs it by hand, and it does not take back an edit made in a client's folder ([ADR 0169](0169-skills-and-personas-are-written-into-the-folders-clients-read.md)).

## Consequences

- One store reaches every connected client on every paired device. An older peer neither sends nor receives items and keeps syncing as before.
- Binary files (images, archives) in a skill's folder are not kept. A skill that holds one cannot be adopted.
- Concurrent edits on two devices between syncs keep only the newer one on both. The older edit stays in the history of the device that received the newer one, and can be restored there.
- Removing an item removes it on every paired device. Disabling it is a state that also syncs.
- A pairing gives the other device's items to this device's clients. That is the trust core memories already have; items from a person who is not the user would need approval, which this record does not provide.

## Invalidation conditions

- If skills with binary assets are common enough that adoption fails for them, store binary files, with a size limit and a content type.
- If edits on two devices within one sync interval turn out to lose work in practice, hold both versions for the user to choose and sync the held conflict.
- If items are to be shared with other people (a team), add approval of what arrives, by content hash, and limit who may change a shared item, in a new record.
- If the clients change their naming rules or the front matter they require, change the validation to match.

## Validation

- `tests/test_registry.py`, run on Windows 11 with the Python 3.13 runtime: files kept verbatim and hashed independently of order; invalid items refused (front matter, names, paths, ignored folders, bytes, size); a credential refused, naming the file and kind without the secret; one active holder per kind and name; history of 10 and restore; newest-wins import with the replaced version in history and invalid records skipped; two devices' same-named items resolving to the same holder; the dataset sent only to a peer that offers it, in bounded streams, and stored by the paired import.
- `tests/test_sync_delta.py` and `tests/test_placement_sync.py` updated for the new dataset; the sync tests pass.
- Not verified: a sync between two real devices.
