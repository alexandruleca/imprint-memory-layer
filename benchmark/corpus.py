"""Public, deterministic corpus snapshot. Never index a contributor's memory."""
import hashlib
import json
from pathlib import Path
import subprocess

SOURCE_FILES = [
    'imprint/chunker.py', 'imprint/config.py', 'imprint/config_schema.py',
    'imprint/embeddings.py', 'imprint/vectorstore.py', 'imprint/server.py',
    'imprint/qdrant_runner.py', 'imprint/tagger.py', 'imprint/queue.py',
    'imprint/queue_lock.py', 'imprint/device.py', 'imprint/workspace_policy.py',
    'imprint/imprint_graph.py', 'imprint/source_state.py', 'imprint/ingest_core.py',
    'imprint/sync_crypto.py', 'imprint/model_profiles.py',
]
DOC_FILES = ['architecture', 'embeddings', 'chunking', 'tagging', 'mcp',
             'workspaces', 'queue', 'devices', 'sync', 'configuration',
             'installation', 'building', 'local-models', 'security-scanning']


def digest(data):
    return hashlib.sha256(data).hexdigest()


def snapshot(repo, revision, destination):
    """Copy only declared tracked UTF-8 sources; git object reads ignore edits."""
    destination = Path(destination)
    paths = SOURCE_FILES + ['docs/' + name + '.md' for name in DOC_FILES]
    tracked = subprocess.check_output(['git', '-C', str(repo), 'ls-tree', '-r', '--name-only', revision], text=True).splitlines()
    paths += [p for p in tracked if p.startswith('docs/adr/') and p.endswith('.md') and Path(p).name[:4].isdigit()]
    manifest = []
    for path in sorted(set(paths)):
        data = subprocess.check_output(['git', '-C', str(repo), 'show', f'{revision}:{path}'])
        data.decode('utf-8')
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        manifest.append({'path': path, 'sha256': digest(data), 'bytes': len(data), 'project': 'imprint'})
    fixtures = Path(__file__).parent / 'fixtures'
    for source in sorted(fixtures.rglob('*')):
        if not source.is_file():
            continue
        path = 'reference/' + source.relative_to(fixtures).as_posix()
        data = source.read_bytes()
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        manifest.append({'path': path, 'sha256': digest(data), 'bytes': len(data), 'project': 'reference'})
    return {'files': manifest, 'sha256': digest(json.dumps(manifest, sort_keys=True).encode())}


def passages(text, size=1800, overlap=180):
    """Fixed line-boundary windows, independent of the model and query labels."""
    lines = text.splitlines(keepends=True)
    pending = ''
    for line in lines:
        while len(line) > size:
            if pending.strip():
                yield pending.strip()
                pending = ''
            yield line[:size].strip()
            line = line[size-overlap:]
        if len(pending) + len(line) > size:
            if pending.strip():
                yield pending.strip()
            pending = pending[-overlap:] + line
        else:
            pending += line
    if pending.strip():
        yield pending.strip()
