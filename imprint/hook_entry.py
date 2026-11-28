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
import time
from pathlib import Path

REMINDER = 'Imprint memory is available. Search prior context before exploring; verify source files before making changes.'


def _cwd(payload):
    """The session's directory. Cursor's payloads name none, and its hooks
    run in their configuration folder, so its first workspace root stands for
    it (ADR 0236)."""
    cwd = payload.get('cwd')
    if isinstance(cwd, str) and cwd:
        return cwd
    roots = payload.get('workspace_roots')
    root = roots[0] if isinstance(roots, list) and roots else None
    return root if isinstance(root, str) and root else None


def read_payload():
    """The hook's JSON payload, or None. Cursor on Windows puts a UTF-8 byte
    order mark before it (ADR 0236)."""
    try:
        stream = getattr(sys.stdin, 'buffer', None)
        text = stream.read().decode('utf-8-sig') if stream is not None else sys.stdin.read().lstrip('\ufeff')
        return json.loads(text)
    except (ValueError, OSError):
        return None


ONCE_SECONDS = 60
ONCE_KEEP_SECONDS = 86400


def first_hook(payload, data_dir):
    """Whether this is the first of Imprint's hooks to answer this event of a
    Cursor turn. Cursor also runs Claude Code's hooks, so where both clients
    are connected Imprint's hook for an event runs twice, at the same moment;
    the first one acts and the other stays silent (ADR 0236). Claims are kept
    per minute, so a resumed session is told again; other clients' payloads,
    and anything that cannot be told apart, always act."""
    if not isinstance(payload, dict) or 'cursor_version' not in payload:
        return True
    session = payload.get('session_id') or payload.get('conversation_id')
    event = payload.get('hook_event_name')
    if not (isinstance(session, str) and session and isinstance(event, str) and event):
        return True
    now = time.time()
    key = '\n'.join((session, event, str(payload.get('generation_id') or ''), str(int(now // ONCE_SECONDS))))
    claims = Path(data_dir) / '.sessions' / 'once'
    try:
        claims.mkdir(parents=True, exist_ok=True)
        os.close(os.open(claims / hashlib.sha256(key.encode()).hexdigest(), os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        return False
    except OSError:
        return True  # a second reminder is better than none
    if event == 'sessionStart':
        for claim in claims.iterdir():
            try:
                if now - claim.stat().st_mtime > ONCE_KEEP_SECONDS:
                    claim.unlink()
            except OSError:
                pass
    return True


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


def remember_transcript(payload):
    """Record where the client keeps this conversation's transcript, for the
    Agents page's Live view (ADR 0233). One small write; it never fails the
    hook. A WSL client's path is kept as this runtime reads it."""
    try:
        session = payload.get('session_id') or payload.get('conversation_id')
        transcript = payload.get('transcript_path')
        if not (isinstance(session, str) and session and isinstance(transcript, str) and transcript):
            return
        from imprint import agent_activity
        from imprint.wsl_paths import native_path
        normalized = transcript.replace('\\', '/')
        client = 'codex' if '/.codex/' in normalized else 'claude-code' if '/.claude/' in normalized else None
        cwd = _cwd(payload)
        agent_activity.record_transcript(session, client, native_path(cwd) if cwd else None, native_path(transcript))
        agent_activity.close()
    except Exception:  # noqa: BLE001 - the session's reminder matters more
        pass


def session_context(payload):
    """The session-start reminder, with the session's workspace or the
    question the agent should ask the user about it. A payload that names no
    directory says nothing about it, so neither does the hook."""
    from imprint import workspace_policy as policy
    if not _cwd(payload):
        return REMINDER
    try:
        resolution = policy.resolve(_cwd(payload), session=True)
    except (OSError, ValueError, sqlite3.Error) as error:
        return f"{REMINDER} Imprint could not resolve this directory's workspace: {error}"
    told = policy.notice(resolution) or (
        f"This session's Imprint workspace is '{resolution['workspace']}' ({policy.describe(resolution)}).")
    board = ''
    try:  # The project's Board (ADRs 0211, 0212); it must never cost the session its reminder.
        from imprint import tasks_tool
        session = payload.get('session_id') or payload.get('conversation_id')
        transcript = str(payload.get('transcript_path') or '').replace('\\', '/')
        client = ('cursor' if 'cursor_version' in payload else 'codex' if '/.codex/' in transcript
                  else 'claude-code' if '/.claude/' in transcript else None)
        board = tasks_tool.session_start(_cwd(payload), resolution['workspace'],
                                         session if isinstance(session, str) else None, resolution.get('project'),
                                         client)
    except Exception:
        board = ''
    return f'{REMINDER} {told}' + (f'\n\n{board}' if board else '')


def main():
    action, data_dir = sys.argv[1:3]
    project_dir = str(Path(__file__).resolve().parent.parent)
    os.environ['PYTHONPATH'] = project_dir
    os.environ['IMPRINT_DATA_DIR'] = data_dir
    payload = read_payload()  # A malformed hook payload must not prevent normal tool use.
    if action in {'session-start', 'pre-compact', 'cursor-pre-compact', 'stop'} and not first_hook(payload, data_dir):
        print('{}')  # Another of Imprint's hooks answers this event (ADR 0236).
        return 0
    if action in {'pre-compact', 'cursor-pre-compact'}:
        # Cursor reads user_message from preCompact (cursor-pre-compact is
        # what its hooks ran before ADR 0236); Claude Code shows systemMessage.
        cursor = action == 'cursor-pre-compact' or isinstance(payload, dict) and 'cursor_version' in payload
        key = 'user_message' if cursor else 'systemMessage'
        print(json.dumps({key: 'Save durable decisions and findings with Imprint before compaction.'}))
        return 0
    if action in {'session-start', 'copilot-session-start'}:
        if isinstance(payload, dict):
            remember_transcript(payload)
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
        remember_transcript(payload)  # sessions that started before Imprint recorded transcripts
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
