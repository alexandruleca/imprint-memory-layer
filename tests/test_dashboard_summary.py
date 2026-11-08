"""The Home summary (ADR 0152): counted from facets, served from a per-workspace
snapshot, answered stale while a rebuild runs, rebuilt after writes, and never
shared between workspaces."""
import os
import threading
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import test_indexing_retrieval as indexing
from imprint import config, dashboard_summary as summary, imprint_graph as kg, vectorstore as vs


class DashboardSummaryTests(unittest.TestCase):
    setUp = indexing.StorageTests.setUp
    cleanup = indexing.StorageTests.cleanup
    records = indexing.StorageTests.records

    def prepare(self):
        self.addCleanup(kg._close_all)
        self.addCleanup(summary.reset)
        summary.reset()
        env = patch.dict(os.environ, {'IMPRINT_WORKSPACE': ''})
        env.start()
        self.addCleanup(env.stop)
        vs.store_batch(self.records([f'alpha memory {i}' for i in range(3)], project='alpha', source='alpha/notes.md'))
        vs.store_batch(self.records(['beta memory'], project='beta', source='beta/app.py'))
        client, coll = vs._ensure_collection()
        rows, _ = client.scroll(coll, with_payload=['content'], limit=100)
        for row in rows:
            client.set_payload(coll, {'tags': {'topics': ['qdrant'], 'lang': 'python', 'domain': ['backend']}}, points=[row.id])

    def api(self):
        from imprint.api import app
        return TestClient(app, base_url='http://127.0.0.1:8420', client=('127.0.0.1', 1234))

    def test_counts_every_group_of_the_workspace(self):
        self.prepare()
        data = summary.get(wait=None)
        self.assertEqual(data['workspace'], 'default')
        self.assertEqual(data['total'], 4)
        self.assertEqual(data['project_count'], 2)
        self.assertEqual(data['projects'][0], ['alpha', 3])
        self.assertEqual(data['source_count'], 2)
        self.assertEqual(data['topic_count'], 1)
        self.assertEqual(data['lang_count'], 1)
        self.assertEqual(len(data['timeline']), summary.MONTHS)
        self.assertEqual(sum(m['count'] for m in data['timeline']), 4)
        self.assertEqual(data['added_7d'], 4)
        self.assertFalse(data['stale'])

    def test_an_unchanged_workspace_is_answered_from_its_snapshot(self):
        self.prepare()
        summary.get(wait=None)
        with patch.object(summary, 'build', side_effect=AssertionError('rebuilt')):
            self.assertEqual(summary.get()['total'], 4)

    def test_a_write_is_answered_stale_then_rebuilt_in_the_background(self):
        self.prepare()
        summary.get(wait=None)
        vs.store_batch(self.records(['gamma memory'], project='gamma', source='gamma/x.md'))
        gate, real = threading.Event(), summary.build

        def slow(workspace):
            gate.wait(5)
            return real(workspace)
        with patch.object(summary, 'build', side_effect=slow), patch.object(summary, 'MIN_INTERVAL', 0):
            stale = summary.get(wait=0.05)
            self.assertTrue(stale['stale'])
            self.assertTrue(stale['refreshing'])
            self.assertEqual(stale['total'], 4)  # The previous snapshot, at once.
            gate.set()
            deadline = time.time() + 5
            while summary.get(wait=0.2)['stale'] and time.time() < deadline:
                pass
        fresh = summary.get()
        self.assertFalse(fresh['stale'])
        self.assertEqual(fresh['total'], 5)

    def test_invalidation_and_age_rebuild_without_a_noticed_change(self):
        self.prepare()
        summary.get(wait=None)
        client, coll = vs._ensure_collection()
        rows, _ = client.scroll(coll, limit=1)
        client.set_payload(coll, {'project': 'moved'}, points=[rows[0].id])  # Count and WAL unchanged.
        self.assertNotIn('moved', [name for name, _ in summary.get()['projects']])
        summary.invalidate()
        self.assertIn('moved', [name for name, _ in summary.get(wait=None)['projects']])
        client.set_payload(coll, {'project': 'later'}, points=[rows[0].id])
        with patch.object(summary, 'MAX_AGE', 0):
            self.assertIn('later', [name for name, _ in summary.get(wait=None)['projects']])

    def test_workspaces_keep_their_own_snapshots(self):
        self.prepare()
        self.assertEqual(summary.get('default', wait=None)['total'], 4)
        other = summary.get('other', wait=None)
        self.assertEqual(other['workspace'], 'other')
        self.assertEqual(other['total'], 0)
        self.assertEqual(len(other['timeline']), summary.MONTHS)
        self.assertEqual(summary.get('default')['total'], 4)

    def test_a_failed_first_build_reaches_the_caller(self):
        self.prepare()
        with patch.object(summary, 'build', side_effect=vs.EmbeddingProfileError('memories', 'mismatch')):
            with self.assertRaises(vs.EmbeddingProfileError):
                summary.get()

    def test_a_failed_rebuild_keeps_the_older_snapshot(self):
        self.prepare()
        summary.get(wait=None)
        summary.invalidate()
        with patch.object(summary, 'build', side_effect=RuntimeError('qdrant busy')), patch.object(summary, 'MIN_INTERVAL', 0):
            data = summary.get(wait=1)
        self.assertEqual(data['total'], 4)
        self.assertTrue(data['stale'])
        self.assertEqual(data['refresh_error'], 'qdrant busy')

    def test_api_serves_the_summary_and_dashboard_writes_invalidate_it(self):
        self.prepare()
        with patch.object(config, 'get_active_workspace', return_value='default'):
            api = self.api()
            first = api.get('/api/dashboard?fresh=1')
            self.assertEqual(first.status_code, 200)
            self.assertEqual(first.json()['total'], 4)
            generation = summary._state('default').generation
            api.post('/api/workspace/roots', json={})  # Refused: nothing changed.
            self.assertEqual(summary._state('default').generation, generation)
            with patch('imprint.api.config.switch_workspace'), patch('imprint.api._reset_after_wipe'):
                self.assertEqual(api.post('/api/workspace/switch', json={'workspace': 'default'}).status_code, 200)
            self.assertGreater(summary._state('default').generation, generation)


if __name__ == '__main__':
    unittest.main()
