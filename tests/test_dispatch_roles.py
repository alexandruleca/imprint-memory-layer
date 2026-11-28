"""A device's Dispatcher does only the roles the person switches on (ADR 0237)."""

import io
import json
import os
import sqlite3
import time
from contextlib import closing, redirect_stdout
from unittest.mock import patch

from imprint import cli_tasks, config, tasks
from imprint.dispatch import dispatched, guards, loop, status, store

from test_dispatch_api import DispatchApiCase
from test_dispatch_loop import LoopCase


class StoreTests(LoopCase):
    def test_roles_are_off_by_default_and_set_exactly(self):
        self.assertEqual(store.roles('default'), set())
        self.assertFalse(store.switched_on('default'))
        self.assertEqual(store.set_roles('default', {'review'}), {'review'})
        self.assertEqual(store.roles('default'), {'review'})
        self.assertTrue(store.switched_on('default'))
        self.assertEqual(store.switched_on_workspaces(), ['default'])
        self.assertEqual(store.workspaces_with('review'), ['default'])
        self.assertEqual(store.workspaces_with('work'), [])
        with self.assertRaisesRegex(ValueError, 'unknown Dispatcher role launch'):
            store.set_roles('default', {'review', 'launch'})
        self.assertEqual(store.roles('default'), {'review'})

    def test_the_old_switches_are_shortcuts(self):
        store.switch('default', True)
        self.assertEqual(store.roles('default'), {'work', 'review'})
        store.switch_refining('default', True)
        self.assertEqual(store.roles('default'), {'work', 'review', 'refine'})
        self.assertTrue(store.refining('default'))
        store.switch_refining('default', False)
        self.assertEqual(store.roles('default'), {'work', 'review'})
        store.switch('default', False)
        self.assertEqual(store.roles('default'), set())

    def test_switches_of_an_earlier_release_become_roles_on_first_read(self):
        path = config.get_data_dir() / 'dispatch.sqlite3'
        path.unlink(missing_ok=True)
        with closing(sqlite3.connect(str(path))) as conn, conn:
            conn.execute('CREATE TABLE switches (workspace TEXT PRIMARY KEY, enabled INTEGER NOT NULL,'
                         ' changed_at REAL NOT NULL)')
            conn.execute('CREATE TABLE refine_switches (workspace TEXT PRIMARY KEY, enabled INTEGER NOT NULL,'
                         ' changed_at REAL NOT NULL)')
            conn.executemany('INSERT INTO switches VALUES (?, ?, ?)', [('default', 1, 10.0), ('quiet', 0, 11.0)])
            conn.executemany('INSERT INTO refine_switches VALUES (?, ?, ?)', [('default', 0, 12.0), ('grooming', 1, 13.0)])

        self.assertEqual(store.roles('default'), {'work', 'review'})
        self.assertEqual(store.roles('quiet'), set())
        self.assertEqual(store.roles('grooming'), {'refine'})
        self.assertEqual(store.last_switched_on(), 13.0)
        # Once: a later change is not undone by migrating again.
        store.set_roles('default', {'review'})
        self.assertEqual(store.roles('default'), {'review'})

    def test_the_old_tables_follow_for_a_release_that_reads_only_them(self):
        store.set_roles('default', {'review'})
        with closing(sqlite3.connect(str(config.get_data_dir() / 'dispatch.sqlite3'))) as conn:
            self.assertEqual(conn.execute("SELECT enabled FROM switches WHERE workspace = 'default'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT enabled FROM refine_switches WHERE workspace = 'default'")
                             .fetchone()[0], 0)

    def test_switching_on_a_role_already_on_keeps_when_it_was_switched_on(self):
        store.set_roles('default', {'work'})
        first = store.last_switched_on()
        time.sleep(0.01)
        store.set_roles('default', {'work', 'review'})
        with closing(sqlite3.connect(str(config.get_data_dir() / 'dispatch.sqlite3'))) as conn:
            at = dict(conn.execute("SELECT role, changed_at FROM roles WHERE workspace = 'default'").fetchall())
        self.assertEqual(at['work'], first)
        self.assertGreater(at['review'], first)


class PassTests(LoopCase):
    def in_review(self):
        dispatched.run(self.task)  # worked: now in Review
        self.assertEqual(tasks.get(self.task)['status'], 'review')
        return tasks.create('Next', project='shop', status='ready', acceptance=['ok'], priority='P0')['id']

    def test_review_only_reviews_and_starts_no_work(self):
        ready = self.in_review()
        status.set_roles('default', {'review'})

        run = self.dispatcher.dispatch()
        self.assertEqual((run['task'], run['role']), (self.task, 'review'))
        self.settle()
        self.assertEqual(tasks.get(self.task)['status'], 'done')
        self.assertIsNone(self.dispatcher.dispatch())
        self.assertEqual(tasks.get(ready)['status'], 'ready')

    def test_work_only_leaves_reviews(self):
        ready = self.in_review()
        status.set_roles('default', {'work'})

        run = self.dispatcher.dispatch()
        self.assertEqual((run['task'], run['role']), (ready, 'work'))
        self.settle()
        self.assertEqual(tasks.get(self.task)['status'], 'review')

    def test_refine_only_refines_and_neither_works_nor_reviews(self):
        tasks.create('Ready work', project='shop', status='ready', acceptance=['ok'])
        backlog = tasks.create('Groom me', project='shop', actor=tasks.USER)['id']
        status.set_roles('default', {'refine'})

        run = self.dispatcher.dispatch()
        self.assertEqual((run['task'], run['role']), (backlog, 'refine'))
        self.settle()

    def test_a_waiting_run_is_called_off_when_its_own_role_goes(self):
        status.set_roles('default', {'work', 'review'})
        self.assertIsNone(self.dispatcher._called_off('default', 'work'))
        status.set_roles('default', {'review'})
        self.assertEqual(self.dispatcher._called_off('default', 'work'), 'work on Tasks was switched off')
        self.assertIsNone(self.dispatcher._called_off('default', 'review'))
        self.assertEqual(self.dispatcher._called_off('default', 'refine'), 'Backlog refinement was switched off')
        status.set_roles('default', set())
        self.assertEqual(self.dispatcher._called_off('default', 'review'), 'the Dispatcher was switched off')


