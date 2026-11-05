---
title: ADR 0136 — Review pages use paged tabs, and decisions reopen
description: Organize and Core memories are reviewed in hash-linked tabs of paged lists with keyboard shortcuts and optimistic decisions that can be undone; every decision of the user can be reopened, a move by taking its placement rule back and a core memory approval by returning it to the proposals.
---

- Status: Accepted
- Date: 2026-11-05
- Amends:
  - [ADR 0129](0129-core-memories-are-user-approved-and-start-every-session.md) — a proposal an agent or Organize made can return to proposed, which undoes its approval or rejection
  - [ADR 0130](0130-reorganization-is-proposed-by-a-background-job-and-reviewed.md) — the user's moves and keeps can be reopened, and review happens in paged tabs with a rail of target projects
  - [ADR 0133](0133-strong-placements-apply-by-themselves.md) — a move Organize made by itself is taken back with Move back, and its undo is reopened and moved as the user's move
- Related: [ADR 0036](0036-move-memories-between-projects.md), [ADR 0064](0064-single-slot-command-queue-and-shared-lock.md), [ADR 0132](0132-organize-runs-in-the-idle-slot-after-background-tagging.md), [ADR 0134](0134-placement-rules-and-organize-proposals-sync.md), [dashboard](../dashboard.md), [API](../api.md)

## Context

The user asked for tabs, pagination and a clean layout instead of one long scrollable page, on both the Organize and the Core memories pages.

The Organize page of ADR 0130 and ADR 0133 was one column: the last run, a card of automatic moves, one card per target project with every proposal in it, every project profile, and a collapsed list of decisions. The dry run of ADR 0133 on the maintainer's workspace found 142 automatic moves and 91 waiting proposals over 46 candidate projects. `organize.review()` returns every proposal except `none` in one response, so the page rendered about 233 rows and 46 profiles at once. The desktop window's minimum is 800×600 and the sidebar takes 224 px of it, which leaves 576 px of content; there the old page showed two or three rows before the first group.

Decisions could not be taken back. `organize.decide` refuses anything but a pending proposal, and a rejection survives later runs (ADR 0130). Only a move Organize made by itself had an undo (ADR 0133), and it records the proposal as rejected. `core_memories.update` refused to make an approved or retired core memory a proposal again. An Undo after an accidental Move, Keep, Approve or Reject therefore had nothing to call.

A designer wrote a spec and an interactive prototype for both pages. The prototype was checked in headless Chromium at 1360×860, 800×600 and 420×800. It shows five whole rows at 800×600 with no horizontal page scroll.

## Decision

**Both pages review in hash-linked tabs of paged lists.** A tab carries its count, and the address names the open one (`/organize/#moved`, `/core/#proposed`), as on the Sync page. Each tab is a toolbar, a page of rows and a pager. The toolbar has a search, facet filters and a select-all for the page; with rows selected it becomes a selection bar. A page holds 20 rows by default, and 10, 20 or 50 are remembered per browser. A decided row leaves at once: the page keeps its number and refills from the next one, and focus moves to the row now in its place. A tab keeps its page, filters and selection while the page is open. Single-key shortcuts act inside the page (`j`/`k`, `a`/`r`/`u`, `x`, `z`, `[`/`]`, `/`, `?`). A per-browser switch turns them off, and they never act while typing or with a dialog, menu or popover open. Below 760 px of content the layout narrows by container query, not window width: the rail becomes a select, and below 620 px the row actions become icons and the facet chips a select.

**Decisions are optimistic and undoable.** A decision updates every count at once and shows a toast with Undo (also `z`). The writes go through one client-side queue:

- one request at a time, because concurrent moves would collide on the ingestion lock, which a move takes without waiting (ADR 0064);
- adjacent writes of one kind merge into one request of at most 10 items;
- a write that has not left is cancelled by Undo without a request;
- what waits is sent with `keepalive` when the page unloads;
- once the queue drains, the page reads the server again.

A write that fails puts its row back with the error and a Retry. While a job holds the ingestion lock, Move and Move back are disabled and say why; Keep here, which takes no lock, still works.

**Every decision of the user can be reopened.** `organize.reopen` and `POST /api/organize/reopen` return accepted and rejected proposals to pending:

- An accepted move is taken back first: its source's placement rule goes through `projects.unplace`, holding the ingestion lock without waiting, as Undo of an automatic move does. `decide` writes the same per-source rule an automatic move does, so this works for both.
- A rejection only reopens.
- The change is stamped after the proposal's last change, so it reaches paired devices (ADR 0134).
- A pending proposal has nothing to reopen. A move Organize made by itself is taken back with **Move back** (`undo`), the old row button **Undo**.

Runs still never reopen anything: only the user does. Undo of a Move back reopens the rejection and moves the source again, so it ends as the user's move; Organize's own move is not restored. `core_memories.update` accepts `state: "proposed"` for a core memory an agent or Organize proposed, clearing `approved_at`, which undoes an approval or a rejection. One the user wrote was never a proposal and is refused. Each change of a core memory is stamped after its last one, so a quick Undo still reaches paired devices.

**Organize** has four tabs:

- **To review**: target projects in a rail by count beside the list, the largest group open first. A group offers Move all and Keep all, and a group that empties offers the next one.
- **Moved automatically**: the same rail, with **New since you last looked**. The time last looked is kept per browser, and only Mark all as seen changes it.
- **Decided**: one list with Reopen.
- **Profiles**: each project's profile, linking to its waiting and moved sources.

