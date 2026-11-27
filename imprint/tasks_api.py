"""The dashboard's /api/tasks: the workspace's Board on the Agents page
(ADRs 0208-0212). Every change here is the user's; the rules are the tasks
module's, which refuses what breaks them."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import config, tasks, tasks_tool

router = APIRouter(prefix="/api/tasks")

EDITABLE = ('title', 'description', 'project', 'priority', 'acceptance', 'position')


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
    features.update({s['id']: s['title'] for s in states if s.get('tasks')})
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
    columns = {name: [] for name in tasks.STATUSES}
    for state in shown:
        columns[state['status']].append(state)
    return {'workspace': workspace, 'columns': columns, 'total': len(shown), 'options': _options(every),
            'autonomy': mode}


@router.get("/inbox")
def inbox():
    """Comments agents addressed to the user, unread, and the Tasks waiting
    in Review: what the dashboard notifies about (ADR 0212)."""
    try:
        comments = tasks.user_inbox(mark=False)
        review = tasks.listing(status='review')
    except (ValueError, LookupError) as error:
        return _refused(error)
    return {'comments': comments, 'review': review}


@router.post("/inbox/read")
async def inbox_read(request: Request):
    try:
        body = await _body(request)
        return {'read': tasks.mark_user_read(_list(body, 'ids'))}
    except (ValueError, LookupError) as error:
        return _refused(error)


@router.put("/autonomy")
async def set_autonomy(request: Request):
    try:
        body = await _body(request)
        return {'autonomy': tasks.set_autonomy(str(body.get('value') or ''))}
    except ValueError as error:
        return _refused(error)


@router.post("")
async def create(request: Request):
    try:
        body = await _body(request)
        made = tasks.create(str(body.get('title') or ''), project=str(body.get('project') or ''),
                            description=str(body.get('description') or ''),
                            status=tasks_tool.status_ref(body['status']) if body.get('status') else 'backlog',
                            priority=str(body.get('priority') or 'P2'), acceptance=_list(body, 'acceptance'),
                            tags=_list(body, 'tags'), assignees=_list(body, 'assignees'),
                            feature=str(body.get('feature') or '') or None, blockers=_list(body, 'blockers'))
    except (ValueError, LookupError, PermissionError) as error:
        return _refused(error)
    return {'task': made}


@router.get("/{task_id}")
def details(task_id: str):
    """One Task, its history and its handoff."""
    try:
        return {'task': tasks.get(task_id), 'history': tasks.history(task_id), 'handoff': tasks.handoff(task_id)}
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
            state = tasks.move(task_id, tasks_tool.status_ref(str(body.get('status') or '')),
                               position=float(position) if position is not None else None)
        elif action == 'edit':
            fields = {key: body[key] for key in EDITABLE if key in body}
            state = tasks.edit(task_id, **fields)
        elif action == 'append':
            state = tasks.append(task_id, str(body.get('text') or ''))
        elif action == 'comment':
            state = tasks.comment(task_id, str(body.get('text') or ''), to=_list(body, 'to'))
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
