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
    python -m imprint.cli_tasks progress [--project P] [--feature F]
    python -m imprint.cli_tasks autonomy [off|stale|ready]
    python -m imprint.cli_tasks comment TASK TEXT [--to A]...
    python -m imprint.cli_tasks inbox [--read]
    python -m imprint.cli_tasks block TASK REASON
    python -m imprint.cli_tasks unblock|cancel|release TASK
    python -m imprint.cli_tasks delete TASK --yes
    python -m imprint.cli_tasks dispatch on|off|status
    python -m imprint.cli_tasks dispatch run TASK [--client claude-code] [--minutes N] [--review]
    python -m imprint.cli_tasks dispatch stop RUN

FILTERS: --project P --feature F --tag T --status S --priority P --assignee A --holder H --text T.
Every command takes ``--workspace W`` (default: the active workspace) and
``--json``. Changes made here are the user's.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

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
    for name in ('project', 'feature', 'tag', 'status', 'priority', 'assignee', 'holder', 'text'):
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
    progress = commands.add_parser('progress', parents=[common], help='How far each project and Feature is, and how long is left')
    progress.add_argument('--project')
    progress.add_argument('--feature')
    autonomy = commands.add_parser('autonomy', parents=[common], help='What agents take unasked here')
    autonomy.add_argument('value', nargs='?', choices=tasks.AUTONOMY)
    comment = commands.add_parser('comment', parents=[common], help='Comment on it')
    comment.add_argument('task')
    comment.add_argument('text')
    comment.add_argument('--to', action='append', default=[], help='Address it (repeatable): a name, a client, '
                         '"@device", "client@device" or "holder"')
    inbox = commands.add_parser('inbox', parents=[common], help='Unread comments agents addressed to you')
    inbox.add_argument('--read', action='store_true', help='Mark them read')
    block = commands.add_parser('block', parents=[common], help='Mark it Blocked')
    block.add_argument('task')
    block.add_argument('reason')
    delete = commands.add_parser('delete', parents=[common], help='Delete it, leaving only a tombstone')
    delete.add_argument('task')
    delete.add_argument('--yes', action='store_true', help='Confirm the deletion')
    dispatch = commands.add_parser('dispatch', help='Start agent sessions for Tasks (ADR 0220)')
    actions = dispatch.add_subparsers(dest='dispatch_command', required=True)
    run = actions.add_parser('run', parents=[common],
                             help='Start a Dispatched session on a Task now and wait for it to end')
    run.add_argument('task')
    run.add_argument('--client', default=None,
                     help="The client to start (default: the one it is assigned to, else this device's default)")
    run.add_argument('--minutes', type=float, help='Its time limit (default: dispatch.session_minutes)')
    run.add_argument('--review', action='store_true', help='Review a Task in Review instead of working on it')
    roles = actions.add_parser('roles', parents=[common],
                               help="Choose what this device's Dispatcher does for the workspace (ADR 0237)")
    roles.add_argument('roles', help='Comma-separated roles to switch on, the rest off: work, review, refine; '
                       'or none')
    actions.add_parser('on', parents=[common], help="Switch the Work and Review roles on for the workspace")
    actions.add_parser('off', parents=[common], help="Switch every role of this device's Dispatcher off for the "
                       'workspace')
    refine = actions.add_parser('refine', parents=[common],
                                help="Switch the Refine role, Backlog refinement, on or off for the workspace (ADR 0230)")
    refine.add_argument('state', choices=('on', 'off'))
    actions.add_parser('status', parents=[common], help='What the Dispatcher does on this device')
    actions.add_parser('stop', parents=[common], help='Stop a running Dispatched session').add_argument('run')
    return parser


def _filters(args) -> dict:
    return tasks_tool.filters(project=args.project, feature=args.feature, tag=args.tag, status=args.status,
                              priority=args.priority, assignee=args.assignee, holder=args.holder, text=args.text)


def _dispatch_status_text(status: dict) -> str:
    roles = status.get('roles') or []
    lines = [f"Dispatcher for {status['workspace']} on this device: {', '.join(roles) if roles else 'off'}"
             f"{'' if status['server'] or not status['on'] else ' (the dashboard server is not running: nothing is dispatched until it is)'}",
             f"Agents take unasked here: {status['autonomy']}"]
    if status['on'] and status['held']:
        lines.append(f"Starting nothing now: {status['held']}")
    if status['devices']:
        lines.append('Also on: ' + ', '.join(f"{d['name']} ({', '.join(d.get('roles') or [])})"
                                            for d in status['devices']))
    lines.append('Clients:')
    lines += [f"  {c['label']}: {'default' if c['default'] else 'allowed' if c['allowed'] else 'unticked' if c['available'] else c['reason']}"
              f"{' (' + c['version'] + ')' if c['version'] else ''}" for c in status['clients']]
    lines.append('Running:' if status['running'] else 'Running: none')
    lines += [f"  {r['id']}  {r['task']} {r['title']} ({r['client']}, since "
              f"{time.strftime('%H:%M', time.localtime(r['started_at']))})" for r in status['running']]
    if status['next']:
        lines.append('Next to take: ' + ', '.join(s['id'] for s in status['next']))
    lines.append('Projects with open Tasks:' if status['projects'] else 'Projects with open Tasks: none')
    lines += [f"  {p['project']}: {p['path']} ({p['branch'] or 'detached'})" if p['ok'] else f"  {p['project']}: {p['reason']}"
              for p in status['projects']]
    if status['recent']:
        lines.append('Recent:')
        lines += [f"  {r['id']}  {r['task']} {r['outcome'] or 'running'}"
                  f"{': ' + r['detail'] if r['detail'] and r['outcome'] != 'review' else ''}" for r in status['recent']]
    return '\n'.join(lines)


