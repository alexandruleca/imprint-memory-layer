"""Workspace-scoped, compressed, redacted source snapshots and chunk manifests."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import json
import math
import sqlite3
import time
import zlib

from . import config, source_memory as sm

MAX_BYTES = 64 * 1024 * 1024
PART_BYTES = 48 * 1024


@contextmanager
def _db():
    directory = config.get_data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'source-snapshots.sqlite3'
    conn = sqlite3.connect(path, timeout=30)
    import os
    os.chmod(path, 0o600)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('''CREATE TABLE IF NOT EXISTS heads (
            workspace TEXT NOT NULL, project TEXT NOT NULL, source TEXT NOT NULL,
            version TEXT NOT NULL, updated_at REAL NOT NULL, metadata TEXT NOT NULL,
            body BLOB NOT NULL, PRIMARY KEY(workspace, project, source))''')
        yield conn
        conn.commit()
    finally:
        conn.close()


def _workspace(workspace):
    from .workspace_policy import workspace_name
    return workspace_name(workspace)


def _inflate(blob: bytes, limit=MAX_BYTES) -> bytes:
    if len(blob) > limit:
        raise ValueError('Source snapshot exceeds the compressed size limit')
    decoder = zlib.decompressobj()
    data = decoder.decompress(blob, limit + 1)
    if len(data) > limit or not decoder.eof or decoder.unused_data:
        raise ValueError('Source snapshot is oversized, incomplete or corrupt')
    return data


def _version(meta):
    shape = [{key: row[key] for key in ('index', 'start', 'end', 'prefix', 'content_hash')}
             for row in meta['manifest']]
    return sm.digest(json.dumps([sm.FORMAT_VERSION, meta['kind'], meta['content_hash'], meta.get('indexer_profile', ''), shape],
                                ensure_ascii=False, separators=(',', ':')))


def prepare(doc: dict, records: list[dict], source: str, project: str) -> dict:
    """Validate full coverage before any Qdrant write or snapshot publication."""
    text = doc['text']
    if len(text.encode('utf-8')) > MAX_BYTES:
        raise ValueError('Source exceeds the 64 MiB snapshot limit; prior source retained')
    if sm.reconstruct(records, len(text)) != text:
        raise ValueError('Chunks do not reconstruct their canonical source')
    from .vectorstore import _make_id
    manifest = []
    for index, record in enumerate(records):
        if record.get('chunk_index', index) != index:
            raise ValueError('Source chunk indices must be consecutive')
        manifest.append({'id': _make_id(record['content'], project, source, index),
                         'index': index, 'start': record['source_start'], 'end': record['source_end'],
                         'prefix': record.get('source_prefix', ''), 'content_hash': sm.digest(record['content'])})
    from . import device
    local_provenance = device.provenance(device.prepare(records[0])) if records else {}
    meta = {'schema': sm.FORMAT_VERSION, 'indexer_profile': sm.profile(), 'source': source, 'project': project,
            'kind': doc.get('kind', 'text'), 'content_hash': sm.digest(text), 'total_chars': len(text),
            'manifest': manifest, 'details': doc.get('metadata', {}), 'updated_at': time.time(),
            'provenance': {**local_provenance, **{key: records[0][key] for key in (
                'source_path', 'source_hash', 'source_size', 'source_mtime', 'source_device',
                'source_path_local', 'source_relative_path', 'origin_project') if records and key in records[0]}}}
    from .redaction import redact_all
    meta['details'] = json.loads(redact_all([json.dumps(meta['details'], ensure_ascii=False)])[0])
    meta['version'] = _version(meta)
    for record in records:
        record.update(source_version=meta['version'], source_content_hash=meta['content_hash'],
                      source_chunk_count=len(records), source_format=sm.FORMAT_VERSION, source_profile=meta['indexer_profile'])
    return {'metadata': meta, 'text': text}


def publish(snapshot: dict, workspace=None, *, imported=False):
    """Atomically replace one source head after its vector writes succeeded."""
    ws = _workspace(workspace)
    meta, text = dict(snapshot['metadata']), snapshot['text']
    _validate(meta, text)
    if imported:
        from .workspace_policy import is_excluded
        if is_excluded(source=meta['source'], path=meta.get('provenance', {}).get('source_path', ''), workspace=ws):
            return False
        meta['provenance'] = {**meta.get('provenance', {}), 'source_path_local': False}
        # Preserve the stricter redaction already held by this workspace, even
        # when the sending device/configuration now redacts less.
        from . import vectorstore as vs
        rows = [{'content': row['prefix'] + text[row['start']:row['end']],
                 'project': meta['project'], 'source': meta['source'], 'chunk_index': row['index'],
                 'source_start': row['start'], 'source_end': row['end'], 'source_prefix': row['prefix']}
                for row in meta['manifest']]
        if rows:
            doc, clean_rows = sm.tighten({'text': text, 'kind': meta['kind'], 'metadata': meta['details']}, vs._redacted(rows, ws))
            if doc['text'] != text:
                clean = prepare(doc, clean_rows, meta['source'], meta['project'])
                clean['metadata'].update(updated_at=meta['updated_at'], provenance=meta['provenance'])
                meta, text = clean['metadata'], clean['text']
    from .workspace_policy import write_guard
    guard_record = {'source': meta['source'], 'project': meta['project'], **meta.get('provenance', {})}
    with write_guard([guard_record], ws), _db() as conn:
        previous = conn.execute('SELECT updated_at FROM heads WHERE workspace=? AND project=? AND source=?',
                                (ws, meta['project'], meta['source'])).fetchone()
        if imported and previous and previous['updated_at'] > meta['updated_at']:
            return False
        if imported:
            _align_received_chunks(meta, text, ws)
        conn.execute('INSERT OR REPLACE INTO heads VALUES (?, ?, ?, ?, ?, ?, ?)',
                     (ws, meta['project'], meta['source'], meta['version'], meta['updated_at'],
                      json.dumps(meta, ensure_ascii=False), zlib.compress(text.encode('utf-8'), 6)))
    return True


def _align_received_chunks(meta, text, workspace):
    """A newer manifest updates spans even when sync reused a stored vector.

    Only align chunks whose normalized content matches the validated source.
    Missing/conflicting points remain visible through the integrity report.
    The accepted head, rather than arrival order of point batches, selects
    which source version annotates reused chunks.
    """
    from . import vectorstore as vs
    from qdrant_client import models as qm
    client, coll = vs._ensure_collection(workspace)
    for start in range(0, len(meta['manifest']), 256):
        rows = meta['manifest'][start:start + 256]
        points = client.retrieve(coll, ids=[vs._point_uuid(row['id']) for row in rows],
                                 with_payload=['content', '_mid'], with_vectors=False)
        existing = {point.id: point.payload or {} for point in points}
        operations = []
        for row in rows:
            point_id = vs._point_uuid(row['id'])
            stored = existing.get(point_id)
            content = row['prefix'] + text[row['start']:row['end']]
            if not stored or stored.get('_mid') != row['id'] or stored.get('content', '').strip() != content.strip():
                continue
            payload = {'content': content, 'source_version': meta['version'], 'source_start': row['start'],
                       'source_end': row['end'], 'source_prefix': row['prefix'], 'source_format': meta['schema'],
                       'source_profile': meta['indexer_profile'], 'source_chunk_count': len(meta['manifest']),
                       'source_content_hash': meta['content_hash']}
            operations.append(qm.SetPayloadOperation(set_payload=qm.SetPayload(payload=payload, points=[point_id])))
        if operations:
            client.batch_update_points(coll, update_operations=operations, wait=True)


def _validate(meta, text):
    if not isinstance(meta, dict) or meta.get('schema') != sm.FORMAT_VERSION or not isinstance(text, str):
        raise ValueError('Unsupported source snapshot format')
    if len(text.encode('utf-8')) > MAX_BYTES:
        raise ValueError('Source snapshot exceeds size limit')
    if not isinstance(meta.get('source'), str) or not 0 < len(meta['source']) <= 4096:
        raise ValueError('Invalid source snapshot key')
    if not isinstance(meta.get('project'), str) or len(meta['project']) > 256:
        raise ValueError('Invalid source snapshot project')
    if not isinstance(meta.get('updated_at'), (int, float)) or not math.isfinite(meta['updated_at']):
        raise ValueError('Invalid source snapshot timestamp')
    if meta.get('content_hash') != sm.digest(text) or meta.get('total_chars') != len(text):
        raise ValueError('Source snapshot checksum mismatch')
    if meta.get('kind') not in ('text', 'conversation', 'extracted', 'inline', 'url'):
        raise ValueError('Invalid source snapshot kind')
    if not isinstance(meta.get('details'), dict) or not isinstance(meta.get('provenance'), dict):
        raise ValueError('Invalid source snapshot metadata')
    if not isinstance(meta.get('indexer_profile'), str) or len(meta['indexer_profile']) != 64:
        raise ValueError('Invalid source snapshot indexer profile')
    manifest = meta.get('manifest')
    if not isinstance(manifest, list) or len(manifest) > 100000:
        raise ValueError('Invalid source snapshot manifest')
    from .vectorstore import _make_id
    chunks = []
    for index, row in enumerate(manifest):
        if not isinstance(row, dict):
            raise ValueError('Invalid source snapshot manifest entry')
        a, b, prefix = row.get('start'), row.get('end'), row.get('prefix')
        if (row.get('index') != index or not isinstance(a, int) or not isinstance(b, int)
                or not 0 <= a <= b <= len(text) or not isinstance(prefix, str) or len(prefix) > 8192):
            raise ValueError('Invalid snapshot chunk span')
        content = prefix + text[a:b]
        if row.get('content_hash') != sm.digest(content) or row.get('id') != _make_id(content, meta['project'], meta['source'], index):
            raise ValueError('Snapshot manifest content mismatch')
        chunks.append({'content': content, 'source_start': a, 'source_end': b, 'source_prefix': prefix})
    if sm.reconstruct(chunks, len(text)) != text or meta.get('version') != _version(meta):
        raise ValueError('Snapshot manifest coverage/version mismatch')


def get(source: str, project: str | None = None, workspace=None, version: str = '') -> dict | None:
    ws = _workspace(workspace)
    if not (config.get_data_dir() / 'source-snapshots.sqlite3').exists():
        return None
    with _db() as conn:
        rows = conn.execute('SELECT * FROM heads WHERE workspace=? AND source=?' + (' AND project=?' if project is not None else ''),
                            (ws, source, project) if project is not None else (ws, source)).fetchall()
    if len(rows) > 1:
        raise ValueError('Source exists in multiple projects; specify project')
    if not rows:
        return None
    row = rows[0]
    meta = json.loads(row['metadata'])
    if version and version != meta['version']:
        raise ValueError('Source version changed; restart reading from start=0')
    text = _inflate(row['body']).decode('utf-8')
    _validate(meta, text)
    return {'metadata': meta, 'text': text}


def list_metadata(workspace=None):
    if not (config.get_data_dir() / 'source-snapshots.sqlite3').exists():
        return []
    with _db() as conn:
        return [json.loads(row['metadata']) for row in conn.execute(
            'SELECT metadata FROM heads WHERE workspace=? ORDER BY project, source', (_workspace(workspace),))]


def remove(source: str | None = None, project: str | None = None, workspace=None):
    if not (config.get_data_dir() / 'source-snapshots.sqlite3').exists():
        return
    clauses, args = ['workspace=?'], [_workspace(workspace)]
    for key, value in (('source', source), ('project', project)):
        if value is not None:
            clauses.append(key + '=?'); args.append(value)
    with _db() as conn:
        conn.execute('DELETE FROM heads WHERE ' + ' AND '.join(clauses), args)


def integrity(snapshot: dict, workspace=None) -> dict:
    from . import vectorstore as vs
    from qdrant_client import models as qm
    meta = snapshot['metadata']
    client, coll = vs._ensure_collection(workspace)
    found, offset = {}, None
    while True:
        points, offset = client.scroll(collection_name=coll, scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key='source', match=qm.MatchValue(value=meta['source'])),
            qm.FieldCondition(key='project', match=qm.MatchValue(value=meta['project']))]),
            offset=offset, limit=256, with_payload=['_mid', 'content', 'chunk_index', *sm.SPAN_FIELDS], with_vectors=False)
        for point in points:
            found[(point.payload or {}).get('_mid') or str(point.id)] = point.payload or {}
        if offset is None:
            break
    missing, changed = [], []
    for row in meta['manifest']:
        record = found.get(row['id'])
        if record is None:
            missing.append(row['index'])
        elif (sm.digest(record.get('content', '')) != row['content_hash']
              or record.get('source_start') != row['start'] or record.get('source_end') != row['end']
              or record.get('source_prefix') != row['prefix'] or record.get('chunk_index') != row['index']
              or record.get('source_chunk_count') != len(meta['manifest'])
              or record.get('source_content_hash') != meta['content_hash']
              or record.get('source_profile') != meta['indexer_profile']
              or record.get('source_version') != meta['version']):
            changed.append(row['index'])
    expected_ids = {row['id'] for row in meta['manifest']}
    extras = len(set(found) - expected_ids)
    return {'complete': not missing and not changed and not extras,
            'expected_chunks': len(expected_ids), 'present_chunks': len(set(found) & expected_ids),
            'missing_chunks': len(missing), 'changed_chunks': len(changed), 'extra_chunks': extras,
            'missing_indices': missing[:50], 'changed_indices': changed[:50]}


def rehome(snapshot, project):
    """Rebind IDs after project placement without changing source-span identity."""
    from .vectorstore import _make_id
    meta, text = dict(snapshot['metadata']), snapshot['text']
    meta['project'] = project
    meta['manifest'] = [{**row, 'id': _make_id(row['prefix'] + text[row['start']:row['end']], project, meta['source'], row['index'])}
                        for row in meta['manifest']]
    return {'metadata': meta, 'text': text}


def export_parts(workspace):
    for meta in list_metadata(workspace):
        snapshot = get(meta['source'], meta['project'], workspace)
        # Export redacts again, including snapshots created under an older policy.
        snapshot = sanitize(snapshot)
        blob = zlib.compress(json.dumps(snapshot, ensure_ascii=False, separators=(',', ':')).encode('utf-8'), 6)
        transfer = sm.digest(base64.b64encode(blob).decode('ascii'))
        parts = max(1, (len(blob) + PART_BYTES - 1) // PART_BYTES)
        for index in range(parts):
            yield {'kind': 'source_snapshot', 'transfer': transfer, 'part': index, 'parts': parts,
                   'data': base64.b64encode(blob[index * PART_BYTES:(index + 1) * PART_BYTES]).decode('ascii')}


def sanitize(snapshot):
    from .redaction import redact_all
    text = redact_all([snapshot['text']])[0]
    details = json.loads(redact_all([json.dumps(snapshot['metadata']['details'], ensure_ascii=False)])[0])
    if text == snapshot['text']:
        return {**snapshot, 'metadata': {**snapshot['metadata'], 'details': details}}
    from .source_memory import remap, redaction_edits
    edits = redaction_edits(text, snapshot['text'])
    meta = dict(snapshot['metadata'])
    chunks = []
    for row in meta['manifest']:
        start, end = remap(row['start'], edits), remap(row['end'], edits, right=True)
        chunks.append({'content': row['prefix'] + text[start:end], 'chunk_index': row['index'],
                       'source_start': start, 'source_end': end, 'source_prefix': row['prefix']})
    result = prepare({'text': text, 'kind': meta['kind'], 'metadata': details}, chunks, meta['source'], meta['project'])
    result['metadata']['provenance'] = meta.get('provenance', {})
    result['metadata']['updated_at'] = meta['updated_at']
    return result


class Importer:
    """Bounded, sequential snapshot frames; incomplete transfers never publish."""
    def __init__(self, workspace):
        self.workspace, self.transfer, self.parts, self.next = workspace, '', 0, 0
        self.blob = bytearray()
        self.imported = 0

    def feed(self, frame):
        if frame.get('part') == 0 and not self.blob:
            self.transfer, self.parts = frame.get('transfer'), frame.get('parts')
            if not isinstance(self.parts, int) or not 0 < self.parts <= 4096:
                raise ValueError('Invalid snapshot transfer size')
        if (frame.get('transfer') != self.transfer or frame.get('part') != self.next
                or frame.get('parts') != self.parts):
            raise ValueError('Out-of-order source snapshot transfer')
        data = base64.b64decode(frame.get('data', ''), validate=True)
        if len(data) > PART_BYTES or len(self.blob) + len(data) > MAX_BYTES:
            raise ValueError('Source snapshot transfer exceeds limits')
        self.blob.extend(data); self.next += 1
        if self.next == self.parts:
            if sm.digest(base64.b64encode(self.blob).decode('ascii')) != self.transfer:
                raise ValueError('Source snapshot transfer checksum mismatch')
            snapshot = json.loads(_inflate(bytes(self.blob), MAX_BYTES * 2))
            if not isinstance(snapshot, dict):
                raise ValueError('Invalid source snapshot envelope')
            _validate(snapshot.get('metadata'), snapshot.get('text'))
            snapshot = sanitize(snapshot)
            from .projects import place
            meta = snapshot['metadata']
            placed = place([{'project': meta['project'], 'source': meta['source'],
                             **meta.get('provenance', {})}], self.workspace)[0]
            snapshot = rehome(snapshot, placed['project'])
            if publish(snapshot, self.workspace, imported=True):
                self.imported += 1
            self.transfer, self.parts, self.next, self.blob = '', 0, 0, bytearray()

    def finish(self):
        if self.next:
            raise ValueError('Incomplete source snapshot transfer')
