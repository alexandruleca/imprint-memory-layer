"""Whether agents still search Imprint first once their client got the
short rules block (ADR 0214).

Setup gives clients that load skills a few always-on lines and moves the
rest of the guidance into the built-in ``imprint`` skill; it records in
``data/instructions.json`` when each client first got them. For each
client this compares the share of its agent sessions whose first Imprint
call is ``search`` over the 14 days before and the 14 days after that
switch, from the calls the Agents page records (ADR 0117). Only sessions
that called Imprint count, each by its first call, on this device.

Upkeep makes the decision once per client: with at least 10 sessions in
each window, a drop of more than 10 points restores the full block (the
client joins ``instructions.full_rules`` and a queued ``connect`` rewrites
its rules), anything less keeps the short one. With too few sessions after
the switch the window after grows, up to 6 weeks; with too few before, or
still too few after 6 weeks, the short block stays. The window before is
counted on the first pass after the switch and kept in the file, since the
Agents page forgets sessions after 30 days.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime
from typing import Callable

from . import config

STATE = 'instructions.json'
DAY = 86400.0
WINDOW_DAYS = 14      # each side of the switch
LONGEST_DAYS = 42     # the window after grows to this while it has too few sessions
MIN_SESSIONS = 10     # in each window, to compare
MAX_DROP = 10.0       # percentage points the share may fall before the full block returns
SEARCH = 'search'

# Each session's first Imprint call, by when it was made.
_FIRST_CALLS = """
SELECT client, tool FROM (
    SELECT s.client AS client, c.tool AS tool, c.started_at AS at,
           ROW_NUMBER() OVER (PARTITION BY c.session_id ORDER BY c.started_at, c.id) AS n
    FROM agent_calls c JOIN agent_sessions s ON s.id = c.session_id
) WHERE n = 1 AND at >= ? AND at < ?
"""


# ── The state file ────────────────────────────────────────────────────

def _path():
    return config.get_data_dir() / STATE


def read() -> dict:
    try:
        data = json.loads(_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict) -> None:
    path = _path()
    temporary = path.with_name(f'{STATE}.{os.getpid()}.tmp')
    temporary.write_text(json.dumps(data, indent=2), encoding='utf-8')
    for attempt in range(5):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:  # Windows refuses while a reader has it open for a moment.
            if attempt == 4:
                raise
            time.sleep(0.2)


def _since(entry) -> float | None:
    value = entry.get('short_since') if isinstance(entry, dict) else None
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value)
    return None


def _clients(data: dict) -> dict[str, dict]:
    clients = data.get('clients')
    if not isinstance(clients, dict):
        return {}
    return {name: entry for name, entry in clients.items() if _since(entry) is not None}


# ── Counting ──────────────────────────────────────────────────────────

def tally(client: str, start: float, end: float) -> dict:
    """``client``'s sessions on this device whose first Imprint call fell in
    [start, end), and how many of those began with ``search``."""
    path = config.get_data_dir() / 'agent_activity.sqlite3'
    rows = []
    if path.exists():
        try:
            conn = sqlite3.connect(f'file:{path.as_posix()}?mode=ro', uri=True, timeout=5)
            try:
                rows = conn.execute(_FIRST_CALLS, (start, end)).fetchall()
            finally:
                conn.close()
        except sqlite3.Error:
            rows = []
    from .tasks import client_id
    firsts = [tool for name, tool in rows if client_id(name) == client]
    return {'sessions': len(firsts), 'search_first': sum(tool == SEARCH for tool in firsts)}


def share(counts: dict | None) -> float | None:
    """The percentage of sessions that searched first, to one decimal."""
    if not counts or not counts.get('sessions'):
        return None
    return round(100.0 * counts['search_first'] / counts['sessions'], 1)


# ── Deciding, once per client ─────────────────────────────────────────

def _next_check(entry: dict) -> float | None:
    """When ``entry`` needs a pass next; None once decided."""
    if entry.get('decision'):
        return None
    since = _since(entry)
    if 'before' not in entry:
        return since
    after = entry.get('after')
    if not isinstance(after, dict):
        return since + WINDOW_DAYS * DAY
    return min(float(after.get('until') or since) + DAY, since + LONGEST_DAYS * DAY)


def due(now: float | None = None) -> bool:
    """Whether a client's measurement has a step to take; reads the state
    file alone."""
    now = time.time() if now is None else now
    return any((at := _next_check(entry)) is not None and at <= now for entry in _clients(read()).values())


def _queue_connect(client: str) -> None:
    from . import queue
    queue.submit('connect', {'target': client})


def _restore(client: str, connect: Callable[[str], None] | None) -> None:
    """Give ``client`` the full block: list it in instructions.full_rules,
    then have ``imprint connect`` rewrite its rules."""
    from .config_schema import resolve, set_value
    listed = [name.strip() for name in str(resolve('instructions.full_rules')[0] or '').split(',') if name.strip()]
    if client not in listed:
        set_value('instructions.full_rules', ','.join([*listed, client]))
    (connect or _queue_connect)(client)


def _decide(entry: dict, now: float) -> str | None:
    since = _since(entry)
    before, after = entry['before'], entry['after']
    if before['sessions'] >= MIN_SESSIONS and after['sessions'] >= MIN_SESSIONS:
        return 'restored' if share(before) - share(after) > MAX_DROP else 'kept'
    if before['sessions'] < MIN_SESSIONS or now >= since + LONGEST_DAYS * DAY:
        return 'too_few'
    return None


def step(now: float | None = None, connect: Callable[[str], None] | None = None) -> list[str]:
    """Take every step that is due; returns a line for each decision."""
    now = time.time() if now is None else now
    data = read()
    lines = []
    for client, entry in _clients(data).items():
        at = _next_check(entry)
        if at is None or at > now:
            continue
        since = _since(entry)
        if 'before' not in entry:
            entry['before'] = tally(client, since - WINDOW_DAYS * DAY, since)
        if now >= since + WINDOW_DAYS * DAY:
            after = entry.get('after')
            if not isinstance(after, dict):
                end = since + WINDOW_DAYS * DAY
                entry['after'] = {**tally(client, since, end), 'until': end}
            elif after['sessions'] < MIN_SESSIONS:
                end = min(now, since + LONGEST_DAYS * DAY)
                if end > after['until']:
                    more = tally(client, after['until'], end)
                    entry['after'] = {'sessions': after['sessions'] + more['sessions'],
                                      'search_first': after['search_first'] + more['search_first'], 'until': end}
            outcome = _decide(entry, now)
            if outcome:
                entry['decision'] = {'outcome': outcome, 'at': now, 'before': share(entry['before']),
                                     'after': share(entry['after'])}
                if outcome == 'restored':
                    _restore(client, connect)
                lines.append(describe(_row(client, entry, now)))
        _write(data)
    return lines


# ── Showing it ────────────────────────────────────────────────────────

def _row(client: str, entry: dict, now: float) -> dict:
    since = _since(entry)
    before = entry.get('before') if isinstance(entry.get('before'), dict) else tally(
        client, since - WINDOW_DAYS * DAY, since)
    after = entry.get('after') if isinstance(entry.get('after'), dict) else tally(
        client, since, min(now, since + WINDOW_DAYS * DAY))
    counts = lambda c: {'sessions': c['sessions'], 'search_first': c['search_first'], 'share': share(c)}
    decision = entry.get('decision') if isinstance(entry.get('decision'), dict) else None
    return {'client': client, 'short_since': since, 'before': counts(before), 'after': counts(after),
            'decide_at': since + WINDOW_DAYS * DAY, 'decision': decision}


def comparison(now: float | None = None) -> list[dict]:
    """Each client that got the short block: its sessions and search-first
    share before and after, and the decision once made."""
    now = time.time() if now is None else now
    return [_row(client, entry, now) for client, entry in sorted(_clients(read()).items())]


def _day(seconds: float) -> str:
    return datetime.fromtimestamp(seconds).strftime('%Y-%m-%d')


def _part(counts: dict, side: str) -> str:
    if counts['share'] is None:
        return f'no sessions {side}'
    return f"{counts['share']:g}% of {counts['sessions']} sessions {side}"


def describe(row: dict) -> str:
    """One line for doctor and upkeep's log."""
    measured = f"searched Imprint first in {_part(row['before'], 'before')} the short rules block and {_part(row['after'], 'after')}"
    decision = row['decision']
    if not decision:
        return (f"Short rules block since {_day(row['short_since'])}, compared on {_day(row['decide_at'])}; "
                f"so far agents {measured}")
    if decision['outcome'] == 'restored':
        return f"Agents {measured}, so Imprint gave {row['client']} the full block again on {_day(decision['at'])}"
    if decision['outcome'] == 'kept':
        return f"Agents {measured}: the short block stays"
    return (f"Too few sessions to compare ({row['before']['sessions']} before, {row['after']['sessions']} after, "
            f"{MIN_SESSIONS} needed in each): the short block stays")
