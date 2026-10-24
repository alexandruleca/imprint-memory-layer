"""Content fingerprints and explicit filesystem boundaries for source indexing."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from . import device

_SECRET_NAMES = {'.env', 'id_rsa', 'id_dsa', 'id_ecdsa', 'id_ed25519'}


def allowed_source(path: str | Path, root: str | Path | None = None) -> bool:
    path = Path(path)
    resolved = path.resolve()
    for name in (path.name.lower(), resolved.name.lower()):
        if name in _SECRET_NAMES or name.startswith('.env.') or name.endswith(('.key', '.p12', '.pfx')):
            return False
    return root is None or resolved.is_relative_to(Path(root).resolve())


def fingerprint(path: str | Path) -> dict:
    path = Path(path)
    before = path.stat()
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    after = path.stat()
    if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
        raise OSError(f'Source changed while reading: {path}')
    return {'source_hash': digest.hexdigest(), 'source_size': after.st_size,
            'source_mtime': after.st_mtime, 'source_path': str(path.resolve()),
            'source_device': device.current(), 'source_path_local': True}


def stamp_records(records: list[dict], path: str, before: dict) -> list[dict]:
    if fingerprint(path) != before:
        raise OSError(f'Source changed while extracting: {path}')
    return [{**record, **before, 'source_relative_path': device.relative_path(record)} for record in records]


def unchanged(current: dict, stored: dict | None) -> bool:
    return bool(stored and device.local_source(stored) and stored.get('source_hash') == current['source_hash']
                and stored.get('source_path') == current['source_path'])


def missing_sources(states: dict[str, dict], roots: list[str]) -> list[str]:
    """Only prune recorded local paths actually gone from a successfully scanned root."""
    resolved = [Path(root).resolve() for root in roots]
    missing = []
    for source, state in states.items():
        if not state.get('source_path') or not device.local_source(state):
            continue
        # Normalize both sides: macOS exposes /var through /private/var, and
        # older provenance may retain an equivalent noncanonical spelling.
        path = Path(state['source_path']).resolve()
        if any(path.is_relative_to(root) for root in resolved) and not path.exists():
            missing.append(source)
    return missing


# ── Documents with nothing to index ─────────────────────────────
#
# A scanned or image-only document has no text for the extractors to return.
# It stores no chunks, so the chunk payloads refresh compares against cannot
# remember it. The result depends only on its bytes and on how documents are
# read, so it is kept per (content hash, extraction profile), shared by every
# workspace: refresh reads a document again only once the file or the profile
# changed, or with --force (ADR 0040).

def extraction_profile() -> dict:
    """What decides whether a document without a text layer can yield text."""
    from .config_schema import resolve
    ocr = bool(resolve('ingest.ocr_enabled')[0])
    profile = {'ocr': ocr, 'kreuzberg': bool(resolve('ingest.use_kreuzberg')[0])}
    if ocr:
        import importlib.util
        import shutil
        profile['ocr_lang'] = str(resolve('ingest.ocr_lang')[0])
        # Installing the tools changes the result, so it changes the profile.
        profile['ocr_tools'] = bool(shutil.which('tesseract') and shutil.which('pdftoppm')
                                    and importlib.util.find_spec('pytesseract')
                                    and importlib.util.find_spec('pdf2image'))
    return profile


def no_text_reason(path: str | Path, profile: dict) -> str:
    """Why ``path`` has nothing to index, and what can change that."""
    if Path(path).suffix.lower() != '.pdf':
        return 'no extractable text'
    if not profile['ocr']:
        return ('no text layer (scanned or image-only). OCR is off: '
                '`imprint config set ingest.ocr_enabled true` (needs tesseract and poppler)')
    if not profile.get('ocr_tools'):
        return ('no text layer, and OCR cannot run: install tesseract, poppler and the '
                'pytesseract and pdf2image packages')
    return f"no text found, even with OCR ({profile.get('ocr_lang')})"


@contextmanager
def _results():
    from . import config
    directory = config.get_data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(directory / 'extraction-results.sqlite3', timeout=10)
    try:
        conn.execute('CREATE TABLE IF NOT EXISTS no_text (source_hash TEXT, profile TEXT, reason TEXT, '
                     'checked_at REAL, PRIMARY KEY(source_hash, profile))')
        yield conn
        conn.commit()
    finally:
        conn.close()


def known_no_text(source_hash: str, profile: dict) -> str | None:
    """The recorded reason these bytes had nothing to index under ``profile``."""
    with _results() as conn:
        row = conn.execute('SELECT reason FROM no_text WHERE source_hash=? AND profile=?',
                           (source_hash, json.dumps(profile, sort_keys=True))).fetchone()
    return row[0] if row else None


def remember_no_text(source_hash: str, profile: dict, reason: str) -> None:
    with _results() as conn:
        conn.execute('INSERT OR REPLACE INTO no_text VALUES (?, ?, ?, ?)',
                     (source_hash, json.dumps(profile, sort_keys=True), reason, time.time()))
