"""Switching the Dispatcher and saying what it does, for the CLI and the dashboard (ADR 0220).

Only the person switches it: ``imprint tasks dispatch roles|on|off|refine``
and the role checkboxes on the Board's strip call ``set_roles`` (ADR 0237);
agents get no tool for it.
"""

from __future__ import annotations

from .. import tasks
from ..device import current
from . import clients, folders, guards, loop, store


def set_roles(workspace: str, roles) -> set[str]:
    """Switch exactly ``roles`` on for ``workspace`` on this device (ADR
    0237), tell paired devices through the Board, and have a running
    Dispatcher look at once. Returns the roles now on."""
    now = store.set_roles(workspace, roles)
    here = current()
    tasks.announce_dispatching(here['id'], here['name'], bool(now), workspace=workspace, roles=now)
    loop.poke()
    return now


def switch(workspace: str, on: bool) -> bool:
    """The old switch, as a shortcut: on adds Work and Review, off switches every role off."""
    set_roles(workspace, store.roles(workspace) | set(store.SWITCH_ROLES) if on else set())
    return on


def switch_refining(workspace: str, on: bool) -> bool:
    """Switch the Refine role (Backlog refinement, ADR 0230) on or off."""
    current_roles = store.roles(workspace)
    set_roles(workspace, current_roles | {'refine'} if on else current_roles - {'refine'})
    return on


def status(workspace: str, *, projects: bool = True, refresh: bool = False) -> dict:
    """This device's Dispatcher for ``workspace``: whether it is on, whether
    the dashboard server that runs it is up, the sessions running and the
    last results, the Tasks it would take next, the other devices whose
    Dispatcher is on, and (with ``projects``) where each project with open
    Tasks lives here."""
    here = current()['id']
    roles = store.roles(workspace)
    shown = roles or set(store.SWITCH_ROLES)
    found = {
        'workspace': workspace, 'on': bool(roles), 'roles': [r for r in store.ROLES if r in roles],
        'refine': 'refine' in roles,
        'server': loop.server_running(),
        # Only this workspace's runs: the Board shows one workspace.
        'autonomy': tasks.autonomy(workspace),
        'running': [r for r in loop.running_runs() if r.get('workspace') in (workspace, None)],
        'recent': [r for r in store.recent(40) if r.get('workspace') in (workspace, None)][:8],
        'held': guards.held(),
        'clients': clients.listing(refresh=refresh, places=folders.distributions(
            workspace, max_age=0.0 if refresh else folders.RUNNING_TTL)),
        # The person's exclusions as set, including clients this device cannot start right now.
        'excluded': sorted(clients.excluded()),
        # With no role on, what Work and Review would take: what switching on would start first.
        'next': [{'id': s['id'], 'title': s['title'], 'project': s['project'], 'client': s['client'], 'role': s['role']}
                 for s in [*(loop.reviews(workspace) if 'review' in shown else []),
                           *(loop.candidates(workspace) if 'work' in shown else []),
                           *(loop.refinements(workspace) if 'refine' in shown else [])]][:5],
        'devices': [d for d in tasks.dispatching(workspace) if d['on'] and d['device'] != here],
    }
    if projects:
        found['projects'] = folders.open_projects(workspace)
    return found
