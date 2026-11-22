"""Upkeep: what is already stored, brought up to date while nothing else
runs (ADR 0201).

When a release changes how memories are stored, the memories stored before
it keep the old form until something writes them again. Upkeep is the
background job's last step (after LLM tagging and Organize, ADRs 0067 and
0132): in the queue's idle slot it works through a list of steps, a piece at
a time, until only new memories are left to handle as they arrive. It runs
below the priority of everything else, rests between pieces, steps aside for
any queued job and stops after its share of a run; the next idle run goes
on where it stopped.

Steps, each finite, resumable and safe to repeat:

- ``timestamps`` (ADR 0200): memories received from a paired device before
  that fix were dated when they arrived; each takes back the time its origin
  device recorded it. Once per workspace.
- ``data-files`` (ADR 0195): data files this device indexed, still held as
  record chunks although ingest now keeps them whole, are indexed again from
  disk, one file at a time, as a refresh would. A file in a WSL distribution
  that is not running waits for it: reading the share would start it.

A device-local ledger, ``data/upkeep.json``, keeps each workspace's finished
step versions, a step's resume point and the files a step could not handle
(retried after a week, or when the file changes), so no pass repeats work.
A later change to how memories are stored adds a step here, or raises a
step's version to run it once more everywhere.

``imprint upkeep`` runs what is due in the foreground; ``--dry-run`` says
what it would change.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from dataclasses import dataclass
from typing import Callable

from . import config

LEDGER = 'upkeep.json'
BUDGET_SECONDS = 600.0   # one background run's share; the next idle run goes on
REST = 1.0               # after each piece, rest this share of the time it took
REST_MAX = 5.0
RETRY_DAYS = 7           # a file a step could not handle is tried again after this
TOLERANCE = 60.0         # seconds an arrival may follow its origin's record time unrepaired
TIMESTAMPS_VERSION = 1
_PAGE = 1000
_DUE_TTL = 30.0
_RUNNING_TTL = 60.0


# ── The ledger ────────────────────────────────────────────────────────

def _ledger_path():
    return config.get_data_dir() / LEDGER


def read() -> dict:
    try:
        data = json.loads(_ledger_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict) -> None:
    path = _ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(LEDGER + '.tmp')
    temporary.write_text(json.dumps(data, indent=1, sort_keys=True), encoding='utf-8')
    for attempt in range(5):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            # Windows refuses while a reader has the file open for a moment.
            if attempt == 4:
                raise
            time.sleep(0.2)


def _entry(ledger: dict, workspace: str, step: str) -> dict:
    return ledger.setdefault('workspaces', {}).setdefault(workspace, {}).setdefault(step, {})


def _time(value) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0:
        return float(value)
    return None


# ── A pass ────────────────────────────────────────────────────────────

class Pass:
    """One run over what is due: when to stop, the ledger, and resting."""

    def __init__(self, stop: Callable[[], bool] | None = None, *, budget: float | None = None,
                 dry_run: bool = False, background: bool = False, command: str = 'upkeep'):
        self._stop = stop or (lambda: False)
        self.command = command
        self.started = time.time()
        self._reported = 0.0
        self.deadline = time.monotonic() + budget if budget else None
        self.dry_run = dry_run
        self.background = background
        self.ledger = read()
        self.stopped = False       # a queued job, or the setting turned off
        self.out_of_time = False   # this run's share is used

    def halt(self) -> bool:
        if self._stop():
            self.stopped = True
        elif self.deadline is not None and time.monotonic() > self.deadline:
            self.out_of_time = True
        return self.stopped or self.out_of_time

    def rest(self, took: float) -> None:
        """In the background, pause as long as the piece took (a few seconds
        at most), so upkeep uses at most about half of what it could."""
        if not self.background:
            return
        end = time.monotonic() + min(REST_MAX, max(0.0, took) * REST)
        while time.monotonic() < end and not self._stop():
            time.sleep(min(0.5, max(0.0, end - time.monotonic())))

    def save(self) -> None:
        if not self.dry_run:
            _write(self.ledger)

    def progress(self, processed: int, total: int, unit: str, workspace: str) -> None:
        """The progress record the Queue page shows, at most once a second."""
        now = time.monotonic()
        if self.dry_run or (now - self._reported < 1.0 and processed < total):
            return
        self._reported = now
        from .progress import write_progress
        write_progress(self.command, processed, max(total, processed), processed, 0, self.started, [workspace],
                       phase='upkeep', unit=unit)


@dataclass(frozen=True)
class Step:
    name: str
    label: str
    due: Callable[[str, dict], bool]
    run: Callable[[str, dict, Pass], bool]   # True once finished; False when halted


# ── timestamps (ADR 0200) ─────────────────────────────────────────────

_wal_scan: dict[str, tuple[int, bool]] = {}


def _paired(path) -> bool:
    """Whether the pairing store holds a pair. Read only: the API creates
    the store empty, so the file alone says nothing."""
    import sqlite3
    if not path.exists():
        return False
    try:
        conn = sqlite3.connect(f'file:{path.as_posix()}?mode=ro', uri=True, timeout=5)
        try:
            return conn.execute('SELECT 1 FROM pairs LIMIT 1').fetchone() is not None
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def _received(workspace: str) -> bool:
    """Whether this device may hold memories live sync brought into
    ``workspace``: it paired with or trusted a device, or the workspace's
    write log records a sync import. A fresh store has nothing to date back,
    so it starts no background run for this step. The log is read once per
    process, then only what was added to it."""
    data = config.get_data_dir()
    if _paired(data / 'sync-pairs.sqlite3') or (data / 'trusted_devices.json').exists():
        return True
    path = config.wal_path(workspace)
    offset, found = _wal_scan.get(str(path), (0, False))
    if found:
        return True
    try:
        size = path.stat().st_size
        if size < offset:
            offset = 0  # the log was replaced
        marker = b'"store_batch_precomputed"'
        with open(path, 'rb') as stream:
            stream.seek(max(0, offset - len(marker)))
            while not found:
                block = stream.read(1 << 20)
                if not block:
                    break
                found = marker in block
                if not found and len(block) == 1 << 20:
                    stream.seek(stream.tell() - len(marker))
    except OSError:
        return False
    _wal_scan[str(path)] = (size, found)
    return found


def _timestamps_due(workspace: str, entry: dict) -> bool:
    return int(entry.get('version') or 0) < TIMESTAMPS_VERSION and _received(workspace)


def _timestamps_run(workspace: str, entry: dict, run: Pass) -> bool:
    from qdrant_client import models as qm
    from . import device, vectorstore as vs
    client, coll = vs._ensure_collection(workspace)
    me = device.current()['id']
    received = qm.Filter(must_not=[qm.FieldCondition(key='recorded_device.id', match=qm.MatchValue(value=me))])
    offset = entry.get('cursor')
    fixed = int(entry.get('fixed') or 0)
    total = int(client.count(coll, count_filter=received, exact=False).count or 0)
    seen = 0
    while True:
        if run.halt():
            entry.update(cursor=offset, fixed=fixed)
            run.save()
            return False
        started = time.monotonic()
        points, following = client.scroll(coll, scroll_filter=received, limit=_PAGE, offset=offset,
                                          with_payload=['timestamp', 'recorded_at', 'recorded_device'],
                                          with_vectors=False)
        now = time.time()
        changes: dict[float, list] = {}
        for point in points:
            payload = point.payload or {}
            origin = payload.get('recorded_device')
            recorded = _time(payload.get('recorded_at'))
            # Without an origin it was stored here before provenance existed.
            if not isinstance(origin, dict) or not origin.get('id') or recorded is None:
                continue
            first = min(recorded, now)
            stamp = _time(payload.get('timestamp'))
            if stamp is not None and stamp - first <= TOLERANCE:
                continue
            changes.setdefault(first, []).append(point.id)
        if changes and not run.dry_run:
            client.batch_update_points(coll, [
                qm.SetPayloadOperation(set_payload=qm.SetPayload(payload={'timestamp': first}, points=ids))
                for first, ids in changes.items()], wait=True)
        fixed += sum(len(ids) for ids in changes.values())
        seen += len(points)
        run.progress(seen, total, 'memories', workspace)
        offset = following
        entry.update(cursor=offset, fixed=fixed)
        run.save()
        if offset is None:
            break
        run.rest(time.monotonic() - started)
    if run.dry_run:
        print(f'  would date {fixed:,} memories from paired devices to when they were first stored', flush=True)
        return True
    if fixed:
        vs._wal_log('upkeep_timestamps', workspace=workspace, count=fixed)
    entry.update(version=TIMESTAMPS_VERSION, cursor=None, fixed=fixed, done_at=time.time())
    run.save()
    print(f'  {fixed:,} memories from paired devices dated to when they were first stored', flush=True)
    return True


# ── data-files (ADR 0195) ─────────────────────────────────────────────

def _suffixes() -> tuple[str, ...]:
    from .data_files import FORMATS
    return tuple(FORMATS)


_running_cache: dict = {'at': 0.0, 'names': None}


def _running() -> set[str]:
    """Lower-case names of the WSL distributions running now (Windows only)."""
    if sys.platform != 'win32':
        return set()
    now = time.monotonic()
    if _running_cache['names'] is None or now - _running_cache['at'] > _RUNNING_TTL:
        from .wsl_paths import running_distributions
        _running_cache.update(at=now, names={name.lower() for name in running_distributions() or []})
    return _running_cache['names']


def _distribution(path: str) -> str | None:
    """The WSL distribution whose share holds ``path`` on a Windows runtime."""
    if sys.platform != 'win32':
        return None
    from .wsl_paths import windows_to_linux
    located = windows_to_linux(path)
    return located[0] if located else None


def _candidates(workspace: str, entry: dict, now: float) -> list[dict]:
    """This device's data files held as record chunks that ingest now keeps
    whole, from the snapshots alone: nothing on disk is read."""
    from . import data_files, device, source_snapshots
    me = device.current()['id']
    skipped = entry.get('skipped') or {}
    cutoff = now - RETRY_DAYS * 86400
    found = []
    for head in source_snapshots.files_ending(_suffixes(), workspace):
        origin = head['provenance']
        path = origin.get('source_path') or ''
        if head.get(source_snapshots.MISSING) or not path or not origin.get('source_path_local'):
            continue
        if (origin.get('source_device') or {}).get('id') != me:
            continue
        rel = origin.get('source_relative_path') or head['source'].split('/', 1)[-1]
        if head['data_format'] or not data_files.reason(rel, int(origin.get('source_size') or 0)):
            continue
        held = skipped.get(head['source'])
        if held and held.get('hash') == origin.get('source_hash') and float(held.get('at') or 0) >= cutoff:
            continue
        found.append({'source': head['source'], 'project': head['project'], 'path': path, 'rel': rel,
                      'hash': origin.get('source_hash') or ''})
    return found


def _data_signature(workspace: str) -> list:
    """What the candidates depend on: the data-file snapshots' count and
    latest update, and the settings that say which files are kept whole."""
    from . import source_snapshots
    from .config_schema import resolve
    return [*source_snapshots.signature(_suffixes(), workspace),
            str(resolve('ingest.data_files')[0]), int(resolve('ingest.data_file_min_kb')[0])]


def _data_due(workspace: str, entry: dict) -> bool:
    signature = _data_signature(workspace)
    if not signature[0]:
        return False  # no data file indexed in this workspace
    if signature != entry.get('checked'):
        return True
    waiting = {name.lower() for name in entry.get('waiting') or []}
    if waiting and waiting & _running():
        return True
    cutoff = time.time() - RETRY_DAYS * 86400
    return any(float(item.get('at') or 0) < cutoff for item in (entry.get('skipped') or {}).values())


def _chunks(workspace: str, source: str) -> int:
    from qdrant_client import models as qm
    from . import vectorstore as vs
    client, coll = vs._ensure_collection(workspace)
    return int(client.count(coll, count_filter=qm.Filter(must=[qm.FieldCondition(
        key='source', match=qm.MatchValue(value=source))]), exact=True).count)


def _data_run(workspace: str, entry: dict, run: Pass) -> bool:
    from . import data_files
    from .long_paths import os_path
    candidates = _candidates(workspace, entry, time.time())
    running = _running()
    waiting: set[str] = set()
    skipped = entry.setdefault('skipped', {})
    if candidates:
        print(f'  {len(candidates):,} data files to keep whole', flush=True)
    for index, candidate in enumerate(candidates):
        run.progress(index, len(candidates), 'files', workspace)
        if run.halt():
            run.save()
            return False
        source = candidate['source']
        distribution = _distribution(candidate['path'])
        if distribution and distribution.lower() not in running:
            waiting.add(distribution)
            continue
        started = time.monotonic()
        before = _chunks(workspace, source)
        try:
            if not os.path.isfile(os_path(candidate['path'])):
                raise FileNotFoundError('not on disk; a refresh marks it missing and keeps its snapshot')
            if run.dry_run:
                print(f'  would keep whole: {source} ({before:,} chunks now)', flush=True)
                continue
            stored = data_files.reindex(candidate['path'], candidate['rel'], candidate['project'], source, workspace)
        except Exception as error:  # noqa: BLE001 - one file never stops the others
            skipped[source] = {'hash': candidate['hash'], 'at': time.time(), 'why': str(error)[:300]}
            print(f'  {source}: left as it is: {error}', flush=True)
        else:
            skipped.pop(source, None)
            print(f'  {source}: {before:,} chunks -> {stored:,}, kept whole', flush=True)
        run.save()
        run.rest(time.monotonic() - started)
    if waiting:
        print(f'  Files in {", ".join(sorted(waiting))} wait until WSL runs it.', flush=True)
    entry['waiting'] = sorted(waiting)
    entry['checked'] = _data_signature(workspace)
    run.save()
    return True


STEPS: tuple[Step, ...] = (
    Step('timestamps', 'memories from paired devices dated to when they were first stored',
         _timestamps_due, _timestamps_run),
    Step('data-files', 'large data files kept whole', _data_due, _data_run),
)
_BY_NAME = {step.name: step for step in STEPS}


# ── What is due, and running it ───────────────────────────────────────

def enabled() -> bool:
    from .config_schema import resolve
    return bool(resolve('upkeep.background')[0])


def _workspaces() -> list[str]:
    """Known workspaces, the active one first. Checking what is due reads
    only the ledger and the snapshots, never the vector store, so the
    dispatcher's polls cannot start one."""
    active = config.get_active_workspace()
    return [active, *(name for name in config.get_known_workspaces() if name != active)]


