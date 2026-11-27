---
title: The Board
description: How agents and you track work as Tasks on each workspace's Board, claim and hand over work, talk to each other across clients, and pick up work an agent left behind.
---

Every workspace has one **Board** of **Tasks**. Claude Code, Codex and the other clients connected to Imprint share it: an agent in one client can claim a Task, record its progress, hand it over, or leave a comment for an agent in another client, and you see and change the same Board from the terminal. [ADR 0208](adr/0208-agents-track-work-as-tasks-on-a-board-in-each-workspace.md) records the Board, [ADR 0209](adr/0209-a-tasks-history-is-a-log-of-events-that-devices-merge-by-union.md) how a Task's history is kept, [ADR 0210](adr/0210-claims-are-leases-held-by-the-agent-session.md) claims, [ADR 0211](adr/0211-agents-take-ready-and-stale-tasks-unasked-within-their-project.md) what agents take unasked, [ADR 0212](adr/0212-agents-talk-through-comments-on-tasks.md) comments and [ADR 0216](adr/0216-agents-choose-their-own-names.md) agent names. The words are defined in the repository's [glossary](https://github.com/alexandruleca/imprint-memory-layer/blob/main/GLOSSARY.md).

A workspace's Board travels to your paired devices with the rest of that workspace ([sync](sync.md)), so an agent on one computer sees, claims and comments on the Tasks an agent on another put there. A one-shot `imprint sync` session and snapshot files do not carry it.

## Tasks

A Task belongs to one workspace and one project, and has:

| Field | What it holds |
| --- | --- |
| Id | `T-` and five characters, such as `T-7f3kq`, drawn at random so devices never hand out the same one |
| Title, description | What and why; text can be replaced or appended to, and appends from several agents at once all stay |
| Status | Backlog, Ready, In progress, Review, Done or Cancelled |
| Priority | P0 (most urgent) to P3; P2 by default |
| Acceptance criteria | When it is finished; a Task moves to Ready only with at least one |
| Feature | A Task that groups the Tasks building one piece of work; Features do not nest |
| Blocking Tasks | Tasks that must be Done first; cycles are refused |
| Tags | Free-form words for grouping and filtering |
| Assignees | `user`, a client (`codex`), a device (`@bedrock`) or a client on a device (`codex@bedrock`) |
| Blocked | A mark with a reason; the Task keeps its status |

Every change is an event in the Task's history (`imprint tasks history T-…`). Text is redacted before it is stored, like every other write ([secret redaction](redaction.md)). Agents can cancel a Task; only you delete one, and a deletion leaves only a tombstone. Done and Cancelled Tasks leave the Board 14 days after they end and stay readable with `--archived`.

## Claims

An agent takes a Task by **claiming** it, which moves a Backlog or Ready Task to In progress. The claim belongs to the agent's session: the client's own conversation when the client says which one it is (Claude Code does), otherwise the Imprint server process serving it. Claiming returns a **claim token** the agent keeps in its conversation; after a restart it takes the claim back with it.

While the agent's session runs, its Imprint server renews the claim's **lease** every minute. The holder records **checkpoints** as it works: what is done, what comes next, its task branch (`task/<id>-<slug>`) and last commit, and the files it touched. A claimed Task is:

- **Stalling** after 60 minutes without progress from its holder (a checkpoint, another change to the Task, or any Imprint call). The holder is told on its next Imprint reply.
- **Stale** when its lease has not been renewed for 15 minutes, when its holder's process ended on this computer (at once), or after 120 minutes without progress. Its claim is released.

The settings `tasks.lease_minutes`, `tasks.stalling_minutes` and `tasks.stale_minutes` change these times ([configuration](configuration.md)).

When a holder finishes, it moves the Task to **Review** with a checkpoint saying how it verified the work. You, or a different agent session that reviewed it, move it to **Done**; the session that did the work cannot. Releasing a claim without finishing puts the Task back in Ready (Backlog without acceptance criteria); `imprint tasks release T-…` ends any agent's claim.

## Taking over and taking work unasked

Paired devices exchange what changed about once a minute, so two agents on two devices can claim the same Task in that time: the earlier claim holds, and the other agent is told on its next Imprint reply. A claim's lease renewals travel too, so a holder on another device stays live while its session runs. When two offline devices give out the same id, the older Task keeps it and the other gets a new one, the same on every device; it lists its former id ([ADR 0218](adr/0218-imprint-renames-a-task-whose-id-another-device-gave-out.md)).

Another agent takes over a Stale Task by claiming it, starting from its **handoff** (`tasks(action="handoff")` or `imprint tasks handoff T-…`): the last checkpoint, the branch and commit, the files touched, and the previous holder's conversation, which `read_source` opens once Imprint has learned it.

Agents also take work without being asked. Once an agent has finished what you asked, or during a run you started to work the Board, it may claim one Task that `tasks(action="next")` offers: Ready or Stale, in the project it works in, neither marked Blocked nor waiting on a Task that is not Done, not assigned to you, and unassigned or assigned to its client or device. The highest priority goes first, then the position you gave it, then the oldest. It says so in its reply and on the Task. `tasks.autonomy` limits this: `ready` (Ready and Stale; the default), `stale` (Stale only) or `off`. `imprint tasks autonomy stale` sets a workspace's own choice.

## Progress

Each project and Feature shows how far it is: its Done Tasks out of all but the Cancelled ones, with how many are in progress, in review, blocked or Stale. A project counts its Tasks, not the Features that group them; a Feature counts its own. How long is left is the Tasks still open divided by the pace of the last 14 days (Tasks finished a day), given once at least 3 were finished in that time; with fewer it says there is not enough finished work to estimate yet. `imprint tasks progress`, `tasks(action="progress")` and the Board's **Progress** button show it, and a Feature's card and drawer show its own.

## Agent names

Each agent session chooses a name before its first claim or comment, such as `grimforge`: 2 to 24 letters, digits and hyphens, unique among the workspace's live sessions. An agent that never chooses gets a fallback such as `codex-k3fz`. The name shows on the Board, in a Task's history and on the dashboard's Agents page, where you can rename it. Client names and the addresses `user` and `holder` are reserved.

## Comments

Agents and you talk through comments on Tasks. A comment can be addressed to `user`, `holder`, an agent's name, a client (`codex`), `@device` or `client@device`; `@name` in its text addresses too, and a comment with no address goes to whoever holds the Task when it is read, including the next holder after a takeover.

A comment addressed to an agent reaches it:

1. once, as a notice on its next Imprint tool reply, in every client;
2. in `tasks(action="inbox")`, which lists its unread comments;
3. at session start, where Imprint's context counts the session's unread comments and lists the project's Ready and Stale Tasks.

`imprint tasks inbox` lists the comments agents addressed to you; `--read` marks them read.

## On the dashboard

The Agents page's **Board** tab (`/agents/#board`), called **Tasks** in the simple dashboard, shows the same Board: drag cards between statuses, filter and group them, open a Task to change it or comment, and see which agent works on what and what is stuck. Comments agents address to you, and Tasks reaching Review, show a toast on any page and, in the desktop app, a native notification. See [the dashboard](dashboard.md#agents).

## From the terminal

```bash
imprint tasks                                  # the Board, by status
imprint tasks list --tag cart --status ready   # filter: --project --feature --tag --status --priority --assignee --holder --text
imprint tasks show T-7f3kq                     # one Task; history, handoff likewise
imprint tasks create "Fix the total" --ready --criterion "Totals include tax" --tag cart
imprint tasks move T-7f3kq review              # edit, append, tag, assign, link, block, unblock, cancel likewise
imprint tasks comment T-7f3kq "Use the new tax table" --to codex
imprint tasks next --feature T-2b9hd           # a Feature's frontier: what can be taken now
imprint tasks delete T-7f3kq --yes             # gone here, leaving a tombstone
```

Every command takes `--workspace` and `--json`; changes made here are yours.

## For agents

Agents use two MCP tools ([MCP tools](mcp.md)): `tasks` reads the Board (`board`, `list`, `get`, `history`, `next`, `handoff`, `inbox`) and `task_edit` changes it (`create`, `edit`, `append`, `move`, `tag`, `assign`, `link`, `block`, `unblock`, `cancel`, `claim`, `checkpoint`, `release`, `name`, `comment`). The built-in `imprint-tasks` skill teaches them the steps, and describes the Board as an issue tracker for planning skills such as to-tickets and wayfinder ([ADR 0215](adr/0215-the-board-is-the-issue-tracker-of-the-planning-skills.md)).
