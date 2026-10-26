"""Dashboard service owned by the desktop app's stdin pipe.

Unlike the explicit ``imprint ui start`` daemon, this process exits when
Electron closes the pipe, including after an Electron crash. No HTTP shutdown
endpoint or shared Qdrant termination is needed. A restart request exits with
``RESTART_EXIT_CODE`` and Electron starts the service again (ADR 0050).
"""

from __future__ import annotations

import argparse
import os
import sys
import threading


def detach_lifetime_pipe():
    """Move the parent's pipe off standard input and return it as a stream.

    On Windows a thread blocked reading a pipe holds that file's I/O lock,
    and loading a DLL whose C runtime queries the standard handles (numpy's
    does) then waits for that read forever. Standard input becomes the null
    device, which also stops child processes from inheriting the pipe.
    """
    pipe = os.dup(sys.stdin.fileno())
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, sys.stdin.fileno())
    os.close(null)
    return os.fdopen(pipe, "rb", buffering=0)


def watch_parent(stream, parent_closed, finished, grace_seconds=10):
    """Watch EOF even during imports; bound shutdown of stuck API workers."""
    try:
        while stream.read(1):
            pass
    finally:
        parent_closed.set()
        if not finished.wait(grace_seconds):
            # Exit only this API process. Qdrant is shared with CLI/MCP clients.
            os._exit(0)


def main():
    parser = argparse.ArgumentParser(description="Desktop-owned Imprint service")
    parser.add_argument("--port", type=int, default=8420)
    args = parser.parse_args()

    parent_closed = threading.Event()
    finished = threading.Event()
    threading.Thread(
        target=watch_parent,
        args=(detach_lifetime_pipe(), parent_closed, finished),
        daemon=True,
    ).start()
    try:
        # Start watching before the comparatively expensive dashboard imports,
        # which wait while setup changes the runtime (ADR 0084).
        from .queue_lock import wait_for_runtime

        wait_for_runtime()
        from .api import DashboardServer, mount_static

        if parent_closed.is_set():
            return
        mount_static()
        server = DashboardServer(port=args.port)

        def stop_on_parent_exit():
            parent_closed.wait()
            server.should_exit = True

        threading.Thread(target=stop_on_parent_exit, daemon=True).start()
        server.run()
    finally:
        finished.set()


if __name__ == "__main__":
    main()
