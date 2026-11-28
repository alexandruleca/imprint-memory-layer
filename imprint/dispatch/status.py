"""Switching the Dispatcher and saying what it does, for the CLI and the dashboard (ADR 0220).

Only the person switches it: ``imprint tasks dispatch on|off`` and the
Board's strip call ``switch``, and ``dispatch refine on|off`` and the strip's
"Refine Backlog" call ``switch_refining``; agents get no tool for either.
"""

from __future__ import annotations

from .. import tasks
from ..device import current
from . import clients, folders, guards, loop, store


def switch(workspace: str, on: bool) -> bool:
    """Switch this device's Dispatcher for ``workspace``, tell paired devices
    through the Board, and have a running Dispatcher look at once."""
    store.switch(workspace, on)
    here = current()
    tasks.announce_dispatching(here['id'], here['name'], on, workspace=workspace)
    loop.poke()
    return on


def switch_refining(workspace: str, on: bool) -> bool:
    """Switch Backlog refinement on this device for ``workspace`` (ADR 0230),
    and have a running Dispatcher look at once."""
    store.switch_refining(workspace, on)
    loop.poke()
    return on


def status(workspace: str, *, projects: bool = True, refresh: bool = False) -> dict:
    """This device's Dispatcher for ``workspace``: whether it is on, whether
    the dashboard server that runs it is up, the sessions running and the
    last results, the Tasks it would take next, the other devices whose
    Dispatcher is on, and (with ``projects``) where each project with open
    Tasks lives here."""
    here = current()['id']
    found = {
        'workspace': workspace, 'on': store.switched_on(workspace), 'refine': store.refining(workspace),
        'server': loop.server_running(),
        'autonomy': tasks.autonomy(workspace), 'running': loop.running_runs(), 'recent': store.recent(8),
        'held': guards.held(), 'clients': clients.listing(refresh=refresh, places=folders.distributions(workspace)),
        'next': [{'id': s['id'], 'title': s['title'], 'project': s['project'], 'client': s['client'], 'role': s['role']}
                 for s in [*loop.reviews(workspace), *loop.candidates(workspace)]][:5],
        'devices': [d for d in tasks.dispatching(workspace) if d['on'] and d['device'] != here],
    }
    if projects:
        found['projects'] = folders.open_projects(workspace)
    return found