class AnnouncementTests(LoopCase):
    def test_paired_devices_see_the_roles_and_older_releases_see_on(self):
        status.set_roles('default', {'review', 'refine'})
        [mine] = tasks.dispatching('default')
        self.assertEqual((mine['on'], mine['roles']), (True, ['refine', 'review']))
        with closing(sqlite3.connect(str(config.get_data_dir() / 'tasks.sqlite3'))) as conn:
            value = json.loads(conn.execute("SELECT value FROM settings WHERE key LIKE 'dispatching:%'").fetchone()[0])
        self.assertTrue(value['on'])  # what a release before roles reads

        status.set_roles('default', set())
        [mine] = tasks.dispatching('default')
        self.assertEqual((mine['on'], mine['roles']), (False, []))

    def test_a_device_on_an_earlier_release_has_the_old_switchs_roles(self):
        tasks.announce_dispatching('old-device', 'bedrock', True, workspace='default')
        tasks.announce_dispatching('off-device', 'anvil', False, workspace='default')
        found = {d['name']: d for d in tasks.dispatching('default')}
        self.assertEqual(found['bedrock']['roles'], ['work', 'review'])
        self.assertEqual(found['anvil']['roles'], [])

    def test_roles_from_another_device_are_kept_to_plain_names(self):
        tasks.announce_dispatching('other', 'bedrock', True, workspace='default',
                                   roles=['review', 'deploy', 'Bad Role', '<script>'])
        [found] = [d for d in tasks.dispatching('default') if d['name'] == 'bedrock']
        self.assertEqual(found['roles'], ['deploy', 'review'])


class FailureLimitTests(LoopCase):
    def test_reaching_the_failure_limit_switches_every_role_off_and_says_so(self):
        status.set_roles('default', {'review', 'refine'})
        now = time.time() + 1
        for number in range(3):
            store.add({'id': f'f{number}', 'workspace': 'default', 'task': self.task, 'title': 'x', 'project': 'shop',
                       'client': 'claude-code', 'role': 'work', 'started_at': now + number,
                       'ended_at': now + number + 0.5, 'outcome': 'failed', 'pid': 4242})
        self.assertTrue(guards.switch_off_after_failures(store.get('f2')))
        self.assertEqual(store.roles('default'), set())
        said = [c['text'] for c in tasks.user_inbox() if 'switched itself off' in c['text']]
        self.assertEqual(len(said), 1)
        self.assertIn('every role', said[0])


class CommandLineTests(LoopCase):
    def cli(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli_tasks.main(['dispatch', *args])
        return code, out.getvalue()

    def test_roles_sets_exactly_those_and_status_lists_them(self):
        code, text = self.cli('roles', 'review,refine')
        self.assertEqual(code, 0)
        self.assertIn('Dispatcher for default on this device: review, refine.', text)
        self.assertEqual(store.roles('default'), {'review', 'refine'})
        code, text = self.cli('status')
        self.assertIn('Dispatcher for default on this device: review, refine', text)

        code, text = self.cli('roles', 'none', '--json')
        self.assertEqual(json.loads(text), {'workspace': 'default', 'roles': [], 'on': False})
        self.assertEqual(store.roles('default'), set())

    def test_an_unknown_role_is_refused(self):
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), patch('sys.stderr', err):
            self.assertEqual(cli_tasks.main(['dispatch', 'roles', 'review,launch']), 1)
        self.assertIn('unknown Dispatcher role launch', err.getvalue())

    def test_a_dispatched_session_may_switch_roles_off_but_never_on(self):
        store.set_roles('default', {'work', 'review'})
        with patch.dict(os.environ, {'IMPRINT_DISPATCH_RUN': 'r1'}):
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), patch('sys.stderr', err):
                self.assertEqual(cli_tasks.main(['dispatch', 'roles', 'work,review,refine']), 1)
            self.assertIn('refused inside a Dispatched session', err.getvalue())
            self.assertEqual(self.cli('roles', 'review')[0], 0)
        self.assertEqual(store.roles('default'), {'review'})


class ApiTests(DispatchApiCase):
    def test_the_board_sets_roles_and_shows_them(self):
        shown = self.api.put('/api/tasks/dispatch', json={'roles': ['review']}).json()
        self.assertEqual((shown['roles'], shown['on'], shown['refine']), (['review'], True, False))
        self.assertEqual(store.roles('default'), {'review'})
        shown = self.api.get('/api/tasks/dispatch').json()
        self.assertEqual(shown['roles'], ['review'])

    def test_roles_must_be_known_names_in_a_list(self):
        self.assertEqual(self.api.put('/api/tasks/dispatch', json={'roles': 'review'}).status_code, 400)
        self.assertEqual(self.api.put('/api/tasks/dispatch', json={'roles': ['launch']}).status_code, 400)
        self.assertEqual(store.roles('default'), set())
