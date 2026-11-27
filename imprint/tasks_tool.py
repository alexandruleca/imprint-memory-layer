"""The Board through Imprint's MCP server: what the ``tasks`` and
``task_edit`` tools reply (ADR 0208). The tools' functions in ``server.py``
pass the session's project and client; everything else is here, so the CLI
and the tools word the Board the same way."""

from __future__ import annotations

import math
import os
import time

from . import tasks

STATUS_LABELS = tasks.STATUS_LABELS
COLUMN_LINES = 20


def session() -> str:
    """The agent session this MCP server serves (ADR 0210): the client's own
    conversation id where it passes one (Claude Code's), else this process."""
    conversation = os.environ.get('CLAUDE_CODE_SESSION_ID') or os.environ.get('IMPRINT_CLIENT_SESSION')
    if conversation:
        return tasks.sessions.conversation(conversation)
    from . import agent_activity
    return tasks.sessions.process(agent_activity.current().get('session') or os.getpid())


def actor(client: str | None = None, workspace: str = '') -> dict:
    """The agent session this MCP server serves, as the actor of its
    changes, with the name it chose on the workspace's Board."""
    from . import agent_activity
    me = session()
    try:
        name = tasks.name_of(me, workspace=workspace or None)
    except ValueError:
        name = None
    return tasks.agent(me, name=name, client=tasks.client_id(client or agent_activity.current().get('client')))


def choose_name(name: str, *, actor: dict, workspace: str = '') -> str:
    chosen = tasks.choose_name(actor['session'], name, client=actor.get('client'), workspace=workspace or None)
    _mirror(actor['session'], chosen)
    return (f'You are {chosen} on this Board: your claims, checkpoints and comments show it, and others '
            f'address you as @{chosen}.')


def _mirror(board_session: str, name: str) -> None:
    """Show the name on the dashboard's Agents page."""
    try:
        from . import agent_activity
        agent_activity.set_board(board_session, name)
    except Exception:
        pass


def _named(actor: dict, workspace: str) -> tuple[dict, str]:
    """The actor with a name, giving it a fallback when it has none, and
    what to tell it about that."""
    if actor.get('name'):
        return actor, ''
    name = tasks.ensure_name(actor['session'], client=actor.get('client'), workspace=workspace or None)
    _mirror(actor['session'], name)
    return {**actor, 'name': name}, (f'You had no name, so you are {name} on this Board; choose your own with '
                                     'task_edit(action="name", name="…").\n')


# ── The holder's Imprint keeps its claims alive ──────────────────

_activity = {'at': None}


def touch() -> None:
    """The session just called Imprint: activity that counts as progress."""
    _activity['at'] = time.time()


def renew_now() -> int:
    return tasks.renew(session(), active_at=_activity['at'])


def start_renewing(interval: float = 60) -> None:
    """Renew this session's leases now and then every ``interval`` seconds,
    for as long as the process runs."""
    import threading

    def loop():
        while True:
            try:
                renew_now()
            except Exception:  # A busy or missing store must not end the renewals.
                pass
            time.sleep(interval)
    threading.Thread(target=loop, name='imprint-task-leases', daemon=True).start()


def project(given: str = '', session_project: str | None = None) -> str:
    """The project a new Task belongs to: the one named, else the session's."""
    if given.strip():
        return given.strip()
    if session_project:
        return session_project
    from .cli_conversations import derive_project
    found = derive_project(os.getcwd())
    if not found:
        raise ValueError('No project found for this directory: name the project')
    return found


NOTICE_COMMENTS = 3
NOTICE_CHARS = 300


