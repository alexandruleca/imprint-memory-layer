import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ENTRY = Path(__file__).resolve().parents[1] / 'imprint' / 'hook_entry.py'


class HookTests(unittest.TestCase):
    def run_hook(self, action, data_dir, payload):
        return subprocess.run([sys.executable, str(ENTRY), action, str(data_dir)],
                              input=json.dumps(payload), text=True, capture_output=True)

    def test_session_isolation_and_safe_paths(self):
        with tempfile.TemporaryDirectory(prefix="imprint's hook ") as directory:
            root = Path(directory)
            payload = {'session_id': '../../outside'}
            self.assertEqual(self.run_hook('pre-read', root, payload).returncode, 2)
            self.assertEqual(self.run_hook('post-search', root, payload).returncode, 0)
            self.assertEqual(self.run_hook('pre-read', root, payload).returncode, 0)
            self.assertEqual(self.run_hook('pre-read', root, {'session_id': 'other'}).returncode, 2)
            self.assertEqual(len(list((root / '.sessions').iterdir())), 1)

    def test_reminder_and_stop_emit_valid_json(self):
        with tempfile.TemporaryDirectory() as directory:
            for action in ('session-start', 'copilot-session-start', 'stop', 'pre-compact', 'cursor-pre-compact'):
                result = self.run_hook(action, directory, {})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIsInstance(json.loads(result.stdout), dict)

    def test_session_start_context_has_each_clients_shape(self):
        # Claude Code and Codex read hookSpecificOutput; GitHub Copilot reads
        # only a top-level additionalContext (its PascalCase SessionStart).
        payload = {'hook_event_name': 'SessionStart', 'session_id': 's1', 'cwd': '/work', 'source': 'startup'}
        with tempfile.TemporaryDirectory() as directory:
            claude = json.loads(self.run_hook('session-start', directory, payload).stdout)
            copilot = json.loads(self.run_hook('copilot-session-start', directory, payload).stdout)
        self.assertEqual(claude['hookSpecificOutput']['hookEventName'], 'SessionStart')
        self.assertEqual(copilot, {'additionalContext': claude['hookSpecificOutput']['additionalContext']})

    def test_copilot_stop_payload_imports_its_transcript(self):
        # Copilot's PascalCase Stop payload is Claude Code's: session_id, cwd,
        # transcript_path, stop_reason and stop_hook_active.
        from unittest.mock import patch
        from imprint import hook_entry
        transcript = '/home/me/.copilot/session-state/3f2a/events.jsonl'
        payload = {'hook_event_name': 'Stop', 'session_id': '3f2a', 'cwd': '/home/me/app', 'timestamp': '2026-10-25T08:00:00Z',
                   'transcript_path': transcript, 'stop_reason': 'end_turn', 'stop_hook_active': False}
        for active, expected in ((False, 3), (True, 0)):
            payload['stop_hook_active'] = active
            with tempfile.TemporaryDirectory() as directory, \
                    patch.object(hook_entry.sys, 'argv', ['hook_entry.py', 'stop', directory]), \
                    patch.object(hook_entry.sys, 'stdin', io.StringIO(json.dumps(payload))), \
                    patch.object(hook_entry.sys, 'stdout', io.StringIO()), \
                    patch.dict(hook_entry.os.environ), \
                    patch.object(hook_entry.subprocess, 'run') as consume:
                self.assertEqual(hook_entry.main(), 0)
            self.assertEqual(consume.call_count, expected)
            if expected:
                self.assertEqual(consume.call_args_list[0].args[0][2:], ['imprint.cli_conversations', '--transcript', transcript])
