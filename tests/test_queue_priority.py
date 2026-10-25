"""The queue's priorities (ADR 0067): every job runs before background
tagging, background tagging steps aside and resumes, and a preemptible lock
holder gives way to any other taker."""

import asyncio
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from imprint import config, queue, queue_lock, tag_backlog

REPO = Path(__file__).resolve().parents[1]
# How a stopped process exits: by SIGTERM on Unix, with code 15 on Windows,
# where os.kill terminates it with the signal number as its exit code.
STOPPED = signal.SIGTERM if os.name == 'nt' else -signal.SIGTERM


class QueueCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.data = self.root / 'data'
        env = patch.dict(os.environ, {'IMPRINT_DATA_DIR': str(self.data), 'IMPRINT_WORKSPACE': ''})
        env.start()
        self.addCleanup(env.stop)
        config.register_workspace('alpha')
        queue._init_db()  # as the server's startup recovery does
        queue._background.update(due=0.0, hold_count=None, hold_until=0.0)
        self.addCleanup(queue._background.update, due=0.0, hold_count=None, hold_until=0.0)
        queue._pending_cache.update(at=0.0, value=None)

    def set_status(self, job_id, status):
        with queue._db_lock, queue._connect() as conn:
            conn.execute('UPDATE jobs SET status=?, started_at=? WHERE id=?', (status, time.time(), job_id))


class BackgroundSchedulingTests(QueueCase):
    """When the dispatcher starts background tagging, and how a run ends."""

    def setUp(self):
        super().setUp()
        self.pending = 3
        for item in (patch.object(tag_backlog, 'deferring', return_value=True),
                     patch.object(queue, '_pending_total', side_effect=lambda: self.pending),
                     patch.object(queue, '_notify_dispatcher')):
            item.start()
            self.addCleanup(item.stop)

    def test_any_waiting_or_running_job_comes_first(self):
        self.assertEqual(queue._startable_backlog(), 3)
        job = queue.enqueue('status', {})
        self.assertIsNone(queue._startable_backlog())
        self.assertEqual(queue._next_queued()['id'], job)
        self.set_status(job, 'running')
        self.assertIsNone(queue._startable_backlog())
        self.set_status(job, 'done')
        self.assertEqual(queue._startable_backlog(), 3)

    def test_nothing_to_tag_or_tagging_off_starts_nothing(self):
        self.pending = 0
        self.assertIsNone(queue._startable_backlog())
        self.pending = 3
        with patch.object(tag_backlog, 'deferring', return_value=False):
            self.assertIsNone(queue._startable_backlog())

    def test_a_job_started_in_a_terminal_holds_it_back(self):
        descriptor = queue_lock.acquire('ingest', 'cli-1')
        try:
            self.assertIsNone(queue._startable_backlog())
        finally:
            queue_lock.release(descriptor)
        self.assertEqual(queue._startable_backlog(), 3)

    def test_the_background_job_is_never_picked_as_a_queued_job(self):
        with queue._db_lock, queue._connect() as conn:
            queue._insert_job(conn, queue.BACKGROUND, {})
        self.assertIsNone(queue._next_queued())
        self.assertFalse(queue.foreground_waiting())
        self.assertEqual(queue.list_queue()['queued'], [])

    def test_how_a_run_ended(self):
        status = queue._background_status
        self.assertEqual(status(0, False), 'done')
        self.assertEqual(status(tag_backlog.YIELDED, False), 'yielded')
        self.assertEqual(status(-signal.SIGTERM, False), 'yielded')  # another lock taker stopped it (Unix)
        self.assertEqual(status(15, False), 'yielded')  # ... on Windows
        self.assertEqual(status(-signal.SIGTERM, True), 'cancelled')
        self.assertEqual(status(1, False), 'failed')

    def run_background(self, exit_status):
        async def fake_run(job, after_job=None):
            queue._finalize(job['id'], status=exit_status, exit_code=None, error=None)
        with patch.object(queue, '_run_job', side_effect=fake_run):
            asyncio.run(queue._run_background_if_due(None))

    def test_a_cancel_pauses_it_until_more_memories_wait(self):
        self.run_background('cancelled')
        queue._background['due'] = 0.0
        self.assertIsNone(queue._startable_backlog())
        self.assertEqual(queue.background_state()['state'], 'paused')
        self.pending = 4
        self.assertEqual(queue._startable_backlog(), 4)

    def test_a_failure_waits_before_retrying_unless_settings_change(self):
        self.run_background('failed')
        self.assertIsNone(queue._startable_backlog())
        self.assertEqual(queue.background_state()['state'], 'failed')
        queue.backlog_changed()
        self.assertEqual(queue._startable_backlog(), 3)

    def test_a_yield_holds_nothing_back(self):
        self.run_background('yielded')
        self.assertEqual(queue.background_state()['state'], 'yielded')
        self.assertEqual(queue._startable_backlog(), 3)

    def test_old_runs_are_pruned(self):
        for _ in range(queue._KEEP_BACKGROUND_RUNS + 5):
            self.run_background('done')
            queue._background['due'] = 0.0
        with queue._db_lock, queue._connect() as conn:
            count = conn.execute('SELECT COUNT(*) FROM jobs WHERE command=?', (queue.BACKGROUND,)).fetchone()[0]
        self.assertEqual(count, queue._KEEP_BACKGROUND_RUNS + 1)

    def test_a_restart_stops_a_run_left_behind_and_records_it_yielded(self):
        left_behind = subprocess.Popen([sys.executable, '-c', PREEMPTIBLE_WORKER, 'old'], stdout=subprocess.PIPE,
                                       text=True, env={**os.environ, 'PYTHONPATH': str(REPO)}, start_new_session=True)
        self.addCleanup(left_behind.stdout.close)
        self.addCleanup(left_behind.wait)
        self.addCleanup(left_behind.kill)
        self.assertEqual(left_behind.stdout.readline().strip(), 'held')
        # The PID recorded for an older run now belongs to an unrelated process.
        unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)
        self.addCleanup(unrelated.wait)
        self.addCleanup(unrelated.kill)
        with queue._db_lock, queue._connect() as conn:
            left = queue._insert_job(conn, queue.BACKGROUND, {})
            older = queue._insert_job(conn, queue.BACKGROUND, {})
            never_started = queue._insert_job(conn, queue.BACKGROUND, {})
            for job_id, process in ((left, left_behind), (older, unrelated)):
                conn.execute("UPDATE jobs SET status='running', pid=?, pgid=? WHERE id=?",
                             (process.pid, process.pid, job_id))
        queue.recover_on_startup()
        self.assertEqual(left_behind.wait(timeout=10), STOPPED)
        self.assertIsNone(unrelated.poll(), 'a process that does not hold the lock was stopped')
        self.assertEqual([queue.get_job(job_id)['status'] for job_id in (left, older, never_started)],
                         ['yielded', 'yielded', 'cancelled'])
        self.assertIsNone(queue_lock.read_holder())

    def test_the_api_reports_background_tagging_apart_and_resumes_it(self):
        from imprint.api import app
        self.run_background('cancelled')

        async def calls():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:8420') as client:
                before = (await client.get('/api/queue')).json()
                resumed = (await client.post('/api/queue/background/resume')).json()
                return before, resumed

        before, resumed = asyncio.run(calls())
        self.assertEqual((before['background']['state'], before['background']['pending']), ('paused', 3))
        self.assertEqual(before['background']['job']['command'], 'tag-backlog')
        self.assertEqual((before['active'], before['queued'], before['recent']), (None, [], []))
        self.assertEqual(resumed['state'], 'waiting')

    def test_changing_a_tagging_setting_lets_it_start_at_once(self):
        from imprint.api import app
        self.run_background('failed')

        async def put():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:8420') as client:
                return await client.put('/api/config/tagger.llm', json={'value': 'true'})

        with patch('imprint.api._provision_runtime', return_value=None):
            self.assertEqual(asyncio.run(put()).status_code, 200)
        self.assertEqual(queue._startable_backlog(), 3)