def notices(workspace: str | None = None, client: str | None = None) -> str:
    """What this session should hear on its next reply, each thing once:
    claims it lost, new comments addressed to it, and its claims that are
    Stalling."""
    me = actor(client, workspace or '')
    workspace = workspace or None
    lines = []
    lost = tasks.lost_claims(me['session'], workspace=workspace)
    told = set(tasks.take_unseen(me['session'], [f"lost:{item['claim']}" for item in lost], workspace=workspace))
    lines += [f"You lost your claim on {item['task']} ({item['title']}) to {tasks.label(item['to'])}, "
              'who claimed it first on another device. Stop working on it.'
              for item in lost if f"lost:{item['claim']}" in told]
    unread = tasks.inbox(me['session'], name=me.get('name'), client=me.get('client'), device=tasks.device_name(),
                         mark=False, workspace=workspace)
    told = set(tasks.take_unseen(me['session'], [f"notified:{item['id']}" for item in unread], workspace=workspace))
    fresh = [item for item in unread if f"notified:{item['id']}" in told]
    for item in fresh[:NOTICE_COMMENTS]:
        text = item['text'] if len(item['text']) <= NOTICE_CHARS else item['text'][:NOTICE_CHARS] + '…'
        lines.append(f"New comment on {item['task']} ({item['title']}) from {_who(item['by'])}: {text}")
    if len(fresh) > NOTICE_COMMENTS:
        lines.append(f'And {len(fresh) - NOTICE_COMMENTS} more: tasks(action="inbox").')
    held = [state for state in tasks.listing(workspace=workspace)
            if (state.get('holder') or {}).get('session') == me['session'] and state.get('stalling')]
    keys = {f"stalling:{state['id']}:{int(state['holder']['progress_at'])}": state for state in held}
    for key in tasks.take_unseen(me['session'], list(keys), workspace=workspace):
        state = keys[key]
        lines.append(f"{state['id']} has had no progress from you for a while: record a checkpoint with "
                     f'task_edit(action="checkpoint", task="{state["id"]}", text="…"), or release it if you stopped.')
    return '\n'.join(lines)


def inbox_text(items: list[dict]) -> str:
    if not items:
        return 'No unread comments.'
    return '\n'.join(f"{item['task']} ({item['title']}), {_when(item['at'])}, from {_who(item['by'])}"
                     f"{' to ' + ', '.join(item['to']) if item['to'] else ''}: {item['text']}" for item in items)


SESSION_START_LINES = 6


def session_start(cwd: str, workspace: str, conversation: str | None, project_name: str | None = None,
                  client: str | None = None) -> str:
    """The Board's part of the session-start context: the project's Ready
    and Stale Tasks, the session's unread comments, its name, and what it
    may take unasked. Nothing when the project has no Tasks."""
    if not board_exists(workspace):
        return ''
    if not project_name:
        from .cli_conversations import derive_project
        project_name = derive_project(cwd) if cwd else ''
    if not project_name:
        return ''
    states = tasks.listing(project=project_name, workspace=workspace)
    if not states:
        return ''
    open_ones = [s for s in states if s['status'] == 'ready' or s.get('stale')]
    counts = {label: sum(1 for s in states if test(s)) for label, test in (
        ('Ready', lambda s: s['status'] == 'ready'), ('Stale', lambda s: s.get('stale')),
        ('in progress', lambda s: s['status'] == 'in_progress' and not s.get('stale')),
        ('in review', lambda s: s['status'] == 'review'))}
    summary = ', '.join(f'{n} {label}' for label, n in counts.items() if n) or 'nothing open'
    lines = [f"Imprint Board, workspace {workspace}, project {project_name}: {summary}."]
    lines += [f'- {line(state)}' for state in open_ones[:SESSION_START_LINES]]
    me = tasks.sessions.conversation(conversation) if conversation else None
    name = tasks.name_of(me, workspace=workspace) if me else None
    if me:
        unread = tasks.inbox(me, name=name, client=tasks.client_id(client), device=tasks.device_name(), mark=False,
                             workspace=workspace)
        if unread:
            lines.append(f"You have {len(unread)} unread comment{'s' if len(unread) != 1 else ''}: tasks(action=\"inbox\").")
    lines.append(f'You are {name} on this Board.' if name else
                 'Choose the name you go by on the Board with task_edit(action="name", name="…") before your '
                 'first claim or comment.')
    mode = tasks.autonomy(workspace)
    if mode != 'off' and open_ones:
        lines.append(f'When you have finished what you were asked, you may take the next Task: tasks(action="next") '
                     f'(autonomy: {mode}).')
    return '\n'.join(lines)


