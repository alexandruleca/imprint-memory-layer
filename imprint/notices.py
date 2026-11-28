"""What an agent session should hear, from every source, each thing once (ADR 0240).

Providers say what is pending for a session: the Board's comments, claims
it lost and its Stalling claims, and other sessions' messages to it. Settle
handlers, one per key prefix, mark what was told. A session hears it on
whichever path comes first:

- in Claude Code, after its next tool call and on its next prompt: the
  conversation's Imprint server keeps ``<conversation>.notice`` (the text,
  one JSON string) and ``<conversation>.notice-keys`` (its keys) in
  ``data/prompt-notices``, and a sourced shell script prints the hook's
  reply around the text for the event its hook command names, takes the
  file, and writes ``<conversation>.notice-by`` naming the event;
- on its next Imprint tool reply, in every client;
- through another session's inbox delivery, which settles with ``inbox``.

The server refreshes the files on each lease renewal and within seconds of
a newer ``<conversation>.poke``, which writing a comment or a message
touches. Nothing here knows a provider's store: providers and settle
handlers register themselves.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# The whole text one notice carries, so a hook never floods a session's context.
CAP = 8000
VIAS = ('prompt', 'tool', 'reply', 'inbox')
# The hook events the script answers, and the path each one is.
EVENTS = {'UserPromptSubmit': 'prompt', 'PostToolUse': 'tool'}
FOLDER = 'prompt-notices'
_CONVERSATION_ID = re.compile(r'[A-Za-z0-9-]{8,128}')


@dataclass
class Section:
    """One provider's part of a notice: what it says under its heading,
    and the keys that settle it. ``more`` is said instead when the section
    does not fit under the cap; its keys then stay pending."""
    heading: str
    keys: list[str]
    lines: list[str]
    more: str = ''


Provider = Callable[[dict, 'str | None'], 'Section | None']  # (the session's actor, workspace)
# settle(keys, session, via, at, *, workspace) -> the keys it newly marked told
Settler = Callable[..., list]

_providers: dict[str, Provider] = {}
_settlers: dict[str, Settler] = {}


def register_provider(name: str, provider: Provider) -> None:
    """Add (or replace) a source of notices under ``name``."""
    _providers[name] = provider


def register_settler(prefix: str, settler: Settler) -> None:
    """Have ``settler`` mark told the keys that start with ``<prefix>:``."""
    if not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', prefix):
        raise ValueError(f'a key prefix is a short lower-case word, not {prefix!r}')
    _settlers[prefix] = settler


def compose(sections: list[Section], cap: int = CAP) -> tuple[list[str], str]:
    """The keys and the text of ``sections``, in order, within ``cap``
    characters. A section that does not fit says its ``more`` pointer
    instead (when that fits), and its keys stay pending."""
    keys, parts, used = [], [], 0
    for section in sections:
        if not section.keys:
            continue
        block = '\n'.join([section.heading, *section.lines])
        needed = len(block) + (2 if parts else 0)
        if used + needed <= cap:
            parts.append(block)
            keys += section.keys
            used += needed
            continue
        pointer = section.more or f'{section.heading} More is waiting.'
        needed = len(pointer) + (2 if parts else 0)
        if used + needed <= cap:
            parts.append(pointer)
            used += needed
    return keys, '\n\n'.join(parts)


def gather(me: dict, workspace: str | None) -> tuple[list[str], str]:
    """Everything pending for the session ``me`` from every provider: its
    keys and its text. A provider that fails is left out this time."""
    sections = []
    for name, provider in list(_providers.items()):
        try:
            section = provider(me, workspace)
        except Exception as error:  # One source failing must not silence the others.
            print(f'[imprint] notices from {name} failed: {error}', file=sys.stderr, flush=True)
            continue
        if section is not None and section.keys:
            sections.append(section)
    return compose(sections)


def settle(keys: list[str], session: str, via: str, at: float | None = None, *,
           workspace: str | None = None) -> list[str]:
    """Mark ``keys`` told to ``session`` by ``via`` at ``at``, each through
    the handler of its prefix; returns the keys newly marked. Keys whose
    prefix has no handler are left as they are, not dropped."""
    if via not in VIAS:
        raise ValueError(f'via is one of {", ".join(VIAS)}')
    at = time.time() if at is None else at
    groups: dict[str, list[str]] = {}
    for key in keys:
        if isinstance(key, str) and ':' in key:
            groups.setdefault(key.split(':', 1)[0], []).append(key)
    told = []
    for prefix, group in groups.items():
        settler = _settlers.get(prefix)
        if settler is None:
            continue
        told += list(settler(group, session, via, at, workspace=workspace) or [])
    return told


# ── The files the Claude Code hooks print ────────────────────────


@dataclass
class Files:
    folder: Path
    notice: Path
    keys: Path
    by: Path
    poke: Path


def files(board_session: str) -> Files | None:
    """The notice files of a Claude Code conversation (``conversation:<id>``);
    None for any other session, which hears through tool replies only."""
    kind, _, conversation = (board_session or '').partition(':')
    if kind != 'conversation' or not _CONVERSATION_ID.fullmatch(conversation):
        return None
    from . import config
    folder = Path(config.get_data_dir()) / FOLDER
    return Files(folder, folder / f'{conversation}.notice', folder / f'{conversation}.notice-keys',
                 folder / f'{conversation}.notice-by', folder / f'{conversation}.poke')


def poke(board_sessions) -> None:
    """Have each session's server refresh its notice within seconds: touch
    ``<conversation>.poke`` for each Claude Code conversation among them."""
    for board_session in dict.fromkeys(board_sessions):
        found = files(board_session)
        if found is None:
            continue
        try:
            found.folder.mkdir(parents=True, exist_ok=True)
            found.poke.touch()
            os.utime(found.poke, None)
        except OSError:
            pass  # The renewal a minute later carries it.


def write(found: Files, keys: list[str], text: str) -> None:
    """The notice first, then its keys, so a keys file never claims a notice
    that was not there to print."""
    found.folder.mkdir(parents=True, exist_ok=True)
    _write(found.notice, json.dumps(text))
    _write(found.keys, json.dumps(keys))


def take_back(found: Files) -> tuple[list[str], str | None, float | None]:
    """Take the notice back, or learn that a hook printed it: (keys the hook
    printed, via, at). A notice still waiting is removed with its keys and
    told nothing. On Windows a hook may hold the file this moment: then
    nothing is decided and ([], None, None) comes back."""
    taken = found.notice.with_name(f'{found.notice.name}.{os.getpid()}.back')
    try:
        os.replace(found.notice, taken)
        taken.unlink(missing_ok=True)
        printed = False
    except FileNotFoundError:
        printed = True
    except OSError:
        return [], None, None
    said: list[str] = []
    via = at = None
    if printed and found.keys.exists():
        try:
            loaded = json.loads(found.keys.read_text(encoding='utf-8'))
            said = [key for key in loaded if isinstance(key, str)] if isinstance(loaded, list) else []
        except (OSError, ValueError):
            said = []
        try:
            event = found.by.read_text(encoding='utf-8').strip()
            via, at = EVENTS.get(event, 'prompt'), found.by.stat().st_mtime
        except OSError:
            via, at = 'prompt', None
    found.keys.unlink(missing_ok=True)
    found.by.unlink(missing_ok=True)
    return said, via, at


def _write(path: Path, text: str) -> None:
    temporary = path.with_name(f'{path.name}.{os.getpid()}.tmp')
    temporary.write_text(text, encoding='utf-8')
    os.replace(temporary, path)


KEEP_DAYS = 1
_swept = {'at': 0.0}


def sweep(folder: Path) -> None:
    """Remove, hourly, what ended conversations left in the folder, including
    the files of releases before this registry (``<conversation>.json``)."""
    now = time.time()
    if now - _swept['at'] < 3600 or not folder.is_dir():
        return
    _swept['at'] = now
    for path in folder.iterdir():
        try:
            if now - path.stat().st_mtime > KEEP_DAYS * 86400:
                path.unlink()
        except OSError:
            pass