# Fake jobs for the real dispatcher: the background one tags until a job is
# queued, the way imprint.tag_backlog does between memories.
BACKGROUND_WORKER = textwrap.dedent('''
    import sys, time
    from imprint import queue
    for _ in range(int(sys.argv[1])):
        if queue.foreground_waiting():
            sys.exit(75)
        time.sleep(0.05)
''')
PREEMPTIBLE_WORKER = textwrap.dedent('''
    import sys, time
    from imprint import queue_lock
    descriptor = queue_lock.acquire('tag-backlog', sys.argv[1], preemptible=True)
    print('held' if descriptor is not None else 'busy', flush=True)
    time.sleep(60)
''')


class DispatcherTests(QueueCase):
    """The real dispatcher loop, with stand-in processes for the jobs."""

    def setUp(self):
        super().setUp()
        self.pending = 3
        self.runs = []
        for item in (patch.object(tag_backlog, 'deferring', return_value=True),
                     patch.object(queue, '_pending_total', side_effect=lambda: self.pending)):
            item.start()
            self.addCleanup(item.stop)
        os.environ['PYTHONPATH'] = str(REPO)

    def argv(self, worker, ticks=60):
        def build(command, body, job_id=None):
            self.runs.append(command)
            if command == queue.BACKGROUND:
                return [sys.executable, '-c', worker, str(ticks) if worker is BACKGROUND_WORKER else job_id]
            return [sys.executable, '-c', 'print("foreground")']
        return patch.object(queue, '_build_argv', side_effect=build)

    async def until(self, predicate, timeout=30.0):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > deadline:
                raise AssertionError('timed out')
            await asyncio.sleep(0.05)

    def background_runs(self):
        with queue._db_lock, queue._connect() as conn:
            rows = conn.execute('SELECT * FROM jobs WHERE command=? ORDER BY created_at', (queue.BACKGROUND,)).fetchall()
        return [queue._row_to_dict(row) for row in rows]

    def test_a_queued_job_runs_first_then_background_tagging_resumes(self):
        async def scenario():
            dispatcher = asyncio.create_task(queue.dispatcher_loop())
            try:
                await self.until(lambda: any(run['status'] == 'running' for run in self.background_runs()))
                job = queue.enqueue('status', {})
                await self.until(lambda: queue.get_job(job)['status'] == 'done')
                self.pending = 1
                await self.until(lambda: len(self.background_runs()) == 2
                                 and self.background_runs()[1]['status'] == 'done')
                return job
            finally:
                dispatcher.cancel()

        with self.argv(BACKGROUND_WORKER, ticks=20):
            job_id = asyncio.run(scenario())
        first, second = self.background_runs()
        job = queue.get_job(job_id)
        self.assertEqual((first['status'], first['exit_code']), ('yielded', tag_backlog.YIELDED))
        self.assertLessEqual(first['ended_at'], job['started_at'])
        self.assertGreaterEqual(second['started_at'], job['ended_at'])
        self.assertEqual(self.runs, [queue.BACKGROUND, 'status', queue.BACKGROUND])

    def test_any_other_lock_taker_stops_it_and_it_resumes_afterwards(self):
        async def scenario():
            dispatcher = asyncio.create_task(queue.dispatcher_loop())
            try:
                await self.until(lambda: (queue_lock.read_holder() or {}).get('preemptible'))
                # A purge from the dashboard or MCP, or any CLI command, takes the lock.
                descriptor = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: queue_lock.acquire('purge', '', block=False))
                self.assertIsNotNone(descriptor, 'the taker waited behind background tagging')
                await self.until(lambda: self.background_runs()[0]['status'] != 'running')
                self.assertIsNone(queue._startable_backlog(), 'it restarted while the taker held the lock')
                queue_lock.release(descriptor)
                queue._notify_dispatcher()
                await self.until(lambda: len(self.background_runs()) == 2)
                for run in self.background_runs():
                    if run['status'] == 'running':
                        queue.cancel(run['id'])
                await self.until(lambda: self.background_runs()[1]['status'] != 'running')
            finally:
                dispatcher.cancel()

        with self.argv(PREEMPTIBLE_WORKER), patch.object(queue, 'BACKLOG_POLL_SECONDS', 1.0):
            asyncio.run(scenario())
        first, second = self.background_runs()
        self.assertEqual(first['status'], 'yielded')
        self.assertEqual(second['status'], 'cancelled')


