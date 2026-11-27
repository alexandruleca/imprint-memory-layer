"""The Board: each workspace's Tasks, kept as a log of events (ADRs 0208, 0209).

Every change to a Task is an event that is never changed afterwards: what
changed, the device and agent session that changed it, and a hybrid logical
clock timestamp. A Task's current state is computed from its events in clock
order, then event id, so every device that holds the same events computes the
same state. The computed state is kept beside the events as a projection that
any write to the Task rebuilds from them.

The store is per workspace (``data/tasks.sqlite3`` for the default one,
``data/tasks_<name>.sqlite3`` for others) and shared by every process of the
installation: the MCP servers of all connected clients, the CLI and the
dashboard. Writes take SQLite's write lock for the clock tick, the event and
the projection together.

Text is redacted before it is stored (ADR 0060).

The package is split by reason to change: ``store`` (events, clock,
projection), ``state`` (folding events into a Task's state), ``view``
(reading the Board), ``changes`` (the user's and agents' changes),
``claims``, ``names``, ``comments``, ``work`` (what to take next, handoffs)
and ``merge`` (events from another device). Callers use the names below.
"""

from __future__ import annotations

from .actors import CLIENTS, USER, agent, client_id, device_name, label
from .store import STATUSES, PRIORITIES, ID_ALPHABET, ID_LENGTH, task_ref
from .validation import ASSIGNEES, CRITERIA, STATUS_LABELS, TAGS, status_ref
from .view import ARCHIVE_DAYS, get, listing, board, history
from .changes import create, edit, append, move, tag, assign, link, block, unblock, delete
from .claims import claim, checkpoint, release, renew, lost_claims, take_unseen, unseen
from .names import RESERVED_NAMES, choose_name, name_of, ensure_name
from .comments import comment, inbox, user_inbox, mark_user_read
from .work import AUTONOMY, autonomy, set_autonomy, takeable, next_task, frontier, handoff
from .merge import import_events, sync_export, sync_import, sync_manifest
from .progress import progress
from . import sessions

__all__ = ['CLIENTS', 'USER', 'agent', 'client_id', 'device_name', 'label', 'STATUS_LABELS', 'status_ref', 'sessions', 'STATUSES', 'PRIORITIES', 'ID_ALPHABET', 'ID_LENGTH', 'task_ref', 'ASSIGNEES', 'CRITERIA', 'TAGS', 'ARCHIVE_DAYS', 'get', 'listing', 'board', 'history', 'create', 'edit', 'append', 'move', 'tag', 'assign', 'link', 'block', 'unblock', 'delete', 'claim', 'checkpoint', 'release', 'renew', 'lost_claims', 'take_unseen', 'unseen', 'RESERVED_NAMES', 'choose_name', 'name_of', 'ensure_name', 'comment', 'inbox', 'user_inbox', 'mark_user_read', 'AUTONOMY', 'autonomy', 'set_autonomy', 'takeable', 'next_task', 'frontier', 'handoff', 'import_events', 'sync_export', 'sync_import', 'sync_manifest', 'progress']
