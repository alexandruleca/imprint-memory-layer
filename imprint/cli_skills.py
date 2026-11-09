"""``imprint skills``: the user's skills and personas (ADRs 0168, 0169).

Usage:
    python -m imprint.cli_skills [list] [--json]
    python -m imprint.cli_skills show NAME [--kind skill|persona]
    python -m imprint.cli_skills add PATH [--persona]
    python -m imprint.cli_skills found [--json]
    python -m imprint.cli_skills adopt (PATH | --all)
    python -m imprint.cli_skills enable|disable|remove NAME [--kind skill|persona]
    python -m imprint.cli_skills apply [--quiet] [--if-needed] [--json]

``add`` keeps a skill's folder (with its ``SKILL.md``) or a persona's
Markdown file; ``adopt`` keeps one ``found`` in a connected client's folder.
Every change is written into the clients' folders at once (``apply``).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import registry, registry_files


def _say(text: str = '') -> None:
    print(text, flush=True)


def _report(report: dict, quiet: bool) -> None:
    if not quiet:
        for folder in report['folders']:
            readers = ', '.join(folder['clients']) or 'no connected client'
            _say(f"{folder['kind']}s → {folder['folder']} ({readers})")
        for key, label in (('written', 'Wrote'), ('removed', 'Removed'), ('taken', 'Took back an edit from'),
                           ('kept', 'Kept an older edit in history from')):
            for path in report[key]:
                _say(f'{label} {path}')
    for problem in report['problems']:
        print(f"! {problem['path']}: {problem['problem']}", file=sys.stderr, flush=True)


def _apply(quiet: bool = False) -> int:
    try:
        report = registry_files.apply()
    except Exception as error:  # noqa: BLE001 - reported to the user
        print(f'Skills and personas were not written to the clients: {error}', file=sys.stderr)
        return 1
    _report(report, quiet)
    return 0


def _list(as_json: bool) -> int:
    items = registry.list_all()
    if as_json:
        _say(json.dumps(items, indent=2))
        return 0
    if not items:
        _say('No skills or personas yet. Add one with `imprint skills add <folder>`, '
             'or adopt one a client already has: `imprint skills found`.')
        return 0
    for item in items:
        flags = [item['state']] if item['state'] != 'active' else []
        if item['shadowed']:
            flags.append('another holds this name')
        suffix = f" [{', '.join(flags)}]" if flags else ''
        _say(f"{item['kind']:<8} {item['name']}{suffix}\n         {item['description']}")
    return 0


def _find(name: str, kind: str | None) -> dict:
    try:
        return registry.find(name, kind)
    except KeyError:
        raise SystemExit(f'No {kind or "skill or persona"} named {name}') from None
    except ValueError as error:
        raise SystemExit(f'{error} (--kind skill or --kind persona)') from None


def _add(path: str, persona: bool) -> int:
    target = Path(path).expanduser()
    if target.is_file() and target.name == registry.MAIN['skill'] and not persona:
        target = target.parent  # A skill is its folder.
    kind = 'persona' if persona or target.is_file() else 'skill'
    files = registry_files.read(kind, target)
    if files is None:
        raise SystemExit(f'{path}: not a skill folder (with a SKILL.md) or a persona file')
    item = registry.create(kind, files)
    _say(f"Added {kind} {item['name']}.")
    return _apply(quiet=True)


def _found(as_json: bool) -> list[dict]:
    items = registry_files.found()
    if as_json:
        _say(json.dumps(items, indent=2))
    elif not items:
        _say("The connected clients' folders hold no skill or persona Imprint does not already keep.")
    for item in [] if as_json else items:
        state = f"  ! {item['problem']}" if item['problem'] else f"  {item['description']}"
        _say(f"{item['kind']:<8} {item['name']}  ({', '.join(item['clients'])})\n         {item['path']}\n       {state}")
    return items


def _adopt(path: str | None, every: bool) -> int:
    candidates = registry_files.found()
    chosen = [c for c in candidates if not c['problem']] if every else [
        c for c in candidates if path and (Path(c['path']) == Path(path).expanduser() or c['name'] == path)]
    if not chosen:
        raise SystemExit('Nothing to adopt: see `imprint skills found`')
    failed = 0
    for candidate in chosen:
        try:
            item = registry_files.adopt(candidate['path'], candidate['kind'])
            _say(f"Adopted {item['kind']} {item['name']} from {candidate['path']}.")
        except (KeyError, ValueError) as error:
            failed += 1
            print(f"! {candidate['path']}: {error}", file=sys.stderr)
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='imprint skills', description='Your skills and personas, on every device.')
    commands = parser.add_subparsers(dest='command')
    listing = commands.add_parser('list', help='List skills and personas')
    listing.add_argument('--json', action='store_true')
    show = commands.add_parser('show', help="Print an item's files")
    show.add_argument('name')
    show.add_argument('--kind', choices=registry.KINDS)
    add = commands.add_parser('add', help="Keep a skill's folder or a persona's .md file")
    add.add_argument('path')
    add.add_argument('--persona', action='store_true', help='The path is a persona file')
    found = commands.add_parser('found', help="Skills and personas in the clients' folders Imprint does not keep")
    found.add_argument('--json', action='store_true')
    adopt = commands.add_parser('adopt', help='Keep one found in a client folder')
    adopt.add_argument('path', nargs='?', help='Its path or name, as `found` lists it')
    adopt.add_argument('--all', action='store_true', help='Adopt every valid one found')
    for state, help_text in (('enable', 'Give it to the clients again'), ('disable', 'Take it out of the clients, keep it'),
                             ('remove', 'Remove it here and on every paired device')):
        command = commands.add_parser(state, help=help_text)
        command.add_argument('name')
        command.add_argument('--kind', choices=registry.KINDS)
    applying = commands.add_parser('apply', help="Write into the clients' folders and take back edits made there")
    applying.add_argument('--quiet', action='store_true', help='Print only problems')
    applying.add_argument('--if-needed', action='store_true',
                          help='Do nothing when there are no items and no folder was written before')
    applying.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)

    try:
        if args.command in (None, 'list'):
            return _list(getattr(args, 'json', False))
        if args.command == 'show':
            item = _find(args.name, args.kind)
            for path, text in item['files'].items():
                _say(f'── {path} ──\n{text}')
            return 0
        if args.command == 'add':
            return _add(args.path, args.persona)
        if args.command == 'found':
            _found(args.json)
            return 0
        if args.command == 'adopt':
            if not args.all and not args.path:
                parser.error('adopt needs a path or name, or --all')
            return _adopt(args.path, args.all)
        if args.command in ('enable', 'disable', 'remove'):
            item = _find(args.name, args.kind)
            state = {'enable': 'active', 'disable': 'disabled', 'remove': 'retired'}[args.command]
            registry.update(item['id'], state=state)
            _say(f"{args.command.capitalize()}d {item['kind']} {item['name']}.")
            return _apply(quiet=True)
        if args.command == 'apply':
            if args.if_needed and not registry_files.needed():
                return 0
            if args.json:
                _say(json.dumps(registry_files.apply(), indent=2))
                return 0
            return _apply(args.quiet)
    except ValueError as error:
        print(f'imprint skills: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
