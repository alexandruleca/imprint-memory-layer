"""Skills and personas in the folders the clients read (ADRs 0169, 0171).

The CLI's clients table says, for each client, the folders it reads skills
and personas from, in the order it prefers them, and how its persona folders
hold one (``imprint clients --json``: ``skills``, ``personas`` and
``persona_format``). ``apply`` writes every active item into the fewest
folders that reach every connected client: a client that already reads a
chosen folder gets nothing more, so an item shows up once in it wherever
possible. A persona sits in Codex's folder as a TOML agent file and
everywhere else as it is kept (``registry_formats``).

A folder Imprint writes into holds ``.imprint-managed.json``: the items it
wrote there, by name, with the content hash of what it wrote. That makes
three rules hold:

- Imprint never writes over or removes what it did not write. A folder of
  the same name it did not write is reported, and can be adopted.
- An item changed in the client (by hand, or by a client's own skill tools)
  is taken back into the registry, so it reaches every device. When the
  registry changed meanwhile too, the newer change wins and the other stays
  in this device's history. An edit that does not validate is left in place,
  untouched, until it does.
- An item removed in Imprint, or a folder no connected client reads any
  more, is removed from that folder only while it still holds what Imprint
  wrote. One removed in the client is written again: Imprint is where items
  are removed.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path

from . import config, registry
from .registry_formats import FORMATS, MARKDOWN, SUFFIX, parsed, rendered

MANIFEST = '.imprint-managed.json'
# The folders this device wrote into, so one no client reads any more is
# cleaned even after the client that read it is gone.
KNOWN = 'registry-folders.json'
REPORT = 'registry-report.json'
LOCK = 'registry-files.lock'
LOCK_WAIT = 60.0
# How often the dashboard takes back edits made in the clients' folders.
INTERVAL = 300.0
_TEMPORARY = '.imprint-tmp'
_LOCK = threading.Lock()
log = logging.getLogger(__name__)


def _key(path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _runtime() -> str:
    return str(config.get_data_dir().resolve())


def place(kind: str, folder, name: str, fmt: str = MARKDOWN) -> Path:
    """Where an item of that name sits in a folder: a skill's folder, or a
    persona's ``<name>.md`` (``<name>.toml`` in Codex's)."""
    return Path(folder) / (name if kind == 'skill' else f'{name}{SUFFIX[fmt]}')


# ── Which folders ─────────────────────────────────────────────────

def _folders(kind: str, client: dict) -> list[str]:
    """The folders a client reads items of a kind from, preferred first. A
    client whose persona format this runtime does not know reads none."""
    if kind == 'skill':
        return [f for f in client.get('skills') or [] if f]
    if (client.get('persona_format') or MARKDOWN) not in FORMATS:
        return []
    folders = client.get('personas') or []
    return [folders] if isinstance(folders, str) else [f for f in folders if f]


def formats(clients: list[dict]) -> dict[str, str]:
    """The format of each persona folder the clients read, by folder key."""
    found: dict[str, str] = {}
    for client in clients:
        for folder in _folders('persona', client):
            found.setdefault(_key(folder), client.get('persona_format') or MARKDOWN)
    return found


def plan(clients: list[dict]) -> dict[str, list[str]]:
    """The folders to write each kind into, for the connected clients.

    Clients that read the fewest folders choose first; a client that reads a
    folder already chosen adds none, and otherwise its preferred one."""
    connected = [c for c in clients if c.get('connected')]
    targets: dict[str, list[str]] = {}
    for kind in registry.KINDS:
        chosen: list[str] = []
        for client in sorted((c for c in connected if _folders(kind, c)), key=lambda c: len(_folders(kind, c))):
            keys = {_key(folder) for folder in chosen}
            if not any(_key(folder) in keys for folder in _folders(kind, client)):
                chosen.append(_folders(kind, client)[0])
        targets[kind] = chosen
    return targets


def readers(kind: str, folder, clients: list[dict]) -> list[str]:
    """The connected clients that read this folder, by label."""
    key = _key(folder)
    return [client.get('label') or client.get('name') for client in clients
            if client.get('connected') and key in {_key(f) for f in _folders(kind, client)}]


def _clients() -> list[dict]:
    from .commands import clients
    return clients()


# ── Reading and writing items on disk ─────────────────────────────

def _text(file: Path) -> str:
    data = file.read_bytes()
    if len(data) > registry.MAX_FILE:
        raise ValueError(f'{file.name} is {len(data):,} bytes; a file may have at most {registry.MAX_FILE:,}')
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        raise ValueError(f'{file.name} is not UTF-8 text: Imprint keeps text files only') from None


def read(kind: str, path) -> dict[str, str] | None:
    """An item's files as the registry keeps them, read from a client's
    folder; None when it is not there. ValueError when it cannot be kept."""
    path = Path(path)
    if kind == 'persona':
        return {registry.MAIN['persona']: _text(path)} if path.is_file() else None
    if not path.is_dir():
        return None
    files: dict[str, str] = {}
    for file in sorted(path.rglob('*')):
        relative = file.relative_to(path)
        if (any(part in registry.IGNORED for part in relative.parts) or not file.is_file()
                or file.name.endswith(_TEMPORARY)):
            continue
        if len(files) >= registry.MAX_FILES:
            raise ValueError(f'{path.name} holds more than {registry.MAX_FILES} files')
        files[relative.as_posix()] = _text(file)
    return files


def _write_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}{_TEMPORARY}')
    temporary.write_bytes(text.encode('utf-8'))
    os.replace(temporary, path)


def _write(kind: str, path: Path, files: dict[str, str]) -> None:
    if kind == 'persona':
        _write_file(path, files[registry.MAIN['persona']])
        return
    path.mkdir(parents=True, exist_ok=True)
    for relative, text in files.items():
        _write_file(path / relative, text)
    # Files the item no longer has go, then the folders they leave empty.
    for file in sorted(path.rglob('*'), reverse=True):
        relative = file.relative_to(path)
        if any(part in registry.IGNORED for part in relative.parts):
            continue
        if file.is_file() and relative.as_posix() not in files:
            file.unlink()
        elif file.is_dir() and not any(file.iterdir()):
            file.rmdir()


def _remove(kind: str, path: Path) -> None:
    if kind == 'persona':
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def _load(kind: str, folder) -> dict:
    """The folder's manifest; a fresh one when there is none. ValueError
    when it cannot be read or another runtime of Imprint wrote it."""
    path = Path(folder) / MANIFEST
    try:
        doc = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {'imprint': 1, 'kind': kind, 'runtime': _runtime(), 'items': {}}
    except (OSError, ValueError) as error:
        raise ValueError(f'{path} cannot be read ({error})') from None
    if not isinstance(doc, dict) or not isinstance(doc.get('items'), dict):
        raise ValueError(f'{path} is not an Imprint manifest')
    if doc.get('runtime') != _runtime():
        raise ValueError(f'{folder} is managed by another Imprint installation ({doc.get("runtime")})')
    items = {}
    for name, entry in doc['items'].items():
        # A name is a path segment here: only valid item names are kept.
        if (isinstance(name, str) and registry.NAME.fullmatch(name) and isinstance(entry, dict)
                and registry._ID.fullmatch(str(entry.get('id'))) and isinstance(entry.get('hash'), str)):
            items[name] = {'id': entry['id'], 'hash': entry['hash']}
    return {**doc, 'kind': kind, 'items': items}


def _save(folder, doc: dict) -> None:
    path = Path(folder) / MANIFEST
    if not doc['items']:
        path.unlink(missing_ok=True)
        return
    _write_file(path, json.dumps({'imprint': 1, 'kind': doc['kind'], 'runtime': doc['runtime'],
                                  'items': dict(sorted(doc['items'].items()))}, indent=2) + '\n')


def _known() -> list[tuple[str, str, str]]:
    """``(kind, folder, format)`` of each folder this device wrote into."""
    try:
        entries = json.loads((config.get_data_dir() / KNOWN).read_text(encoding='utf-8'))
        return [(e['kind'], e['folder'], e.get('format') or MARKDOWN) for e in entries
                if e.get('kind') in registry.KINDS and e.get('folder') and (e.get('format') or MARKDOWN) in FORMATS]
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        return []


def _save_known(folders) -> None:
    path = config.get_data_dir() / KNOWN
    if folders:
        _write_file(path, json.dumps([{'kind': k, 'folder': f, **({'format': fmt} if fmt != MARKDOWN else {})}
                                      for k, f, fmt in folders], indent=2) + '\n')
    else:
        path.unlink(missing_ok=True)


class _FileLock:
    """Excludes other processes applying at the same time (the dashboard,
    the CLI and connect)."""

    def __enter__(self):
        from .queue_lock import try_lock
        self.path = config.get_data_dir() / LOCK
        deadline = time.monotonic() + LOCK_WAIT
        while (fd := try_lock(self.path)) is None:
            if time.monotonic() > deadline:
                raise RuntimeError('Another Imprint process is writing skills; try again shortly')
            time.sleep(0.2)
        self.fd = fd
        return self

    def __exit__(self, *exc):
        from .queue_lock import unlock
        unlock(self.fd, self.path)


# ── Apply ─────────────────────────────────────────────────────────

def _take(item_id: str, files: dict[str, str], at: float, report: dict, path: Path) -> None:
    """Take an edit made in a client's folder into the registry: the newer
    of it and the registry's own change wins; the other is kept in history.
    ValueError when the edit is not a valid item."""
    item = registry.get(item_id)
    if item is None:
        raise LookupError(item_id)
    if at > item['updated_at']:
        registry.update(item_id, files=files, at=at)
        report['taken'].append(str(path))
    else:
        registry.keep_version(item_id, files, at)
        report['kept'].append(str(path))


def apply(clients: list[dict] | None = None) -> dict:
    """Write the registry into the clients' folders and take back what was
    changed there. Returns what it did, and the folders it left alone and
    why (``problems``). When the clients cannot be listed it raises, and
    nothing is written or removed."""
    with _LOCK, _FileLock():
        if clients is None:
            clients = _clients()
        targets = plan(clients)
        shapes = formats(clients)

        def shape(kind: str, folder: str) -> str:
            return shapes.get(_key(folder), MARKDOWN) if kind == 'persona' else MARKDOWN

        report = {'folders': [{'kind': kind, 'folder': folder, 'clients': readers(kind, folder, clients),
                               **({'format': shape(kind, folder)} if shape(kind, folder) != MARKDOWN else {})}
                              for kind in registry.KINDS for folder in targets[kind]],
                  'written': [], 'removed': [], 'taken': [], 'kept': [], 'problems': [], 'at': time.time()}
        wanted = {(kind, _key(folder)) for kind in registry.KINDS for folder in targets[kind]}
        known = _known()
        folders: dict[tuple[str, str], tuple[str, str, str]] = {}
        for kind, folder, fmt in [(k, f, shape(k, f)) for k in registry.KINDS for f in targets[k]] + known:
            folders.setdefault((kind, _key(folder)), (kind, folder, fmt))
        manifests: dict[tuple[str, str], dict] = {}
        unreadable: list[tuple[str, str, str]] = []
        for key, (kind, folder, fmt) in folders.items():
            try:
                manifests[key] = _load(kind, folder)
            except ValueError as error:
                report['problems'].append({'path': folder, 'problem': str(error)})
                if (kind, folder, fmt) in known:
                    unreadable.append((kind, folder, fmt))
        held: set[tuple[str, str]] = set()

        # 1. Edits made in the clients come back first. A manifest records
        # what was written in the folder's own format.
        for key, doc in manifests.items():
            kind, folder, fmt = folders[key]
            for name, entry in list(doc['items'].items()):
                path = place(kind, folder, name, fmt)
                try:
                    files = read(kind, path)
                    if files is None or registry.MAIN[kind] not in files:
                        continue  # Removed in the client: written again below.
                    digest = registry.content_hash(kind, files)
                    if digest == entry['hash']:
                        continue
                    item = registry.get(entry['id'])
                    if item is None:
                        raise LookupError(entry['id'])
                    _take(entry['id'], parsed(kind, fmt, files, item['files']), path.stat().st_mtime
                          if kind == 'persona' else max(f.stat().st_mtime for f in [path, *path.rglob('*')]),
                          report, path)
                    entry['hash'] = digest
                except LookupError:
                    del doc['items'][name]  # Its item is unknown here: the folder is the user's now.
                except (OSError, ValueError) as error:
                    held.add((key[1], name))
                    report['problems'].append({'path': str(path), 'problem': f'{error}. Imprint leaves it as it is '
                                                                             'until it is valid.'})

        # 2. Then each folder gets what its clients should have.
        active = {kind: registry.active(kind) for kind in registry.KINDS}
        for key, doc in manifests.items():
            kind, folder, fmt = folders[key]
            _settle(kind, folder, fmt, doc, active[kind] if key in wanted else [], held, report)
            try:
                _save(folder, doc)
            except OSError as error:
                report['problems'].append({'path': folder, 'problem': f'The manifest could not be written: {error}'})
        _save_known([folders[key] for key, doc in manifests.items() if doc['items']] + unreadable)
        try:
            _write_file(config.get_data_dir() / REPORT, json.dumps(report, indent=2) + '\n')
        except OSError:
            pass
        return report


def _settle(kind: str, folder: str, fmt: str, doc: dict, items: list[dict], held: set, report: dict) -> None:
    managed = doc['items']
    by_name = {item['name']: item for item in items}
    folder_key = _key(folder)
    for name, entry in list(managed.items()):
        item = by_name.get(name)
        if (folder_key, name) in held or (item is not None and item['id'] == entry['id']):
            continue
        path = place(kind, folder, name, fmt)
        try:
            files = read(kind, path)
            if files is not None and registry.content_hash(kind, files) == entry['hash']:
                _remove(kind, path)
                report['removed'].append(str(path))
        except (OSError, ValueError) as error:
            report['problems'].append({'path': str(path), 'problem': f'Could not be removed: {error}'})
            continue
        del managed[name]
    for item in items:
        name = item['name']
        if (folder_key, name) in held:
            continue
        path = place(kind, folder, name, fmt)
        entry = managed.get(name)
        try:
            want = rendered(kind, fmt, item['files'])
            want_hash = registry.content_hash(kind, want)
            files = read(kind, path)
            if files is not None:
                digest = registry.content_hash(kind, files)
                if digest == want_hash:
                    managed[name] = {'id': item['id'], 'hash': want_hash}
                    continue
                if entry is None:
                    # What Imprint did not write may still be the item itself,
                    # in that folder's format: it is managed from then on.
                    try:
                        same = registry.content_hash(kind, parsed(kind, fmt, files, item['files'])) == item['hash']
                    except ValueError:
                        same = False
                    if same:
                        managed[name] = {'id': item['id'], 'hash': digest}
                        continue
                    report['problems'].append({'path': str(path), 'problem': f'Imprint did not write this {kind}, so it '
                                               'is left as it is: adopt it, or rename one of the two'})
                    continue
            _write(kind, path, want)
        except (OSError, ValueError) as error:
            report['problems'].append({'path': str(path), 'problem': f'Could not be written: {error}'})
            continue
        managed[name] = {'id': item['id'], 'hash': want_hash}
        report['written'].append(str(path))


def needed() -> bool:
    """Whether there is anything to apply: items, or folders written before."""
    return (registry.exists() and bool(registry.manifest())) or bool(_known())


def apply_quietly(clients: list[dict] | None = None) -> dict | None:
    """``apply`` when there is anything to apply, logging instead of raising:
    for the dashboard's background pass and after a sync."""
    try:
        return apply(clients) if needed() else None
    except Exception as error:  # noqa: BLE001 - a background pass must not stop its caller
        log.warning('Skills and personas were not written to the clients: %s', error)
        return None


