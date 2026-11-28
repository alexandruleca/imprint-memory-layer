# The Imprint Board as a repository's issue tracker

Skills such as to-tickets, triage, wayfinder and implement-spec read and write "the configured issue tracker", described in the repository's `docs/agents/issue-tracker.md`. To make the Imprint Board that tracker, choose **Other** when `setup-matt-pocock-skills` asks for the tracker, and give it the description below.

## The description for `docs/agents/issue-tracker.md`

```markdown
# Issue tracker: the Imprint Board

Issues are Tasks on this workspace's Imprint Board, read with the `tasks` MCP tool and changed with `task_edit` (or `imprint tasks …` in a shell). Task ids look like `T-7f3kq`. Load the `imprint-tasks` skill for the Board's rules.

- Create: `task_edit(action="create", title, description, acceptance=[…], tags=[…])`; add `status="Ready"` for agent-ready work.
- Read: `tasks(action="get", task=…)`, with its comments; history with `action="history"`.
- List: `tasks(action="list", status=…, tag=…, feature=…, assignee=…)`.
- Parent and sub-issues: the parent is a Feature; create each sub-issue with `feature=<parent id>`, or `task_edit(action="link", task=…, feature=<parent id>)`. Create the parent in Backlog and move it to Ready once its sub-issues exist: until then it looks like a standalone Task agents may take.
- Blocked by: `blockers=[…]` at create, or `task_edit(action="link", task=…, add=[<blocker ids>])`; open blockers show on the Task.
- Comment: `task_edit(action="comment", task=…, text=…)`.
- Labels: tags, with `task_edit(action="tag", task=…, add=[…], remove=[…])`.
- Assign or claim: an agent claims with `task_edit(action="claim", task=…)`; assign a person or client with `task_edit(action="assign", task=…, add=[…])`.
- Close: comment first, then `task_edit(action="move", task=…, status="Done")` (from Review, by a session that did not do the work).
- Append to the body: `task_edit(action="append", task=…, text=…)`; appends from several sessions at once all stay.
- Frontier (open, unblocked, unclaimed sub-issues in order): `tasks(action="next", feature=<parent id>)`.
```

## How the skills' words map onto the Board

| Tracker word | On the Board |
| --- | --- |
| `needs-triage`, `needs-info` | Backlog, with that tag |
| `ready-for-agent` | Ready |
| `ready-for-human` | Ready, assigned to `user` |
| `wontfix` | Cancelled |
| label | tag |
| sub-issue of a parent | a Task of that Feature |
| blocked-by edge | blocking Task |
| assign (claim) | claim |
| close | Done, after the closing comment |
| append to the body | append to the description |
| frontier | `tasks(action="next", feature=…)` |

Triage states other than the ready ones stay tags, so the Board's columns remain its fixed statuses.
