"""``imprint restore``: write indexed files back from their snapshots (ADR 0197).

Every indexed file keeps one redacted full-text snapshot (ADR 0081), also
for a while after the file is gone from disk (ADR 0196). This writes those
snapshots back as files. A preview, the default, says what it would write;
``--apply`` writes.

- ``<dir>``: the files this device indexed under ``<dir>``, back where they
  were, or under ``--to <out>`` at the same paths relative to ``<dir>``.
- ``--project NAME --to <out>``: every file of the project, indexed on any
  device, under ``<out>`` at its path within the project.

Only files the destination lacks are written. ``--all`` also compares the
ones that exist: identical ones are unchanged, different ones are replaced
only with ``--overwrite``.

A code, text or kept-whole data file (ADR 0195) is written as its snapshot's
UTF-8 text with the line endings it had, and compared with the SHA-256
recorded when it was indexed: exact, holding redacted secrets, or otherwise
different. A document (PDF, Office, an image read by OCR, an audio file) has
only the text read out of it, written as ``<name>.extracted.txt`` beside
where it was, never under its own name. Conversations, URLs and inline
content are not files and are skipped.

Snapshots and their paths can come from paired devices, so a destination
that would land outside the destination folder is refused. Restore only
reads snapshots and writes outside the data folder, so it takes no queue lock.

Usage:
    python -m imprint.cli_restore <dir> [--to OUT] [--all] [--overwrite] [--apply] [--workspace NAME]
    python -m imprint.cli_restore --project NAME --to OUT [--all] [--overwrite] [--apply] [--workspace NAME]
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import os
import re
import secrets
import shutil
import sys
from collections import Counter
from pathlib import Path

from . import device, source_snapshots
from .long_paths import os_path, realpath

PLACEHOLDER = re.compile(r'\[REDACTED:[a-z0-9-]+:[0-9]+\]')
EXTRACTED = '.extracted.txt'
SHOWN = 200  # Per-file lines before the summary.

# Statuses, in the order the listing shows them. Errors first.
ORDER = ('refused', 'failed', 'write', 'extract', 'replace', 'keep', 'same', 'older')
ERRORS = ('refused', 'failed')
WRITES = ('write', 'extract', 'replace')


def form(head: dict) -> str:
    """``file`` when the snapshot is the file's own text, ``extracted`` when
    it is text read out of the file, '' when the source is no file."""
    if not head['provenance'].get('source_path'):
        return ''
    if head['kind'] == 'text':
        # A spreadsheet kept whole holds its extracted text (ADR 0195).
        return 'extracted' if head['data_format'] == 'xlsx' else 'file'
    if head['kind'] == 'extracted' or (head['kind'] == 'conversation' and head['media'] == 'audio'):
        return 'extracted'
    return ''


def _key(path: Path) -> str:
    return os.path.normcase(str(path))


def _under(path: str, roots: list[str]) -> bool:
    """A cheap test before resolving: a recorded path is already resolved."""
    path = os.path.normcase(os.path.normpath(path))
    return any(path.startswith(root) for root in roots)


def _select(heads, *, root=None, project=None):
    """(head, relative path or '', form) of the heads to restore, and how
    many sources were no file."""
    chosen, not_files = [], 0
    if root is not None:
        base = Path(realpath(root))
        prefixes = {os.path.join(os.path.normcase(os.path.normpath(p)), '') for p in (str(base), os.path.abspath(root))}
    for head in heads:
        provenance = head['provenance']
        if root is not None:
            path = provenance.get('source_path')
            if not isinstance(path, str) or not path or not _under(path, prefixes) or not device.local_source(provenance):
                continue
            real = Path(realpath(path))
            if real == base or not real.is_relative_to(base):
                continue
            rel = real.relative_to(base).as_posix()
        else:
            if head['project'] != project:
                continue
            # Untrusted: device.relative_path refuses absolute paths, drives and "..".
            rel = device.relative_path({**provenance, 'source': head['source'], 'project': head['project'],
                                        'source_type': 'file'})
        kind = form(head)
        if not kind:
            not_files += 1
            continue
        chosen.append((head, rel, kind))
    return chosen, not_files


def _contained(dest: Path, base: Path) -> bool:
    real = Path(realpath(dest))
    return real != base and real.is_relative_to(base)


def _quality(text: str, data: bytes, recorded: str | None) -> tuple[str, str]:
    if recorded and hashlib.sha256(data).hexdigest() == recorded:
        return 'exact', 'exact'
    secrets_found = len(PLACEHOLDER.findall(text))
    if secrets_found:
        return 'redacted', (f"{secrets_found} redacted secret{'s' if secrets_found != 1 else ''} "
                            "([REDACTED:…] placeholders); otherwise as indexed")
    return 'differs', 'differs from the file as indexed (its SHA-256 does not match)'


def _write(dest: Path, data: bytes, *, replace: bool, mtime: float | None) -> None:
    """Write through a temporary file in the same folder, so a file is
    either whole or not there; never over a file that appeared meanwhile."""
    os.makedirs(os_path(dest.parent), exist_ok=True)
    temporary = dest.parent / f'.imprint-restore-{secrets.token_hex(6)}.tmp'
    descriptor = os.open(os_path(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), 0o666)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            shutil.copymode(os_path(dest), os_path(temporary))
        elif os.path.lexists(os_path(dest)):
            raise FileExistsError('a file appeared there meanwhile; kept it')
        if mtime:
            os.utime(os_path(temporary), (mtime, mtime))
        os.replace(os_path(temporary), os_path(dest))
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(os_path(temporary))
        raise


def restore(workspace=None, *, root=None, project=None, to=None, every=False, overwrite=False,
            apply=False) -> dict:
    """Restore the files of ``root`` (this device's) or of ``project`` (any
    device's, under ``to``). Returns each file's entry and the counts."""
    if (root is None) == (project is None):
        raise ValueError('Give a folder or --project')
    if project is not None and not to:
        raise ValueError('--project needs --to <folder>: files of other devices have no place here')
    base = Path(realpath(to if to else root))
    chosen, not_files = _select(source_snapshots.list_files(workspace), root=root, project=project)

    entries, by_dest = [], {}
    for head, rel, kind in chosen:
        entry = {'source': head['source'], 'project': head['project'], 'form': kind, 'path': rel or head['source'],
                 'status': '', 'detail': '', 'quality': ''}
        entries.append(entry)
        if not rel:
            entry.update(status='refused', detail='no safe path within the project')
            continue
        original = base / rel
        dest = original.with_name(original.name + EXTRACTED) if kind == 'extracted' else original
        entry['path'] = dest.relative_to(base).as_posix() if dest.is_relative_to(base) else rel
        if not _contained(dest, base):
            entry.update(status='refused', detail=f'would land outside {base}')
            continue
        entry.update(dest=dest, original=original, updated_at=head['updated_at'])
        # One file per destination: the newest snapshot of it.
        held = by_dest.get(_key(dest))
        if held is None or held['updated_at'] < entry['updated_at']:
            if held is not None:
                held.update(status='older', detail=f"{entry['source']} is newer")
            by_dest[_key(dest)] = entry
        else:
            entry.update(status='older', detail=f"{held['source']} is newer")

    counts = Counter(not_files=not_files)
    for entry in entries:
        if entry['status']:
            continue
        dest, original = entry.pop('dest'), entry.pop('original')
        try:
            if entry['form'] == 'extracted' and os.path.lexists(os_path(original)):
                entry['status'] = 'present'  # The file itself is there.
                continue
            if os.path.islink(os_path(dest)):
                entry.update(status='refused', detail='is a link; links are not followed')
                continue
            exists = os.path.lexists(os_path(dest))
            if exists and os.path.isdir(os_path(dest)):
                entry.update(status='failed', detail='a folder is in the way')
                continue
            if exists and not every:
                entry['status'] = 'present'
                continue
            snapshot = source_snapshots.get(entry['source'], entry['project'], workspace)
            if snapshot is None:
                entry.update(status='failed', detail='its snapshot is gone')
                continue
            text = snapshot['text']
            data = text.encode('utf-8')
            provenance = snapshot['metadata'].get('provenance') or {}
            if entry['form'] == 'extracted':
                quality, detail = 'extracted', 'text read out of the file, not the file itself'
            else:
                quality, detail = _quality(text, data, provenance.get('source_hash'))
            entry.update(quality=quality, detail=detail)
            if exists:
                if Path(os_path(dest)).read_bytes() == data:
                    entry.update(status='same', detail='unchanged')
                    continue
                if not overwrite:
                    entry.update(status='keep', detail=f'exists and differs; --overwrite replaces it with the snapshot ({detail})')
                    continue
                entry['status'] = 'replace'
            else:
                entry['status'] = 'extract' if entry['form'] == 'extracted' else 'write'
            if apply:
                mtime = provenance.get('source_mtime') if quality == 'exact' else None
                _write(dest, data, replace=exists, mtime=mtime if isinstance(mtime, (int, float)) else None)
        except (OSError, ValueError, UnicodeError) as error:
            entry.update(status='failed', detail=str(error))
    for entry in entries:
        entry.pop('dest', None), entry.pop('original', None), entry.pop('updated_at', None)
        counts[entry['status']] += 1
        if entry['status'] in WRITES:
            counts['quality:' + entry['quality']] += 1
    return {'workspace': workspace, 'applied': apply, 'destination': str(base), 'entries': entries,
            'counts': counts, 'errors': sum(counts[status] for status in ERRORS)}


LABELS = {'write': ('would write', 'wrote'), 'extract': ('would extract', 'extracted'),
          'replace': ('would replace', 'replaced'), 'keep': ('kept', 'kept'), 'same': ('same', 'same'),
          'older': ('skipped', 'skipped'), 'refused': ('refused', 'refused'), 'failed': ('failed', 'failed')}


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def report(result: dict, *, shown: int = SHOWN) -> str:
    """Up to ``shown`` lines of files, errors first, then the totals."""
    applied, counts = result['applied'], result['counts']
    listed = sorted((entry for entry in result['entries'] if entry['status'] in ORDER),
                    key=lambda entry: (ORDER.index(entry['status']), entry['path']))
    width = max((len(LABELS[entry['status']][applied]) for entry in listed[:shown]), default=0)
    lines = [f"  {LABELS[entry['status']][applied]:<{width}}  {entry['path']}  {entry['detail']}".rstrip()
             for entry in listed[:shown]]
    if len(listed) > shown:
        lines.append(f'  … {len(listed) - shown} more not listed')
    if lines:
        lines.append('')
    exact, redacted, differs = counts['quality:exact'], counts['quality:redacted'], counts['quality:differs']
    written = []
    if exact + redacted + differs:
        written.append(f"{_plural(exact + redacted + differs, 'file')} ({exact} exact, "
                       f"{redacted} with redacted secrets, {differs} differing)")
    if counts['quality:extracted']:
        written.append(f"{_plural(counts['quality:extracted'], 'extracted text')} (<name>.extracted.txt)")
    if written:
        lines.append(f"  {'Wrote' if applied else 'Would write'} {' and '.join(written)} to {result['destination']}.")
    else:
        lines.append(f"  Nothing to write to {result['destination']}.")
    rest = [f'{label}: {counts[key]}{hint}' for key, label, hint in (
        ('same', 'Unchanged', ''), ('keep', 'Differ, kept', ' (--overwrite replaces them)'),
        ('present', 'Already on disk', ' (--all compares them)'),
        ('older', 'Older snapshots of a path a newer one restores', ''),
        ('not_files', 'Not files', ' (conversations, URLs, inline content)')) if counts[key]]
    if rest:
        lines.append('  ' + '. '.join(rest) + '.')
    if result['errors']:
        lines.append(f"  Errors: {counts['refused']} refused, {counts['failed']} failed.")
    if not applied and written:
        lines.append('  Preview: nothing was written. Run again with --apply to write.')
    return '\n'.join(lines)


def _workspace(args) -> tuple[str, str]:
    from . import workspace_policy as policy
    if args.workspace:
        return policy.workspace_name(args.workspace), '--workspace'
    if args.dir:
        with contextlib.suppress(OSError, ValueError):
            resolution = policy.resolve(args.dir)
            return resolution['workspace'], policy.describe(resolution)
    return policy.workspace_name(), 'IMPRINT_WORKSPACE' if os.environ.get('IMPRINT_WORKSPACE') else 'the active workspace'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog='imprint restore',
        description='Write indexed files back from their source snapshots. Previews unless --apply.')
    parser.add_argument('dir', nargs='?', help='Folder whose files this device indexed')
    parser.add_argument('--project', help='Every file of this project, from any device (needs --to)')
    parser.add_argument('--to', help='Write under this folder instead of where the files were')
    parser.add_argument('--all', dest='every', action='store_true',
                        help='Also compare files that exist; with --overwrite, replace those that differ')
    parser.add_argument('--overwrite', action='store_true', help='Replace existing files that differ (with --all)')
    parser.add_argument('--apply', action='store_true', help='Write the files; otherwise only preview')
    parser.add_argument('--workspace', help="Workspace to read (default: the folder's, else the active one)")
    args = parser.parse_args(argv)
    if bool(args.dir) == bool(args.project):
        parser.error('give a folder, or --project NAME --to <folder>')
    if args.project and not args.to:
        parser.error('--project needs --to <folder>')
    if args.overwrite and not args.every:
        print('  --overwrite applies with --all: without it, existing files are not compared.', file=sys.stderr)
    try:
        workspace, why = _workspace(args)
    except ValueError as error:
        print(f'  {error}', file=sys.stderr)
        return 1
    print(f'  Workspace: {workspace} ({why})')
    origin = f'project {args.project}' if args.project else str(Path(realpath(args.dir)))
    print(f'  Restoring {origin} from its snapshots' + ('' if args.apply else ' (preview)'))
    print()
    result = restore(workspace, root=args.dir, project=args.project, to=args.to, every=args.every,
                     overwrite=args.overwrite, apply=args.apply)
    print(report(result))
    if args.dir and not result['entries'] and not result['counts']['not_files']:
        print('  This device indexed no files there. Files indexed on another device: '
              'imprint restore --project NAME --to <folder>')
    return 1 if result['errors'] else 0


if __name__ == '__main__':
    from ._cli_signals import install
    install()
    sys.exit(main())
