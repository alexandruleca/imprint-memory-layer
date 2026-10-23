"""Dashboard service owned by the desktop app's stdin pipe.

Unlike the explicit ``imprint ui start`` daemon, this process exits when
Electron closes the pipe, including after an Electron crash. No HTTP shutdown
endpoint or shared Qdrant termination is needed.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading


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
        args=(sys.stdin.buffer, parent_closed, finished),
        daemon=True,
    ).start()
    try:
        # Start watching before the comparatively expensive dashboard imports.
        import uvicorn
        from .api import app, mount_static, _shutdown_event

        if parent_closed.is_set():
            return
        mount_static()
        server = uvicorn.Server(uvicorn.Config(
            app, host="127.0.0.1", port=args.port, proxy_headers=False,
        ))

        def stop_on_parent_exit():
            parent_closed.wait()
            _shutdown_event.set()  # Unblock streaming responses.
            server.should_exit = True

        threading.Thread(target=stop_on_parent_exit, daemon=True).start()
        server.run()
    finally:
        finished.set()


if __name__ == "__main__":
    main()