def last_report() -> dict | None:
    try:
        return json.loads((config.get_data_dir() / REPORT).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


async def loop() -> None:
    """The dashboard's pass: at start, then every INTERVAL seconds."""
    import asyncio
    while True:
        await asyncio.to_thread(apply_quietly)
        await asyncio.sleep(INTERVAL)


# ── Items found in the clients' folders ───────────────────────────

def found(clients: list[dict] | None = None) -> list[dict]:
    """Skills and personas in the connected clients' folders that Imprint
    did not write and does not hold, for the user to adopt."""
    clients = _clients() if clients is None else clients
    held = {(item['kind'], item['hash']) for item in registry.list_all(with_files=False)}
    shapes = formats(clients)
    folders: dict[tuple[str, str], tuple[str, str, str]] = {}
    for client in clients:
        if not client.get('connected'):
            continue
        for kind in registry.KINDS:
            for folder in _folders(kind, client):
                fmt = shapes.get(_key(folder), MARKDOWN) if kind == 'persona' else MARKDOWN
                folders.setdefault((kind, _key(folder)), (kind, folder, fmt))
    results = []
    for kind, folder, fmt in folders.values():
        try:
            managed = _load(kind, folder)['items']
        except ValueError:
            managed = {}
        base = Path(folder)
        try:
            children = sorted(base.iterdir()) if base.is_dir() else []
        except OSError:
            continue
        for child in children:
            if kind == 'skill':
                if not (child.is_dir() and (child / registry.MAIN['skill']).is_file()) or child.name in managed:
                    continue
                name = child.name
            else:
                if not (child.is_file() and child.suffix == SUFFIX[fmt]) or child.stem in managed:
                    continue
                name = child.stem
            entry = {'kind': kind, 'name': name, 'path': str(child), 'folder': folder,
                     'clients': readers(kind, folder, clients), 'description': '', 'problem': '', 'format': fmt}
            try:
                files = parsed(kind, fmt, read(kind, child))
                if (kind, registry.content_hash(kind, files)) in held:
                    continue
                valid = registry.validate(kind, files)
                entry.update(name=valid['name'], description=valid['description'])
            except (OSError, ValueError) as error:
                entry['problem'] = str(error)
            results.append(entry)
    return results


def adopt(path: str, kind: str, clients: list[dict] | None = None) -> dict:
    """Keep a skill or persona found in a client's folder in the registry.
    Only what ``found`` lists can be adopted; the folder stays where it is,
    and Imprint manages it from then on where it writes that kind."""
    clients = _clients() if clients is None else clients
    match = next((f for f in found(clients) if f['kind'] == kind and _key(f['path']) == _key(path)), None)
    if match is None:
        raise KeyError(path)
    if match['problem']:
        raise ValueError(match['problem'])
    item = registry.create(kind, parsed(kind, match['format'], read(kind, path)), origin='adopted')
    apply(clients)
    return item
