"""Coding-client transcripts: Claude Code sessions, Codex rollouts, Cursor
agent conversations, GitHub Copilot sessions and Cline sessions.

Claude Code, Codex, Cursor and Copilot append one JSON record per line. Cline
writes a session's messages as one JSON document beside the session's
manifest.
Learning reads three things from them: user/assistant text, the working
directories the client recorded and the session identity. Transcript content
is never rewritten.

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
# GitHub Copilot (VS Code's Copilot harness, the Copilot CLI) appends a
# session's events to session-state/<id>/events.jsonl: {"type", "data",
# "agentId"?, ...}. A session's working directory is in the context of its
# session.start and session.resume events and in session.context_changed.
_COPILOT_EVENTS = 'events.jsonl'
_COPILOT_CONTEXT = ('session.start', 'session.resume')
_COPILOT_MESSAGES = ('user.message', 'assistant.message')
# Cline keeps a session in sessions/<id>/: <id>.json is its manifest and
# <id>.messages.json its messages. Its subagents' and teammates' messages files
# in the folder have other names.
_CLINE_MESSAGES = '.messages.json'
# Cline wraps what the user typed in <user_input mode="..."> or, for a slash
# command, <user_command slash="...">, and prepends a <mode_notice> after a
# mode switch (formatUserInputBlock, formatUserCommandBlock).
_CLINE_COMMAND = re.compile(r'<user_command\b[^>]*?\bslash="([^"]+)"[^>]*>(.*?)</user_command>', re.DOTALL)
_CLINE_INPUT = re.compile(r'<user_input\b[^>]*>(.*?)</user_input>', re.DOTALL)
_CLINE_NOTICE = re.compile(r'<mode_notice>.*?</mode_notice>', re.DOTALL)
# Prompts Cline's runtime sends as the user, which its transcripts hide: hook
# output, task resumption and the plan-to-act continuation.
_CLINE_SYNTHETIC = ('<hook_context', '[TASK RESUMPTION]')
_CLINE_ACT_CONTINUATION = 'The user approved switching to act mode. Continue with the approved plan now.'


def records(path):
    """Each JSON object in a transcript; lines that are not one are skipped.

    A Cline messages file is one JSON document: its records are the
    document's "messages".
    """
    with open(path, encoding='utf-8') as transcript:
        if str(path).endswith(_CLINE_MESSAGES):
            try:
                document = json.load(transcript)
            except ValueError:
                return
            messages = document.get('messages') if isinstance(document, dict) else None
            if isinstance(messages, list):
                yield from (record for record in messages if isinstance(record, dict))
            return
        for line in transcript:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict):
                yield record


def recorded_cwd(record):
    """The working directory a record states, if any."""
    kind = record.get('type')
    if kind in _CODEX_CONTEXT:
        payload = record.get('payload')
        return payload.get('cwd') if isinstance(payload, dict) else None
    if kind in _COPILOT_CONTEXT or kind == 'session.context_changed':
        data = record.get('data')
        context = data.get('context') if isinstance(data, dict) and kind in _COPILOT_CONTEXT else data
        return context.get('cwd') if isinstance(context, dict) else None
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
    conversation text. A Cursor or Cline user message's text is what the user
    typed.
    """
    typed = None
    if record.get('type') == 'response_item':  # Codex
        item = record.get('payload')
        if not isinstance(item, dict) or item.get('type') != 'message':
            return None
        role, content = item.get('role'), item.get('content')
    elif record.get('type') in _COPILOT_MESSAGES:
        data = record.get('data')
        if not isinstance(data, dict) or not _copilot_conversation(record, data):
            return None
        role, content = record['type'].split('.')[0], data.get('content')
    elif 'role' not in record:  # Claude Code names the role in "type"
        role, body = record.get('type'), record.get('message')
        content = body.get('content') if isinstance(body, dict) else None
    elif 'message' in record:  # Cursor
        role, body = record.get('role'), record.get('message')
        content = body.get('content') if isinstance(body, dict) else None
        typed = _cursor_query
    else:  # Cline
        if _cline_hidden(record):
            return None
        role, content = record.get('role'), record.get('content')
        typed = _cline_input
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
    if typed and role == 'user':
        text = typed(text)
    return role, text