def _settle(name: str, workspace: str, entry: dict) -> None:
    """A workspace without a collection has nothing to bring up to date."""
    if name == 'timestamps':
        entry.update(version=TIMESTAMPS_VERSION, cursor=None, fixed=0, done_at=time.time())
    else:
        entry.update(checked=_data_signature(workspace), waiting=[])


_due_cache: dict = {'at': 0.0, 'value': None}


def reset() -> None:
    _due_cache.update(at=0.0, value=None)
    _running_cache.update(at=0.0, names=None)


def due(*, fresh: bool = False) -> list[tuple[str, str]]:
    """(workspace, step) pairs with work to do, from the ledger, payload
    indexes and the snapshots' signatures; cached for half a minute."""
    now = time.monotonic()
    if not fresh and _due_cache['value'] is not None and now - _due_cache['at'] < _DUE_TTL:
        return _due_cache['value']
    ledger = read()
    found = []
    for workspace in _workspaces():
        for step in STEPS:
            try:
                if step.due(workspace, _entry(ledger, workspace, step.name)):
                    found.append((workspace, step.name))
            except Exception as error:  # noqa: BLE001 - one unreadable workspace leaves the rest
                print(f'Upkeep cannot check {step.name} in {workspace}: {error}', file=sys.stderr)
    _due_cache.update(at=now, value=found)
    return found