A status strip says whether Organize runs by itself and whether strong matches move, the progress of a running pass and what holds moves back. It also gives the last run and a link with the live count of core memory proposals. Proposals carry their conversation's `title`, the first question, from `source_snapshots.conversation_titles`, so a key like `conversation/9a6cc9c6` reads as what it was about.

**Core memory proposals are reviewed only on Core memories**: tabs Proposed, Active and Retired, the session budget strip above them, and an Add dialog in place of the always-open card. They are global and Organize is per workspace, judging one needs kind, scope and the budget, and agents propose them too.

**Memories stored without a project keep that origin when moved.** `device.origin_project` returned the memory's current project when its recorded origin was the empty project. So `unplace` could not find a source moved out of "no project", and its memories stayed in the target after the rule went. This affected both Reopen and Undo of an automatic move. An empty recorded origin now counts. Only a memory stored before the field existed, which has no `origin_project`, falls back to its project.

## Rejected alternatives

- **One long page with collapsible sections.** That was the page before. At the maintainer's scale it held about 233 rows and 46 profiles, and the user ruled it out.
- **Infinite scroll or virtualisation.** The user asked for pagination. Scrolling loses the place and the "page done" rhythm of batch review, and 20 rows a page need no virtualisation.
- **Grouping by evidence strength or source kind.** The bulk decision is "these belong in X", per target project, as ADR 0130 already grouped them. Evidence and origin are filters.
- **A three-column layout of groups, list and details.** It does not fit the 576 px of content an 800×600 window leaves. Expanding a row and the existing source reader cover the details.
- **Core memory proposals on Organize as well.** That makes two places to approve, and a page scoped to a workspace would show records outside every workspace. It would also show only Organize's proposals: agents propose through `store`.
- **A client-side grace period instead of a reopen endpoint**, holding decisions a few seconds before sending them. Decisions are lost when the window closes within it, nothing can be undone later from Decided, and Reopen needs the endpoint anyway.
- **A server-side "reviewed" flag for automatic moves.** It would be a new synced state for a convenience, while a per-browser time last looked covers "what is new" with no schema change.
- **Confirming every bulk decision.** Undo makes the dialogs friction. Only **Move back all** asks first, because reversing it takes two steps per source.
- **Paging on the server.** `GET /api/organize` stays one response. About 400 proposals of at most 240 characters of preview and 400 of quote are a few hundred kB on localhost, and counts, filters and the rail need them all.

## Consequences

- At 800×600 the To review tab shows five rows of the first group without scrolling, and the page never scrolls sideways at 1360, 800 or 420 px wide.
- A Move, a Keep, an approval and a rejection can be undone, from the toast or later from Decided and Retired. Reopening a move moves the memories again, so they take new IDs once more (ADR 0036).
- After an undone Move back the source is listed as moved by the user, not by Organize.
- The client grew a write queue, keyboard handling and per-tab state. Counts, filters, the rail, what is new and the budget impact are all computed in the browser from one response.
- What was last looked at is per browser: a second device shows the same automatic moves as new.
- The sidebar already names its navigation "Pages", so the pager's navigation is named "Pagination".
- Peers place memories moved out of "no project" by their own synced rules (ADR 0134). They used to keep the project those memories were moved to.
- Known gaps: a toast that counts quick successive decisions, sidebar badges, profiles on the Projects page and a dark theme are not part of this change.

## Invalidation conditions

- If Decided grows into thousands of rows, or `GET /api/organize` passes about 1 MB, add server-side status filtering and paging.
- If users review on more than one device and miss what is new, sync the time last looked.
- If moves stop taking the ingestion lock, or the server serialises them itself, drop the client's one-request-at-a-time rule.
- If the desktop window's minimum grows past about 1,000 px, the narrow layouts can go.
- If a single-key shortcut is reported to fire while typing in some field the guards miss, turn single-key shortcuts off by default.

## Validation

- `tests/test_organize.py`, `ReopenTests`:
  - reopening a move takes its rule back and returns its memories, from "no project" too;
  - a keep only reopens;
  - a later run leaves reopened proposals waiting;
  - automatic moves, pending proposals and unknown ones are refused;
  - a job holding the lock refuses the move back and changes nothing;
  - an undone Move back can be reopened and moved;
  - the stamp moves within one clock tick and the peer takes it;
  - proposals carry their conversation's title;
  - `POST /api/organize/reopen` and its validation.
- `tests/test_core_memories.py`: an approval and a rejection return to proposed, unapproved and stamped after the last change; one the user wrote is refused, directly and through `PATCH /api/core/{id}`.
- `imprint/ui/tests/organize.spec.ts` and `core.spec.ts`, in headless Chromium against the static build, with the API mocked:
  - review by group, with the rail;
  - running without the local helper;
  - Move back;
  - strong evidence waiting while automatic moves are off;
  - the keyboard flow with undo, and keys typed into Search not acting;
  - refill and clamp of pages;
  - a whole page moved in requests of ten;
  - a finished group offering the next one;
  - the 800×600 layout;
  - a failed move with Retry;
  - a running Organize's progress and refetch;
  - Reopen from Decided;
  - on Core memories: undo of an approval, paging and the kind filter, and the keyboard.
- Screenshots of both pages at 1360×860, 800×600 and 420×800 in headless Chromium on macOS.
- Not verified: the Electron desktop window itself, Windows, a screen reader, and the pages against the maintainer's real workspace rather than mocked data at its scale.
