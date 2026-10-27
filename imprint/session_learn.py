"""Learn a conversation after its client's Stop hook (ADR 0077).

Stop ends every turn, and a session that wakes on background events ends a
turn every few seconds. The hook starts this module detached and returns, so
no client timeout cuts a learner short, and the learners a session starts
coalesce:

1. A learner takes the transcript's waiting slot, or exits when another
   learner holds it: that learner has not read the transcript yet, so it
   learns this turn too.
2. It lets the session settle for ``SETTLE_SECONDS``, so a burst of turns is
   learned once.
3. It waits for the queue lock, as every job that loads the embedding model
   does (ADR 0064), and stops background tagging (ADR 0067).
4. It gives up its slot, so a later turn starts a learner that waits behind
   it, and learns in one process: the conversation's exchanges, the decisions
   extracted from it and, with ``summarizer.enabled``, a summary.

So at most one learner per transcript waits, and one Imprint job runs at a
time. Output goes to ``session_learn.log`` in the data directory.

Usage: python -m imprint.session_learn <transcript>
"""

import hashlib
import os
import sys
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from . import queue_lock

# A burst of turns that ends within this many seconds is learned once.
SETTLE_SECONDS = 60.0
# The log starts over once it grows past this size.
LOG_LIMIT = 1 << 20


def slot_path(transcript: str) -> Path:
    """The transcript's waiting slot. A turn is learned in its session's
    workspace (ADR 0072), so each workspace has its own."""
    from .config import get_data_dir
    key = f"{os.environ.get('IMPRINT_WORKSPACE', '')}\n{transcript}".encode("utf-8")
    return get_data_dir() / "learn_slots" / f"{hashlib.sha256(key).hexdigest()}.lock"


def learn(transcript: str) -> None:
    slot = slot_path(transcript)
    waiting = queue_lock.try_lock(slot)
    if waiting is None:
        return
    try:
        time.sleep(SETTLE_SECONDS)
        lock = None
        while lock is None:  # On Windows a blocking attempt gives up after about 10 s.
            lock = queue_lock.acquire("learn", "", block=True)
    finally:
        queue_lock.unlock(waiting, slot)
    try:
        with _log() as log, redirect_stdout(log), redirect_stderr(log):
            _learn(transcript)
    finally:
        queue_lock.release(lock)


def _learn(transcript: str) -> None:
    """The Stop hook's three learners in one process, so the embedding model
    (and the privacy filter, when on) loads once. A step that fails does not
    stop the others. Each step reports in the progress record (ADR 0089)."""
    from . import cli_conversations, cli_extract, cli_summarize
    from .progress import clear_progress, write_progress
    print(time.strftime("%Y-%m-%d %H:%M:%S"), transcript, flush=True)
    started = time.time()

    def report(phase: str, done: int = 0, total: int = 0) -> None:
        write_progress("learn", done, total, done, 0, started, [], phase=phase, unit="chunks")

    steps = (("conversation", "reading",
              lambda: cli_conversations.index_transcript(transcript, progress=lambda done, total: report("embedding", done, total))),
             ("extraction", "extracting", lambda: cli_extract.extract(transcript)),
             ("summary", "summarizing", lambda: cli_summarize.summarize_transcript(transcript)))
    try:
        for name, phase, step in steps:
            report(phase)
            try:
                print(f"  {name}: {step()}", flush=True)
            except Exception as error:
                print(f"  {name} failed: {type(error).__name__}: {error}", flush=True)
    finally:
        clear_progress()


def _log():
    from .config import get_data_dir
    path = get_data_dir() / "session_learn.log"
    try:
        full = path.stat().st_size > LOG_LIMIT
    except OSError:
        full = False
    return open(path, "w" if full else "a", encoding="utf-8")


def main() -> int:
    from ._cli_signals import install
    install()
    learn(sys.argv[1])
    return 0


if __name__ == "__main__":
    sys.exit(main())