class PreemptibleLockTests(QueueCase):
    """imprint/queue_lock.py; internal/queuelock implements the same rule."""

    def holder(self, preemptible):
        script = textwrap.dedent(f'''
            import time
            from imprint import queue_lock
            descriptor = queue_lock.acquire('holder', 'job', preemptible={preemptible})
            print('held' if descriptor is not None else 'busy', flush=True)
            time.sleep(60)
        ''')
        child = subprocess.Popen([sys.executable, '-c', script], stdout=subprocess.PIPE, text=True,
                                 env={**os.environ, 'PYTHONPATH': str(REPO)})
        self.addCleanup(child.stdout.close)
        self.addCleanup(child.wait)  # Windows keeps the lock file open until it exits
        self.addCleanup(child.kill)
        self.assertEqual(child.stdout.readline().strip(), 'held')
        return child

    def test_a_taker_stops_a_preemptible_holder_and_takes_the_lock(self):
        child = self.holder(preemptible=True)
        self.assertTrue(queue_lock.read_holder()['preemptible'])
        started = time.monotonic()
        descriptor = queue_lock.acquire('purge', '', block=False)
        self.assertIsNotNone(descriptor)
        try:
            self.assertLess(time.monotonic() - started, queue_lock.PREEMPT_WAIT)
            self.assertEqual(child.wait(timeout=10), STOPPED)
            self.assertEqual(queue_lock.read_holder()['command'], 'purge')
            self.assertNotIn('preemptible', queue_lock.read_holder())
        finally:
            queue_lock.release(descriptor)

    def test_other_holders_are_never_stopped(self):
        child = self.holder(preemptible=False)
        self.assertIsNone(queue_lock.acquire('purge', '', block=False))
        self.assertIsNone(child.poll())

    def test_a_preemptible_taker_stops_no_one(self):
        child = self.holder(preemptible=True)
        self.assertIsNone(queue_lock.acquire('tag-backlog', 'other', preemptible=True))
        self.assertIsNone(child.poll())


if __name__ == '__main__':
    unittest.main()
