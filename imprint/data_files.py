"""Data files kept whole (ADR 0195).

A large data file (JSON, CSV, TSV, XLSX), or one in a folder of fixtures,
snapshots, generated output, training data or translations, is stored as a
few search chunks of at most ``PIECE_MAX`` characters instead of one chunk per
record. Its snapshot is the file's own text, so ``read_source`` gives back the
file as it was, redacted like every source. Every piece starts with a digest
(path, format, size, structure and a first record), and the piece's vector is
computed from that digest and a sample of the piece. Keyword search counts
the digest's words, exact identifiers and verbatim phrases, not every word of
the data, so a data file is found by what it is without flooding results.

The digest's length is in its header line, so any device can tell digest and
data apart from the chunk text alone: older devices that receive these chunks
store and search them as ordinary long chunks.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re

from . import config
from .long_events import PIECE_MAX, reduce

FORMATS = {'.json': 'json', '.jsonc': 'json', '.csv': 'csv', '.tsv': 'tsv', '.xlsx': 'xlsx'}
# Folders whose data files are records of something else: test inputs and
# expected outputs, generated results, datasets, translation catalogs.
FOLDER = re.compile(
    r'(?:^|/)(fixtures?|__fixtures__|__snapshots__|snapshots?|testdata|test-data|golden|goldens|baselines?|'
    r'outputs?|regress[^/]*|training|datasets?|seeds?|dumps?|locales?|i18n|translations)/', re.IGNORECASE)
# A data file in one of those folders is kept whole above this size; smaller
# ones are a chunk or two anyway.
FOLDER_MIN_BYTES = 16 * 1024
# Chat exports keep their per-conversation documents (json_doc detects them).
_EXPORT = re.compile(r'conversations(?:-\d+)?\.json$', re.IGNORECASE)
_HEAD = re.compile(r'\[[^\n]*\]\n--- (\{"kind": "data_file"[^\n]*\}) ---\n')
DIGEST_MAX = 2400


def reason(path: str, size: int) -> str:
    """Why the file at ``path`` (relative or absolute) is kept whole, or ''
    to index it record by record as before."""
    from .config_schema import resolve
    if str(resolve('ingest.data_files')[0]).strip().lower() != 'whole':
        return ''
    path = path.replace('\\', '/')
    name = path.rsplit('/', 1)[-1]
    if os.path.splitext(name)[1].lower() not in FORMATS or _EXPORT.fullmatch(name):
        return ''
    folder = FOLDER.search(path)
    if folder and size > FOLDER_MIN_BYTES:
        return folder.group(1).lower()
    if size > max(1, int(resolve('ingest.data_file_min_kb')[0])) * 1024:
        return 'large'
    return ''


def stored_whole(state: dict | None) -> bool:
    return bool(((state or {}).get('doc_metadata') or {}).get('data_file'))


def converts(path: str, size: int, state: dict | None) -> bool:
    """Whether a stored source must be indexed again because it is held in
    the other form: record chunks for a file now kept whole, or the reverse
    after ``ingest.data_files`` changed. Unchanged files convert at the next
    ingest or refresh on the device that holds them."""
    return state is not None and bool(reason(path, size)) != stored_whole(state)


# ── The file's text ───────────────────────────────────────────────────

def read_text(path: str) -> tuple[str, str]:
    """The file's text and its format. JSON and CSV are read verbatim, so the
    snapshot restores them byte for byte (after redaction); a spreadsheet, or
    a text file that is not UTF-8, is its extracted text."""
    from . import extractors
    from .long_paths import os_path
    fmt = FORMATS[os.path.splitext(path)[1].lower()]
    limit = extractors.max_doc_bytes()
    if os.path.getsize(os_path(path)) > limit:
        raise extractors.DocumentTooLarge(
            f"Document exceeds ingest.max_doc_size_mb ({limit // 1048576} MiB); prior chunks retained")
    if fmt != 'xlsx':
        try:
            with open(os_path(path), 'r', encoding='utf-8', newline='') as stream:
                return stream.read(), fmt
        except UnicodeDecodeError:
            pass
    from .source_memory import extracted_documents
    docs = [doc for doc in extractors.dispatch_by_ext(os_path(path)) if doc.text.strip()]
    document, _ = extracted_documents(docs)
    return document['text'], fmt


# ── Digest ────────────────────────────────────────────────────────────

def _shape(value, depth=0) -> str:
    if isinstance(value, dict):
        if depth >= 3:
            return f'object ({len(value)} keys)'
        keys = list(value)[:12]
        more = f', … {len(value) - len(keys)} more' if len(value) > len(keys) else ''
        return '{' + ', '.join(f'{key}: {_shape(value[key], depth + 1)}' for key in keys) + more + '}'
    if isinstance(value, list):
        if not value:
            return 'empty array'
        return f'array of {len(value):,} × {_shape(value[0], depth + 1)}'
    if isinstance(value, bool) or value is None:
        return 'boolean' if isinstance(value, bool) else 'null'
    return 'number' if isinstance(value, (int, float)) else 'string'


def _first_record(value):
    while isinstance(value, dict) and len(value) == 1:
        value = next(iter(value.values()))
    if isinstance(value, dict):
        arrays = [item for item in value.values() if isinstance(item, list) and item]
        if arrays:
            value = max(arrays, key=len)
    return value[0] if isinstance(value, list) and value else value


def _describe(text: str, fmt: str) -> list[str]:
    if fmt == 'json':
        try:
            from .extractors.json_doc import _loads
            value = _loads(text)
        except ValueError as error:
            return [f'JSON that does not parse ({str(error)[:120]}).', 'Start: ' + reduce(text, 500)]
        return ['JSON ' + _shape(value)[:1200],
                'First record: ' + json.dumps(_first_record(value), ensure_ascii=False, default=str)[:500]]
    if fmt in ('csv', 'tsv'):
        rows = list(csv.reader(io.StringIO(text[:200_000]), delimiter='\t' if fmt == 'tsv' else ','))
        count = text.count('\n') + (0 if text.endswith('\n') else 1)
        lines = [f'{fmt.upper()} of about {max(0, count - 1):,} rows; columns: ' + ', '.join(rows[0][:30])[:900] if rows else
                 f'Empty {fmt.upper()}.']
        return lines + ['Row: ' + ', '.join(row)[:300] for row in rows[1:3]]
    return ['Spreadsheet text. Start: ' + reduce(text, 800)]


def prefix(rel: str, text: str, fmt: str, why: str, size: int) -> str:
    """The digest every piece of the file starts with: retrieval context, not
    source bytes, so the snapshot still rebuilds the file exactly."""
    body = '\n'.join(['Data file kept whole: search finds it by this digest; read_source returns all of it.',
                      *_describe(text, fmt)])[:DIGEST_MAX] + '\n'
    header = json.dumps({'kind': 'data_file', 'format': fmt, 'reason': why, 'bytes': size,
                         'chars': len(text), 'digest': len(body)}, ensure_ascii=False)
    return f'[{rel}]\n--- {header} ---\n{body}'


def split(content: str) -> tuple[str, str] | None:
    """(digest with its path and header, data) for a kept-whole piece, else None."""
    match = _HEAD.match(content)
    if not match:
        return None
    try:
        length = int(json.loads(match.group(1)).get('digest', 0))
    except (ValueError, TypeError, AttributeError):
        return None
    end = match.end() + max(0, length)
    return content[:end], content[end:]


def embedding_view(content: str) -> str | None:
    """What a kept-whole piece's vector is computed from: its digest and a
    reduced sample of its data. None for any other chunk."""
    parts = split(content)
    if parts is None:
        return None
    head, data = parts
    return head + reduce(data, max(500, config.CHUNK_SIZE_PROSE - len(head)))


# ── Pieces ────────────────────────────────────────────────────────────

def pieces(text: str) -> list[tuple[int, int]]:
    """Contiguous spans of at most PIECE_MAX characters, cut at a line end
    when one is near, covering the whole text."""
    out, start = [], 0
    while start < len(text):
        stop = min(len(text), start + PIECE_MAX)
        if stop < len(text):
            cut = text.rfind('\n', start + PIECE_MAX // 2, stop)
            stop = cut + 1 if cut >= 0 else stop
        out.append((start, stop))
        start = stop
    return out


def records(path: str, rel: str, project: str, source_key: str, *, why: str, size: int, mtime: float,
            pending: bool, zero_shot: bool) -> tuple[dict, list[dict]]:
    """The redacted document and its kept-whole chunk records."""
    from . import source_memory, tagger
    text, fmt = read_text(path)
    document = source_memory.document(text, kind='text', metadata={'data_file': {'format': fmt, 'reason': why}})
    text = document['text']
    if not text:
        return document, []
    head = prefix(rel, text, fmt, why, size)
    tags = tagger.build_payload_tags(head, rel_path=rel, llm=False, zero_shot=zero_shot,
                                     neighbor_context='', project_hint=project)
    tags.pop('_llm_type', None)
    spans = pieces(text)
    meta = {'data_file': {'format': fmt, 'reason': why, 'bytes': size, 'pieces': len(spans)}}
    rows = [{'content': head + text[a:b], 'project': project, 'type': 'data', 'tags': dict(tags),
             'source': source_key, 'source_type': 'file', 'doc_metadata': meta, 'chunk_index': index,
             'source_mtime': mtime, 'tag_pending': pending, 'source_start': a, 'source_end': b,
             'source_prefix': head, 'source_format': source_memory.FORMAT_VERSION}
            for index, (a, b) in enumerate(spans)]
    rows[0]['_source_document'] = document
    return document, rows


def reindex(path: str, rel: str, project: str, source_key: str, workspace: str | None = None) -> int:
    """Index one stored data file again, kept whole, as ingest and refresh
    now would, into ``workspace``: upkeep's conversion (ADR 0201). Returns
    the chunks stored. Raises, keeping the stored chunks, when the file
    cannot be read, has no text or is no longer one to keep whole."""
    from .config_schema import resolve
    from .long_paths import os_path
    from .source_state import fingerprint, stamp_records
    from .tag_backlog import marking
    from . import vectorstore as vs
    before = fingerprint(path)
    # Judged by the path within its project, as a directory refresh judges it.
    why = reason(rel, before['source_size'])
    if not why:
        raise ValueError('no longer a data file to keep whole')
    _, rows = records(path, rel, project, source_key, why=why, size=before['source_size'],
                      mtime=os.path.getmtime(os_path(path)), pending=marking(workspace),
                      zero_shot=resolve('tagger.zero_shot')[0])
    if not rows:
        raise ValueError('the file has no text now')
    inserted, _ = vs.replace_source(stamp_records(rows, path, before), source_key, project, workspace)
    return inserted
