"""A Dispatched run's lifecycle at its edges (T-2tsmh): a run called off
while its claim travels, what a client leaves running, one session a Task,
the claim's time, the time limit, the daily count and Blocked reviews."""

import io
import os
import signal
import subprocess
import sys
import threading
import time
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from imprint import processes, sync_pairs, tasks
from imprint.dispatch import claimcheck, dispatched, guards, loop, store

from test_dispatch_claims import _pair
from test_dispatch_loop import LoopCase, _until
from test_dispatch_run import DispatchCase


def _gone(pid: int) -> bool:
    """Whether ``pid`` has ended: not running, or a zombie its new parent has not reaped yet."""
    if not processes.is_alive(pid):
        return True
    state = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)], capture_output=True, text=True).stdout.strip()
    return not state or state.startswith('Z')


class CalledOffTests(LoopCase):
    """Switched off, or the server stopping, while a run's claim travels to a paired device."""

    def _start_while(self, state: dict, change) -> dict | None:
        pair, done = _pair(seen=30), threading.Event()

        def meanwhile():
            while not (sync_pairs.get(pair['id']) or {}).get('exchange_requested') and not done.is_set():
                time.sleep(0.05)
            change()
            # Many polls later the claim arrives: without a check the client would start then.
            if not done.wait(1.0):
                sync_pairs.update(pair['id'], last_exchange_began=time.time())
        worker = threading.Thread(target=meanwhile, daemon=True)
        worker.start()
        try:
            with patch.object(claimcheck, 'POLL', 0.05), redirect_stderr(io.StringIO()):
                return self.dispatcher._start(state, 'default')
        finally:
            done.set()
            worker.join(5)

    def _assert_not_started(self, started, why: str, status: str):
        self.assertIsNone(started)  # the pass ends
        self.assertFalse(self.record.exists(), 'the client started')
        run = store.recent(1)[0]
        self.assertEqual((run['outcome'], run['detail']), ('stopped', f'was not started: {why}'))
        self.assertIsNone(run['pid'])
        state = tasks.get(run['task'])
        self.assertIsNone(state['holder'])
        self.assertEqual(state['status'], status)

    def test_switching_off_while_the_claim_travels_starts_no_client(self):
        store.switch('default', True)
        [state] = loop.candidates('default')
        started = self._start_while(state, lambda: store.switch('default', False))
        self._assert_not_started(started, 'the Dispatcher was switched off', 'ready')

    def test_the_server_stopping_while_the_claim_travels_starts_no_client(self):
        store.switch('default', True)
        [state] = loop.candidates('default')
        started = self._start_while(state, self.dispatcher._stopping.set)
        self._assert_not_started(started, 'the dashboard server is stopping', 'ready')

    def test_switching_refinement_off_while_the_claim_travels_starts_no_client(self):
        groom = tasks.create('Groom me', project='shop')['id']
        store.switch_refining('default', True)
        [state] = loop.refinements('default')
        self.assertEqual(state['id'], groom)
        started = self._start_while(state, lambda: store.switch_refining('default', False))
        self._assert_not_started(started, 'Backlog refinement was switched off', 'backlog')


@unittest.skipIf(sys.platform == 'win32', 'on Windows a process group cannot be found once its first process ended')
class LeftoverTests(DispatchCase):
    def test_what_a_client_leaves_running_ends_with_its_session(self):
        with patch.dict(os.environ, {'FAKE_MODE': 'spawn-and-exit'}):
            run = dispatched.run(self.task)
        child = int(Path(str(self.record) + '.child').read_text())
        self.addCleanup(lambda: _gone(child) or os.kill(child, signal.SIGKILL))
        self.assertEqual(run['outcome'], 'failed')
        self.assertTrue(_until(lambda: _gone(child), timeout=10), 'what the client left is still running')


