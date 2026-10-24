"""Device provenance is portable evidence; local path authority is not."""
import os
import json
import platform
import re
import secrets
import socket
import time
from pathlib import Path, PurePosixPath, PureWindowsPath

from . import config

# origin_project: the project its writer named, which the project-relative
# path and project mapping refer to after the memory moves (ADR 0036).
FIELDS = ['recorded_device', 'recorded_at', 'source_device', 'source_relative_path', 'source_path_local',
          'origin_project']
TRANSFER_FIELDS = FIELDS + ['source_path', 'source_hash', 'source_size', 'source_type', 'source_url',
                           'doc_metadata', 'etag', 'last_modified']
UNKNOWN = {'id': '', 'name': 'Unknown origin (legacy)', 'os': ''}


def current():
    directory = config.get_data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'device_id.txt'  # Shared with existing Go/Python sync identity.
    for _ in range(200):
        try:
            identity = path.read_text().strip()
        except FileNotFoundError:
            identity = ''
        if len(identity) >= 8:
            if not re.fullmatch(r'[A-Za-z0-9_-]{8,128}', identity):
                raise ValueError('Invalid device_id.txt; restore the correct local device identity')
            return {'id': identity, 'name': socket.gethostname(), 'os': platform.system().lower()}
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            time.sleep(.01)
            continue
        with os.fdopen(fd, 'w') as stream:
            stream.write(secrets.token_hex(4) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
    raise OSError('Device identity is empty or being provisioned; retry or restore device_id.txt')


def _identity(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = None
    if not isinstance(value, dict) or not isinstance(value.get('id'), str) or not value['id']:
        return dict(UNKNOWN)
    if not re.fullmatch(r'[A-Za-z0-9_-]{8,128}', value['id']):
        return dict(UNKNOWN)
    clean = lambda text: ''.join(c if c.isprintable() else ' ' for c in str(text))
    return {'id': value['id'], 'name': clean(value.get('name', ''))[:128], 'os': clean(value.get('os', ''))[:32]}


def relative_path(record):
    rel = record.get('source_relative_path', '')
    if not rel and record.get('source_type') in ('file', 'ocr'):
        prefix = str(origin_project(record)) + '/'
        source = record.get('source', '')
        if source.startswith(prefix):
            rel = source[len(prefix):]
    if not isinstance(rel, str) or not rel:
        return ''
    rel = rel.replace('\\', '/')
    if PurePosixPath(rel).is_absolute() or PureWindowsPath(rel).drive or '..' in PurePosixPath(rel).parts or '\x00' in rel:
        return ''
    return rel


def origin_project(record):
    """The project the memory's writer named; a moved memory keeps it."""
    return record.get('origin_project') or record.get('project', '')


def prepare(record, *, imported=False):
    """Preserve origin on transfer; never label an old import as recorded here."""
    record = dict(record)
    origin = record.get('recorded_device')
    record['recorded_device'] = _identity(origin) if origin is not None or imported else current()
    record['recorded_at'] = record.get('recorded_at') or record.get('timestamp') or (None if imported else time.time())
    if record.get('source_path'):
        source = record.get('source_device')
        record['source_device'] = _identity(source) if source is not None or imported else current()
        record['source_path_local'] = False if imported else (
            record.get('source_path_local', True) and record['source_device']['id'] == current()['id'])
    else:
        record['source_device'] = _identity(record.get('source_device'))
        record['source_path_local'] = False
    record['source_relative_path'] = relative_path(record)
    return record


def provenance(record):
    return {key: record.get(key) for key in FIELDS}


def recorded_here(record):
    """Whether this device recorded the record. Imported records always carry
    their origin (unknown when the sender had none); a record without one
    predates provenance and was stored locally."""
    origin = record.get('recorded_device')
    return origin is None or _identity(origin)['id'] == current()['id']


def local_source(record):
    return bool(record.get('source_path_local') and
                _identity(record.get('source_device'))['id'] == current()['id'])


def context(record, workspace=None, cwd=None):
    """Resolve only approved local mappings. Never probe a foreign absolute path."""
    from . import workspace_policy as policy
    device = current()
    working = Path(cwd or os.getcwd()).resolve()
    result = {'current_device': device, 'working_directory': str(working),
              'recorded_device': _identity(record.get('recorded_device')),
              'source_device': _identity(record.get('source_device')),
              'original_source_path': record.get('source_path', ''), 'local_source_path': '',
              'path_status': 'no-file-path'}
    from .wsl_paths import client_context, client_path
    result.update(client_context())
    if not record.get('source_path'):
        return result
    result['path_status'] = 'foreign-unmapped' if result['source_device']['id'] else 'unknown-origin'
    rel = relative_path(record)
    mapping = policy.project_root(origin_project(record), workspace)
    candidate = None
    if mapping and rel:
        root = Path(mapping).resolve()
        mapped = (root / rel).resolve()
        if mapped.is_relative_to(root):
            candidate = mapped
    elif local_source(record):
        candidate = Path(record['source_path']).resolve()
    if candidate is None:
        return result
    if not any(candidate.is_relative_to(Path(root)) for root in policy.roots(workspace)):
        result['path_status'] = 'outside-workspace-roots'
    elif not candidate.is_relative_to(working):
        result['path_status'] = 'outside-working-directory'
    elif not candidate.is_file():
        result['path_status'] = 'local-file-missing'
    else:
        result['local_source_path'] = client_path(str(candidate))
        result['path_status'] = 'local' if local_source(record) else 'foreign-mapped'
    return result
