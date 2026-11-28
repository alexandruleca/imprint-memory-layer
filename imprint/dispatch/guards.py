"""What bounds the Dispatcher's spending (ADR 0220, "Limits", "Failures", "Its own Tasks").

- ``dispatch.daily_limit`` sessions start a day on this device;
- after ``dispatch.failure_limit`` failures in a row, sessions that failed
  to start included, the Dispatcher switches itself off and tells the
  person (ADR 0228);
- a Task whose Dispatched sessions fail twice goes to the person;
- a Task a Dispatched session created, or took out of Backlog, is never
  worked on or reviewed until the person moves it, so dispatched work
  cannot feed itself (ADR 0227).

A session the person stopped, or the server's shutdown, is *stopped*: it
is no failure.
"""

from __future__ import annotations

import datetime
import re
import sys
import time

from .. import tasks
from ..tasks.actors import SYSTEM
from . import store

TASK_FAILURES = 2


def limit(key: str) -> int:
    """One of the Dispatcher's limits, read afresh so a change applies on the
    next pass; its default when the value set is out of its bounds (an
    earlier release accepted any number), with a warning."""
    from ..config_schema import SETTINGS_BY_KEY, resolve
    try:
        return int(resolve(key)[0])
    except (ValueError, TypeError) as error:
        default = int(SETTINGS_BY_KEY[key].default)
        print(f'[imprint] dispatcher uses {key} = {default}, its default: {error}', file=sys.stderr, flush=True)
        return default


_setting = limit


def started_today(now: float | None = None) -> int:
    """Sessions started on this device since local midnight: runs whose
    client started, and runs still starting. A run refused, lost, stopped
    or failed before its client started spent no session (T-2tsmh)."""
    midnight = datetime.datetime.combine(datetime.date.fromtimestamp(now or time.time()), datetime.time()).timestamp()
    return sum(1 for run in store.recent(500)
               if run['started_at'] >= midnight and (run['pid'] is not None or run['ended_at'] is None))


def held() -> str | None:
    """Why the Dispatcher starts nothing now despite being on, or None."""
    limit = _setting('dispatch.daily_limit')
    if started_today() >= limit:
        return (f'{limit} Dispatched sessions started today, the daily limit (dispatch.daily_limit): '
                'more start tomorrow')
    return None


def failures_in_a_row() -> int:
    """Failed runs since the last success, counting only runs started after
    the Dispatcher was last switched on; stopped and refused runs do not count."""
    since = store.last_switched_on()
    count = 0
    for run in store.recent(100):
        if run['ended_at'] is None or run['outcome'] in ('stopped', 'refused'):
            continue
        if run['started_at'] < since or run['outcome'] != 'failed':
            break
        count += 1
    return count


def run_ends(task: str, workspace: str) -> dict[str, dict]:
    """How each Dispatched run on ``task`` ended, by run id, on every paired
    device: the tags on Imprint's notes at the runs' ends in the Task's
    synced history, and this device's own runs (some may predate the tags),
    each run once (ADR 0234). Only Imprint's own notes count."""
    ends = {r['id']: {'role': r['role'], 'outcome': r['outcome'], 'started': bool(r['pid'])}
            for r in store.runs_of(task) if r['ended_at'] is not None and r['workspace'] == workspace}
    try:
        history = tasks.history(task, workspace=workspace)
    except LookupError:
        return ends
    for event in history:
        tag = event['data'].get('dispatch') if event['kind'] == 'comment' and event['actor'] == 'system' else None
        if isinstance(tag, dict) and isinstance(tag.get('run'), str):
            ends[tag['run']] = {'role': tag.get('role'), 'outcome': tag.get('outcome'),
                                'started': tag.get('started') is True}
    return ends


def after_failure(run: dict) -> None:
    """A Task whose Dispatched sessions failed twice goes to the person. A
    session that never started (no client process) says nothing about the
    Task and is not counted (ADR 0228)."""
    failed = sum(1 for end in run_ends(run['task'], run['workspace']).values()
                 if end['outcome'] == 'failed' and end['started'])
    if failed < TASK_FAILURES:
        return
    state = tasks.get(run['task'], workspace=run['workspace'])
    if 'user' in state['assignees']:
        return
    tasks.assign(run['task'], add=['user'], actor=SYSTEM, workspace=run['workspace'])
    tasks.comment(run['task'], f'Dispatched sessions failed on this Task {failed} times, so the Dispatcher leaves it '
                  'to you: their comments above say where each stopped. Unassign yourself to let agents take it again.',
                  to=['user'], actor=SYSTEM, workspace=run['workspace'])


