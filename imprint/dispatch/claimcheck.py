"""A Dispatched session's claim across paired devices (ADR 0224).

Of two claims two devices make on one Task before they sync, the earlier
one holds on every device. Before starting a client, the Dispatcher has
sync exchange at once and waits, at most ``WAIT_SECONDS``, until an
exchange that began after its claim has completed with every enabled pair
of the Task's workspace seen in the last ``SEEN_WITHIN`` seconds; a pair
not seen that recently is not waited for. Then it starts the client only
if its claim still holds. While the session runs, the Dispatcher checks
the claim, and stops the session itself when another device's earlier
claim won.
"""

from __future__ import annotations

import time

from .. import tasks

WAIT_SECONDS = 120.0
SEEN_WITHIN = 600.0
POLL = 1.0


def peers(workspace: str, now: float | None = None) -> list[dict]:
    """The paired devices that sync ``workspace`` and were seen recently enough to wait for."""
    from .. import sync_pairs
    now = time.time() if now is None else now
    return [pair for pair in sync_pairs.pairs()
            if pair.get('enabled') and pair.get('state') == 'active' and pair.get('workspace') == workspace
            and pair.get('last_seen') and now - float(pair['last_seen']) < SEEN_WITHIN]


def holder(task: str, workspace: str) -> dict | None:
    return tasks.get(task, workspace=workspace).get('holder')


def ours(task: str, workspace: str, sessions: set[str]) -> tuple[bool, str | None]:
    """Whether the Task's claim is still one of ``sessions``; else who holds it, as people read it."""
    held = holder(task, workspace)
    if held and held.get('session') in sessions:
        return True, None
    state = tasks.get(task, workspace=workspace)
    if not held and state['status'] in ('review', 'done', 'cancelled'):
        return True, None  # finished: nothing to lose any more
    return False, (f"{tasks.label(held)} on {held.get('device') or 'another device'}" if held else 'nobody')


def confirm(task: str, workspace: str, session: str, claimed_at: float, *, on_wait=None,
            wait: float = WAIT_SECONDS) -> tuple[bool, str | None]:
    """Wait until the claim ``session`` made at ``claimed_at`` has reached
    the paired devices to wait for, then say whether it still holds:
    (True, None), or (False, the winner)."""
    from .. import sync_pairs
    waiting = peers(workspace)
    if waiting:
        sync_pairs.request_exchange([pair['id'] for pair in waiting])
        deadline = time.monotonic() + wait
        ids = {pair['id'] for pair in waiting}
        while time.monotonic() < deadline:
            fresh = [pair for pair in sync_pairs.pairs() if pair['id'] in ids]
            if all(float(pair.get('last_exchange_began') or 0) >= claimed_at for pair in fresh):
                break
            if on_wait:
                on_wait()
            time.sleep(POLL)
    return ours(task, workspace, {session})
