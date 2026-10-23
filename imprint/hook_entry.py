"""Standalone hook entry point; launched by absolute path on every platform.

Keep imports stdlib-only so session reminders also work during dependency repair.
"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


def main():
    action, data_dir = sys.argv[1:3]
    project_dir = str(Path(__file__).resolve().parent.parent)
    os.environ['PYTHONPATH'] = project_dir
    os.environ['IMPRINT_DATA_DIR'] = data_dir
    if action == 'session-start':
        print(json.dumps({'hookSpecificOutput': {'hookEventName': 'SessionStart',
              'additionalContext': 'Imprint memory is available. Search prior context before exploring; verify source files before making changes.'}}))
        return 0
    if action in {'pre-compact', 'cursor-pre-compact'}:
        key = 'user_message' if action == 'cursor-pre-compact' else 'systemMessage'
        print(json.dumps({key: 'Save durable decisions and findings with Imprint before compaction.'}))
        return 0
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0  # A malformed hook payload must not prevent normal tool use.
    if action == 'stop':
        if payload.get('stop_hook_active'):
            return 0
        transcript = payload.get('transcript_path')
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
    sys.exit(main())
