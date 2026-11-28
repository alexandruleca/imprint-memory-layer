"""The dashboard's /api/tasks: the workspace's Board on the Agents page
(ADRs 0208-0212). Every change here is the user's; the rules are the tasks
module's, which refuses what breaks them."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import config, tasks, tasks_tool

router = APIRouter(prefix="/api/tasks")

EDITABLE = ('title', 'description', 'project', 'priority', 'acceptance', 'position', 'kind')


def _refused(error: Exception) -> JSONResponse:
    status = 404 if isinstance(error, LookupError) else 403 if isinstance(error, PermissionError) else 400
    return JSONResponse({"error": str(error)}, status_code=status)


async def _body(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise ValueError("A JSON object is required.")
    return body


def _list(body: dict, key: str) -> list:
    value = body.get(key) or []
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list")
    return value


def _options(states: list[dict]) -> dict:
    """What the Board's filters offer: every value the Tasks hold."""
    features = {s['feature']: s['feature_title'] for s in states if s.get('feature')}
    features.update({s['id']: s['title'] for s in states if s['kind'] == 'feature'})
    return {
        'projects': sorted({s['project'] for s in states}),
        'tags': sorted({t for s in states for t in s['tags']}),
        'assignees': sorted({a for s in states for a in s['assignees']}),
        'holders': sorted({s['holder']['name'] for s in states if s.get('holder') and s['holder'].get('name')}),
        'features': [{'id': i, 'title': t} for i, t in sorted(features.items(), key=lambda item: item[1].lower())],
    }


@router.get("/board")
def board(project: str = "", feature: str = "", tag: str = "", status: str = "", priority: str = "",
          assignee: str = "", holder: str = "", text: str = "", archived: bool = False):
    """The Board by status, filtered, with the values every filter offers."""
    try:
        found = tasks_tool.filters(project=project, feature=feature, tag=tag, status=status, priority=priority,
                                   assignee=assignee, holder=holder, text=text)
        shown = tasks.listing(archived=archived, **found)
        every = tasks.listing(archived=archived) if found else shown
        workspace = config.get_active_workspace()
        mode = tasks.autonomy()
    except (ValueError, LookupError) as error:
        return _refused(error)
    # A Feature's status follows its Tasks, so it has no column: the Features tab shows it (ADR 0242).
    shown = [state for state in shown if state['kind'] != 'feature']
    columns = {name: [] for name in tasks.STATUSES}
    for state in shown:
        columns[state['status']].append(state)
    return {'workspace': workspace, 'columns': columns, 'total': len(shown), 'options': _options(every),
            'autonomy': mode}


@router.get("/features")
def features(project: str = "", archived: bool = False):
    """Every Feature with its progress and its Tasks in working order, for
    the Features tab (ADR 0242)."""
    try:
        found = tasks.features(project=project or None, archived=archived)
        workspace = config.get_active_workspace()
    except (ValueError, LookupError) as error:
        return _refused(error)
    return {'workspace': workspace, 'features': found}


@router.get("/progress")
def progress(project: str = "", feature: str = ""):
    """How far each project and Feature is, and how long is left at the
    pace of the last 14 days."""
    try:
        return tasks.progress(project=project or None, feature=feature or None)
    except (ValueError, LookupError) as error:
        return _refused(error)


@router.get("/inbox")
def inbox():
    """Comments agents addressed to the user, unread, and the Tasks waiting
    in Review: what the dashboard notifies about (ADR 0212)."""
    active = config.get_active_workspace()
    try:
        # Comments from every workspace, each saying which (a Dispatcher's switch-off or hand-off
        # elsewhere still tells); Tasks in Review from the workspace on show.
        comments = [{**comment, 'workspace': workspace}
                    for workspace in [active, *(w for w in config.get_known_workspaces() if w != active)]
                    for comment in tasks.user_inbox(mark=False, workspace=workspace)]
        review = tasks.listing(status='review')
    except (ValueError, LookupError) as error:
        return _refused(error)
    return {'workspace': active, 'comments': comments, 'review': review}


@router.get("/needs-you")
def needs_you():
    """What waits for the person, each row with its reason (ADR 0241): the
    list on Home and atop the Board."""
    from . import needs_you as listing
    try:
        return listing.rows()
    except (ValueError, LookupError) as error:
        return _refused(error)


@router.post("/needs-you/dismiss")
async def needs_you_dismiss(request: Request):
    """Dismiss one row: a comment is marked read, any other row is hidden
    until its Task changes; the mark reaches paired devices (ADR 0241)."""
    from . import needs_you as listing
    try:
        body = await _body(request)
        workspace = str(body.get('workspace') or '') or None
        return {'dismissed': listing.dismiss(str(body.get('id') or ''), workspace=workspace)}
    except (ValueError, LookupError) as error:
        return _refused(error)


@router.post("/inbox/read")
async def inbox_read(request: Request):
    try:
        body = await _body(request)
        workspace = str(body.get('workspace') or '') or None
        return {'read': tasks.mark_user_read(_list(body, 'ids'), workspace=workspace)}
    except (ValueError, LookupError) as error:
        return _refused(error)


@router.put("/autonomy")
async def set_autonomy(request: Request):
    try:
        body = await _body(request)
        return {'autonomy': tasks.set_autonomy(str(body.get('value') or ''))}
    except ValueError as error:
        return _refused(error)