def due_count() -> int:
    """Steps due across workspaces while background upkeep is on; else 0."""
    try:
        return len(due()) if enabled() else 0
    except Exception:  # noqa: BLE001 - the dispatcher keeps going
        return 0


def _lower_priority() -> None:
    """Background upkeep runs below everything the person is doing: Windows'
    background mode (lower CPU, disk and memory priority), elsewhere nice."""
    try:
        if sys.platform == 'win32':
            import ctypes
            kernel = ctypes.windll.kernel32
            kernel.GetCurrentProcess.restype = ctypes.c_void_p
            kernel.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x00100000)  # PROCESS_MODE_BACKGROUND_BEGIN
        else:
            os.nice(10)
    except Exception:  # noqa: BLE001 - priority is a courtesy
        pass


def run(stop: Callable[[], bool] | None = None, *, budget: float | None = None, dry_run: bool = False,
        background: bool = False, lower_priority: bool = False) -> int:
    """Each due step of each workspace, in order. Returns 0 when all of it
    is done or this run's share is used, ``tag_backlog.YIELDED`` when
    ``stop`` asked it to step aside, 1 when a step failed (the others still
    ran). ``background`` rests between pieces and reports as the background
    job, which clears its own progress record; ``lower_priority`` drops the
    whole process below everything else, so only the job's own process
    passes it."""
    try:
        return _run(stop, budget=budget, dry_run=dry_run, background=background, lower_priority=lower_priority)
    finally:
        # A dry run writes no record, and may run without the lock.
        if not background and not dry_run:
            from .progress import clear_progress
            clear_progress()