def switch_off_after_failures(run: dict) -> bool:
    """Switch every role of every workspace's Dispatcher on this device off
    after ``dispatch.failure_limit`` failures in a row (ADR 0237), and tell
    the person once, on the last failed Task. True when it switched off."""
    limit = _setting('dispatch.failure_limit')
    if limit <= 0 or failures_in_a_row() < limit:
        return False
    from . import status
    for workspace in store.switched_on_workspaces():
        status.set_roles(workspace, set())
    tasks.comment(run['task'], f'The Dispatcher on this device switched itself off, every role, after {limit} '
                  'Dispatched sessions failed in a row; the comments on their Tasks say why. Switch its roles on '
                  'again from the Board when that is fixed.', to=['user'], actor=SYSTEM, workspace=run['workspace'])
    return True


def fed_by_dispatched(task: str, workspace: str, dispatched: set[str]) -> bool:
    """Whether dispatched work put ``task`` where the Dispatcher would take
    it (ADR 0227): a Dispatched session created it, or took it out of
    Backlog, by a move or by a claim that ended in another status, and the
    person has not moved it since. A Task a review sends back to Ready, or
    one a Dispatched session claimed and released back to Backlog, was not
    fed. ``dispatched`` is the workspace's Dispatched sessions
    (``tasks.dispatched_sessions``)."""
    fed, resting, claimed_from, holder = False, 'backlog', None, None
    for event in tasks.history(task, workspace=workspace):
        kind, data, actor = event['kind'], event['data'], event['actor']
        by_dispatched = actor == 'agent' and event['session'] in dispatched
        if kind == 'created':
            resting, fed = data.get('status') or 'backlog', by_dispatched
        elif kind == 'claimed':
            claimed_from = resting if claimed_from is None else claimed_from  # a takeover keeps where the work began
            holder = event['session']
            resting = 'in_progress' if resting in ('backlog', 'ready') else resting
        elif kind in ('moved', 'released'):
            status = data.get('status') or resting
            ending = kind == 'released' or status in ('review', 'done', 'cancelled')
            if actor == 'user':
                fed = False  # the person decided where it goes
            elif status != 'backlog' and 'backlog' in (resting, claimed_from) and \
                    (by_dispatched or (actor == 'system' and holder in dispatched)):
                fed = True
            resting = status
            if ending:
                claimed_from = holder = None
    return fed


# The person's changes that make a refined Task worth refining again (ADR 0230).
_PERSONS_CHANGES = ('created', 'edited', 'appended', 'moved', 'comment', 'assigned', 'tagged', 'linked')


def refined_since_change(task: str, workspace: str) -> bool:
    """Whether a Dispatched refinement started on ``task`` after the person's
    last change to it (ADR 0230): each Task is refined once, until the
    person changes it."""
    from .dispatched import REFINE_MARK
    refined = False
    for event in tasks.history(task, workspace=workspace):
        if event['kind'] == 'checkpoint' and (event['session'] or '').startswith('dispatch:') and \
                (event['data'].get('text') or '').startswith(REFINE_MARK):
            refined = True
        elif event['actor'] == 'user' and event['kind'] in _PERSONS_CHANGES:
            refined = False
    return refined


MISSING_WORK = "is not in this device's copy of the repository"


# How often one Task's branch is fetched at most: about once a pass (ADR 0238).
FETCH_EVERY = 60.0
_fetched: dict[tuple[str, str], float] = {}
NO_UPSTREAM = 'no upstream'
_BASE_BRANCH = re.compile(r', made from (\S+) at [0-9a-f]{7,40}\.')


def recorded_base(task: str, workspace: str) -> str | None:
    """The branch the Dispatcher made the Task's branch from, as its first
    checkpoint says (ADR 0223); None when it did not make it."""
    for event in tasks.history(task, workspace=workspace):
        if event['kind'] == 'checkpoint' and (event['session'] or '').startswith('dispatch:'):
            made = _BASE_BRANCH.search(event['data'].get('text') or '')
            if made:
                return made.group(1)
    return None


def remote_of(folder: dict, base: str | None = None) -> str | None:
    """The remote Task branches travel through (ADR 0238): the one ``base``
    tracks here, else the one the folder's checked-out branch tracks."""
    from . import git
    for branch in dict.fromkeys(b for b in (base, folder.get('branch')) if b):
        remote = git.upstream_remote(folder['path'], branch)
        if remote:
            return remote
    return None


def fetch_work(task: str, folder: dict, workspace: str, handoff: dict) -> str | None:
    """Fetch the Handoff's branch when its last commit is not here (ADR
    0238), at most once a pass for a branch. None when the commit is here
    or the fetch ran; otherwise why it could not (``NO_UPSTREAM`` when the
    base tracks no remote). A failed fetch is logged, and the caller goes on
    with what is here."""
    from . import git
    commit, branch = handoff.get('commit'), handoff.get('branch')
    if not commit or not branch or git.has_commit(folder['path'], commit):
        return None
    remote = remote_of(folder, recorded_base(task, workspace))
    if not remote:
        return NO_UPSTREAM
    key, now = (folder['path'], branch), time.monotonic()
    if key in _fetched and now - _fetched[key] < FETCH_EVERY:
        return None
    _fetched[key] = now
    try:
        git.fetch_branch(folder['path'], remote, branch)
    except git.GitError as error:
        print(f'[imprint] dispatcher could not fetch {branch} from {remote}: {error}', file=sys.stderr, flush=True)
        return f'fetching {branch} from {remote} failed: {error}'
    return None


