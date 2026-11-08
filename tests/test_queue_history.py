"""The Queue page's history: finished jobs a page at a time with counts per
period, and Retry, which queues a finished job again through ``submit`` so
an identical waiting job absorbs it (ADR 0068)."""

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from imprint import config, queue


class HistoryCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        for item in (patch.dict(os.environ, {'IMPRINT_DATA_DIR': str(Path(temp.name) / 'data'), 'IMPRINT_WORKSPACE': ''}),
                     patch.object(queue, '_notify_dispatcher')):
            item.start()
            self.addCleanup(item.stop)
        config.register_workspace('alpha')
        queue._init_db()

    def finish(self, job_id, status='done', ended_at=None):
        ended_at = time.time() if ended_at is None else ended_at
        with queue._db_lock, queue._connect() as conn:
            conn.execute('UPDATE jobs SET status=?, started_at=?, ended_at=? WHERE id=?',
                         (status, ended_at - 1, ended_at, job_id))

    def finished(self, command='refresh-urls', body=None, status='done', ended_at=None):
        with queue._db_lock, queue._connect() as conn:
            job_id = queue._insert_job(conn, command, {**(body or {}), '_workspace': config.get_active_workspace()})
        self.finish(job_id, status, ended_at)
        return job_id


class HistoryTests(HistoryCase):
    def test_pages_follow_the_cursor_without_gaps_or_repeats(self):
        now = time.time()
        ids = [self.finished(ended_at=now - i) for i in range(7)]
        # Two jobs that ended at the same moment are told apart by their ID.
        ids.append(self.finished(ended_at=now - 6))
        seen, cursor = [], None
        while True:
            page = queue.list_history(limit=3, before=cursor)
            self.assertEqual(page['total'], 8)
            seen += [job['id'] for job in page['jobs']]
            cursor = page['next']
            if cursor is None:
                break
        self.assertEqual(sorted(seen), sorted(ids))
        self.assertEqual(len(seen), len(set(seen)))
        self.assertEqual(seen[:6], ids[:6])

    def test_waiting_running_and_background_jobs_are_not_history(self):
        queue.enqueue('refresh-urls', {})
        with queue._db_lock, queue._connect() as conn:
            background = queue._insert_job(conn, queue.BACKGROUND, {})
        self.finish(background)
        self.assertEqual(queue.list_history()['jobs'], [])

    def test_counts_per_period_with_empty_periods(self):
        now = time.time()
        for age in (10, 20, 3_000, 90_000, 400_000):
            self.finished(ended_at=now - age)
        # Today, Yesterday, a period whose start is later than Yesterday's
        # (empty), and everything older.
        bounds = [now - 1_000, now - 100_000, now - 50]
        counts = queue.list_history(limit=1, bounds=bounds)['counts']
        self.assertEqual(counts, [2, 2, 0, 1])
        self.assertIsNone(queue.list_history(limit=1)['counts'])

    def test_a_status_keeps_the_jobs_that_ended_so(self):
        now = time.time()
        failed = self.finished(status='failed', ended_at=now - 5)
        self.finished(ended_at=now - 1)
        self.finished(status='cancelled', ended_at=now - 2)
        page = queue.list_history(status='failed', bounds=[now - 100])
        self.assertEqual(([job['id'] for job in page['jobs']], page['total'], page['counts']), ([failed], 1, [1, 0]))
        with self.assertRaises(ValueError):
            queue.list_history(status='queued')

    def test_a_finished_job_names_its_identical_waiting_twin(self):
        failed = self.finished(status='failed')
        other = self.finished('refresh', {'dir': '/somewhere/else'}, status='failed')
        waiting = queue.enqueue('refresh-urls', {})
        jobs = {job['id']: job for job in queue.list_history()['jobs']}
        self.assertEqual(jobs[failed]['twin'], {'id': waiting, 'status': 'queued'})
        self.assertIsNone(jobs[other]['twin'])


class RetryTests(HistoryCase):
    def test_retry_queues_the_same_command_arguments_and_workspace(self):
        failed = self.finished('ingest-url', {'url': 'https://example.com/a', 'project': 'docs', '_merged': 3}, status='failed')
        config.register_workspace('beta')
        with patch.object(config, 'get_active_workspace', return_value='beta'):
            retried = queue.resubmit(failed)
        self.assertEqual((retried['merged'], retried['retry_of']), (False, failed))
        job = queue.get_job(retried['job_id'])
        self.assertEqual((job['command'], job['status']), ('ingest-url', 'queued'))
        self.assertEqual(job['body'], {'url': 'https://example.com/a', 'project': 'docs',
                                       '_workspace': queue.get_job(failed)['body']['_workspace']})

    def test_retry_joins_an_identical_waiting_job(self):
        failed = self.finished(status='failed')
        waiting = queue.enqueue('refresh-urls', {})
        self.assertEqual(queue.resubmit(failed), {'job_id': waiting, 'merged': True, 'retry_of': failed})

    def test_jobs_that_have_not_ended_and_background_work_are_refused(self):
        waiting = queue.enqueue('refresh-urls', {})
        with self.assertRaises(queue.NotRetryable):
            queue.resubmit(waiting)
        with queue._db_lock, queue._connect() as conn:
            background = queue._insert_job(conn, queue.BACKGROUND, {})
        self.finish(background, 'failed')
        with self.assertRaises(queue.NotRetryable):
            queue.resubmit(background)
        with self.assertRaises(LookupError):
            queue.resubmit('missing')


class ApiTests(HistoryCase):
    def api(self):
        from imprint.api import app
        return TestClient(app, base_url='http://127.0.0.1:8420', client=('127.0.0.1', 1234))

    def test_history_endpoint_pages_counts_and_describes_jobs(self):
        now = time.time()
        for age in (1, 2, 200_000):
            self.finished(ended_at=now - age)
        api = self.api()
        first = api.get('/api/queue/history', params={'limit': 2, 'bounds': f'{now - 100},{now - 100_000}'}).json()
        self.assertEqual((len(first['jobs']), first['total'], first['counts']), (2, 3, [2, 0, 1]))
        self.assertEqual(first['jobs'][0]['label'], 'Refresh URLs')
        rest = api.get('/api/queue/history', params={'limit': 2, 'before': first['next']}).json()
        self.assertEqual((len(rest['jobs']), rest['next']), (1, None))
        self.assertEqual(api.get('/api/queue/history', params={'bounds': 'soon'}).status_code, 400)

    def test_retry_endpoint(self):
        api = self.api()
        failed = self.finished(status='failed')
        reply = api.post(f'/api/jobs/{failed}/retry').json()
        self.assertEqual((reply['merged'], reply['position'], reply['retry_of']), (False, 0, failed))
        again = api.post(f'/api/jobs/{failed}/retry').json()
        self.assertEqual((again['job_id'], again['merged']), (reply['job_id'], True))
        self.assertEqual(api.post(f"/api/jobs/{reply['job_id']}/retry").status_code, 409)
        self.assertEqual(api.post('/api/jobs/missing/retry').status_code, 404)


if __name__ == '__main__':
    unittest.main()
