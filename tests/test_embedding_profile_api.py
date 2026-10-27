"""An incompatible index remains intact and reports recovery through the API."""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from qdrant_client import QdrantClient, models

from imprint import config, vectorstore as vs
from imprint.api import app
from imprint.cli_viz import reset_cache


class EmbeddingProfileAPITests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.client = QdrantClient(':memory:')
        self.addCleanup(self.client.close)
        for item in [
            patch.dict(os.environ, {'IMPRINT_WORKSPACE': ''}),
            patch.object(config, 'get_data_dir', return_value=Path(directory.name)),
            patch.object(config, 'QDRANT_COLLECTION', 'memories'),
            patch.object(config, 'EMBEDDING_DIM', 3),
            patch.object(vs, '_get_client', return_value=self.client),
            patch.object(self.client, 'create_payload_index'),
        ]:
            item.start()
            self.addCleanup(item.stop)
        vs._collections_ready.clear()
        reset_cache()
        self.addCleanup(vs._collections_ready.clear)
        self.addCleanup(reset_cache)

    def request(self, method, path, **kwargs):
        async def send():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:8420',
            ) as client:
                return await client.request(method, path, **kwargs)
        return asyncio.run(send())

    def old_collection(self, identity='previous-encoder', dim=3):
        self.client.create_collection(
            'memories', vectors_config={'dense': models.VectorParams(size=dim, distance=models.Distance.COSINE)},
            metadata={'imprint_embedding_identity': identity} if identity else None,
        )
        self.client.upsert('memories', [models.PointStruct(
            id=1, vector={'dense': [1.] + [0.] * (dim - 1)},
            payload={'content': 'An irreplaceable manual note', 'source': 'manual'},
        )])

    def assert_preserved(self, metadata):
        self.assertEqual(self.client.get_collection('memories').config.metadata, metadata)
        rows, _ = self.client.scroll('memories', with_vectors=True)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].payload['content'], 'An irreplaceable manual note')
        self.assertEqual(rows[0].vector['dense'], [1., 0., 0.])
        self.assertNotIn('memories', vs._collections_ready)

    def test_incompatible_index_returns_actionable_conflict_for_data_endpoints(self):
        self.old_collection()
        metadata = self.client.get_collection('memories').config.metadata
        for path in ['/api/stats', '/api/overview', '/api/graph?scope=root&depth=1']:
            with self.subTest(path=path):
                response = self.request('GET', path)
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(response.json()['error'], 'embedding_profile_conflict')
                self.assertEqual(response.json()['collection'], 'memories')
                self.assertIn('Create a new workspace and reindex', response.json()['detail'])
                self.assert_preserved(metadata)
        # Connectivity remains healthy even though the index cannot be used.
        self.assertEqual(self.request('GET', '/api/ping').status_code, 200)
        self.assertEqual(self.request('GET', '/api/workspaces').json()['active'], 'default')

    def test_unversioned_index_reports_rebuild_without_relabelling(self):
        self.old_collection(identity=None)
        metadata = self.client.get_collection('memories').config.metadata
        response = self.request('GET', '/api/stats')
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn('unversioned embeddings', response.json()['detail'])
        self.assert_preserved(metadata)

    def test_wrong_dimensions_report_the_same_compatibility_conflict(self):
        self.old_collection(identity=config.embedding_identity(), dim=4)
        response = self.request('GET', '/api/stats')
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()['error'], 'embedding_profile_conflict')
        self.assertEqual(self.client.get_collection('memories').config.params.vectors['dense'].size, 4)
        self.assertEqual(self.client.count('memories', exact=True).count, 1)

    def test_new_workspace_recovers_dashboard_and_keeps_old_memories(self):
        self.old_collection()
        metadata = self.client.get_collection('memories').config.metadata
        self.assertEqual(self.request('GET', '/api/stats').status_code, 409)
        self.assertEqual(self.request('POST', '/api/workspace/create', json={'name': 'rebuilt'}).status_code, 200)
        self.assertEqual(config.get_active_workspace(), 'default')
        response = self.request('POST', '/api/workspace/switch', json={'workspace': 'rebuilt'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.request('GET', '/api/stats').json()['total'], 0)
        self.assertEqual(self.request('GET', '/api/overview').json()['projects'], [])
        self.assertEqual(self.request('GET', '/api/graph?scope=root&depth=1').status_code, 200)
        self.assert_preserved(metadata)

    def test_unrelated_value_errors_remain_unexpected_errors(self):
        with patch.object(vs, '_ensure_collection', side_effect=ValueError('unexpected storage failure')):
            with self.assertRaisesRegex(ValueError, 'unexpected storage failure'):
                self.request('GET', '/api/stats')


if __name__ == '__main__':
    unittest.main()