def takeover(state: dict, folder: dict, workspace: str, *, doing: str = 'take this Stale Task over') -> tuple[bool, dict]:
    """Whether this device can take ``state`` over (ADR 0223): it has no
    commit yet, or its Handoff's last commit is here, fetched first from
    the remote when it is not (ADR 0238). When it cannot, the Task gets one
    comment saying so, and is left to the device that has the work.
    Returns (whether to take it, its Handoff)."""
    from . import git
    handoff = tasks.handoff(state['id'], workspace=workspace)
    commit = handoff.get('commit')
    why = fetch_work(state['id'], folder, workspace, handoff) if commit else None
    if not commit or git.has_commit(folder['path'], commit):
        return True, handoff
    marker = f'{commit[:12]} {MISSING_WORK}'
    said = any(e['kind'] == 'comment' and e['actor'] == 'system' and marker in e['data'].get('text', '')
               for e in tasks.history(state['id'], workspace=workspace))
    if not said:
        unpushed = (" This project's branch has no upstream here, so the work was not pushed anywhere this "
                    'device can fetch it from.' if why == NO_UPSTREAM else '')
        tasks.comment(state['id'], f"The Dispatcher on this device did not {doing}: its last commit "
                      f"{marker} (branch {handoff.get('branch') or 'unknown'}), so it is left to the device that has "
                      f"the work, or to you once that commit is pushed and fetched here.{unpushed}",
                      actor=SYSTEM, workspace=workspace)
    return False, handoff


def worker_client(state: dict, workspace: str) -> str | None:
    """The client of the session that did the Task's work."""
    worked_by = state.get('worked_by')
    if not worked_by:
        return None
    claims = [e for e in tasks.history(state['id'], workspace=workspace)
              if e['kind'] in ('claimed', 'moved') and e['session'] == worked_by and e.get('client')]
    return tasks.client_id(claims[-1]['client']) if claims else None


def reviewer(state: dict, choices: list[str], workspace: str) -> str | None:
    """The client that reviews ``state`` (ADR 0221): an allowed one other
    than the client that did the work, else that client in a fresh session."""
    from . import clients
    did = worker_client(state, workspace)
    others = [client for client in choices if client != did]
    if others:
        return clients.default(others)
    return did if did in choices else None


_MADE_FROM = re.compile(r', made from .+ at ([0-9a-f]{7,40})\.')


def review_base(state: dict, workspace: str, *, folder: dict | None = None, tip: str | None = None) -> str | None:
    """What a reviewer diffs the work against (ADR 0223): the commit the
    Task's branch was made from, when the Dispatcher made it fresh and said
    so in its checkpoint; else, for other work (an interactive session's,
    or a branch made elsewhere), the merge base of the work's last commit
    (``tip``, else the Handoff's) and the project's checked-out branch."""
    events = tasks.history(state['id'], workspace=workspace)
    for event in events:
        if event['kind'] == 'checkpoint' and (event['session'] or '').startswith('dispatch:'):
            made = _MADE_FROM.search(event['data'].get('text') or '')
            if made:
                return made.group(1)
    commit = tip or next((e['data']['commit'] for e in reversed(events)
                          if e['kind'] == 'checkpoint' and e['data'].get('commit')), None)
    if not folder or not commit:
        return None
    from . import git
    try:
        return git.run(folder['path'], 'merge-base', commit, 'HEAD') or None
    except git.GitError:
        return None


REVIEW_ROUNDS = 2


def after_review(run: dict, outcome: str) -> None:
    """A Task sent back by ``REVIEW_ROUNDS`` reviews goes to the person (ADR 0221)."""
    if outcome != 'returned':
        return
    returned = sum(1 for end in run_ends(run['task'], run['workspace']).values()
                   if end['role'] == 'review' and end['outcome'] == 'returned')
    if returned < REVIEW_ROUNDS:
        return
    state = tasks.get(run['task'], workspace=run['workspace'])
    if 'user' not in state['assignees']:
        tasks.assign(run['task'], add=['user'], actor=SYSTEM, workspace=run['workspace'])
        tasks.comment(run['task'], f'Dispatched reviews sent this Task back {returned} times, so the Dispatcher leaves '
                      'it to you: the reviews above say what they found. Unassign yourself to let agents take it again.',
                      to=['user'], actor=SYSTEM, workspace=run['workspace'])
