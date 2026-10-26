import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from qdrant_client import QdrantClient, models
from imprint import vectorstore as vs
from imprint import config
from imprint.source_state import allowed_source, fingerprint, unchanged, missing_sources
from imprint.retrieval import fuse, query_terms


class FingerprintTests(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.TemporaryDirectory()
        self.addCleanup(self.data.cleanup)
        self.data_patch = patch.object(config, 'get_data_dir', return_value=Path(self.data.name))
        self.data_patch.start()
        self.addCleanup(self.data_patch.stop)

    def test_changes_with_preserved_timestamp(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'app.py'
            path.write_text('first version')
            original = fingerprint(path)
            path.write_text('other version')
            os.utime(path, (original['source_mtime'], original['source_mtime']))
            self.assertFalse(unchanged(fingerprint(path), original))

    def test_pruning_stays_inside_requested_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            states = {'local': {'source_path': str(root / 'gone.py')},
                      'other': {'source_path': str(root.parent / 'elsewhere.py')}, 'legacy': {}}
            from imprint.device import current
            for state in states.values():
                state.update(source_device=current(), source_path_local=True)
            self.assertEqual(missing_sources(states, [directory]), ['local'])

    def test_pruning_normalizes_aliases_without_following_escapes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'root'
            root.mkdir()
            alias = Path(directory) / 'alias'
            outside = Path(directory) / 'outside'
            outside.mkdir()
            try:
                alias.symlink_to(root, target_is_directory=True)
                (root / 'escape').symlink_to(outside, target_is_directory=True)
            except OSError as error:
                self.skipTest(f'Directory symlinks unavailable: {error}')
            states = {'local': {'source_path': str(alias / 'gone.py')},
                      'escape': {'source_path': str(root / 'escape' / 'gone.py')}}
            from imprint.device import current
            for state in states.values():
                state.update(source_device=current(), source_path_local=True)
            self.assertEqual(missing_sources(states, [str(root)]), ['local'])
            self.assertEqual(missing_sources(states, [str(alias)]), ['local'])

    def test_secrets_and_symlink_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('.env', '.env.local', 'id_rsa', 'private.key'):
                self.assertFalse(allowed_source(root / name))
            self.assertFalse(allowed_source(root.parent / 'outside.py', root))


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.data_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.data_directory.cleanup)
        self.client = QdrantClient(':memory:')
        self.patches = [patch.object(config, 'get_data_dir', return_value=Path(self.data_directory.name)),
                        patch.object(vs, '_get_client', return_value=self.client),
                        patch.object(vs, '_wal_log'), patch.object(config, 'EMBEDDING_DIM', 3),
                        patch('imprint.embeddings.embed_documents_batch', side_effect=lambda texts: [[1., 0., 0.] for _ in texts]),
                        patch('imprint.embeddings.embed_document', return_value=[1., 0., 0.]),
                        patch('imprint.embeddings.embed_query', return_value=[1., 0., 0.]),
                        patch('imprint.tagger.derive_zero_shot', return_value=[]),
                        patch.object(self.client, 'create_payload_index')]
        for item in self.patches:
            item.start()
        vs._collections_ready.clear()
        vs._id_caches.clear()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        vs._collections_ready.clear()
        vs._id_caches.clear()
        for item in reversed(self.patches):
            item.stop()
        self.client.close()

    def records(self, texts, project='project', source='project/app.py'):
        return [{'content': text, 'project': project, 'source': source, 'source_type': 'file',
                 'chunk_index': i} for i, text in enumerate(texts)]

    def contents(self, project='project'):
        client, coll = vs._ensure_collection()
        rows, _ = client.scroll(coll, scroll_filter=models.Filter(must=[models.FieldCondition(key='project', match=models.MatchValue(value=project))]), limit=100)
        return sorted(row.payload['content'] for row in rows)

    def test_full_content_identity_and_repeated_chunks(self):
        prefix = 'A' * 220
        records = self.records([prefix + 'one', prefix + 'two', prefix + 'two'])
        inserted, _ = vs.store_batch(records)
        self.assertEqual(inserted, 3)
        self.assertEqual(len(self.contents()), 3)
        self.assertNotIn('_mid', records[0])

    def test_forced_replacement_updates_vectors_for_identical_content(self):
        records = self.records(['unchanged source bytes'])
        vs.replace_source(records, 'project/app.py', 'project')
        with patch('imprint.embeddings.embed_documents_batch', return_value=[[0., 1., 0.]]) as encode:
            self.assertEqual(vs.replace_source(records, 'project/app.py', 'project'), (0, 1))
            encode.assert_not_called()
            self.assertEqual(vs.replace_source(records, 'project/app.py', 'project', force=True), (1, 0))
            encode.assert_called_once()
        rows, _ = self.client.scroll(config.collection_name(), with_vectors=True)
        self.assertEqual(rows[0].vector['dense'], [0., 1., 0.])

    def test_collection_rejects_equal_dimension_model_changes(self):
        vs.store_batch(self.records(['indexed with original profile']))
        vs._collections_ready.clear()
        with patch.object(config, 'embedding_identity', return_value='different-same-dimension-model'):
            with self.assertRaisesRegex(ValueError, 'profile mismatch'):
                vs.store_batch(self.records(['new content']))

    def test_legacy_nonempty_collection_requires_explicit_rebuild(self):
        self.client.create_collection(config.collection_name(), vectors_config={'dense': models.VectorParams(size=3, distance=models.Distance.COSINE)})
        self.client.upsert(config.collection_name(), [models.PointStruct(id=1, vector={'dense': [1., 0., 0.]})])
        with self.assertRaisesRegex(ValueError, 'unversioned'):
            vs._ensure_collection()

    def test_precomputed_vectors_require_same_profile(self):
        record = {**self.records(['synced content'])[0], 'vector': [1., 0., 0.]}
        with self.assertRaisesRegex(ValueError, 'profile mismatch'):
            vs.store_batch_precomputed([record])
        record['embedding_identity'] = config.embedding_identity()
        self.assertEqual(vs.store_batch_precomputed([record]), (1, 0, 0))

    def test_sync_preserves_note_identity_and_rejects_unrelated_ids(self):
        mid = vs.store('A durable note', project='project', source='manual')
        record = {'content': 'A durable note', 'project': 'project', 'source': 'manual',
                  'chunk_index': 0, 'id': mid, 'vector': [1., 0., 0.],
                  'embedding_identity': config.embedding_identity()}
        self.assertEqual(vs.store_batch_precomputed([record]), (0, 0, 1))
        record['id'] = 'aaaaaaaaaaaaaaaa'
        with self.assertRaisesRegex(ValueError, 'logical ID'):
            vs.store_batch_precomputed([record])

    def received(self, mid, **classification):
        return {'content': 'A durable note', 'project': 'project', 'source': 'manual', 'chunk_index': 0,
                'id': mid, 'vector': [1., 0., 0.], 'embedding_identity': config.embedding_identity(),
                **classification}

    def classification(self, mid):
        point = self.client.retrieve(config.collection_name(), [vs._point_uuid(mid)], with_payload=True)[0]
        return {key: point.payload.get(key) for key in ('tags', 'type', 'llm_tagged', 'tagged_at')}

    def test_a_newer_received_classification_replaces_the_local_one(self):
        mid = vs.store('A durable note', project='project', source='manual')
        retagged = {'tags': {'domain': ['auth'], 'topics': ['token-refresh']}, 'type': 'decision',
                    'llm_tagged': True, 'tagged_at': 2000.0}
        self.assertEqual(vs.store_batch_precomputed([self.received(mid, **retagged)]), (0, 1, 0))
        self.assertEqual(self.classification(mid), {
            'tags': {'lang': '', 'layer': '', 'kind': '', 'domain': ['auth'], 'topics': ['token-refresh']},
            'type': 'decision', 'llm_tagged': True, 'tagged_at': 2000.0})

    def test_an_older_or_equal_received_classification_is_ignored(self):
        mid = vs.store('A durable note', project='project', source='manual')
        client, coll = vs._ensure_collection()
        with patch('imprint.vectorstore.time.time', return_value=2000.0):
            vs.set_classifications(client, coll, [(vs._point_uuid(mid), {'domain': ['local']}, 'pattern')])
        local = self.classification(mid)
        for stamp in (1999.0, local['tagged_at']):
            stale = {'tags': {'domain': ['stale']}, 'type': 'bug', 'tagged_at': stamp}
            self.assertEqual(vs.store_batch_precomputed([self.received(mid, **stale)]), (0, 0, 1))
        self.assertEqual(self.classification(mid), local)

    def test_a_local_retag_supersedes_the_stamp_it_replaces_whatever_the_clock(self):
        mid = vs.store('A durable note', project='project', source='manual')
        client, coll = vs._ensure_collection()
        point = vs._point_uuid(mid)
        vs.store_batch_precomputed([self.received(mid, tags={'domain': ['peer']}, tagged_at=5000.0)])
        with patch('imprint.vectorstore.time.time', return_value=100.0):  # this clock is far behind
            vs.set_classifications(client, coll, [(point, {'domain': ['local']}, '')])
        self.assertGreater(self.classification(mid)['tagged_at'], 5000.0)
        self.assertEqual(self.classification(mid)['tags']['domain'], ['local'])

    def test_force_refresh_bypasses_unchanged_file_hashes(self):
        from imprint import cli_index
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'app.py'
            path.write_text('unchanged implementation with useful source content')
            settings = lambda key: (False, 'env') if key.startswith(('tagger.', 'redact.')) else (2_000_000, 'default')
            with patch('sys.argv', ['index', directory, directory + ':fixture']), \
                 patch('imprint.config_schema.resolve', side_effect=settings), \
                 patch('imprint.chunker.chunk_file', side_effect=lambda text, *args, **kwargs: [(text, 0)]), \
                 patch('imprint.tagger.build_payload_tags', return_value={}), \
                 patch('imprint.embeddings.embed_documents_batch', return_value=[[1., 0., 0.]]) as encode, \
                 contextlib.redirect_stdout(io.StringIO()):
                cli_index.main()
                encode.reset_mock()
                cli_index.main(command='refresh')
                encode.assert_not_called()
                with patch('sys.argv', ['refresh', '--force', directory, directory + ':fixture']):
                    cli_index.main(command='refresh')
                encode.assert_called_once()

    def test_replacement_preserves_old_source_on_embedding_failure(self):
        vs.replace_source(self.records(['old complete content']), 'project/app.py', 'project')
        with patch('imprint.embeddings.embed_documents_batch', side_effect=RuntimeError('embedding unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'embedding unavailable'):
                vs.replace_source(self.records(['new content']), 'project/app.py', 'project')
        self.assertEqual(self.contents(), ['old complete content'])

    def test_replacement_prunes_old_chunks_without_cross_project_deletion(self):
        vs.store_batch(self.records(['old chunk', 'removed chunk']))
        vs.store_batch(self.records(['other project content'], project='other'))
        vs.replace_source(self.records(['new chunk']), 'project/app.py', 'project')
        self.assertEqual(self.contents(), ['new chunk'])
        self.assertEqual(self.contents('other'), ['other project content'])
        vs.replace_source(self.records(['new chunk']), 'project/app.py', 'project')
        self.assertEqual(self.contents(), ['new chunk'])

    def test_incomplete_embedding_batch_never_prunes(self):
        vs.store_batch(self.records(['old content']))
        with patch('imprint.embeddings.embed_documents_batch', return_value=[]):
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                vs.replace_source(self.records(['replacement']), 'project/app.py', 'project')
        self.assertEqual(self.contents(), ['old content'])

    def test_cross_process_deletion_cannot_make_cached_ids_skip_replacement(self):
        records = self.records(['restore this content'])
        vs.store_batch(records)
        vs._get_existing_ids()  # Simulate an MCP process holding an old cache.
        _, coll = vs._ensure_collection()
        self.client.delete(coll, models.FilterSelector(filter=models.Filter(must=[
            models.FieldCondition(key='project', match=models.MatchValue(value='project'))])))
        vs.replace_source(records, 'project/app.py', 'project')
        self.assertEqual(self.contents(), ['restore this content'])

    def test_storage_cleanup_failure_is_visible(self):
        with patch.object(self.client, 'delete', side_effect=RuntimeError('storage unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'storage unavailable'):
                vs.delete_by_source('project/app.py', project='project')

    def test_logical_memory_id_can_be_read(self):
        from imprint.cli_viz import get_memory
        mid = vs.store('A durable decision', project='project', type='decision')
        self.assertEqual(get_memory(mid)['content'], 'A durable decision')
        self.assertEqual(get_memory(vs._point_uuid(mid))['content'], 'A durable decision')

    def test_index_refresh_and_deletion_share_fingerprints(self):
        from imprint import cli_index
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'app.py'
            path.write_text('first implementation of the source')
            settings = lambda key: (False, 'env') if key.startswith(('tagger.', 'redact.')) else (2_000_000, 'default')
            with patch('sys.argv', ['index', directory, directory + ':fixture']), \
                 patch('imprint.config_schema.resolve', side_effect=settings), \
                 patch('imprint.chunker.chunk_file', side_effect=lambda text, *args, **kwargs: [(text, 0)]), \
                 patch('imprint.tagger.build_payload_tags', return_value={}), \
                 contextlib.redirect_stdout(io.StringIO()):
                cli_index.main()
                old_time = path.stat().st_mtime
                path.write_text('other implementation of the source')
                os.utime(path, (old_time, old_time))
                cli_index.main(command='refresh')
                self.assertEqual(self.contents('fixture'), ['other implementation of the source'])
                path.unlink()
                cli_index.main(command='refresh')
                self.assertEqual(self.contents('fixture'), [])
                path.write_text('source before clearing')
                cli_index.main(command='refresh')
                path.write_text('')
                cli_index.main(command='refresh')
                self.assertEqual(self.contents('fixture'), [])

    def test_single_file_handles_large_empty_and_invalid_text(self):
        from imprint import cli_index
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'single.py'
            settings = lambda key: (False, 'env') if key.startswith(('tagger.', 'redact.')) else (2_000_000, 'default')
            with patch('imprint.config_schema.resolve', side_effect=settings), \
                 patch('imprint.chunker.chunk_file', side_effect=lambda text, *args, **kwargs: [(text, 0)]), \
                 patch('imprint.tagger.build_payload_tags', return_value={}), \
                 contextlib.redirect_stdout(io.StringIO()):
                for newline in ('\n', '\r\n'):
                    with self.subTest(newline=repr(newline)):
                        content = ('large source content' + newline) * 3000
                        # Text-mode writes translate LF to CRLF on Windows.
                        # Ingestion now preserves the bytes actually written.
                        path.write_bytes(content.encode('utf-8'))
                        cli_index.ingest_single_file(str(path), 'fixture')
                        # A failed assertEqual on thousands of near-identical
                        # lines spends minutes constructing a difflib report.
                        self.assertTrue(self.contents('fixture') == [content],
                                        'Large source did not retain its exact line endings')
                        path.write_bytes(b'\xff invalid UTF-8')
                        with self.assertRaises(UnicodeDecodeError):
                            cli_index.ingest_single_file(str(path), 'fixture')
                        self.assertTrue(self.contents('fixture') == [content],
                                        'Invalid UTF-8 replaced the prior source')
                        path.write_bytes(b'')
                        cli_index.ingest_single_file(str(path), 'fixture')
                        self.assertEqual(self.contents('fixture'), [])


class RetrievalTests(unittest.TestCase):
    def test_identifier_found_by_lexical_path_is_not_lost(self):
        semantic = [{'id': 'semantic', 'content': 'general background information', 'similarity': .7}]
        lexical = [{'id': 'exact', 'content': 'def rotate_refresh_token(): pass', 'similarity': 0.}]
        results = fuse('rotate_refresh_token', semantic, lexical)
        self.assertEqual(results[0]['id'], 'exact')
        self.assertEqual(results[0]['match_type'], 'lexical')

    def test_fusion_order_is_deterministic_and_deduplicated(self):
        rows = [{'id': 'b', 'content': 'retry connection'}, {'id': 'a', 'content': 'retry connection'}]
        result = fuse('retry', rows, list(reversed(rows)))
        self.assertEqual(len(result), 2)
        self.assertEqual(result, fuse('retry', rows, rows))
        self.assertEqual(query_terms('How should we use rotate_token?'), ['rotate_token'])