@router.get("/dispatch")
def dispatch_status(refresh: bool = False):
    """This device's Dispatcher for the active workspace (ADR 0220): the
    Board's strip; ``refresh`` checks the clients again (ADR 0222)."""
    from .dispatch import status
    try:
        return status.status(config.get_active_workspace(), projects=False, refresh=refresh)
    except (ValueError, LookupError, OSError) as error:
        return _refused(error)


@router.put("/dispatch")
async def dispatch_switch(request: Request):
    """Switch this device's Dispatcher for the active workspace; the person's
    choice, made on the Board (agents have no tool for it). ``roles`` sets
    exactly the roles on (ADR 0237); ``on`` and ``refine`` are the older
    switches, kept as shortcuts."""
    from .dispatch import status
    try:
        body = await _body(request)
        given = [key for key in ('on', 'refine') if key in body]
        if any(not isinstance(body[key], bool) for key in given):
            raise ValueError('on and refine are true or false')
        roles = body.get('roles')
        if roles is not None and (not isinstance(roles, list) or not all(isinstance(r, str) for r in roles)):
            raise ValueError('roles is a list of role names')
        if not given and roles is None:
            raise ValueError('give roles, on or refine')
        workspace = config.get_active_workspace()
        if roles is not None:
            status.set_roles(workspace, roles)
        if 'on' in body:
            status.switch(workspace, body['on'])
        if 'refine' in body:
            status.switch_refining(workspace, body['refine'])  # Backlog refinement (ADR 0230)
        return status.status(workspace, projects=False)
    except (ValueError, LookupError, OSError) as error:
        return _refused(error)


@router.post("/dispatch/{run_id}/stop")
def dispatch_stop(run_id: str):
    """Stop a running Dispatched session, as `imprint tasks dispatch stop` does."""
    from .dispatch import dispatched
    try:
        return {'run': dispatched.request_stop(run_id)}
    except (ValueError, LookupError) as error:
        return _refused(error)


@router.post("")
async def create(request: Request):
    try:
        body = await _body(request)
        made = tasks.create(str(body.get('title') or ''), project=str(body.get('project') or ''),
                            description=str(body.get('description') or ''),
                            status=tasks.status_ref(body['status']) if body.get('status') else 'backlog',
                            priority=str(body.get('priority') or 'P2'), acceptance=_list(body, 'acceptance'),
                            tags=_list(body, 'tags'), assignees=_list(body, 'assignees'),
                            feature=str(body.get('feature') or '') or None, blockers=_list(body, 'blockers'),
                            kind=str(body.get('kind') or 'task'))
    except (ValueError, LookupError, PermissionError) as error:
        return _refused(error)
    return {'task': made}


@router.get("/{task_id}")
def details(task_id: str):
    """One Task, its history, its handoff and the Tasks it links to."""
    try:
        return {'task': tasks.get(task_id), 'history': tasks.history(task_id), 'handoff': tasks.handoff(task_id),
                'related': tasks.related(task_id)}
    except (ValueError, LookupError) as error:
        return _refused(error)


@router.delete("/{task_id}")
def delete(task_id: str):
    try:
        return tasks.delete(task_id)
    except (ValueError, LookupError, PermissionError) as error:
        return _refused(error)


@router.post("/{task_id}/{action}")
async def change(task_id: str, action: str, request: Request):
    """move, edit, append, comment, tags, assignees, link, block, unblock or
    release, as the user."""
    try:
        body = await _body(request) if action not in ('unblock', 'release') else {}
        if action == 'move':
            position = body.get('position')
            state = tasks.move(task_id, tasks.status_ref(str(body.get('status') or '')),
                               position=float(position) if position is not None else None)
        elif action == 'edit':
            fields = {key: body[key] for key in EDITABLE if key in body}
            state = tasks.edit(task_id, **fields)
        elif action == 'append':
            state = tasks.append(task_id, str(body.get('text') or ''))
        elif action == 'comment':
            state = tasks.comment(task_id, str(body.get('text') or ''), to=_list(body, 'to'))
            # Replying reads what was written to the person on this Task (ADR 0241).
            unread = [c['id'] for c in tasks.user_inbox(mark=False) if c['task'] == state['id']]
            if unread:
                tasks.mark_user_read(unread)
        elif action == 'tags':
            state = tasks.tag(task_id, add=_list(body, 'add'), remove=_list(body, 'remove'))
        elif action == 'assignees':
            state = tasks.assign(task_id, add=_list(body, 'add'), remove=_list(body, 'remove'))
        elif action == 'link':
            links = {'add_blockers': _list(body, 'add_blockers'), 'remove_blockers': _list(body, 'remove_blockers')}
            if 'feature' in body:
                links['feature'] = str(body.get('feature') or '')
            state = tasks.link(task_id, **links)
        elif action == 'block':
            state = tasks.block(task_id, str(body.get('reason') or ''))
        elif action == 'unblock':
            state = tasks.unblock(task_id)
        elif action == 'release':
            state = tasks.release(task_id)
        else:
            return JSONResponse({"error": f"Unknown action {action!r}"}, status_code=404)
    except (ValueError, LookupError, PermissionError) as error:
        return _refused(error)
    return {'task': state}