class OneSessionATaskTests(LoopCase):
    def _under_way(self, task: str) -> None:
        """A run of ``task`` on this device that has not ended, as while its client finishes."""
        store.add({'id': 'running1', 'workspace': 'default', 'task': task, 'title': 't', 'project': 'shop',
                   'client': 'claude-code', 'role': 'work', 'started_at': time.time(), 'watcher': os.getpid(),
                   'watcher_started': processes.started(os.getpid())})

    def test_a_task_with_a_session_here_is_neither_worked_nor_started_again(self):
        self._under_way(self.task)
        self.assertEqual(loop.candidates('default'), [])
        with self.assertRaisesRegex(ValueError, 'already has a Dispatched session'):
            dispatched.start(self.task)
        self.assertFalse(self.record.exists())

    def test_no_reviewer_starts_while_the_worker_still_runs(self):
        dispatched.run(self.task)  # worked: in Review
        self.assertEqual([r['id'] for r in loop.reviews('default')], [self.task])
        self._under_way(self.task)
        self.assertEqual(loop.reviews('default'), [])

    def test_no_refinement_starts_while_a_session_runs_on_it(self):
        groom = tasks.create('Groom me', project='shop')['id']
        self._under_way(groom)
        self.assertEqual(loop.refinements('default'), [])


class ClaimTimeTests(DispatchCase):
    def test_the_wait_for_paired_devices_counts_from_the_recorded_claim(self):
        seen, real = {}, claimcheck.confirm

        def confirm(task, workspace, session, claimed_at, **kwargs):
            claim = [e for e in tasks.history(task, workspace=workspace) if e['kind'] == 'claimed'][-1]
            seen.update(claimed_at=claimed_at, recorded_at=claim['recorded_at'])
            return real(task, workspace, session, claimed_at, **kwargs)
        with patch.object(claimcheck, 'confirm', confirm):
            dispatched.run(self.task)
        self.assertGreaterEqual(seen['claimed_at'], seen['recorded_at'])

    def test_the_time_limit_counts_from_the_clients_start(self):
        real = claimcheck.confirm

        def travelling(*args, **kwargs):
            time.sleep(1.0)  # a claim on its way to a paired device
            return real(*args, **kwargs)
        with patch.object(claimcheck, 'confirm', travelling), patch.dict(os.environ, {'FAKE_MODE': 'hang'}):
            run, proc = dispatched.start(self.task)
        self.addCleanup(lambda: (dispatched.kill(proc.pid, proc), proc.wait()))
        claim = [e for e in tasks.history(self.task) if e['kind'] == 'claimed'][0]
        self.assertGreaterEqual(run['started_at'] - claim['recorded_at'], 1.0)
        self.assertEqual(store.get(run['id'])['started_at'], run['started_at'])
        session = dispatched.Session(run, proc, minutes=0.9 / 60)
        self.assertIsNone(session.step())  # the second of waiting does not count toward its 0.9 seconds


class DailyCountTests(DispatchCase):
    def test_runs_that_never_started_a_client_spend_no_session(self):
        now = time.time()

        def add(n, **fields):
            store.add({'id': f'r{n}', 'workspace': 'default', 'task': self.task, 'title': 't', 'project': 'shop',
                       'client': 'claude-code', 'role': 'work', 'started_at': now, **fields})
        add(1, ended_at=now, outcome='lost', detail='lost its claim before it started')
        add(2, ended_at=now, outcome='stopped', detail='was not started: the person released its claim')
        add(3, ended_at=now, outcome='failed', detail='could not start')
        add(4, ended_at=now, outcome='refused')
        self.assertEqual(guards.started_today(), 0)
        add(5, ended_at=now, outcome='review', pid=4242)
        add(6)  # still starting: its claim travels
        self.assertEqual(guards.started_today(), 2)


class BlockedReviewTests(LoopCase):
    def test_a_blocked_task_in_review_is_not_reviewed(self):
        dispatched.run(self.task)
        tasks.block(self.task, 'waiting on the API key', actor=tasks.USER)
        self.assertEqual(loop.reviews('default'), [])

    def test_a_task_in_review_waiting_on_another_task_is_reviewed_once_that_is_done(self):
        dispatched.run(self.task)
        first = tasks.create('First', project='shop', status='ready', acceptance=['ok'])['id']
        tasks.link(self.task, add_blockers=[first], actor=tasks.USER)
        self.assertEqual(loop.reviews('default'), [])
        tasks.move(first, 'done', actor=tasks.USER)
        self.assertEqual([r['id'] for r in loop.reviews('default')], [self.task])


if __name__ == '__main__':
    unittest.main()
