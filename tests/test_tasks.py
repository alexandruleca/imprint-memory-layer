"""The Board (ADRs 0208-0212): Tasks per workspace, kept as a log of events
whose current state is computed from them, read and changed through the
tasks module's public functions."""
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from imprint import config, tasks

GITHUB_TOKEN = 'ghp_' + 'A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8'


class TasksTestCase(unittest.TestCase):
    """A data directory of its own, one device, and two workspaces."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data = Path(directory.name)
        for patcher in (patch.object(config, 'get_data_dir', return_value=self.data),
                        patch.object(tasks, '_device', return_value='device-a'),
                        patch.dict(os.environ, {'IMPRINT_WORKSPACE': ''})):
            patcher.start()
            self.addCleanup(patcher.stop)
        config.register_workspace('work')


class CreateAndReadTests(TasksTestCase):
    def test_a_created_task_is_read_back_in_its_workspace_only(self):
        made = tasks.create('Add retries to the uploader', project='imprint', description='Uploads fail on 503.',
                            workspace='default')
        self.assertRegex(made['id'], r'^T-[0-9a-hjkmnp-tv-z]{5}$')
        self.assertEqual(made['status'], 'backlog')
        self.assertEqual(made['priority'], 'P2')
        got = tasks.get(made['id'], workspace='default')
        self.assertEqual((got['title'], got['project'], got['description']),
                         ('Add retries to the uploader', 'imprint', 'Uploads fail on 503.'))
        self.assertEqual([t['id'] for t in tasks.listing(workspace='default')], [made['id']])
        self.assertEqual(tasks.listing(workspace='work'), [])
        with self.assertRaisesRegex(LookupError, f'No task {made["id"]}'):
            tasks.get(made['id'], workspace='work')

    def test_a_task_needs_a_title_and_a_project(self):
        with self.assertRaisesRegex(ValueError, 'title'):
            tasks.create('  ', project='imprint', workspace='default')
        with self.assertRaisesRegex(ValueError, 'project'):
            tasks.create('Fix it', project='', workspace='default')

    def test_ids_do_not_repeat_and_a_drawn_id_already_used_is_drawn_again(self):
        first = tasks.create('One', project='p', workspace='default')
        taken = first['id'][2:]
        drawn = iter([*taken, *'abcde'])
        with patch.object(tasks.secrets, 'choice', side_effect=lambda alphabet: next(drawn)):
            second = tasks.create('Two', project='p', workspace='default')
        self.assertEqual(second['id'], 'T-abcde')
        ids = {tasks.create(f'n{n}', project='p', workspace='default')['id'] for n in range(50)}
        self.assertEqual(len(ids), 50)

    def test_every_change_is_an_event_with_its_device_session_and_clock(self):
        made = tasks.create('Write docs', project='p', workspace='default',
                            actor=tasks.agent('session-1', name='grimforge', client='claude-code'))
        [event] = tasks.history(made['id'], workspace='default')
        self.assertEqual(event['kind'], 'created')
        self.assertEqual((event['device'], event['session'], event['agent'], event['client']),
                         ('device-a', 'session-1', 'grimforge', 'claude-code'))
        self.assertRegex(event['hlc'], r'^\d{13}\.\d{4}$')
        self.assertEqual(made['created_by'], {'kind': 'agent', 'session': 'session-1', 'name': 'grimforge',
                                              'client': 'claude-code', 'device': 'device-a'})
        by_user = tasks.create('Review the plan', project='p', workspace='default')
        self.assertEqual(by_user['created_by'], {'kind': 'user', 'device': 'device-a'})

    def test_clocks_keep_order_when_the_wall_clock_stands_still_or_goes_back(self):
        with patch.object(tasks.time, 'time', return_value=1_800_000_000.0):
            a = tasks.create('a', project='p', workspace='default')
            b = tasks.create('b', project='p', workspace='default')
        with patch.object(tasks.time, 'time', return_value=1_700_000_000.0):
            c = tasks.create('c', project='p', workspace='default')
        clocks = [tasks.history(t['id'], workspace='default')[0]['hlc'] for t in (a, b, c)]
        self.assertEqual(clocks, sorted(clocks))
        self.assertEqual(len(set(clocks)), 3)

    def test_credentials_are_stored_redacted(self):
        made = tasks.create(f'Rotate {GITHUB_TOKEN}', project='p', description=f'token={GITHUB_TOKEN}',
                            acceptance=[f'{GITHUB_TOKEN} no longer works'], workspace='default')
        stored = (self.data / 'tasks.sqlite3').read_bytes()
        self.assertNotIn(GITHUB_TOKEN.encode(), stored)
        got = tasks.get(made['id'], workspace='default')
        for text in (got['title'], got['description'], got['acceptance'][0]):
            self.assertNotIn(GITHUB_TOKEN, text)
            self.assertIn('[REDACTED:', text)

    def test_each_workspace_keeps_its_tasks_in_a_store_of_its_own(self):
        tasks.create('Here', project='p', workspace='default')
        tasks.create('There', project='p', workspace='work')
        self.assertTrue((self.data / 'tasks.sqlite3').is_file())
        self.assertTrue((self.data / 'tasks_work.sqlite3').is_file())
        with self.assertRaisesRegex(ValueError, 'workspace'):
            tasks.listing(workspace='../escape')
        with self.assertRaisesRegex(ValueError, "Unknown workspace 'nowhere'"):
            tasks.create('Lost', project='p', workspace='nowhere')
        self.assertFalse((self.data / 'tasks_nowhere.sqlite3').exists())

    def test_reading_an_empty_workspace_makes_no_store(self):
        self.assertEqual(tasks.listing(workspace='default'), [])
        self.assertEqual(tasks.board(workspace='default')['columns']['ready'], [])
        self.assertFalse((self.data / 'tasks.sqlite3').exists())

    def test_the_board_lays_tasks_out_by_status(self):
        a = tasks.create('a', project='p', workspace='default')
        b = tasks.create('b', project='q', workspace='default')
        board = tasks.board(workspace='default')
        self.assertEqual(list(board['columns']), list(tasks.STATUSES))
        self.assertEqual([t['id'] for t in board['columns']['backlog']], [a['id'], b['id']])
        self.assertEqual([t['id'] for t in tasks.board(project='q', workspace='default')['columns']['backlog']],
                         [b['id']])

    def test_a_task_is_found_by_its_id_in_any_case_and_with_or_without_the_prefix(self):
        made = tasks.create('Find me', project='p', workspace='default')
        for form in (made['id'], made['id'].upper(), made['id'][2:]):
            self.assertEqual(tasks.get(form, workspace='default')['id'], made['id'])
        with self.assertRaisesRegex(LookupError, 'No task T-zzzzz'):
            tasks.get('T-zzzzz', workspace='default')


if __name__ == '__main__':
    unittest.main()
