import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from imprint import config, queue, workspace_policy as policy


class EnqueueRootApprovalTests(unittest.TestCase):
    """Queued ingests are checked against the workspace roots before they run."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        env = patch.dict(os.environ, {'IMPRINT_DATA_DIR': str(self.root / 'data'), 'IMPRINT_WORKSPACE': ''})
        env.start()
        self.addCleanup(env.stop)
        notify = patch.object(queue, '_notify_dispatcher')
        notify.start()
        self.addCleanup(notify.stop)
        config.register_workspace('alpha')
        config.register_workspace('beta')
        config.switch_workspace('alpha')
        self.a, self.b = self.root / 'project', self.root / 'project-other'
        self.a.mkdir()
        self.b.mkdir()
        (self.a / 'notes.md').write_text('notes')
        (self.b / 'report.md').write_text('report')

    def assert_mirrors_ensure(self, path, workspace, refused):
        """unapproved_root(path) reports `refused` without binding anything, and
        ensure_ingest_paths([path]) accepts the path exactly when it reports None."""
        before = {ws: policy.roots(ws) for ws in ('alpha', 'beta')}
        self.assertEqual(policy.unapproved_root(str(path), workspace), refused)
        self.assertEqual({ws: policy.roots(ws) for ws in ('alpha', 'beta')}, before)
        if refused is None:
            policy.ensure_ingest_paths([str(path)], workspace)
        else:
            with self.assertRaises(ValueError):
                policy.ensure_ingest_paths([str(path)], workspace)
            self.assertEqual({ws: policy.roots(ws) for ws in ('alpha', 'beta')}, before)

    def test_check_mirrors_ensure_ingest_paths(self):
        # A workspace without roots binds its first ingest's directory.
        self.assert_mirrors_ensure(self.a, 'alpha', None)
        self.assertEqual(policy.roots('alpha'), [str(self.a)])
        self.assert_mirrors_ensure(self.a / 'notes.md', 'alpha', None)
        # Outside the roots: the directory to approve (a file's folder).
        self.assert_mirrors_ensure(self.b, 'alpha', {'workspace': 'alpha', 'root': str(self.b), 'overlaps': []})
        self.assert_mirrors_ensure(self.b / 'report.md', 'alpha', {'workspace': 'alpha', 'root': str(self.b), 'overlaps': []})
        # A first bind into another workspace's root needs its sharing confirmed.
        self.assert_mirrors_ensure(self.a / 'notes.md', 'beta', {'workspace': 'beta', 'root': str(self.a), 'overlaps': ['alpha']})
        self.assert_mirrors_ensure(self.a / 'missing' / 'file.md', 'alpha', None)
        with self.assertRaisesRegex(ValueError, 'Ingest root does not exist'):
            policy.unapproved_root(str(self.root / 'missing' / 'file.md'), 'beta')
        with self.assertRaisesRegex(ValueError, 'Ingest root does not exist'):
            policy.ensure_ingest_paths([str(self.root / 'missing' / 'file.md')], 'beta')

    def test_enqueue_refuses_an_unapproved_root_and_accepts_it_once_added(self):
        policy.add_root(str(self.a), 'alpha')
        with self.assertRaises(queue.RootNotApproved) as refused:
            queue.enqueue('refresh', {'dir': str(self.b / 'report.md')})
        self.assertEqual((refused.exception.workspace, refused.exception.root, refused.exception.overlaps),
                         ('alpha', str(self.b), []))
        self.assertEqual(queue.list_queue(recent_limit=5), {'active': None, 'queued': [], 'recent': []})
        policy.add_root(str(self.b), 'alpha')
        job = queue.enqueue('refresh', {'dir': str(self.b / 'report.md')})
        self.assertEqual(queue.get_job(job)['body']['_workspace'], 'alpha')

    def test_enqueue_checks_only_filesystem_ingest_paths(self):
        policy.add_root(str(self.a), 'alpha')
        queue.enqueue('ingest', {'dir': str(self.a / 'notes.md')})
        queue.enqueue('ingest-url', {'url': 'https://example.com/'})
        # CLI argv is checked when the job runs, like a direct CLI call.
        queue.enqueue('ingest', {'args': [str(self.b)]})
        self.assertEqual(len(queue.list_queue(recent_limit=0)['queued']), 3)

    def test_http_refusal_names_the_root_to_approve(self):
        from imprint.api import app
        policy.add_root(str(self.a), 'beta')

        async def request(body):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:8420') as client:
                return await client.post('/api/commands/ingest', json=body)

        response = asyncio.run(request({'dir': str(self.a / 'notes.md')}))
        self.assertEqual(response.status_code, 409)
        self.assertEqual({key: value for key, value in response.json().items() if key != 'error'},
                         {'code': 'root_not_approved', 'workspace': 'alpha', 'root': str(self.a), 'overlaps': ['beta']})
        self.assertIn('not an approved root', response.json()['error'])
        policy.add_root(str(self.a), 'alpha', allow_overlap=True)
        response = asyncio.run(request({'dir': str(self.a / 'notes.md')}))
        self.assertEqual(response.status_code, 200)
        self.assertIn('job_id', response.json())


if __name__ == '__main__':
    unittest.main()
