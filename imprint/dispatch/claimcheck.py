"""A Dispatched session's claim across paired devices (ADR 0224).

Of two claims two devices make on one Task before they sync, the earlier
one holds on every device. Before starting a client, the Dispatcher has
sync exchange at once and waits, at most ``WAIT_SECONDS``, until an
exchange that began after its claim has completed with every enabled pair
of the Task's workspace seen in the last ``SEEN_WITHIN`` seconds; a pair
not seen that recently is not waited for. Then it starts the client only
if its claim still holds. While the session runs, the Dispatcher checks
the claim and stops the session itself when it no longer holds it: lost
to another session's claim, released, or ended by the person (ADR 0225).
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


HELD, LOST, FINISHED, RELEASED, ENDED = 'held', 'lost', 'finished', 'released', 'ended'
_MOVES = {'review': 'moved the Task to Review', 'done': 'moved the Task to Done', 'cancelled': 'cancelled the Task'}


def _who(event: dict) -> str:
    if event['actor'] == 'user':
        return 'the person'
    if event['actor'] == 'system':
        return 'Imprint'
    return tasks.label({'name': event.get('agent'), 'client': event.get('client'), 'session': event['session']})


def holders(events: list[dict], session: str) -> set[str]:
    """The Board sessions that hold the claims ``session`` made, from a
    Task's ``events``: ``session`` and each session that took one of its
    claims over with the token (ADR 0224). A takeover of a Stale claim
    without the token is another agent's: it gets a token of its own."""
    ours, tokens = {session}, {}
    for event in events:
        if event['kind'] != 'claimed':
            continue
        data = event['data']
        replaced = data.get('replaces')
        if event['session'] == session or (replaced in tokens and data.get('token_hash') == tokens[replaced]):
            tokens[event['id']] = data.get('token_hash')
            ours.add(event['session'])
    return ours


def standing(task: str, workspace: str, session: str) -> tuple[str, str | None]:
    """How the claim the Dispatcher's ``session`` made for a run stands, held
    by it or by a session that took it over with the token (``holders``,
    ADR 0225):

    - (HELD, None): one of them holds it, live or Stale;
    - (LOST, winner): another session's claim holds or took the Task (ADR 0224);
    - (FINISHED, None): one of them ended it by moving the Task to Review, Done or Cancelled;
    - (RELEASED, None): one of them released it unfinished;
    - (ENDED, what happened): the person, Imprint or another agent released
      it, or moved the Task to Review, Done or Cancelled.

    Raises LookupError when the Task was deleted."""
    state = tasks.get(task, workspace=workspace)
    # Events only accumulate, so history read after the state holds every
    # takeover the state shows. Read the other way round, a takeover with the
    # token landing between the two reads looks like another agent's claim.
    events = tasks.history(task, workspace=workspace)
    sessions = holders(events, session)
    held = state.get('holder') or (state.get('previous_holder') if state.get('stale') else None)
    if held and held.get('session') in sessions:
        return HELD, None
    if held:
        return LOST, f"{tasks.label(held)} on {held.get('device') or 'another device'}"
    claims, ending = set(), None
    for event in events:
        kind, data = event['kind'], event['data']
        if kind == 'claimed' and event['session'] in sessions:
            claims.add(event['id'])
            ending = None
        elif not claims or ending is not None:
            continue
        elif kind == 'claimed' and data.get('replaces') in claims:
            ending = event  # another agent took it over, then let it go
        elif kind == 'released' and data.get('claim') in claims:
            ending = event
        elif kind == 'moved' and data.get('status') in _MOVES:
            ending = event
    if ending is None:
        return ENDED, 'its claim ended'
    if ending['kind'] == 'claimed':
        return LOST, f"{_who(ending)} on {ending.get('device') or 'another device'}"
    if ending['session'] in sessions and ending['actor'] == 'agent':
        return (RELEASED, None) if ending['kind'] == 'released' else (FINISHED, None)
    what = 'released its claim' if ending['kind'] == 'released' else _MOVES[ending['data']['status']]
    return ENDED, f'{_who(ending)} {what}'


def confirm(task: str, workspace: str, session: str, claimed_at: float, *, on_wait=None,
            wait: float = WAIT_SECONDS) -> tuple[str, str | None]:
    """Wait until the claim ``session`` made at ``claimed_at`` has reached
    the paired devices to wait for, then say how it stands (``standing``):
    (HELD, None) when the client may start."""
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
    return standing(task, workspace, session)