def _run(stop, *, budget, dry_run, background, lower_priority) -> int:
    from .tag_backlog import YIELDED
    pending = due(fresh=True)
    if not pending:
        print('Upkeep: everything stored is up to date.', flush=True)
        return 0
    if lower_priority:
        _lower_priority()
    from . import vectorstore as vs
    work = Pass(stop, budget=budget, dry_run=dry_run, background=background,
                command='tag-backlog' if background else 'upkeep')
    client = vs._get_client()
    code = 0
    for workspace, name in pending:
        step = _BY_NAME[name]
        entry = _entry(work.ledger, workspace, name)
        if not client.collection_exists(config.collection_name(workspace)):
            _settle(name, workspace, entry)
            work.save()
            continue
        print(f'Upkeep in {workspace}: {step.label}', flush=True)
        try:
            finished = step.run(workspace, entry, work)
        except Exception as error:  # noqa: BLE001 - the run's log says why
            print(f'  failed: {error}', flush=True)
            code = 1
            continue
        if not finished:
            reset()
            if work.stopped:
                print('Upkeep stepped aside; it goes on when the queue is idle.', flush=True)
                return YIELDED
            print('Upkeep used its share of this run; the next idle run goes on.', flush=True)
            return code
    reset()
    return code


USAGE = 'Usage: imprint upkeep [--dry-run]'


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if any(arg in ('-h', '--help') for arg in args):
        print(USAGE)
        return 0
    if [arg for arg in args if arg != '--dry-run']:
        print(USAGE, file=sys.stderr)
        return 2
    return run(dry_run='--dry-run' in args)


if __name__ == '__main__':
    sys.exit(main())
