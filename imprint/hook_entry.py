"""Standalone hook entry point; launched by absolute path on every platform.

Keep imports stdlib-only, Imprint's own included (``workspace_policy`` is), so
session reminders also work during dependency repair.
"""
import hashlib
import json
import os
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
            for module, args in (
                ('imprint.cli_conversations', ['--transcript', transcript]),
                ('imprint.cli_extract', [transcript]),
                ('imprint.cli_summarize', [transcript]),
            ):
                subprocess.run([sys.executable, '-m', module, *args],
                               stdout=sys.stderr, check=False)
        print('{}')  # Valid Stop output for clients requiring a JSON object.
        return 0
    # Hash untrusted session identifiers instead of treating them as paths.
    session = hashlib.sha256(str(payload.get('session_id', 'default')).encode()).hexdigest()
    sentinel = Path(data_dir) / '.sessions' / session
    if action == 'post-search':
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.touch()
    elif action == 'pre-read' and not sentinel.exists():
        print('Imprint: search prior context before Read/Grep/Glob.', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    # Run by path, Python puts imprint/ first on sys.path, where imprint/queue.py
    # would shadow the standard library's; import Imprint from its parent.
    sys.path[0] = str(Path(__file__).resolve().parent.parent)
    sys.exit(main())
