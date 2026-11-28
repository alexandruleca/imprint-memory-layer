"""A Task's state, computed from its events in clock order (ADR 0209). Each kind of event changes the
state it is applied to; for a field the last edit wins, appended text and comments accumulate. Rules
are checked when a change is made, not here: an event from a paired device is applied as written."""

from __future__ import annotations

from .actors import _who


def _created(state: dict | None, event: dict) -> dict:
    data = event['data']
    return {
        'uid': event['task'], 'id': data['id'], 'title': data['title'],
        'description': data.get('description', ''), 'appended': [], 'project': data['project'],
        'status': data.get('status', 'backlog'), 'priority': data.get('priority', 'P2'), 'position': None,
        'acceptance': list(data.get('acceptance', [])), 'tags': list(data.get('tags', [])),
        'assignees': list(data.get('assignees', [])), 'feature': data.get('feature'),
        'blockers': list(data.get('blockers', [])), 'blocked': None,
        'holder': None, 'previous_holder': None, 'worked_by': None, 'lost_claims': [],
        'last_checkpoint': None, 'checkpoints': 0, 'comments': [], 'comment_count': 0,
        'created_at': event['recorded_at'], 'created_hlc': event['hlc'], 'created_by': _who(event),
        'updated_at': event['recorded_at'], 'ended_at': None, 'status_at': event['recorded_at'], 'former_ids': [],
    }


_EDITABLE = ('title', 'description', 'project', 'priority', 'acceptance', 'position')


def _edited(state: dict, event: dict) -> dict:
    for field, value in event['data'].items():
        if field in _EDITABLE:
            state[field] = value
            if field == 'description':
                state['appended'] = []  # A replacement was written seeing what was appended before it.
    return state


def _appended(state: dict, event: dict) -> dict:
    state['appended'].append(event['data']['text'])
    return state


def _short(holder: dict) -> dict:
    return {key: holder[key] for key in ('session', 'name', 'client', 'device', 'since') if key in holder}


def _end_claim(state: dict) -> None:
    holder = state['holder']
    if holder:
        state['worked_by'] = holder['session']
        state['previous_holder'] = _short(holder)
        state['holder'] = None


def _moved(state: dict, event: dict) -> dict:
    status = event['data']['status']
    if status in ('review', 'done', 'cancelled'):
        if state['holder']:
            _end_claim(state)
        elif status == 'review' and event['actor'] == 'agent':
            state['worked_by'] = event['session']
    if status == 'review' and event['actor'] == 'agent':
        # The session that hands the work in counts as having done it too, even
        # when another session held the claim (a Dispatched worker that never
        # took the Dispatcher's claim over), so it cannot also mark it Done.
        state['submitted_by'] = event['session']
    if status in ('done', 'cancelled') and state['status'] not in ('done', 'cancelled'):
        state['ended_at'] = event['recorded_at']
    elif status not in ('done', 'cancelled'):
        state['ended_at'] = None
    if status != state['status']:
        state['status_at'] = event['recorded_at']
    state['status'] = status
    if 'position' in event['data']:
        state['position'] = event['data']['position']
    return state


def _set_changes(values: list, data: dict) -> list:
    values = [v for v in values if v not in data.get('remove', [])]
    return values + [v for v in data.get('add', []) if v not in values]


def _tagged(state: dict, event: dict) -> dict:
    state['tags'] = _set_changes(state['tags'], event['data'])
    return state


def _assigned(state: dict, event: dict) -> dict:
    state['assignees'] = _set_changes(state['assignees'], event['data'])
    return state


def _linked(state: dict, event: dict) -> dict:
    data = event['data']
    if 'feature' in data:
        state['feature'] = data['feature']
    state['blockers'] = _set_changes(state['blockers'], {'add': data.get('add_blockers', []),
                                                         'remove': data.get('remove_blockers', [])})
    return state


