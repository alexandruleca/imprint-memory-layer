import asyncio
from pathlib import Path
import unittest
from unittest.mock import patch

import httpx
import test_indexing_retrieval as indexing
from imprint import config, imprint_graph as kg, vectorstore as vs, workspace_policy as policy
from imprint.purge import purge


class WorkspacePurgeTests(unittest.TestCase):
    setUp = indexing.StorageTests.setUp
    cleanup = indexing.StorageTests.cleanup
    records = indexing.StorageTests.records
    contents = indexing.StorageTests.contents

    def prepare(self):
        self.addCleanup(kg._close_all)
        config.register_workspace('other')
        records = [self.records([text], source=source)[0] for text, source in
                   [('one', 'project/dir/a.py'), ('two', 'project/dir/b.py'), ('keep', 'project/dir-other/c.py')]]
        vs.store_batch(records)
        vs.store_batch(records, workspace='other')
        return records

    def test_preview_then_directory_purge_is_scoped_and_boundary_aware(self):
        self.prepare()
        kg.add('a', 'uses', 'b', source='project/dir/a.py')
        kg.add('c', 'uses', 'd', source='project/dir-other/c.py')
        result = purge('project/dir', kind='source', recursive=True)
        self.assertEqual((result['chunks'], result['sources']), (2, 2))
        self.assertEqual(len(self.contents()), 3)
        self.assertEqual(policy.exclusions(), [])
        purge('project/dir', kind='source', recursive=True, apply=True)
        self.assertEqual(self.contents(), ['keep'])
        self.assertEqual(vs.status(workspace='other')['total_memories'], 3)
        self.assertEqual(kg.count(), 1)
        with self.assertRaisesRegex(ValueError, 'excluded after purge'):
            kg.add('a', 'uses', 'b', source='project/dir/a.py')
        with self.assertRaisesRegex(ValueError, 'excluded after purge'):
            vs.store_batch(self.records(['restore by mistake'], source='project/dir/a.py'), force=True)
        policy.restore('project/dir', 'source')
        vs.store_batch(self.records(['explicitly restored'], source='project/dir/a.py'))

    def test_physical_path_purge_does_not_match_sibling_directory(self):
        self.addCleanup(kg._close_all)
        root = Path(self.data_directory.name) / 'project'
        records = self.records(['one'], source='project/a.py')
        records[0]['source_path'] = str(root / 'old' / 'gone.py')
        keep = self.records(['keep'], source='project/b.py')
        keep[0]['source_path'] = str(root / 'old-other' / 'keep.py')
        vs.store_batch(records + keep)
        purge(str(root / 'old'), recursive=True, apply=True)
        self.assertEqual(self.contents(), ['keep'])
        with self.assertRaises(ValueError):
            vs.store_batch(records)

    def test_purge_operates_without_loading_embeddings_or_requiring_current_profile(self):
        self.prepare()
        with patch.object(config, 'embedding_identity', return_value={'different': 'model'}), \
             patch('imprint.embeddings.embed_document') as embed:
            result = purge('project/dir/a.py', kind='source', apply=True)
            self.assertEqual(result['chunks'], 1)
            embed.assert_not_called()

    def test_orphan_graph_facts_are_previewed_and_removed_by_source(self):
        self.addCleanup(kg._close_all)
        kg.add('a', 'uses', 'b', source='project/old/file.py')
        kg.add('c', 'uses', 'd', source='project/old-other/file.py')
        preview = purge('project/old', kind='source', recursive=True)
        self.assertEqual((preview['chunks'], preview['facts']), (0, 1))
        self.assertEqual(kg.count(), 2)
        purge('project/old', kind='source', recursive=True, apply=True)
        self.assertEqual(kg.count(), 1)

    def test_purge_during_inference_blocks_the_late_upsert(self):
        self.prepare()
        def embed(texts):
            purge('project/dir', kind='source', recursive=True, apply=True)
            return [[1., 0., 0.] for _ in texts]
        with patch('imprint.embeddings.embed_documents_batch', side_effect=embed):
            with self.assertRaisesRegex(ValueError, 'excluded after purge'):
                vs.store_batch(self.records(['late result'], source='project/dir/late.py'))
        self.assertEqual(self.contents(), ['keep'])

    def test_replacement_does_not_follow_mid_write_workspace_switch(self):
        self.addCleanup(kg._close_all)
        config.register_workspace('alpha')
        config.register_workspace('beta')
        records = self.records(['old alpha'])
        vs.store_batch(records, workspace='alpha')
        vs.store_batch(self.records(['keep beta']), workspace='beta')
        config.switch_workspace('alpha')
        def embed(texts):
            config.switch_workspace('beta')
            return [[1., 0., 0.] for _ in texts]
        with patch('imprint.embeddings.embed_documents_batch', side_effect=embed):
            vs.replace_source(self.records(['new alpha']), 'project/app.py', 'project')
        self.assertEqual(vs.get_chunks_by_source('project/app.py', workspace='alpha')[0]['content'], 'new alpha')
        self.assertEqual(vs.get_chunks_by_source('project/app.py', workspace='beta')[0]['content'], 'keep beta')

    def test_http_apply_requires_explicit_workspace_and_strict_booleans(self):
        from imprint.api import app
        self.prepare()
        async def requests():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:8420') as client:
                response = await client.post('/api/sources/purge', json={'target': 'project/dir', 'kind': 'source', 'recursive': True})
                self.assertEqual(response.json()['chunks'], 2)
                response = await client.post('/api/sources/purge', json={'target': 'project/dir', 'apply': True})
                self.assertEqual(response.status_code, 400)
                response = await client.post('/api/sources/purge', json={'target': 'project/dir', 'apply': 'false', 'workspace': 'default'})
                self.assertEqual(response.status_code, 400)
                response = await client.post('/api/sources/purge', json={'target': 'project/dir', 'kind': 'source', 'recursive': True, 'apply': True, 'workspace': 'default'})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['chunks'], 2)
        asyncio.run(requests())
        self.assertEqual(vs.status(workspace='other')['total_memories'], 3)
