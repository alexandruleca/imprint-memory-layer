"""Meeting recordings on paired devices that both turned it on (ADR 0177).

With ``sync.recordings`` on at both ends, a finished recording's audio,
details and transcript reach the other device; the transcript's memories
travel as any memories do. The audio goes in pieces of ``PIECE`` bytes, each
its own stored and acknowledged stream (ADR 0116), into a partial file that
becomes the recording's audio only once its size and SHA-256 match; an
interrupted download resumes from the bytes already held. A recording deleted
on one device is deleted on the other (ADR 0176); audio deleted on one is
deleted on the other, since that change is a newer version of the recording.

The manifest lists each finished recording by id with the time it last
changed, and also, as ``<id>#audio``, the size of the audio held, and as
``<id>#part``, the bytes of a download in progress: the sender sends the
audio only to a device that lacks it, from where that device stopped.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import shutil

from . import recordings

PIECE = 1024 * 1024
PART = 'audio.webm.part'
MAX_TRANSCRIPT = 4 * 1024 * 1024
# Statuses a recording can arrive in; one still recording or transcribing is not sent.
ARRIVING = ('stopped', 'ready', 'failed')
log = logging.getLogger(__name__)


def enabled() -> bool:
    from .config_schema import resolve
    return bool(resolve('sync.recordings')[0])


def manifest() -> dict[str, float]:
    """The finished recordings, their audio and downloads in progress, when
    this device syncs recordings."""
    if not enabled():
        return {}
    listed: dict[str, float] = {}
    for meta in recordings.list_all():
        if meta['status'] not in ARRIVING:
            continue
        listed[meta['id']] = recordings.version(meta)
        if meta['audio_bytes']:
            listed[meta['id'] + '#audio'] = float(meta['audio_bytes'])
    if recordings.root().exists():
        for part in recordings.root().glob('*/' + PART):
            listed[part.parent.name + '#part'] = float(part.stat().st_size)
    return listed


def plan(keys: list[str], remote: dict[str, float]) -> list[str]:
    """The recordings to send, as ``<id>@<offset>``: where the audio starts,
    its size when the other device holds it already."""
    planned = []
    for rid in sorted({key.split('#', 1)[0] for key in keys if not key.endswith('#part')}):
        try:
            size = recordings.get(rid)['audio_bytes']
        except KeyError:
            continue
        held = remote.get(rid + '#part', 0)
        offset = size if size and remote.get(rid + '#audio') == size else int(held) if 0 < held < size else 0
        planned.append(f'{rid}@{offset}')
    return planned


def pending_bytes(planned: list[str]) -> int:
    """How much audio the planned recordings send."""
    total = 0
    for entry in planned:
        rid, _, offset = entry.partition('@')
        try:
            total += max(0, recordings.get(rid)['audio_bytes'] - int(offset))
        except (KeyError, ValueError):
            continue
    return total


def _sha256(path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as audio:
        for block in iter(lambda: audio.read(PIECE), b''):
            digest.update(block)
    return digest.hexdigest()


def export(planned: list[str]):
    """Each planned recording as records, one per piece of its audio; the
    last carries the transcript and is ``final``."""
    for entry in planned:
        rid, _, offset = entry.partition('@')
        try:
            meta = recordings.get(rid)
            offset = int(offset)
        except (KeyError, ValueError):
            continue
        folder = recordings.root() / rid
        size = meta['audio_bytes']
        details = {key: meta.get(key) for key in recordings.SYNCED}
        transcript = folder / 'transcript.md'
        text = transcript.read_text(encoding='utf-8') if transcript.exists() else None
        sha = _sha256(folder / 'audio.webm') if size else ''
        common = {'id': rid, 'meta': details, 'size': size, 'sha256': sha}
        if offset >= size:
            yield {**common, 'offset': size, 'data': '', 'final': True, 'transcript': text}
            continue
        with open(folder / 'audio.webm', 'rb') as audio:
            audio.seek(offset)
            at = offset
            while at < size:
                piece = audio.read(min(PIECE, size - at))
                if not piece:
                    break
                last = at + len(piece) >= size
                yield {**common, 'offset': at, 'data': base64.b64encode(piece).decode('ascii'), 'final': last,
                       **({'transcript': text} if last else {})}
                at += len(piece)


def _details(meta) -> dict:
    """A recording's details from another device, checked: ValueError when
    they cannot be kept."""
    if not isinstance(meta, dict):
        raise ValueError('details missing')
    number = lambda key, empty=False: (None if empty and meta.get(key) is None  # noqa: E731
                                       else float(meta[key]) if type(meta.get(key)) in (int, float) else 0.0)
    from .projects import project_name
    status = meta.get('status') if meta.get('status') in ARRIVING else 'stopped'
    return {'title': ' '.join(str(meta.get('title') or '').split())[:200] or 'Meeting',
            'project': project_name(str(meta.get('project') or recordings.DEFAULT_PROJECT)[:128]),
            'others': meta.get('others') is True, 'consent': True, 'status': status,
            'started_at': number('started_at'), 'stopped_at': number('stopped_at', True),
            'duration': number('duration'), 'error': str(meta.get('error') or '')[:500],
            'memories': int(meta['memories']) if type(meta.get('memories')) is int else 0,
            'transcribed_at': number('transcribed_at', True), 'updated_at': number('updated_at'),
            'audio_removed_at': number('audio_removed_at', True)}


def import_records(records: list[dict]) -> dict[str, int]:
    """Store pieces of recordings another device sent. A piece out of order
    is skipped, and a finished audio file that does not match its size and
    checksum is dropped: the next sync resumes from what is held, as the
    manifest says, without holding up the rest of the sync."""
    stats = {'completed': 0, 'skipped': 0}
    if not enabled():
        stats['skipped'] += len(records)
        return stats
    from . import sync_deletions
    from .config_schema import resolve
    deleted = sync_deletions.held(None, 'recordings')
    limit = int(resolve('audio.max_file_size_mb')[0]) * 1024 * 1024
    for record in records:
        rid = str(record.get('id') or '')
        try:
            if not recordings._ID.fullmatch(rid):
                raise ValueError('not a recording id')
            details = _details(record.get('meta'))
            size, offset = int(record.get('size')), int(record.get('offset'))
            data = base64.b64decode(record.get('data') or '', validate=True)
        except (TypeError, ValueError):
            stats['skipped'] += 1
            continue
        if (rid in deleted and details['started_at'] < deleted[rid]) or not 0 <= size <= limit:
            stats['skipped'] += 1
            continue
        folder = recordings.root() / rid
        folder.mkdir(parents=True, exist_ok=True)
        audio, part = folder / 'audio.webm', folder / PART
        if size == 0:
            # No audio there: deleted on that device, so here too.
            audio.unlink(missing_ok=True)
            part.unlink(missing_ok=True)
        elif data:
            held = part.stat().st_size if part.exists() else 0
            if offset != held and offset != 0:
                log.warning('A piece of recording %s arrived out of order; the next sync resumes it', rid)
                stats['skipped'] += 1
                continue
            if offset == 0:
                part.unlink(missing_ok=True)
            if offset + len(data) > size:
                part.unlink(missing_ok=True)
                stats['skipped'] += 1
                continue
            with part.open('ab') as out:
                out.write(data)
        if not record.get('final'):
            continue
        if size and part.exists():
            if part.stat().st_size != size or _sha256(part) != record.get('sha256'):
                log.warning('The audio of recording %s did not arrive whole; the next sync sends it again', rid)
                part.unlink(missing_ok=True)
                stats['skipped'] += 1
                continue
            part.replace(audio)
        transcript = record.get('transcript')
        if isinstance(transcript, str) and len(transcript.encode('utf-8')) <= MAX_TRANSCRIPT:
            (folder / 'transcript.md').write_text(transcript, encoding='utf-8')
        recordings._write_meta({'id': rid, **details})
        stats['completed'] += 1
    return stats


def delete_local(recording_id: str) -> None:
    """Remove a recording another device deleted. Its memories go through
    their own deletions (ADR 0176)."""
    shutil.rmtree(recordings._folder(recording_id), ignore_errors=True)
