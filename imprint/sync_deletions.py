"""Deletions that reach paired devices (ADR 0176).

A device records each memory it deleted on purpose, by key and time, per
workspace, for ``RETENTION``. When two paired devices sync, after they swap
manifests, each tells the other which of the other's memories it deleted:
only keys from the other device's own manifest, so no device learns the ID
of a memory it never held (an ID hashes its content, ADR 0060). The other
device deletes its copy when that copy is older than the deletion, and
records the deletion in turn, with its original time, so it travels on to
the devices beyond. A device never imports a memory older than a deletion it
holds, so one that was away cannot bring it back.

Which deletions are recorded is decided where they happen: the user's own
deletions of any memory, and the chunks this device recorded that a newer
version of their source replaced. Not a file gone from disk, a scrub (it
would keep a hash of what it removed), a move, a wipe or a migration.
"""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager

from . import config

# Datasets whose deletions travel. Recordings belong to the device, not to a
# workspace: their deletions are kept once and every paired workspace carries them.
DATASETS = ('memories', 'recordings')
DEVICE_WIDE = ('recordings',)
# How long a deletion is kept: a device away longer can bring the memory back.
RETENTION = 180 * 24 * 3600
# One sync never deletes more than this share of a workspace's memories, nor
# fewer than MASS_MIN at most: a larger request is refused and reported, in
# case the other device is broken rather than its user thorough.
MASS_SHARE = 0.5
MASS_MIN = 200


class MassDeletion(Exception):
    """The other device asked to delete more than one sync may."""

    def __init__(self, asked: int, held: int):
        self.asked, self.held = asked, held
        super().__init__(f'The other device asked to delete {asked:,} of the {held:,} memories here; '
                         f'one sync deletes at most half of a workspace, so they were kept')


@contextmanager
def _db():
    directory = config.get_data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(directory / 'sync-deletions.sqlite3', timeout=10)
    try:
        conn.execute('CREATE TABLE IF NOT EXISTS deletions (workspace TEXT NOT NULL, dataset TEXT NOT NULL, '
                     'key TEXT NOT NULL, deleted_at REAL NOT NULL, PRIMARY KEY (workspace, dataset, key))')
        yield conn
        conn.commit()
    finally:
        conn.close()


def _workspace(workspace: str | None, dataset: str = 'memories') -> str:
    return '*' if dataset in DEVICE_WIDE else workspace or config.get_active_workspace()


def record(workspace: str | None, keys, dataset: str = 'memories', at: float | None = None) -> None:
    """Remember that these were deleted here (``at``, default now). A key
    deleted again keeps the later time."""
    times = keys if isinstance(keys, dict) else dict.fromkeys(keys, at or time.time())
    rows = [(_workspace(workspace, dataset), dataset, key, float(when)) for key, when in times.items()
            if isinstance(key, str) and key]
    if not rows:
        return
    with _db() as conn:
        conn.executemany('INSERT INTO deletions VALUES (?, ?, ?, ?) ON CONFLICT (workspace, dataset, key) '
                         'DO UPDATE SET deleted_at = max(deleted_at, excluded.deleted_at)', rows)
        conn.execute('DELETE FROM deletions WHERE deleted_at < ?', (time.time() - RETENTION,))


def held(workspace: str | None, dataset: str = 'memories') -> dict[str, float]:
    """The deletions kept for a workspace, by key."""
    with _db() as conn:
        rows = conn.execute('SELECT key, deleted_at FROM deletions WHERE workspace = ? AND dataset = ? '
                            'AND deleted_at >= ?', (_workspace(workspace, dataset), dataset, time.time() - RETENTION))
        return {key: deleted_at for key, deleted_at in rows}


def doomed(workspace: str, local: dict, remote: dict) -> dict[str, dict[str, float]]:
    """What to ask the other device to delete: of its own manifest, the keys
    deleted here and not held here again."""
    asked: dict[str, dict[str, float]] = {}
    for dataset in DATASETS:
        theirs, here = remote.get(dataset, {}), local.get(dataset, {})
        mine = held(workspace, dataset)
        asked[dataset] = {key: when for key, when in mine.items() if key in theirs and key not in here}
    return asked


def blocked(workspace: str, records: list[dict]) -> set[str]:
    """The incoming memories older than a deletion this device holds: they
    are not stored again."""
    deleted = held(workspace)
    if not deleted:
        return set()
    return {record['id'] for record in records if record.get('id') in deleted
            and float(record.get('recorded_at') or record.get('timestamp') or 0) < deleted[record['id']]}


def apply(workspace: str, requested: dict, local: dict) -> dict[str, list[str]]:
    """Delete what the other device deleted, of what this device holds, when
    this copy is older than the deletion; record each deletion so it travels
    on. Returns the keys deleted, per dataset. Raises :class:`MassDeletion`,
    deleting nothing, when a request is too large."""
    asked = {dataset: {key: float(when) for key, when in (requested.get(dataset) or {}).items()
                       if key in local.get(dataset, {}) and type(when) in (int, float)} for dataset in DATASETS}
    for dataset, keys in asked.items():
        count = len([key for key in local.get(dataset, {}) if '#' not in key])
        if len(keys) > max(MASS_MIN, MASS_SHARE * count):
            raise MassDeletion(len(keys), count)
    gone: dict[str, list[str]] = {}
    if asked['memories']:
        from . import vectorstore as vs
        payloads = vs.retrieve_payloads(list(asked['memories']), ['_mid', 'recorded_at', 'timestamp'], workspace,
                                        with_vectors=False)
        gone['memories'] = [pl['_mid'] for pl in payloads if pl.get('_mid') in asked['memories']
                            and float(pl.get('recorded_at') or pl.get('timestamp') or 0) < asked['memories'][pl['_mid']]]
        if gone['memories']:
            vs.delete_memories(gone['memories'], workspace)
    if asked['recordings']:
        from . import recording_sync, recordings
        gone['recordings'] = []
        for rid, when in asked['recordings'].items():
            try:
                started = float(recordings.get(rid).get('started_at') or 0)
            except (KeyError, TypeError, ValueError):
                continue
            if started < when:
                recording_sync.delete_local(rid)
                gone['recordings'].append(rid)
    for dataset, keys in gone.items():
        record(workspace, {key: asked[dataset][key] for key in keys}, dataset=dataset)
    return {dataset: keys for dataset, keys in gone.items() if keys}
