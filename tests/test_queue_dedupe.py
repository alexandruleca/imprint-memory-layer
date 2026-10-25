"""Merged submissions (ADR 0068): a job identical to one still waiting runs
once, and one follow-up may wait behind an identical running job."""

import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from imprint import config, queue


class DedupeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        for item in (patch.dict(os.environ, {'IMPRINT_DATA_DIR': str(Path(temp.name) / 'data'), 'IMPRINT_WORKSPACE': ''}),
                     patch.object(queue, '_notify_dispatcher')):
            item.start()
            self.addCleanup(item.stop)
        config.register_workspace('alpha')
        queue._init_db()

    def set_status(self, job_id, status):
        with queue._db_lock, queue._connect() as conn:
            conn.execute('UPDATE jobs SET status=?, started_at=? WHERE id=?', (status, time.time(), job_id))

    def test_an_identical_waiting_job_absorbs_the_submission(self):
        first = queue.submit('refresh-urls', {})
        second = queue.submit('refresh-urls', {})
        self.assertEqual((first['merged'], second), (False, {'job_id': first['job_id'], 'merged': True}))
        self.assertEqual(len(queue.list_queue()['queued']), 1)
        self.assertEqual(queue.get_job(first['job_id'])['body']['_merged'], 1)
        queue.submit('refresh-urls', {})
        self.assertEqual(queue.get_job(first['job_id'])['body']['_merged'], 2)

    def test_dashboard_fields_and_cli_arguments_name_the_same_job(self):
        cases = [
            ({'dir': '/home/me/project/'}, {'args': ['/home/me/project']}),
            ({'dir': '/home/me/project', 'force': True, 'batch_size': 8},
             {'args': ['--batch-size=8', '/home/me/./project', '--force']}),
            ({'url': 'https://example.com/a', 'project': 'docs'}, {'args': ['--project', 'docs', 'https://example.com/a']}),
            ({'args': ['--path', '/b', '--path', '/a/']}, {'args': ['--path', '/a', '--path', '/b']}),
        ]
        for command, (dashboard, cli) in zip(('refresh', 'ingest', 'ingest-url', 'learn'), cases):
            with self.subTest(command=command):
                self.assertEqual(queue.dedupe_key(command, {**dashboard, '_workspace': 'alpha'}),
                                 queue.dedupe_key(command, {**cli, '_workspace': 'alpha'}))

    def test_different_requests_stay_separate_jobs(self):
        base = {'_workspace': 'alpha', 'args': ['/home/me/project']}
        different = [
            ('ingest', {**base, 'args': ['/home/me/other']}),
            ('ingest', {**base, 'args': ['/home/me/project', '--force']}),
            ('ingest', {**base, '_workspace': 'beta'}),
            ('refresh', base),
            # A relative path means nothing without the submitter's directory.
            ('ingest', {**base, 'args': ['project']}),
            # The CLI keeps the last value of a repeated option.
            ('ingest', {**base, 'args': ['/home/me/project', '--batch-size', '4', '--batch-size', '8']}),
        ]
        key = queue.dedupe_key('ingest', {**base, 'args': ['/home/me/project', '--batch-size', '8', '--batch-size', '4']})
        for command, body in different:
            with self.subTest(command=command, body=body):
                self.assertNotEqual(queue.dedupe_key(command, body), key)

    def test_one_follow_up_waits_behind_an_identical_running_job(self):
        running = queue.enqueue('refresh-urls', {})
        self.set_status(running, 'running')
        follow_up = queue.submit('refresh-urls', {})
        self.assertFalse(follow_up['merged'])
        self.assertNotEqual(follow_up['job_id'], running)
        self.assertEqual(queue.submit('refresh-urls', {}), {'job_id': follow_up['job_id'], 'merged': True})
        self.assertEqual([job['id'] for job in queue.list_queue()['queued']], [follow_up['job_id']])

    def test_finished_and_cancelled_jobs_absorb_nothing(self):
        done = queue.enqueue('refresh-urls', {})
        self.set_status(done, 'done')
        cancelled = queue.enqueue('scrub', {})
        queue.cancel(cancelled)
        self.assertFalse(queue.submit('refresh-urls', {})['merged'])
        self.assertFalse(queue.submit('scrub', {})['merged'])

    def test_browser_captures_are_never_merged(self):
        capture = {'url': 'https://example.com', 'title': 't', 'text': 'same text', 'workspace': 'alpha'}
        self.assertIsNone(queue.dedupe_key('browser-capture', capture))
        first, second = queue.enqueue('browser-capture', capture), queue.enqueue('browser-capture', capture)
        self.assertNotEqual(first, second)

    def test_the_api_says_when_a_submission_was_merged(self):
        from imprint.api import app

        async def post():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:8420') as client:
                return [(await client.post('/api/commands/refresh-urls', json={})).json() for _ in range(2)]

        first, second = asyncio.run(post())
        self.assertEqual((first['merged'], second['merged'], second['job_id']), (False, True, first['job_id']))


if __name__ == '__main__':
    unittest.main()
