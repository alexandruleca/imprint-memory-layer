"""``imprint tasks``: the user's view of the Board from a terminal (ADR 0208)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class CliTasksTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data = Path(directory.name) / 'data'
        self.data.mkdir()
        self.cwd = Path(directory.name) / 'shop'
        self.cwd.mkdir()
        (self.cwd / 'package.json').write_text('{"name": "shop"}')
        (self.data / 'workspace.json').write_text('{"active": "default", "known": ["default", "work"]}')

    def run_cli(self, *args, ok=True):
        env = {**os.environ, 'PYTHONPATH': str(ROOT), 'IMPRINT_DATA_DIR': str(self.data), 'IMPRINT_WORKSPACE': ''}
        done = subprocess.run([sys.executable, '-m', 'imprint.cli_tasks', *args], cwd=self.cwd, env=env,
                              capture_output=True, text=True, encoding='utf-8', timeout=120)
        if ok:
            self.assertEqual(done.returncode, 0, done.stderr)
        return done

    def test_create_then_list_show_and_board(self):
        made = json.loads(self.run_cli('create', 'Fix the checkout total', '--priority', 'p1', '--tag', 'cart',
                                       '--criterion', 'Totals include tax', '--ready', '--json').stdout)
        self.assertEqual((made['project'], made['status'], made['priority'], made['tags']),
                         ('shop', 'ready', 'P1', ['cart']))
        self.assertEqual(made['created_by']['kind'], 'user')
        listed = self.run_cli('list').stdout
        self.assertIn(f"{made['id']} P1 Fix the checkout total · shop #cart", listed)
        shown = self.run_cli('show', made['id']).stdout
        self.assertIn('Created by the user', shown)
        self.assertIn('- Totals include tax', shown)
        board = self.run_cli().stdout
        self.assertIn('Ready (1)', board)
        self.assertEqual(json.loads(self.run_cli('list', '--status', 'Ready', '--json').stdout)[0]['id'], made['id'])
        self.assertEqual(json.loads(self.run_cli('list', '--project', 'elsewhere', '--json').stdout), [])

    def test_mistakes_exit_nonzero_with_the_reason(self):
        done = self.run_cli('create', 'No criteria', '--ready', ok=False)
        self.assertEqual(done.returncode, 1)
        self.assertIn('acceptance criterion', done.stderr)
        done = self.run_cli('show', 'T-zzzzz', ok=False)
        self.assertEqual(done.returncode, 1)
        self.assertIn('No task T-zzzzz', done.stderr)

    def test_tasks_stay_in_their_workspace(self):
        self.run_cli('create', 'Here', '--workspace', 'default')
        listed = json.loads(self.run_cli('list', '--workspace', 'work', '--json').stdout)
        self.assertEqual(listed, [])
        done = self.run_cli('create', 'Lost', '--workspace', 'nowhere', ok=False)
        self.assertIn("Unknown workspace 'nowhere'", done.stderr)


if __name__ == '__main__':
    unittest.main()
