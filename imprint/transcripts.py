"""Coding-client transcripts: Claude Code sessions, Codex rollouts and Cursor
agent conversations.

Each client appends one JSON record per line. Learning reads three things from
them: user/assistant text, the working directories the client recorded and the
session identity. Transcript content is never rewritten.

Stdlib only: ``workspace_policy`` reads recorded cwds through it, including
for the WSL bridge's Stop check.
"""
import json
import ntpath
import os
import posixpath
import re
import string
import sys
from functools import lru_cache
from pathlib import Path

from .wsl_paths import native_path

# Codex keeps record data under "payload". The session's working directory is
# in its session_meta record and each turn's in a turn_context record.
_CODEX_CONTEXT = ('session_meta', 'turn_context')
_TEXT_BLOCKS = ('text', 'input_text', 'output_text')
# Cursor wraps what the user typed in <user_query>, after the context it adds
# (a timestamp, attached image and document lists).
_CURSOR_QUERY = re.compile(r'<user_query>\s*(.*?)\s*</user_query>', re.DOTALL)
_FOLDER_SEPARATORS = re.compile('[^A-Za-z0-9]+')


def records(path):
    """Each JSON object in a transcript; lines that are not one are skipped."""
    with open(path, encoding='utf-8') as transcript:
        for line in transcript:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict):
                yield record


def recorded_cwd(record):
    """The working directory a record states, if any."""
    if record.get('type') in _CODEX_CONTEXT:
        payload = record.get('payload')
        return payload.get('cwd') if isinstance(payload, dict) else None
    return record.get('cwd')


def codex_session(record):
    """A Codex rollout's session id, from its session_meta record."""
    payload = record.get('payload')
    if record.get('type') == 'session_meta' and isinstance(payload, dict):
        session = payload.get('id')
        return session if isinstance(session, str) and session else None
    return None


def message(record):
    """``(role, text)`` for a user or assistant message record, else None.

    Only text blocks count: tool calls, tool output and reasoning are not
    conversation text. A Cursor user message's text is what the user typed.
    """
    cursor = False
    if record.get('type') == 'response_item':  # Codex
        item = record.get('payload')
        if not isinstance(item, dict) or item.get('type') != 'message':
            return None
        role, content = item.get('role'), item.get('content')
    else:  # Claude Code names the role in "type", Cursor in "role"
        cursor = 'role' in record
        role, body = record.get('role' if cursor else 'type'), record.get('message')
        content = body.get('content') if isinstance(body, dict) else None
    if role not in ('user', 'assistant'):
        return None
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = '\n\n'.join(block['text'] for block in content
                            if isinstance(block, dict) and block.get('type') in _TEXT_BLOCKS
                            and isinstance(block.get('text'), str))
    else:
        return None
    if cursor and role == 'user':
        text = '\n\n'.join(_CURSOR_QUERY.findall(text))
    return role, text


def cwds(path):
    """Each working directory a transcript records, in order, repeats included.

    Claude Code and Codex state them in records. Cursor's records state none:
    it files a conversation at ``projects/<folder>/agent-transcripts/<id>/``,
    where ``<folder>`` is its directory's path as a folder name. That name is
    the conversation's working directory when it stands for exactly one
    existing directory (ADR 0052).
    """
    path = Path(path)
    if path.parent.parent.name == 'agent-transcripts':
        directory = folder_directory(path.parent.parent.parent.name)
        if directory:
            yield directory
        return
    for record in records(path):
        cwd = recorded_cwd(record)
        if cwd:
            yield cwd


def folder_name(path):
    """A path as a client folder name: every run of characters other than ASCII
    letters and digits becomes "-", none at either end."""
    return _FOLDER_SEPARATORS.sub('-', path).strip('-')


@lru_cache(maxsize=256)
def folder_directory(folder):
    """The one existing directory whose path has this folder name, else None.

    The name loses every separator and punctuation mark, so the filesystem is
    walked from its root, entering only directories whose own folder name
    begins what is left of it. No match, several matches, a match that is not
    a directory, or a candidate directory that cannot be listed leaves the
    name standing for no directory. Paths are the client's: Linux paths in a
    WSL path context, listed through the bridge's translator (ADR 0037).
    Windows paths start with a drive and compare without case. A directory
    whose name has no letter or digit vanishes from folder names and is not
    entered.
    """
    windows = sys.platform == 'win32' and not os.environ.get('IMPRINT_WSL_DISTRO')
    fold = str.lower if windows else str
    found = []
    pending = [('', fold(folder))]
    while pending:
        directory, rest = pending.pop()
        try:
            if windows and not directory:
                entries = [(letter, f'{letter}:\\', lambda drive=f'{letter}:\\': os.path.isdir(drive))
                           for letter in string.ascii_uppercase]
            else:
                join = ntpath.join if windows else posixpath.join
                with os.scandir(native_path(directory or '/')) as listing:
                    entries = [(entry.name, join(directory or '/', entry.name), entry.is_dir) for entry in listing]
            for name, child, is_dir in entries:
                key = fold(folder_name(name))
                if key and key == rest:
                    found.append((child, is_dir()))
                elif key and rest.startswith(key + '-') and is_dir():
                    pending.append((child, rest[len(key) + 1:]))
        except OSError:
            return None
    if len(found) != 1 or not found[0][1]:
        return None
    return found[0][0]
