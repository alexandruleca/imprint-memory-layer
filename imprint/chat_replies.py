"""Chat replies run in the API until they are answered or stopped (ADR 0145).

A reply belongs to its chat session, not to the request that asked for it:
closing the tab, opening another chat or a dropped connection only stops
watching it. Watchers replay its events from the first one, so a page that
comes back mid-reply shows everything so far and the rest as it comes. Only
an explicit stop cancels it. The reply records itself in the session as it
always did; a finished reply is read from the session, not from here.
"""
from __future__ import annotations

import threading
from typing import Callable, Iterable


class Reply:
    """One reply being written: its events so far, and the stop the user can give."""

    def __init__(self, sid: str):
        self.sid = sid
        self.events: list[str] = []
        self.finished = False
        self.cancel = threading.Event()
        self._changed = threading.Condition()

    def emit(self, event: str) -> None:
        with self._changed:
            self.events.append(event)
            self._changed.notify_all()

    def finish(self) -> None:
        with self._changed:
            self.finished = True
            self._changed.notify_all()

    def wait(self, seen: int, timeout: float) -> tuple[list[str], bool]:
        """Events after the first `seen`, once there are any, the reply ends or `timeout` passes."""
        with self._changed:
            self._changed.wait_for(lambda: len(self.events) > seen or self.finished, timeout)
            return self.events[seen:], self.finished


_lock = threading.Lock()
_replies: dict[str, Reply] = {}
# One reply is written at a time: leaving a chat mid-reply makes a second one
# easy to start, and the local model is not safe to run from two threads.
_writing = threading.Lock()


class Busy(Exception):
    """The session already has a reply being written."""


def current(sid: str) -> Reply | None:
    with _lock:
        return _replies.get(sid)


def start(sid: str, produce: Callable[[threading.Event], Iterable[str]],
          prepare: Callable[[], None] | None = None, waiting: str | None = None) -> Reply:
    """Write a reply on its own thread. `produce` gets the reply's stop and
    yields its events; nothing a watcher does can stop it. `prepare` runs
    once the session is claimed and before the reply starts, so the
    question is recorded before anything of its answer. While another
    chat's reply is written, this one waits and first emits `waiting`."""
    with _lock:
        if sid in _replies:
            raise Busy(sid)
        reply = _replies[sid] = Reply(sid)
    if prepare is not None:
        try:
            prepare()
        except BaseException:
            with _lock:
                del _replies[sid]
            raise

    def run() -> None:
        writing = False
        try:
            writing = _writing.acquire(blocking=False)
            if not writing and waiting:
                reply.emit(waiting)
            while not writing and not reply.cancel.is_set():
                writing = _writing.acquire(timeout=.2)
            if writing:
                for event in produce(reply.cancel):
                    reply.emit(event)
        finally:
            if writing:
                _writing.release()
            with _lock:
                if _replies.get(sid) is reply:
                    del _replies[sid]
            reply.finish()

    threading.Thread(target=run, name="chat-reply", daemon=True).start()
    return reply


def stop(sid: str) -> bool:
    """The user's stop: the reply ends before its next token or tool call."""
    reply = current(sid)
    if reply is None:
        return False
    reply.cancel.set()
    return True


def stop_all() -> None:
    with _lock:
        replies = list(_replies.values())
    for reply in replies:
        reply.cancel.set()
