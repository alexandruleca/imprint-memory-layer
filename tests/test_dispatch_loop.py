"""The Dispatcher's switch and loop (ADR 0220, Dispatcher 3)."""

import functools
import io
import json
import os
import subprocess
import sys
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from imprint import cli_tasks, processes, tasks
from imprint.dispatch import dispatched, loop, store

from test_dispatch_run import DispatchCase


def _until(predicate, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


class LoopCase(DispatchCase):
    def setUp(self):
        super().setUp()
        self.dispatcher = loop.Dispatcher(interval=0, poll=0.05)
        self.addCleanup(self.dispatcher.stop, 'was stopped because the test ended')

    def settle(self):
        """Watch until every session the Dispatcher started has ended."""
        self.assertTrue(_until(lambda: (self.dispatcher.watch(), not self.dispatcher.sessions)[1]))


class SwitchTests(LoopCase):
    def test_the_dispatcher_is_off_by_default_and_dispatches_nothing(self):
        self.assertFalse(store.switched_on('default'))
        self.assertIsNone(self.dispatcher.dispatch())
        self.assertFalse(self.record.exists())

    def test_switched_on_it_dispatches_the_first_ready_task_and_records_the_outcome(self):
        later = tasks.create('Second', project='shop', status='ready', acceptance=['ok'], priority='P3')['id']
        store.switch('default', True)

        run = self.dispatcher.dispatch()
        self.assertEqual(run['task'], self.task)  # P2 before P3
        self.settle()

        self.assertEqual(store.get(run['id'])['outcome'], 'review')
        self.assertEqual(tasks.get(self.task)['status'], 'review')
        self.assertEqual(tasks.get(later)['status'], 'ready')

    def test_one_session_at_a_time_by_default(self):
        tasks.create('Second', project='shop', status='ready', acceptance=['ok'])
        store.switch('default', True)
        with patch.dict(os.environ, {'FAKE_MODE': 'hang'}):
            first = self.dispatcher.dispatch()
            self.assertIsNotNone(first)
            self.assertIsNone(self.dispatcher.dispatch())
            self.assertTrue(self.dispatcher.busy())
            self.dispatcher.sessions[first['id']].stop('was stopped because the test ended')
            self.settle()
        self.assertFalse(self.dispatcher.busy())

    def test_a_workspace_switched_off_gives_no_work(self):
        from imprint import config
        config.register_workspace('other')
        tasks.create('Elsewhere', project='shop', status='ready', acceptance=['ok'], workspace='other')
        store.switch('other', True)
        store.switch('default', False)
        with patch.object(loop.folders, 'resolve', return_value={'ok': False, 'reason': 'not here'}):
            self.dispatcher.dispatch()
        self.assertEqual(self.dispatcher.skipped, {'shop': 'not here'})  # only the other workspace was looked at
        self.assertEqual(tasks.get(self.task)['status'], 'ready')

    def test_tasks_outside_the_rules_of_next_are_not_taken(self):
        tasks.move(self.task, 'backlog')
        feature = tasks.create('A feature', project='shop', status='ready', acceptance=['ok'])['id']
        tasks.create('Part of it', project='shop', status='ready', acceptance=['ok'], feature=feature)
        tasks.create('For Codex', project='shop', status='ready', acceptance=['ok'], assignees=['codex'])
        tasks.create('For you', project='shop', status='ready', acceptance=['ok'], assignees=['user'])
        store.switch('default', True)

        self.assertEqual(loop.candidates('default'), [])
        self.assertIsNone(self.dispatcher.dispatch())

    def test_autonomy_off_dispatches_nothing(self):
        tasks.set_autonomy('off')
        store.switch('default', True)
        self.assertIsNone(self.dispatcher.dispatch())

    def test_a_project_without_a_folder_is_passed_over_with_its_reason(self):
        tasks.create('Nowhere', project='nowhere', status='ready', acceptance=['ok'], priority='P0')
        store.switch('default', True)
        run = self.dispatcher.dispatch()

        self.assertEqual(run['task'], self.task)
        self.assertIn('imprint workspace map nowhere', self.dispatcher.skipped['nowhere'])
        self.settle()

    def test_concurrency_zero_starts_none(self):
        store.switch('default', True)
        with patch.dict(os.environ, {'IMPRINT_DISPATCH_CONCURRENCY': '0'}):
            self.assertIsNone(self.dispatcher.dispatch())


class StoppingTests(LoopCase):
    def test_a_session_past_its_limit_is_stopped(self):
        store.switch('default', True)
        with patch.dict(os.environ, {'FAKE_MODE': 'hang'}), \
             patch.object(loop.dispatched, 'Session', functools.partial(dispatched.Session, minutes=0.03)):
            run = self.dispatcher.dispatch()
            self.settle()
        ended = store.get(run['id'])
        self.assertEqual(ended['outcome'], 'failed')
        self.assertIn('limit', ended['detail'])

    def test_stop_from_another_process_records_the_persons_stop_and_releases_the_claim(self):
        store.switch('default', True)
        with patch.dict(os.environ, {'FAKE_MODE': 'hang'}):
            run = self.dispatcher.dispatch()
            self.assertTrue(_until(lambda: self.record.exists()))
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(cli_tasks.main(['dispatch', 'stop', run['id']]), 0)
            self.settle()
        ended = store.get(run['id'])
        self.assertEqual((ended['outcome'], ended['detail']), ('stopped', 'was stopped by the person'))
        self.assertIsNone(tasks.get(self.task)['holder'])
        self.assertIn('was stopped by the person', [e for e in tasks.history(self.task) if e['kind'] == 'comment'][-1]['data']['text'])

    def test_stopping_the_server_stops_its_sessions(self):
        store.switch('default', True)
        with patch.dict(os.environ, {'FAKE_MODE': 'hang'}):
            run = self.dispatcher.dispatch()
            self.dispatcher.stop()
        ended = store.get(run['id'])
        self.assertEqual(ended['detail'], 'was stopped because the dashboard server stopped')
        self.assertFalse(processes.is_alive(run['pid']))

    def test_the_module_reports_busy_while_its_sessions_run(self):
        store.switch('default', True)
        with patch.object(loop, '_dispatcher', self.dispatcher), patch.dict(os.environ, {'FAKE_MODE': 'hang'}):
            self.dispatcher.dispatch()
            self.assertTrue(loop.busy())
            for session in list(self.dispatcher.sessions.values()):
                session.stop('was stopped because the test ended')
            self.settle()
            self.assertFalse(loop.busy())


class RecoveryTests(LoopCase):
    def _run(self, *, pid, watcher):
        return store.add({'id': f'r{pid}', 'workspace': 'default', 'task': self.task, 'title': 't', 'project': 'shop',
                          'client': 'claude-code', 'role': 'work', 'session': 'dispatch:r', 'pid': pid,
                          'watcher': watcher, 'started_at': time.time()})

    def test_a_gone_session_nobody_watched_is_recorded_as_ended(self):
        gone = subprocess.Popen([sys.executable, '-c', 'pass'])
        gone.wait()
        run = self._run(pid=gone.pid, watcher=gone.pid)
        self.dispatcher.recover()

        self.assertEqual(store.get(run['id'])['detail'], 'ended while no Dispatcher watched it')

    def test_a_running_session_whose_watcher_went_away_is_adopted(self):
        alive = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)
        self.addCleanup(lambda: (alive.kill(), alive.wait()))
        gone = subprocess.Popen([sys.executable, '-c', 'pass'])
        gone.wait()
        run = self._run(pid=alive.pid, watcher=gone.pid)
        self.dispatcher.recover()

        self.assertIn(run['id'], self.dispatcher.sessions)

    def test_a_session_another_live_process_watches_is_left_to_it(self):
        alive = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        self.addCleanup(lambda: (alive.kill(), alive.wait()))
        run = self._run(pid=alive.pid, watcher=alive.pid)
        self.dispatcher.recover()

        self.assertNotIn(run['id'], self.dispatcher.sessions)
        self.assertEqual(len(loop.running_runs()), 1)  # but it counts against the limit


class CommandLineTests(LoopCase):
    def cli(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli_tasks.main(['dispatch', *args])
        return code, out.getvalue()

    def test_on_off_and_status(self):
        code, text = self.cli('status')
        self.assertEqual(code, 0)
        self.assertIn('Dispatcher for default on this device: off', text)
        self.assertIn(f'Next to take: {self.task}', text)
        self.assertIn('shop: ', text)

        code, text = self.cli('on')
        self.assertIn('Dispatcher for default on this device: on.', text)
        self.assertTrue(store.switched_on('default'))
        self.assertIn('imprint ui start', text)  # no dashboard server runs in the test

        code, text = self.cli('off', '--json')
        self.assertEqual(json.loads(text), {'workspace': 'default', 'on': False})
        self.assertFalse(store.switched_on('default'))

    def test_stopping_an_unknown_session_fails(self):
        with redirect_stdout(io.StringIO()), patch('sys.stderr', io.StringIO()):
            self.assertEqual(cli_tasks.main(['dispatch', 'stop', 'nope']), 1)


if __name__ == '__main__':
    unittest.main()
