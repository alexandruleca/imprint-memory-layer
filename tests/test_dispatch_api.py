"""The Board's Dispatcher strip: /api/tasks/dispatch (ADR 0220, Dispatcher 4)."""

import io
import json
import os
import time
from contextlib import redirect_stdout
from unittest.mock import patch

from fastapi.testclient import TestClient

from imprint import cli_tasks, tasks
from imprint.dispatch import loop, store

from test_dispatch_run import DispatchCase


class DispatchApiCase(DispatchCase):
    def setUp(self):
        super().setUp()
        # Without the app's startup: no Dispatcher thread runs in these tests.
        from imprint.api import app
        self.api = TestClient(app, base_url='http://127.0.0.1:8420', client=('127.0.0.1', 1234))


class StripTests(DispatchApiCase):
    def test_the_strip_starts_off_and_says_what_would_be_taken(self):
        shown = self.api.get('/api/tasks/dispatch').json()

        self.assertEqual((shown['workspace'], shown['on']), ('default', False))
        self.assertEqual([s['id'] for s in shown['next']], [self.task])
        self.assertEqual((shown['running'], shown['recent'], shown['devices']), ([], [], []))

    def test_the_strip_shows_this_workspaces_runs_and_every_exclusion_the_person_set(self):
        from imprint import config
        config.register_workspace('other')
        now = time.time()
        for run_id, workspace in (('here1', 'default'), ('there1', 'other')):
            store.add({'id': run_id, 'workspace': workspace, 'task': self.task, 'title': 't', 'project': 'shop',
                       'client': 'claude-code', 'role': 'work', 'started_at': now, 'ended_at': now + 1,
                       'outcome': 'review', 'detail': 'review'})
        with patch.dict(os.environ, {'IMPRINT_DISPATCH_EXCLUDED_CLIENTS': 'codex,cursor'}):
            shown = self.api.get('/api/tasks/dispatch').json()
        self.assertEqual([r['id'] for r in shown['recent']], ['here1'])
        self.assertEqual(shown['excluded'], ['codex', 'cursor'])  # cursor too, though not installed here

    def test_the_strips_poll_asks_which_distributions_run_at_most_every_half_minute(self):
        from imprint.dispatch import folders
        with patch('imprint.wsl_paths.running_distributions', return_value=['Ubuntu']) as asked:
            for _ in range(3):
                self.api.get('/api/tasks/dispatch')
            self.assertEqual(asked.call_count, 1)
            folders._running['at'] -= folders.RUNNING_TTL + 1
            self.api.get('/api/tasks/dispatch')
            self.assertEqual(asked.call_count, 2)
            # The Dispatcher itself asks afresh, even with the clock standing still (Windows' coarse monotonic clock).
            with patch('time.monotonic', return_value=folders._running['at']):
                folders.distributions('default')
                folders.distributions('default')
            self.assertEqual(asked.call_count, 4)

    def test_switching_on_the_board_is_switching_on_the_device(self):
        with patch.object(loop, 'poke') as poke:
            response = self.api.put('/api/tasks/dispatch', json={'on': True})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['on'])
        self.assertTrue(store.switched_on('default'))
        poke.assert_called_once()
        out = io.StringIO()
        with redirect_stdout(out):
            cli_tasks.main(['dispatch', 'status'])
        self.assertIn('Dispatcher for default on this device: work, review', out.getvalue())  # ADR 0237

        self.api.put('/api/tasks/dispatch', json={'on': False})
        self.assertFalse(store.switched_on('default'))

    def test_backlog_refinement_is_switched_on_the_board_apart_from_the_dispatcher(self):
        self.assertFalse(self.api.get('/api/tasks/dispatch').json()['refine'])
        with patch.object(loop, 'poke'):
            shown = self.api.put('/api/tasks/dispatch', json={'refine': True}).json()
        # The Refine role alone: the device is on, for refinement only (ADR 0237).
        self.assertEqual((shown['refine'], shown['on'], shown['roles']), (True, True, ['refine']))
        self.assertTrue(store.refining('default'))
        self.assertEqual(self.api.put('/api/tasks/dispatch', json={'refine': 'yes'}).status_code, 400)
        self.assertEqual(self.api.put('/api/tasks/dispatch', json={}).status_code, 400)

    def test_the_switch_takes_a_boolean_only(self):
        response = self.api.put('/api/tasks/dispatch', json={'on': 'yes'})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(store.switched_on('default'))

    def test_running_sessions_show_and_stop_from_the_board(self):
        with patch.dict(os.environ, {'FAKE_MODE': 'hang'}):
            from imprint.dispatch import dispatched
            run, proc = dispatched.start(self.task)
            session = dispatched.Session(run, proc)
            shown = self.api.get('/api/tasks/dispatch').json()
            self.assertEqual([r['id'] for r in shown['running']], [run['id']])

            response = self.api.post(f"/api/tasks/dispatch/{run['id']}/stop")
            self.assertEqual(response.status_code, 200, response.text)
            deadline = time.monotonic() + 20
            while session.step() is None and time.monotonic() < deadline:
                time.sleep(0.1)

        shown = self.api.get('/api/tasks/dispatch').json()
        self.assertEqual(shown['running'], [])
        recent = shown['recent'][0]
        self.assertEqual((recent['task'], recent['client'], recent['outcome'], recent['detail']),
                         (self.task, 'claude-code', 'stopped', 'was stopped by the person'))
        self.assertIsNotNone(recent['ended_at'])

    def test_stopping_an_unknown_session_is_not_found(self):
        self.assertEqual(self.api.post('/api/tasks/dispatch/nope/stop').status_code, 404)