def _blocked(state: dict, event: dict) -> dict:
    state['blocked'] = {'reason': event['data']['reason'], 'by': _who(event), 'at': event['recorded_at']}
    return state


def _unblocked(state: dict, event: dict) -> dict:
    state['blocked'] = None
    return state


def _claimed(state: dict, event: dict) -> dict:
    """A claim takes a free Task, or replaces the claim it names (a Stale
    holder's, or its own after a restart). Any other claim lost: of two
    made on two devices before they synced, the earlier one holds."""
    data, holder = event['data'], state['holder']
    if holder and data.get('replaces') != holder['claim']:
        state['lost_claims'].append({'claim': event['id'], 'session': event['session'], 'name': event['agent'],
                                     'client': event['client'], 'device': event['device'],
                                     'at': event['recorded_at'], 'to': _short(holder)})
        return state
    if holder:
        state['previous_holder'] = _short(holder)
    state['holder'] = {'claim': event['id'], 'session': event['session'], 'name': event['agent'],
                       'client': event['client'], 'device': event['device'], 'since': event['recorded_at'],
                       'token_hash': data.get('token_hash'), 'progress_at': event['recorded_at']}
    if state['status'] in ('backlog', 'ready'):
        state['status'], state['status_at'] = 'in_progress', event['recorded_at']
    return state


def _released(state: dict, event: dict) -> dict:
    holder = state['holder']
    if holder and holder['claim'] == event['data'].get('claim'):
        state['previous_holder'] = _short(holder)
        state['holder'] = None
        if event['data'].get('status') and event['data']['status'] != state['status']:
            state['status'], state['status_at'] = event['data']['status'], event['recorded_at']
    return state


def _checkpoint(state: dict, event: dict) -> dict:
    state['last_checkpoint'] = {**event['data'], 'by': _who(event), 'at': event['recorded_at']}
    state['checkpoints'] += 1
    return state


COMMENTS_KEPT = 20


def _commented(state: dict, event: dict) -> dict:
    comment = {'id': event['id'], 'text': event['data']['text'], 'to': list(event['data'].get('to', [])),
               'by': _who(event), 'at': event['recorded_at']}
    state['comments'] = (state['comments'] + [comment])[-COMMENTS_KEPT:]
    state['comment_count'] += 1
    return state


def _renamed(state: dict, event: dict) -> dict:
    """Every device that noticed the clash records the same new id; the
    second record changes nothing."""
    new = event['data'].get('id')
    if new and new != state['id']:
        state['former_ids'] = [*state.get('former_ids', []), state['id']]
        state['id'] = new
    return state


_APPLY = {'renamed': _renamed, 'created': _created, 'comment': _commented, 'claimed': _claimed, 'released': _released, 'checkpoint': _checkpoint, 'edited': _edited, 'appended': _appended, 'moved': _moved, 'tagged': _tagged,
          'assigned': _assigned, 'linked': _linked, 'blocked': _blocked, 'unblocked': _unblocked}


def _fold(events: list[dict]) -> dict:
    tombstone = next((event for event in events if event['kind'] == 'deleted'), None)
    if tombstone is not None:  # Nothing else applies, and the other events lost their content.
        return {'uid': tombstone['task'], 'id': tombstone['data'].get('id'), 'deleted': True,
                'deleted_at': tombstone['recorded_at'], 'updated_at': tombstone['recorded_at']}
    state = None
    for event in events:
        apply = _APPLY.get(event['kind'])
        if apply is None or (state is None and event['kind'] != 'created'):
            continue  # A kind from a newer release, or an event before its Task's creation arrived.
        state = apply(state, event)
        state['updated_at'] = max(state['updated_at'], event['recorded_at'])
        holder = state['holder']
        if holder and event['session'] == holder['session']:  # Anything the holder writes is progress.
            holder['progress_at'] = max(holder['progress_at'], event['recorded_at'])
    if state is None:
        raise LookupError('task has no created event')
    return state
