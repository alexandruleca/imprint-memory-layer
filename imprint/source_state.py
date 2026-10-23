"""Content fingerprints and explicit filesystem boundaries for source indexing."""
from __future__ import annotations

import hashlib
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