def board_exists(workspace: str) -> bool:
    from . import config
    try:
        return config.tasks_db_path(workspace).exists()
    except ValueError:
        return False


# ── Wording ──────────────────────────────────────────────────────

def _who(person: dict) -> str:
    if person.get('kind') == 'user':
        return 'the user'
    if person.get('kind') == 'system':
        return 'Imprint'
    name = person.get('name') or 'an agent'
    client = person.get('client')
    return f'{name} ({client})' if client else name


def _when(seconds: float) -> str:
    return time.strftime('%Y-%m-%d %H:%M', time.localtime(seconds))


def _ago(seconds: float) -> str:
    minutes = max(0, int((time.time() - seconds) // 60))
    return f'{minutes} min ago' if minutes < 120 else f'{minutes // 60} h ago'


def line(state: dict) -> str:
    """One Task on one line: id, priority, title, project, tags, assignees and
    what holds it up."""
    text = f"{state['id']} {state['priority']} {state['title']} · {state['project']}"
    if state.get('tags'):
        text += ' ' + ' '.join(f'#{tag}' for tag in state['tags'])
    if state.get('assignees'):
        text += ' → ' + ', '.join(state['assignees'])
    if state.get('holder'):
        text += f" [{'stalling, ' if state.get('stalling') else ''}held by {tasks.label(state['holder'])}]"
    elif state.get('stale'):
        text += f" [stale: {tasks.label(state['previous_holder'])} stopped]"
    if state.get('blocked'):
        text += ' [blocked]'
    if state.get('open_blockers'):
        text += f" [waits on {', '.join(state['open_blockers'])}]"
    return text


def detail(state: dict) -> str:
    lines = [f"{state['id']} · {state['title']}",
             f"Status: {STATUS_LABELS[state['status']]} · Priority: {state['priority']} · Project: {state['project']}"]
    if state.get('feature'):
        lines.append(f"Feature: {state['feature']} {state.get('feature_title') or ''}".rstrip())
    if state.get('tasks'):
        lines.append('Tasks: ' + ', '.join(state['tasks']))
    if state.get('tags'):
        lines.append('Tags: ' + ', '.join(state['tags']))
    if state.get('assignees'):
        lines.append('Assignees: ' + ', '.join(state['assignees']))
    if state.get('blockers'):
        open_ones = set(state.get('open_blockers') or [])
        lines.append('Blocked by: ' + ', '.join(f"{b} ({'open' if b in open_ones else 'done'})"
                                                for b in state['blockers']))
    if state.get('blocked'):
        lines.append(f"Blocked: {state['blocked']['reason']} ({_who(state['blocked']['by'])})")
    holder = state.get('holder')
    if holder:
        lines.append(f"Held by {tasks.label(holder)} on {holder['device']} since {_when(holder['since'])}, "
                     f"last progress {_ago(holder['progress_at'])}")
        if state.get('stalling'):
            lines.append('Stalling: no progress for a while; checkpoint, or release it if you stopped')
    elif state.get('stale'):
        lines.append(f"Stale: {tasks.label(state['previous_holder'])} stopped working on it; "
                     'take it over with task_edit(action="claim")')
    checkpoint = state.get('last_checkpoint')
    if checkpoint:
        where = ' @ '.join(part for part in (checkpoint.get('branch'), checkpoint.get('commit')) if part)
        lines.append(f"Last checkpoint ({_who(checkpoint['by'])}, {_when(checkpoint['at'])}"
                     f"{', ' + where if where else ''}): {checkpoint['text']}")
        if checkpoint.get('next'):
            lines.append(f"Next: {checkpoint['next']}")
        if checkpoint.get('files'):
            lines.append('Files: ' + ', '.join(checkpoint['files'][:20]))
    if state.get('worked_by') and state['status'] == 'review':
        lines.append('Waiting for review by the user or another session')
    lines.append(f"Created by {_who(state['created_by'])}, {_when(state['created_at'])}")
    if state.get('ended_at'):
        lines.append(f"Ended {_when(state['ended_at'])}")
    if state.get('acceptance'):
        lines.append('Acceptance criteria:')
        lines.extend(f'- {criterion}' for criterion in state['acceptance'])
    if state.get('description'):
        lines.extend(['Description:', state['description']])
    comments = state.get('comments') or []
    if comments:
        more = state.get('comment_count', len(comments)) - len(comments[-5:])
        lines.append(f"Comments{f' (last 5 of {len(comments[-5:]) + more})' if more > 0 else ''}:")
        lines.extend(f"- {_when(c['at'])} {_who(c['by'])}{' to ' + ', '.join(c['to']) if c['to'] else ''}: {c['text']}"
                     for c in comments[-5:])
    return '\n'.join(lines)


def board_text(board: dict, project: str | None = None) -> str:
    total = sum(len(column) for column in board['columns'].values())
    scope = f" (project {project})" if project else ''
    if not total:
        return (f"No tasks yet on workspace {board['workspace']}'s Board{scope}. "
                'Create one with task_edit(action="create", title="...").')
    lines = [f"Board of workspace {board['workspace']}{scope}: {total} tasks"]
    for status, column in board['columns'].items():
        lines.append(f'{STATUS_LABELS[status]} ({len(column)})')
        lines.extend(f'- {line(state)}' for state in column[:COLUMN_LINES])
        if len(column) > COLUMN_LINES:
            lines.append(f'- and {len(column) - COLUMN_LINES} more: tasks(action="list", status="{status}")')
    return '\n'.join(lines)


def list_text(states: list[dict]) -> str:
    if not states:
        return 'No tasks match.'
    return '\n'.join(line(state) for state in states)


def history_text(events: list[dict]) -> str:
    lines = []
    for event in events:
        who = ('the user' if event['actor'] == 'user' else 'Imprint' if event['actor'] == 'system'
               else (event.get('agent') or event.get('client') or 'an agent'))
        data = {k: v for k, v in event['data'].items() if k != 'id'}
        lines.append(f"{_when(event['recorded_at'])} {event['kind']} by {who}" + (f': {data}' if data else ''))
    return '\n'.join(lines)


# ── The tools ────────────────────────────────────────────────────

def filters(*, project='', feature='', tag='', status='', priority='', assignee='', holder='', text='') -> dict:
    """The filters given, in the tasks module's form."""
    return {key: value for key, value in {
        'project': project or None, 'feature': feature or None, 'tag': tag or None,
        'status': tasks.status_ref(status) if status else None, 'priority': priority or None,
        'assignee': assignee or None, 'holder': holder or None, 'text': text or None}.items() if value}


def frontier_text(feature: dict, states: list[dict]) -> str:
    head = f"Frontier of {feature['id']} ({feature['title']}): {len(states)} to take now"
    return '\n'.join([head, *(f'- {line(state)}' for state in states)])


def next_text(state: dict | None, project: str, mode: str) -> str:
    if state is None:
        return (f'Nothing on the Board for you to take unasked in project {project} (autonomy: {mode}). '
                'Ask the user, or look at tasks(action="board").')
    task = state['id']
    start_from = (f'start from its handoff: tasks(action="handoff", task="{task}")' if state.get('stale')
                  else 'work to its acceptance criteria')
    return (f"Take {task} next: {line(state)}\n"
            f'Claim it with task_edit(action="claim", task="{task}"), say so in your reply, and {start_from}.\n'
            + detail(state))


def estimate_text(days_left: float | None) -> str:
    """How long is left, in words, from ``tasks.progress``."""
    if days_left is None:
        return 'not enough finished work to estimate yet'
    if days_left == 0:
        return 'finished'
    if days_left < 1:
        return 'less than a day left'
    days = math.ceil(days_left)
    return f"about {days} day{'s' if days != 1 else ''} left"


def _progress_line(entry: dict) -> str:
    share = round(100 * entry['done'] / entry['total']) if entry['total'] else 0
    parts = [f"{entry['done']} of {entry['total']} done ({share}%)"]
    counts = entry['counts']
    parts += [f'{counts[status]} {label}' for status, label in (('in_progress', 'in progress'), ('review', 'in review'))
              if counts[status]]
    if entry['blocked']:
        parts.append(f"{entry['blocked']} blocked")
    if entry['stale']:
        parts.append(f"{entry['stale']} stale")
    parts.append(estimate_text(entry['days_left']))
    return ' · '.join(parts)


def progress_text(report: dict) -> str:
    if not report['projects'] and not report['features']:
        return 'No tasks to measure yet.'
    lines = []
    if report['projects']:
        lines.append('Projects:')
        lines += [f"- {p['name']}: {_progress_line(p)}" for p in report['projects']]
    if report['features']:
        lines.append('Features:')
        lines += [f"- {f['id']} {f['title']} ({f['project']}): {_progress_line(f)}" for f in report['features']]
    lines.append('Estimates follow the pace of the last 14 days, once 3 Tasks were finished in them.')
    return '\n'.join(lines)


def handoff_text(handoff: dict) -> str:
    state = handoff['task']
    lines = [f"Handoff for {state['id']} · {state['title']}"]
    previous = handoff['previous_holder']
    if previous:
        lines.append(f"Previous holder: {tasks.label(previous)} on {previous.get('device')}, since {_when(previous['since'])}")
    else:
        lines.append('Nobody has held it yet.')
    last = handoff['last_checkpoint']
    if last:
        lines.append(f"Last checkpoint ({_when(last['at'])}): {last['text']}")
        if last.get('next'):
            lines.append(f"Next: {last['next']}")
    if handoff['branch'] or handoff['commit']:
        lines.append('Work: ' + ' @ '.join(p for p in (handoff['branch'], handoff['commit']) if p))
    if handoff['files']:
        lines.append('Files: ' + ', '.join(handoff['files'][:30]))
    earlier = handoff['checkpoints'][:-1]
    if earlier:
        lines.append('Earlier checkpoints:')
        lines.extend(f"- {_when(c['at'])}: {c['text']}" for c in earlier)
    if handoff['conversation']:
        lines.append(f'Conversation: read_source(source="{handoff["conversation"]}", project="{state["project"]}"), '
                     'if Imprint has learned it')
    lines.append(f'Take it over with task_edit(action="claim", task="{state["id"]}").')
    lines.extend(['', detail(state)])
    return '\n'.join(lines)


def read(action: str, *, task: str = '', archived: bool = False, workspace: str = '', session_project: str | None = None,
         client: str | None = None, **given) -> str:
    workspace = workspace or None
    if action == 'progress':
        return progress_text(tasks.progress(project=given.get('project') or None, feature=given.get('feature') or None,
                                            workspace=workspace))
    if action == 'inbox':
        me = actor(client, workspace or '')
        return inbox_text(tasks.inbox(me['session'], name=me.get('name'), client=me.get('client'),
                                      device=tasks.device_name(), workspace=workspace))
    if action == 'next':
        if given.get('feature'):
            feature = tasks.get(given['feature'], workspace=workspace)
            return frontier_text(feature, tasks.frontier(given['feature'], workspace=workspace))
        where = project(given.get('project') or '', session_project)
        found = tasks.next_task(project=where, client=tasks.client_id(client), device=tasks.device_name(), workspace=workspace)
        return next_text(found, where, tasks.autonomy(workspace))
    found = filters(**given)
    if action == 'board':
        return board_text(tasks.board(workspace=workspace, **found), found.get('project'))
    if action == 'list':
        return list_text(tasks.listing(archived=archived, workspace=workspace, **found))
    if not task:
        raise ValueError(f'Name the task: task="T-…"')
    if action == 'get':
        return detail(tasks.get(task, workspace=workspace))
    if action == 'history':
        return history_text(tasks.history(task, workspace=workspace))
    if action == 'handoff':
        return handoff_text(tasks.handoff(task, workspace=workspace))
    raise ValueError(f'Unknown action {action!r}')


def create(*, title: str, project: str, description: str = '', status: str = '', priority: str = '',
           acceptance=None, tags=None, assignees=None, feature: str = '', blockers=None, actor: dict,
           workspace: str = '') -> str:
    made = tasks.create(title, project=project, description=description or '',
                        status=tasks.status_ref(status) if status else 'backlog', priority=priority or 'P2',
                        acceptance=acceptance or (), tags=tags or (), assignees=assignees or (),
                        feature=feature or None, blockers=blockers or (), actor=actor, workspace=workspace or None)
    return f"Created {made['id']}.\n{detail(made)}"


def change(action: str, task: str, *, actor: dict, workspace: str = '', title=None, description=None,
           project=None, status='', priority=None, position=None, acceptance=None, feature=None,
           add=None, remove=None, text='', token='', branch='', commit='', files=None, next_step='', to=None) -> str:
    """Every task_edit action but create, on one existing Task."""
    if not task:
        raise ValueError('Name the task: task="T-…"')
    where = {'actor': actor, 'workspace': workspace or None}
    if action == 'claim':
        actor, told = _named(actor, workspace)
        claimed = tasks.claim(task, actor=actor, token=token or None, workspace=workspace or None)
        state = claimed['task']
        if not claimed['token']:
            return f"{told}You already hold {state['id']}.\n{detail(state)}"
        return (f"{told}Claimed {state['id']}. Your claim token: {claimed['token']}\n"
                'Keep the token in this conversation, not on the Board; if your session restarts, take the '
                f'claim back with task_edit(action="claim", task="{state["id"]}", token="…"). Record a '
                'checkpoint at each step and at least every 30 minutes.\n' + detail(state))
    if action == 'comment':
        actor, told = _named(actor, workspace)
        state = tasks.comment(task, text, to=to or (), **{**where, 'actor': actor})
        return told + detail(state)
    if action == 'checkpoint':
        state = tasks.checkpoint(task, text, branch=branch or None, commit=commit or None, files=files or (),
                                 next_step=next_step or None, **where)
    elif action == 'release':
        state = tasks.release(task, status=tasks.status_ref(status) if status else None, **where)
    elif action == 'edit':
        fields = {k: v for k, v in {'title': title, 'description': description, 'project': project,
                                    'priority': priority, 'acceptance': acceptance, 'position': position}.items()
                  if v is not None}
        state = tasks.edit(task, **fields, **where)
    elif action == 'append':
        state = tasks.append(task, text, **where)
    elif action == 'move':
        if not status:
            raise ValueError('Name the status to move to')
        state = tasks.move(task, tasks.status_ref(status), position=position, **where)
    elif action == 'cancel':
        state = tasks.move(task, 'cancelled', **where)
    elif action == 'tag':
        state = tasks.tag(task, add=add or (), remove=remove or (), **where)
    elif action == 'assign':
        state = tasks.assign(task, add=add or (), remove=remove or (), **where)
    elif action == 'link':
        links = {'add_blockers': add or (), 'remove_blockers': remove or ()}
        if feature is not None:
            links['feature'] = feature
        state = tasks.link(task, **links, **where)
    elif action == 'block':
        state = tasks.block(task, text, **where)
    elif action == 'unblock':
        state = tasks.unblock(task, **where)
    else:
        raise ValueError(f'Unknown action {action!r}')
    return detail(state)
