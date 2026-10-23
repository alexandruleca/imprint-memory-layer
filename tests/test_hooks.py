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
            for action in ('session-start', 'stop', 'pre-compact', 'cursor-pre-compact'):
                result = self.run_hook(action, directory, {})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIsInstance(json.loads(result.stdout), dict)