def _refuse_inside_dispatched(action: str) -> None:
    """A Dispatched session never switches the Dispatcher on or starts more
    sessions: only the person does (ADR 0229)."""
    if os.environ.get('IMPRINT_DISPATCH_RUN'):
        raise PermissionError(f'`imprint tasks dispatch {action}` is refused inside a Dispatched session: only the '
                              'person switches the Dispatcher on or starts Dispatched sessions')


def _dispatch(args, workspace: str | None, as_json: bool) -> None:
    from .dispatch import dispatched, guards, loop, status as dispatch_status
    from .workspace_policy import workspace_name
    action, name = args.dispatch_command, workspace_name(workspace)
    if action in ('on', 'run') or (action == 'refine' and args.state == 'on'):
        _refuse_inside_dispatched('refine on' if action == 'refine' else action)
    if action == 'roles':
        wanted = set() if args.roles.strip().lower() == 'none' else \
            {role.strip().lower() for role in args.roles.split(',') if role.strip()}
        from .dispatch import store as dispatch_store
        if wanted - dispatch_store.roles(name):  # switching a role on (ADR 0229)
            _refuse_inside_dispatched(f'roles {args.roles}')
        now = dispatch_status.set_roles(name, wanted)
        shown = [role for role in dispatch_store.ROLES if role in now]
        note = ('' if not now or loop.server_running() else
                ' It dispatches while the dashboard server runs: start it with `imprint ui start`.')
        _print({'workspace': name, 'roles': shown, 'on': bool(now)}, as_json,
               f"Dispatcher for {name} on this device: {', '.join(shown) if shown else 'off'}.{note}")
        return
    if action == 'refine':
        dispatch_status.switch_refining(name, args.state == 'on')
        _print({'workspace': name, 'refine': args.state == 'on'}, as_json,
               f"Backlog refinement for {name} on this device: {args.state}.")
        return
    if action in ('on', 'off'):
        dispatch_status.switch(name, action == 'on')
        note = ('' if action == 'off' or loop.server_running() else
                ' It dispatches while the dashboard server runs: start it with `imprint ui start`.')
        _print({'workspace': name, 'on': action == 'on'}, as_json,
               f"Dispatcher for {name} on this device: {action}.{note}")
        return
    if action == 'status':
        status = dispatch_status.status(name, refresh=True)
        _print(status, as_json, _dispatch_status_text(status))
        return
    if action == 'stop':
        stopped = dispatched.request_stop(args.run)
        _print(stopped, as_json, f"Stopped Dispatched session {stopped['id']} on {stopped['task']}.")
        return

    def started(run):
        if not as_json:
            print(f"Dispatched {run['task']} to {run['client']}: worktree {run['worktree']} on {run['branch']}.\n"
                  f"Its log: {dispatched.store.log_path(run['id'])}\nWaiting for the session to end "
                  '(Ctrl+C stops it)...', flush=True)

    # The person's one-off start, switched on or not, within the same limits as the loop (ADR 0229).
    held = guards.held()
    if held:
        raise PermissionError(f'Not started: {held}')
    from .config_schema import resolve
    limit = max(0, int(resolve('dispatch.concurrency')[0]))
    if len(loop.running_runs()) >= limit:
        raise PermissionError(f'Not started: {limit} Dispatched session{"s" if limit != 1 else ""} already run on this '
                              'device, the limit (dispatch.concurrency)')
    run = dispatched.run(args.task, workspace=workspace, client=args.client, minutes=args.minutes, on_start=started,
                         role='review' if args.review else 'work')
    verdicts = {'review': 'reached Review', 'passed': 'passed its review: Done',
                'returned': 'was sent back to Ready by its review'}
    verdict = verdicts.get(run['outcome']) or f"{run['outcome']}: the session {run['detail']}"
    _print(run, as_json, f"{run['task']} {verdict}.")
    if run['outcome'] not in verdicts:
        raise SystemExit(1)


def _run(args, workspace: str | None, as_json: bool) -> None:
    command = args.command or 'board'
    if command == 'dispatch':
        _dispatch(args, workspace, as_json)
        return
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
    if command == 'progress':
        report = tasks.progress(project=args.project, feature=args.feature, workspace=workspace)
        _print(report, as_json, tasks_tool.progress_text(report))
        return
    if command == 'handoff':
        handoff = tasks.handoff(args.task, workspace=workspace)
        _print(handoff, as_json, tasks_tool.handoff_text(handoff))
        return
    if command == 'inbox':
        items = tasks.user_inbox(mark=args.read, workspace=workspace)
        _print(items, as_json, tasks_tool.inbox_text(items))
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
            raise ValueError(f'Deleting {tasks.task_ref(args.task)} removes its content for good: '
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
        state = tasks.move(args.task, tasks.status_ref(args.status), position=args.position, **user)
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
    elif command == 'comment':
        state = tasks.comment(args.task, args.text, to=args.to, **user)
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