class PairedDeviceTests(DispatchApiCase):
    def test_another_devices_dispatcher_shows_without_switching_this_one(self):
        record = {'type': 'setting', 'key': 'dispatching:bedrock-id', 'updated_at': time.time(),
                  'value': json.dumps({'on': True, 'name': 'bedrock'})}
        tasks.sync_import([record])

        shown = self.api.get('/api/tasks/dispatch').json()
        self.assertEqual([d['name'] for d in shown['devices']], ['bedrock'])
        self.assertFalse(shown['on'])
        self.assertFalse(store.switched_on('default'))

    def test_this_devices_switch_travels_with_the_board(self):
        self.api.put('/api/tasks/dispatch', json={'on': True})
        manifest = tasks.sync_manifest()
        keys = [key for key in manifest if key.startswith('s:dispatching:')]
        self.assertEqual(len(keys), 1)
        exported = tasks.sync_export(keys)
        self.assertTrue(json.loads(exported[0]['value'])['on'])
        # This device does not list itself among the others.
        self.assertEqual(self.api.get('/api/tasks/dispatch').json()['devices'], [])

    def test_a_malformed_announcement_is_refused(self):
        stats = tasks.sync_import([{'type': 'setting', 'key': 'dispatching:x', 'value': 'on', 'updated_at': 1.0},
                                   {'type': 'setting', 'key': 'something', 'value': 'x', 'updated_at': 1.0}])
        self.assertEqual(stats.get('inserted', 0), 0)
        self.assertEqual(tasks.dispatching(), [])



class ServerStartupTests(DispatchCase):
    def test_the_dashboard_server_starts_with_a_run_whose_task_was_deleted(self):
        import subprocess
        import sys
        from imprint.api import app
        from imprint.dispatch import dispatched
        dead = subprocess.Popen([sys.executable, '-c', 'pass'])
        dead.wait()
        store.add({'id': 'r1', 'workspace': 'default', 'task': self.task, 'title': 't', 'project': 'shop',
                   'client': 'claude-code', 'role': 'work', 'session': dispatched.session_of('r1'), 'pid': dead.pid,
                   'started_at': time.time() - 60, 'watcher': dead.pid})
        tasks.delete(self.task, actor=tasks.USER)
        from imprint import queue
        from imprint.dispatch import clients
        # The app's startup points the job queue at the test client's event loop, closed afterwards.
        with patch('imprint.updates.auto_loop', lambda: __import__('asyncio').sleep(0)), \
                patch.object(queue, '_dispatcher_loop', None), patch.object(queue, '_dispatcher_wake', None), \
                patch.object(clients, 'detected'), \
                TestClient(app, base_url='http://127.0.0.1:8420', client=('127.0.0.1', 1234)) as api:
            self.assertEqual(api.get('/api/tasks/dispatch').status_code, 200)
        self.assertEqual(store.get('r1')['outcome'], 'stopped')


if __name__ == '__main__':
    import unittest
    unittest.main()
