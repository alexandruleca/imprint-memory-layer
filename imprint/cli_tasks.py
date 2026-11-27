"""``imprint tasks``: the workspace's Board from a terminal (ADR 0208).

Usage:
    python -m imprint.cli_tasks [board] [--project P] [--json]
    python -m imprint.cli_tasks list [--project P] [--status S] [--json]
    python -m imprint.cli_tasks show TASK [--json]
    python -m imprint.cli_tasks create TITLE [--project P] [--description D] [--ready]
                                      [--priority P0-P3] [--criterion C]... [--tag T]... [--assign A]... [--json]

Every command takes ``--workspace W``; the default is the active workspace.
Changes made here are the user's.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import tasks, tasks_tool


def _utf8_output() -> None:
    """Titles and descriptions are Unicode, and Windows gives redirected
    output its ANSI code page."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')


def _print(value, as_json: bool, text: str) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2) if as_json else text, flush=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='imprint tasks', description="The workspace's Board of Tasks.")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--workspace', help='Workspace (default: the active one)')
    common.add_argument('--json', action='store_true', help='Print JSON')
    commands = parser.add_subparsers(dest='command')
    board = commands.add_parser('board', parents=[common], help='Tasks by status (the default)')
    board.add_argument('--project')
    listing = commands.add_parser('list', parents=[common], help='Tasks matching filters')
    listing.add_argument('--project')
    listing.add_argument('--status')
    show = commands.add_parser('show', parents=[common], help='One Task')
    show.add_argument('task')
    create = commands.add_parser('create', parents=[common], help='Put a new Task on the Board')
    create.add_argument('title')
    create.add_argument('--project', help="Default: this directory's project")
    create.add_argument('--description', default='')
    create.add_argument('--ready', action='store_true', help='Start in Ready (needs --criterion)')
    create.add_argument('--priority', default='P2')
    create.add_argument('--criterion', action='append', default=[], help='An acceptance criterion (repeatable)')
    create.add_argument('--tag', action='append', default=[], help='A tag (repeatable)')
    create.add_argument('--assign', action='append', default=[], help='An assignee (repeatable)')
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    _utf8_output()
    command = args.command or 'board'
    workspace = getattr(args, 'workspace', None) or None
    as_json = getattr(args, 'json', False)
    try:
        if command == 'board':
            board = tasks.board(project=getattr(args, 'project', None), workspace=workspace)
            _print(board, as_json, tasks_tool.board_text(board, getattr(args, 'project', None)))
        elif command == 'list':
            states = tasks.listing(project=args.project,
                                   status=tasks_tool.status_ref(args.status) if args.status else None,
                                   workspace=workspace)
            _print(states, as_json, tasks_tool.list_text(states))
        elif command == 'show':
            state = tasks.get(args.task, workspace=workspace)
            _print(state, as_json, tasks_tool.detail(state))
        elif command == 'create':
            from .cli_conversations import derive_project
            project = args.project or derive_project(os.getcwd())
            if not project:
                raise ValueError('No project found for this directory: pass --project')
            made = tasks.create(args.title, project=project, description=args.description,
                                status='ready' if args.ready else 'backlog', priority=args.priority,
                                acceptance=args.criterion, tags=args.tag, assignees=args.assign,
                                actor=tasks.USER, workspace=workspace)
            _print(made, as_json, f"Created {made['id']}.\n{tasks_tool.detail(made)}")
    except (ValueError, LookupError, OSError) as error:
        print(f'imprint tasks: {error}', file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
