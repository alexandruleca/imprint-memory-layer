"""Install SIGTERM/SIGINT handlers for CLI entrypoints.

The queue dispatcher sends SIGTERM on cancel; a 3-second grace window
later it escalates to SIGKILL. Raising SystemExit on SIGTERM lets
try/finally blocks (Qdrant flushes, progress cleanup) run before the
kill.
"""

from __future__ import annotations

import signal
import sys


def install() -> None:
    def _handler(signum, _frame):
        sys.exit(130 if signum == signal.SIGINT else 143)

    try:
        signal.signal(signal.SIGTERM, _handler)
    except (ValueError, OSError):
        pass  # Not in main thread — skip.
    try:
        signal.signal(signal.SIGINT, _handler)
    except (ValueError, OSError):
        pass
