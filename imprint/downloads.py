"""Verify pinned upstream runtime archives before extraction/execution."""
import hashlib
import json
from pathlib import Path


def expected_digest(project: str, version: str, asset: str) -> str:
    manifest = json.loads((Path(__file__).resolve().parents[1] / 'requirements' / 'runtime-manifest.json').read_text())
    entry = manifest.get(project, {})
    if entry.get('version') != version or asset not in entry.get('assets', {}):
        raise ValueError(f'No pinned checksum for {project} {version} {asset}; update the runtime manifest or provide a managed binary')
    return entry['assets'][asset]


def verify_archive(path: Path, expected: str) -> None:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    if digest.hexdigest() != expected:
        raise ValueError(f'Checksum mismatch for {path.name}; archive was not installed')
