"""``imprint tasks``: the workspace's Board from a terminal (ADR 0208).

Usage:
    python -m imprint.cli_tasks [board] [FILTERS] [--json]
    python -m imprint.cli_tasks list [FILTERS] [--archived] [--json]
    python -m imprint.cli_tasks show TASK [--json]
    python -m imprint.cli_tasks history TASK [--json]
    python -m imprint.cli_tasks create TITLE [--project P] [--description D] [--ready] [--priority P0-P3]
                                      [--criterion C]... [--tag T]... [--assign A]... [--feature F] [--blocker B]...
    python -m imprint.cli_tasks edit TASK [--title T] [--description D] [--project P] [--priority P]
                                    [--criterion C]... [--position N]
    python -m imprint.cli_tasks append TASK TEXT
    python -m imprint.cli_tasks move TASK STATUS [--position N]
    python -m imprint.cli_tasks tag|assign TASK [--add X]... [--remove X]...
    python -m imprint.cli_tasks link TASK [--feature F | --no-feature] [--blocker B]... [--unblocker B]...
    python -m imprint.cli_tasks next [--project P] [--feature F] [--client C] [--device D]
    python -m imprint.cli_tasks handoff TASK
    python -m imprint.cli_tasks autonomy [off|stale|ready]
    python -m imprint.cli_tasks block TASK REASON
    python -m imprint.cli_tasks unblock|cancel|release TASK
    python -m imprint.cli_tasks delete TASK --yes

FILTERS: --project P --feature F --tag T --status S --priority P --assignee A --text T.
Every command takes ``--workspace W`` (default: the active workspace) and
``--json``. Changes made here are the user's.
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
    filtered = argparse.ArgumentParser(add_help=False)
    for name in ('project', 'feature', 'tag', 'status', 'priority', 'assignee', 'text'):
        filtered.add_argument(f'--{name}', default='')
    commands = parser.add_subparsers(dest='command')
    commands.add_parser('board', parents=[common, filtered], help='Tasks by status (the default)')
    listing = commands.add_parser('list', parents=[common, filtered], help='Tasks matching filters')
    listing.add_argument('--archived', action='store_true', help='Include Tasks that ended over 14 days ago')
    for name, help_text in (('show', 'One Task'), ('history', "One Task's changes"),
                            ('unblock', 'Clear the Blocked mark'), ('cancel', 'Move it to Cancelled'),
                            ('release', "End an agent's claim on it")):
        commands.add_parser(name, parents=[common], help=help_text).add_argument('task')
    create = commands.add_parser('create', parents=[common], help='Put a new Task on the Board')
    create.add_argument('title')
    create.add_argument('--project', help="Default: this directory's project")
    create.add_argument('--description', default='')
    create.add_argument('--ready', action='store_true', help='Start in Ready (needs --criterion)')
    create.add_argument('--priority', default='P2')
    create.add_argument('--criterion', action='append', default=[], help='An acceptance criterion (repeatable)')
    create.add_argument('--tag', action='append', default=[], help='A tag (repeatable)')
    create.add_argument('--assign', action='append', default=[], help='An assignee (repeatable)')
    create.add_argument('--feature', help='The Feature it belongs to')
    create.add_argument('--blocker', action='append', default=[], help='A Task that must be Done first (repeatable)')
    edit = commands.add_parser('edit', parents=[common], help='Replace some of its fields')
    edit.add_argument('task')
    for name in ('title', 'description', 'project', 'priority'):
        edit.add_argument(f'--{name}')
    edit.add_argument('--criterion', action='append', help='Replaces the acceptance criteria (repeatable)')
    edit.add_argument('--position', type=float)
    append = commands.add_parser('append', parents=[common], help='Add text to its description')
    append.add_argument('task')
    append.add_argument('text')
    move = commands.add_parser('move', parents=[common], help='Move it to a status')
    move.add_argument('task')
    move.add_argument('status')
    move.add_argument('--position', type=float)
    for name, what in (('tag', 'tags'), ('assign', 'assignees')):
        command = commands.add_parser(name, parents=[common], help=f'Add or remove {what}')
        command.add_argument('task')
        command.add_argument('--add', action='append', default=[])
        command.add_argument('--remove', action='append', default=[])
    link = commands.add_parser('link', parents=[common], help='Set its Feature and blockers')
    link.add_argument('task')
    link.add_argument('--feature')
    link.add_argument('--no-feature', action='store_true', help='Take it out of its Feature')
    link.add_argument('--blocker', action='append', default=[], help='Add a blocking Task (repeatable)')
    link.add_argument('--unblocker', action='append', default=[], help='Remove a blocking Task (repeatable)')
    nxt = commands.add_parser('next', parents=[common], help="What an agent may take unasked; a Feature's frontier")
    nxt.add_argument('--project', help="Default: this directory's project")
    nxt.add_argument('--feature', help="List this Feature's frontier instead")
    nxt.add_argument('--client', help='As an agent of this client')
    nxt.add_argument('--device', help='As an agent on this device')
    commands.add_parser('handoff', parents=[common], help="Where a Task's work stands").add_argument('task')
    autonomy = commands.add_parser('autonomy', parents=[common], help='What agents take unasked here')
    autonomy.add_argument('value', nargs='?', choices=tasks.AUTONOMY)
    block = commands.add_parser('block', parents=[common], help='Mark it Blocked')
    block.add_argument('task')
    block.add_argument('reason')
    delete = commands.add_parser('delete', parents=[common], help='Delete it here and on every paired device')
    delete.add_argument('task')
    delete.add_argument('--yes', action='store_true', help='Confirm the deletion')
    return parser


def _filters(args) -> dict:
    return tasks_tool.filters(project=args.project, feature=args.feature, tag=args.tag, status=args.status,
                              priority=args.priority, assignee=args.assignee, text=args.text)


def _run(args, workspace: str | None, as_json: bool) -> None:
    command = args.command or 'board'
    user = {'actor': tasks.USER, 'workspace': workspace}
    if command == 'board':
        found = _filters(args) if args.command else {}
        board = tasks.board(workspace=workspace, **found)
        _print(board, as_json, tasks_tool.board_text(board, found.get('project')))
        return
    if command == 'list':
        states = tasks.listing(archived=args.archived, workspace=workspace, **_filters(args))
        _print(states, as_json, tasks_tool.list_text(states))
        return
    if command == 'show':
        state = tasks.get(args.task, workspace=workspace)
        _print(state, as_json, tasks_tool.detail(state))
        return
    if command == 'history':
        events = tasks.history(args.task, workspace=workspace)
        _print(events, as_json, tasks_tool.history_text(events))
        return
    if command == 'next':
        if args.feature:
            feature = tasks.get(args.feature, workspace=workspace)
            states = tasks.frontier(args.feature, workspace=workspace)
            _print(states, as_json, tasks_tool.frontier_text(feature, states))
            return
        from .cli_conversations import derive_project
        project = args.project or derive_project(os.getcwd())
        found = tasks.next_task(project=project, client=args.client, device=args.device, workspace=workspace)
        _print(found, as_json, tasks_tool.next_text(found, project, tasks.autonomy(workspace)))
        return
    if command == 'handoff':
        handoff = tasks.handoff(args.task, workspace=workspace)
        _print(handoff, as_json, tasks_tool.handoff_text(handoff))
        return
    if command == 'autonomy':
        value = tasks.set_autonomy(args.value, workspace=workspace) if args.value else tasks.autonomy(workspace)
        _print({'autonomy': value}, as_json, f'Agents take unasked here: {value}')
        return
    if command == 'create':
        from .cli_conversations import derive_project
        project = args.project or derive_project(os.getcwd())
        if not project:
            raise ValueError('No project found for this directory: pass --project')
        state = tasks.create(args.title, project=project, description=args.description,
                             status='ready' if args.ready else 'backlog', priority=args.priority,
                             acceptance=args.criterion, tags=args.tag, assignees=args.assign,
                             feature=args.feature, blockers=args.blocker, **user)
        _print(state, as_json, f"Created {state['id']}.\n{tasks_tool.detail(state)}")
        return
    if command == 'delete':
        if not args.yes:
            raise ValueError(f'Deleting {tasks.task_ref(args.task)} removes it on every paired device: '
                             'pass --yes to confirm, or cancel it instead')
        gone = tasks.delete(args.task, **user)
        _print(gone, as_json, f"Deleted {gone['id']}.")
        return
    if command == 'edit':
        fields = {k: v for k, v in {'title': args.title, 'description': args.description, 'project': args.project,
                                    'priority': args.priority, 'acceptance': args.criterion,
                                    'position': args.position}.items() if v is not None}
        state = tasks.edit(args.task, **fields, **user)
    elif command == 'append':
        state = tasks.append(args.task, args.text, **user)
    elif command == 'move':
        state = tasks.move(args.task, tasks_tool.status_ref(args.status), position=args.position, **user)
    elif command == 'cancel':
        state = tasks.move(args.task, 'cancelled', **user)
    elif command == 'tag':
        state = tasks.tag(args.task, add=args.add, remove=args.remove, **user)
    elif command == 'assign':
        state = tasks.assign(args.task, add=args.add, remove=args.remove, **user)
    elif command == 'link':
        links = {'add_blockers': args.blocker, 'remove_blockers': args.unblocker}
        if args.no_feature or args.feature:
            links['feature'] = '' if args.no_feature else args.feature
        state = tasks.link(args.task, **links, **user)
    elif command == 'block':
        state = tasks.block(args.task, args.reason, **user)
    elif command == 'release':
        state = tasks.release(args.task, **user)
    else:  # unblock
        state = tasks.unblock(args.task, **user)
    _print(state, as_json, tasks_tool.detail(state))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _utf8_output()
    try:
        _run(args, getattr(args, 'workspace', None) or None, getattr(args, 'json', False))
    except (ValueError, LookupError, PermissionError, OSError) as error:
        print(f'imprint tasks: {error}', file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