def _copilot_conversation(record, data):
    """Whether a Copilot message is the user's conversation with the main
    agent: not a subagent's, not an assistant message inside a tool call, and
    for the user, typed rather than injected by a skill or an agent, or sent
    by autopilot to continue."""
    if record.get('agentId'):
        return False
    if record['type'] == 'assistant.message':
        return not data.get('parentToolCallId')
    return data.get('source') in (None, 'user') and not data.get('isAutopilotContinuation')


def _cursor_query(text):
    """What a Cursor user typed: the content of its <user_query> blocks."""
    return '\n\n'.join(_CURSOR_QUERY.findall(text))


def _cline_hidden(record):
    """Whether Cline's transcripts hide a message: runtime context for the
    model (hook output, compaction summaries) and display-only errors."""
    metadata = record.get('metadata')
    if not isinstance(metadata, dict):
        return False
    role = metadata.get('displayRole')
    return metadata.get('displayOnly') is True or (
        isinstance(role, str) and role.strip().lower() in ('system', 'status', 'error'))


def _cline_input(text):
    """What a Cline user typed, as Cline's transcripts show it: the content of
    its <user_input> wrapper, or a slash command as "/name content", without
    <mode_notice> elements. Sessions written before the wrappers hold the
    typed text as it is. Prompts the runtime sent as the user contribute
    nothing.
    """
    text = _CLINE_NOTICE.sub('', text)
    command = _CLINE_COMMAND.search(text)
    if command:
        content = command.group(2).strip()
        text = f'/{command.group(1)} {content}' if content else f'/{command.group(1)}'
    else:
        typed = _CLINE_INPUT.findall(text)
        text = '\n\n'.join(typed) if typed else text
    text = text.strip()
    if text.startswith(_CLINE_SYNTHETIC) or text == _CLINE_ACT_CONTINUATION:
        return ''
    return text


def cline_manifest(path):
    """The manifest of the Cline session whose messages file ``path`` is, or
    None: for another messages file in its folder, or a manifest that is
    missing or not a JSON object."""
    path = Path(path)
    if path.name != path.parent.name + _CLINE_MESSAGES:
        return None
    try:
        with open(path.parent / f'{path.parent.name}.json', encoding='utf-8') as file:
            manifest = json.load(file)
    except (OSError, ValueError):
        return None
    return manifest if isinstance(manifest, dict) else None


def cline_imported(path):
    """Whether a Cline session was imported from another client's store,
    which owns the conversation: Cline marks the import's origin when it
    creates the session and records its source when the import finishes."""
    metadata = (cline_manifest(path) or {}).get('metadata')
    if not isinstance(metadata, dict):
        return False
    origin = metadata.get('sessionHistoryOrigin')
    return 'importedFrom' in metadata or (isinstance(origin, dict) and origin.get('mode') == 'import')


def session(path):
    """A transcript's session id where the client states it in full, else None.

    Codex states it in its session_meta record, which comes no later than its
    first cwd. Cline and Copilot name a session's folder after it, and Cline
    its messages file too. Claude Code and Cursor ids are random UUIDs their
    file names carry.
    """
    path = Path(path)
    if path.name.endswith(_CLINE_MESSAGES):
        return path.parent.name if path.name == path.parent.name + _CLINE_MESSAGES else None
    if path.name == _COPILOT_EVENTS and path.parent.parent.name == 'session-state':
        return path.parent.name
    for record in records(path):
        found = codex_session(record)
        if found or recorded_cwd(record):
            return found
    return None


def cwds(path):
    """Each working directory a transcript records, in order, repeats included.

    Claude Code, Codex and Copilot state them in records (Copilot in its
    session context events, subagents' included). Cursor's records state none:
    it files a conversation at ``projects/<folder>/agent-transcripts/<id>/``,
    where ``<folder>`` is its directory's path as a folder name. That name is
    the conversation's working directory when it stands for exactly one
    existing directory (ADR 0052). Cline states a session's working directory
    and workspace root in the session's manifest, beside its messages; a
    session without a readable manifest records none (ADR 0056).
    """
    path = Path(path)
    if path.name.endswith(_CLINE_MESSAGES):
        manifest = cline_manifest(path) or {}
        for key in ('cwd', 'workspace_root'):
            if manifest.get(key):
                yield manifest[key]
        return
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
