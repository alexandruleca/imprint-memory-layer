# Imprint

Imprint keeps a person's project knowledge, decisions and agent work in one local memory that every connected agent client and every paired device shares.

## Language

### Where things live

**Workspace**:
An isolated set of memories, facts and Tasks; nothing in one workspace is visible from another.
_Avoid_: namespace, collection

**Project**:
A codebase or body of work under its canonical name, the same on every device.
_Avoid_: repo, folder

**Client**:
An application an agent runs in and that connects to Imprint, such as Claude Code, Codex or Cursor.
_Avoid_: harness, tool, app

**Agent session**:
One conversation of an agent in a Client, identified by the Client's own conversation id where the Client reports one, otherwise by its Imprint server process.
_Avoid_: MCP session, process

**Agent name**:
The name an agent chooses for itself in an Agent session, shown wherever it acts and used to address it.
_Avoid_: nickname, handle, alias

### Tracking work

**Board**:
The Tasks of one workspace, laid out by Status; each workspace has exactly one.
_Avoid_: kanban, tracker, project board

**Task**:
One unit of work on a Board, belonging to one Project.
_Avoid_: issue, ticket, card, todo

**Status**:
Where a Task stands: **Backlog** (captured, not yet ready to start), **Ready** (described well enough for an agent to start), **In progress** (claimed and being worked on), **Review** (finished, waiting to be checked), **Done**, or **Cancelled**.
_Avoid_: column, state, stage

**Blocked**:
A mark on a Task, with a reason, that work on it cannot continue; it is not a Status, so the Task keeps its place.
_Avoid_: on hold, stuck

**Blocker**:
Another Task that must be Done before this one can start.
_Avoid_: dependency, prerequisite

**Feature**:
A Task that groups the Tasks building one piece of work; its Tasks have no Tasks of their own.
_Avoid_: epic, parent, story, sub-issue, map

**Tag**:
A free-form word on a Task for grouping and filtering.
_Avoid_: label, category

**Assignee**:
Who a Task is meant for: the person, a Client, a device, or a Client on a device. A Task assigned to the person is never claimed by an agent.
_Avoid_: owner, holder, human task

### Working a Task

**Claim**:
An Agent session's hold on one Task while it works on it.
_Avoid_: lock, assignment, checkout

**Holder**:
The Agent session that holds a Task's Claim.
_Avoid_: owner, assignee, worker

**Claim token**:
The proof an Agent session keeps that a Claim is its own, so it can take the Claim back after a resume or restart.
_Avoid_: lock id, ticket

**Lease**:
How long a Claim lasts without renewal; a Claim whose Lease runs out is released.
_Avoid_: lock timeout, heartbeat

**Checkpoint**:
A progress record the Holder adds to its Task: what is done, what is next, and where the work is.
_Avoid_: status update, progress note

**Comment**:
A message on a Task from the person or an agent, optionally addressed to a Client, a device or the Holder.
_Avoid_: message, chat, note

**Stalling**:
A claimed Task whose Holder is alive but has shown no progress for a while; a warning, not yet a release.
_Avoid_: slow, idle, stuck

**Stale**:
A Task whose Claim was released because its Holder stopped renewing the Lease or stopped making progress for too long.
_Avoid_: abandoned, dead, orphaned

**Handoff**:
What the next agent starts from when it takes a Task over: the last Checkpoint, where the work is, and the previous Holder's conversation.
_Avoid_: brief, summary, context dump

**Takeover**:
Claiming a Stale Task and continuing it from its Handoff.
_Avoid_: hijack, steal, reassignment

### Dispatching

**Dispatcher**:
The part of Imprint on one device that, when the person switches it on for a workspace, picks Tasks that are open or waiting for Review and starts Dispatched sessions for them; it is not an agent itself.
_Avoid_: loop agent, orchestrator, scheduler, runner

**Dispatched session**:
An Agent session the Dispatcher started, without the person, to work on one Task, to review it, or to refine it.
_Avoid_: background agent, headless agent, worker, job

**Refinement**:
A Dispatched session that grooms one Backlog Task without acceptance criteria (criteria, description, priority, tags), reading a throwaway copy of the project, and leaves the Task in Backlog for the person; switched on as "Refine Backlog".
_Avoid_: enhancement agent, grooming bot, triage

### Skills

**Built-in skill**:
A skill that ships with an Imprint release, read-only, the same on every device running that release.
_Avoid_: system skill, default skill, bundled skill

**Fork**:
The person's own editable copy of a Built-in skill, under the same name, which replaces the Built-in skill on every device.
_Avoid_: override, custom copy
