"""Standalone hook entry point; launched by absolute path on every platform.

Keep imports stdlib-only, Imprint's own included (``workspace_policy`` is), so
session reminders also work during dependency repair.
"""
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

REMINDER = 'Imprint memory is available. Search prior context before exploring; verify source files before making changes.'


def _cwd(payload):
    cwd = payload.get('cwd')
    return cwd if isinstance(cwd, str) and cwd else None


def pin_session(payload):
    """Run the Stop hook's steps in the workspace an MCP session in the
    payload's cwd uses (ADR 0072)."""
    from imprint import workspace_policy as policy
    if not os.environ.get('IMPRINT_WORKSPACE'):
        os.environ['IMPRINT_WORKSPACE'] = policy.resolve(_cwd(payload), session=True)['workspace']


def learn_later(transcript, *options):
    """Start the conversation's learner and return without waiting (ADR 0077).

    The learner is detached on purpose: it may wait behind other jobs for the
    queue lock, and clients stop a hook that outlives its timeout but not the
    processes it started. It inherits the session's workspace and paths."""
    detach = ({'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
              if os.name == 'nt' else {'start_new_session': True})
    subprocess.Popen([sys.executable, '-m', 'imprint.session_learn', *options, transcript], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **detach)


def session_context(payload):
    """The session-start reminder, with the session's workspace or the
    question the agent should ask the user about it. A payload without a cwd
    (Cursor's) says nothing about the directory, so neither does the hook."""
    from imprint import workspace_policy as policy
    if not _cwd(payload):
        return REMINDER
    try:
        resolution = policy.resolve(_cwd(payload), session=True)
    except (OSError, ValueError, sqlite3.Error) as error:
        return f"{REMINDER} Imprint could not resolve this directory's workspace: {error}"
    told = policy.notice(resolution) or (
        f"This session's Imprint workspace is '{resolution['workspace']}' ({policy.describe(resolution)}).")
    return f'{REMINDER} {told}'


def main():
    action, data_dir = sys.argv[1:3]
    project_dir = str(Path(__file__).resolve().parent.parent)
    os.environ['PYTHONPATH'] = project_dir
    os.environ['IMPRINT_DATA_DIR'] = data_dir
    if action in {'pre-compact', 'cursor-pre-compact'}:
        key = 'user_message' if action == 'cursor-pre-compact' else 'systemMessage'
        print(json.dumps({key: 'Save durable decisions and findings with Imprint before compaction.'}))
        return 0
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        payload = None  # A malformed hook payload must not prevent normal tool use.
    if action in {'session-start', 'copilot-session-start'}:
        context = session_context(payload if isinstance(payload, dict) else {})
        # Claude Code and Codex read the context from hookSpecificOutput;
        # GitHub Copilot reads only a top-level additionalContext.
        if action == 'copilot-session-start':
            print(json.dumps({'additionalContext': context}))
        else:
            print(json.dumps({'hookSpecificOutput': {'hookEventName': 'SessionStart', 'additionalContext': context}}))
        return 0
    if not isinstance(payload, dict):
        return 0
    if action == 'opencode-idle':
        # Imprint's OpenCode plugin: {"session_id", "cwd"} when a session goes
        # idle. The learner exports the session from OpenCode's database
        # (ADR 0124); an id that is not OpenCode's is ignored.
        session_id = payload.get('session_id')
        if isinstance(session_id, str) and re.fullmatch(r'ses_[A-Za-z0-9]+', session_id):
            try:
                pin_session(payload)
            except (OSError, ValueError, sqlite3.Error) as error:
                print(f'Imprint skipped this conversation: {error}', file=sys.stderr)
                return 0
            learn_later(session_id, '--opencode')
        return 0
    if action == 'stop':
        if payload.get('stop_hook_active'):
            return 0
        transcript = payload.get('transcript_path')
        if transcript:
            try:
                pin_session(payload)
            except (OSError, ValueError, sqlite3.Error) as error:
                print(f'Imprint skipped this conversation: {error}', file=sys.stderr)
                transcript = None
        if transcript:
            learn_later(transcript)
        print('{}')  # Valid Stop output for clients requiring a JSON object.
        return 0
    # Hash untrusted session identifiers instead of treating them as paths.
    # Cursor names its session conversation_id.
    key = payload.get('session_id') or payload.get('conversation_id') or 'default'
    session = hashlib.sha256(str(key).encode()).hexdigest()
    sentinel = Path(data_dir) / '.sessions' / session
    if action == 'post-search':
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.touch()
    elif action in {'pre-read', 'cursor-post-read'}:
        return read_reminder(action, payload, sentinel)
    return 0


READ_REMINDER = ("Imprint may already hold context for this: earlier decisions, findings and conversations. "
                 "Prefer searching it with Imprint's search tool before reading or grepping further; "
                 "if Imprint is unavailable, carry on.")


def read_reminder(action, payload, sentinel):
    """Remind a session that reads before searching Imprint, once; never
    refuse the read (ADR 0142). Claude Code shows the reminder beside the
    read's result; Cursor's permission hook can only allow or deny, so
    there it comes from a postToolUse hook instead."""
    reminded = sentinel.with_name(sentinel.name + '.reminded')
    if sentinel.exists() or reminded.exists():
        return 0
    cursor = action == 'cursor-post-read' or payload.get('hook_event_name') == 'preToolUse' or 'cursor_version' in payload
    if action == 'pre-read' and cursor:
        return 0
    try:
        reminded.parent.mkdir(parents=True, exist_ok=True)
        reminded.touch()
    except OSError:
        pass
    if cursor:
        print(json.dumps({'additional_context': READ_REMINDER}))
    else:
        print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'additionalContext': READ_REMINDER}}))
    return 0


if __name__ == '__main__':
    # Run by path, Python puts imprint/ first on sys.path, where imprint/queue.py
    # would shadow the standard library's; import Imprint from its parent.
    sys.path[0] = str(Path(__file__).resolve().parent.parent)
    sys.exit(main())
