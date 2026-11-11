"""Chat replies run in the API until they are answered or stopped (ADR 0145).

A reply belongs to its chat session, not to the request that asked for it:
closing the tab, opening another chat or a dropped connection only stops
watching it. Watchers replay its events from the first one, so a page that
comes back mid-reply shows everything so far and the rest as it comes. Only
an explicit stop cancels it. The reply records itself in the session as it
always did; a finished reply is read from the session, not from here.

A reply can ask the user a question and wait for the answer (ADR 0159). The
question is one of its events, so a page that comes back sees it, and it is
answered through the API, not through the connection that saw it.
"""
from __future__ import annotations

import threading
from typing import Callable, Iterable


# The longest answer the user can give to a question (ADR 0159).
MAX_ANSWER = 2000


class Question:
    """A question a reply asked the user, waiting for its answer (ADR 0159).
    It closes once answered, stopped or given up on; the reply's thread waits
    on it without polling."""

    def __init__(self, event: dict):
        self.event = event
        self.answer: str | None = None
        self.closed = False
        self._done = threading.Condition()
        # Set by the reply: give up the writing slot while the user thinks.
        self.pause: Callable[[], None] = lambda: None
        self.resume: Callable[[], None] = lambda: None

    @property
    def id(self) -> str:
        return self.event["id"]

    def give(self, text: str) -> bool:
        """The user's answer; False when the question no longer waits for one."""
        with self._done:
            if self.closed:
                return False
            self.answer, self.closed = text, True
            self._done.notify_all()
            return True

    def close(self) -> None:
        """Stop waiting: the reply was stopped or ended."""
        with self._done:
            self.closed = True
            self._done.notify_all()

    def wait(self, timeout: float) -> str | None:
        """The answer, or None when the reply was stopped or `timeout` passed
        first. Other chats' replies can be written meanwhile: a reply that
        waits on the user is not using the model."""
        self.pause()
        try:
            with self._done:
                self._done.wait_for(lambda: self.closed, timeout)
                self.closed = True
                return self.answer
        finally:
            self.resume()


class Rejected(Exception):
    """An answer the API does not take: ``status`` is its HTTP status."""

    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(message)


def check_answer(question: Question, text: object) -> str:
    """The answer as the reply gets it, or Rejected with why not."""
    if not isinstance(text, str) or not text.strip():
        raise Rejected(400, "An answer is required.")
    text = text.strip()
    if len(text) > MAX_ANSWER:
        raise Rejected(400, f"Keep the answer under {MAX_ANSWER} characters.")
    options = question.event.get("options") or []
    if options and not question.event.get("allow_other", True) and text not in options:
        raise Rejected(400, "Choose one of the options.")
    return text


class Reply:
    """One reply being written: its events so far, the question it waits on
    and the stop the user can give."""

    def __init__(self, sid: str):
        self.sid = sid
        self.events: list[str] = []
        self.finished = False
        self.cancel = threading.Event()
        self.question: Question | None = None
        self.writing = False  # Holds the writing slot.
        self._changed = threading.Condition()

    def ask(self, event: dict) -> Question:
        """Pose a question; the reply's thread then waits on it, without the
        writing slot, and takes the slot back before it goes on."""
        question = self.question = Question(event)
        question.pause, question.resume = self._release, self._claim
        if self.cancel.is_set():
            question.close()
        return question

    def _release(self) -> None:
        if self.writing:
            self.writing = False
            _writing.release()

    def _claim(self) -> None:
        """Take the writing slot back; a stop given meanwhile ends the wait
        and the reply then stops before its next step."""
        while not self.writing and not self.cancel.is_set():
            self.writing = _writing.acquire(timeout=.2)

    def pending(self) -> Question | None:
        """The question waiting for the user's answer, if any."""
        question = self.question
        return question if question is not None and not question.closed else None

    def stop(self) -> None:
        self.cancel.set()
        if self.question is not None:
            self.question.close()

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


def writing() -> bool:
    """Whether any reply is being written now (an update waits for it, ADR 0175)."""
    with _lock:
        return bool(_replies)


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
        try:
            reply.writing = _writing.acquire(blocking=False)
            if not reply.writing and waiting:
                reply.emit(waiting)
            reply._claim()
            if reply.writing:
                for event in produce(reply.cancel):
                    reply.emit(event)
        finally:
            reply._release()
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
    reply.stop()
    return True


def answer(sid: str, question_id: object, text: object) -> None:
    """The user's answer to the question this chat's reply waits on."""
    reply = current(sid)
    if reply is None:
        raise Rejected(404, "No reply is being written in this chat.")
    question = reply.pending()
    if question is None or question.id != question_id:
        raise Rejected(409, "That question no longer waits for an answer.")
    if not question.give(check_answer(question, text)):
        raise Rejected(409, "That question no longer waits for an answer.")


def stop_all() -> None:
    with _lock:
        replies = list(_replies.values())
    for reply in replies:
        reply.stop()
