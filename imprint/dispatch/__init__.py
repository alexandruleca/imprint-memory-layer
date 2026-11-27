"""The Dispatcher: Imprint starting agent sessions for Board Tasks (ADRs 0220-0224).

When the person switches it on for a workspace on this device, the
Dispatcher picks Tasks by the Board's own rules and starts Dispatched
sessions, one-shot runs of a client, to work on them or review them. It is
plain code, not an agent.

The package is split by reason to change: ``git`` (running git where a
folder lives, natively or inside its WSL distro), ``folders`` (where a
project lives on this device), ``worktrees`` (each Task's worktree and
branch), ``runners`` (how each client is started, and the flags that never
pass), ``store`` (the runs this device started, and their logs) and
``dispatched`` (starting one Dispatched session, watching it, and recording
how it ended).
"""

from __future__ import annotations

from .folders import resolve, open_projects

__all__ = ['resolve', 'open_projects']
