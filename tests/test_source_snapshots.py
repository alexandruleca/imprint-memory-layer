"""Full-source reconstruction, redaction, integrity and lifecycle integration."""
import asyncio
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import zlib
from unittest.mock import patch

from qdrant_client import QdrantClient, models as qm
from fastmcp import Client
from imprint import config, imprint_graph as kg, source_memory as sm, source_snapshots as ss, vectorstore as vs


class SourceFixture(unittest.TestCase):
    def setUp(self):
        kg._close_all()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = patch.dict(os.environ, {'IMPRINT_DATA_DIR': str(self.root), 'IMPRINT_WORKSPACE': 'default',
                                      'IMPRINT_LLM_TAGS': '0', 'IMPRINT_REDACT_PRIVACY_FILTER': 'false'})
        env.start(); self.addCleanup(env.stop)
        self.client = QdrantClient(':memory:')
        self.addCleanup(self.client.close)
        for item in (patch.object(vs, '_get_client', return_value=self.client),
                     patch.object(self.client, 'create_payload_index'),
                     patch.object(config, 'EMBEDDING_DIM', 3),
                     patch('imprint.embeddings.embed_documents_batch', side_effect=lambda xs: [[1., 0., 0.] for _ in xs]),
                     patch('imprint.tagger.derive_zero_shot', return_value=[])):
            item.start(); self.addCleanup(item.stop)
        vs._collections_ready.clear(); vs._id_caches.clear()
        self.addCleanup(vs._collections_ready.clear)
        self.addCleanup(vs._id_caches.clear)
        # Sync opens a cached graph connection. Windows cannot remove the
        # disposable data directory until that connection has been closed.
        self.addCleanup(kg._close_all)

    def rows(self, text, *, source='project/code.py', project='project', size=80):
        doc = sm.document(text)
        with patch.object(config, 'CHUNK_SIZE_CODE', size), patch.object(config, 'CHUNK_OVERLAP', 12):
            rows = sm.records(doc, prefix='[code.py]\n', code=True)
        for row in rows:
            row.update(project=project, source=source, type='finding', source_type='file',
                       source_path=str(self.root / 'code.py'))
        if rows:
            rows[0]['_source_document'] = doc
        return rows

    def store(self, text, *, source='project/code.py', project='project', workspace=None, **kw):
        return vs.replace_source(self.rows(text, source=source, project=project), source, project, workspace, **kw)


class SourceSnapshotTests(SourceFixture):
    def test_exact_utf8_indentation_and_line_endings_roundtrip(self):
        text = '  # café 🐈\r\n\r\ndef answer():\r\n    return "yes"\r\n' * 5 + 'x'
        self.store(text)
        snapshot = ss.get('project/code.py', 'project')
        self.assertEqual(snapshot['text'].encode(), text.encode())
        chunks = vs.get_chunks_by_source('project/code.py', project='project')
        self.assertEqual(sm.reconstruct(chunks, len(text)), text)
        self.assertTrue(ss.integrity(snapshot)['complete'])
        self.assertEqual(snapshot['metadata']['content_hash'], sm.digest(text))

    def test_failed_replacement_keeps_old_snapshot_then_retry_finishes(self):
        old = 'original source\n' * 20
        new = 'new content\n' * 35
        self.store(old)
        original = vs._store_redacted
        calls = 0
        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('embedding interrupted')
            return original(*args, **kwargs)
        with patch.object(vs, '_store_redacted', side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                self.store(new, batch_size=1)
        self.assertEqual(ss.get('project/code.py', 'project')['text'], old)
        self.store(new, batch_size=1)
        snapshot = ss.get('project/code.py', 'project')
        self.assertEqual(snapshot['text'], new)
        self.assertTrue(ss.integrity(snapshot)['complete'])

    def test_missing_chunk_is_detected_and_snapshot_still_reads(self):
        text = 'line with important evidence\n' * 30
        self.store(text)
        snapshot = ss.get('project/code.py', 'project')
        mid = snapshot['metadata']['manifest'][1]['id']
        _, coll = vs._ensure_collection()
        self.client.delete(coll, qm.PointIdsList(points=[vs._point_uuid(mid)]))
        integrity = ss.integrity(snapshot)
        self.assertFalse(integrity['complete'])
        self.assertEqual(integrity['missing_indices'], [1])
        self.assertEqual(ss.get('project/code.py', 'project')['text'], text)
        with self.assertRaisesRegex(ValueError, 'Missing source'):
            sm.reconstruct(vs.get_chunks_by_source('project/code.py'), len(text))

    def test_redaction_precedes_chunks_even_across_a_cut(self):
        secret = 'ghp_' + 'A2b3C4d5E6f7G8h9I0j1K2l3M4n5O6p7Q8r9'
        # The credential would straddle the 80-character raw-text cut.
        text = 'prefix ' * 10 + secret + '\nend\n'
        self.store(text)
        snapshot = ss.get('project/code.py', 'project')
        self.assertNotIn(secret, snapshot['text'])
        self.assertIn('[REDACTED:', snapshot['text'])
        self.assertTrue(ss.integrity(snapshot)['complete'])
        # A looser policy cannot put the old credential back into its snapshot.
        with patch('imprint.redaction.redact_all', side_effect=lambda xs: xs):
            self.store(text)
        self.assertNotIn(secret, ss.get('project/code.py', 'project')['text'])

    def test_workspace_isolation_version_pagination_and_unknown_sources(self):
        from imprint import server
        config.register_workspace('other')
        self.store('Canonical source with useful evidence.\n' * 20)
        async def read():
            async with Client(server.mcp) as client:
                response = await client.call_tool('read_source', {'source': 'project/code.py', 'project': 'project', 'max_chars': 100})
                first = json.loads(response.content[0].text)
                self.assertTrue(first['index']['complete'])
                output, start = first['content'], first['next_start']
                while start is not None:
                    page = json.loads((await client.call_tool('read_source', {'source': 'project/code.py', 'project': 'project',
                        'start': start, 'max_chars': 100, 'version': first['version']})).content[0].text)
                    output += page['content']; start = page['next_start']
                self.assertEqual(output, ss.get('project/code.py', 'project')['text'])
                stale = json.loads((await client.call_tool('read_source', {'source': 'project/code.py', 'version': 'old'})).content[0].text)
                self.assertIn('version changed', stale['error'])
                foreign = json.loads((await client.call_tool('read_source', {'source': 'project/code.py', 'workspace': 'other'})).content[0].text)
                self.assertFalse(foreign['snapshot_available'])
        asyncio.run(read())

    def test_live_export_import_preserves_source_and_integrity(self):
        from imprint import sync_ws
        config.register_workspace('other')
        text = 'source context and decision rationale\n' * 30
        self.store(text)
        lines = sync_ws.stream_export_lines('default', batch_size=2)
        transfer = self.root / 'sync.jsonl'; transfer.write_text('\n'.join(lines))
        stats = sync_ws.process_import_buffer(str(transfer), 'other')
        self.assertEqual(stats['sources']['imported'], 1)
        snapshot = ss.get('project/code.py', 'project', 'other')
        self.assertEqual(snapshot['text'], text)
        self.assertFalse(snapshot['metadata']['provenance']['source_path_local'])
        self.assertTrue(ss.integrity(snapshot, 'other')['complete'])

    def test_sync_updates_manifest_on_chunks_that_reuse_existing_vectors(self):
        from imprint import sync_ws
        config.register_workspace('other')
        text = 'Repeated stable source evidence.\n' * 30
        transfer = self.root / 'sync.jsonl'
        for body in (text, text + 'A new final finding.\n'):
            self.store(body)
            transfer.write_text('\n'.join(sync_ws.stream_export_lines('default', batch_size=2)))
            sync_ws.process_import_buffer(str(transfer), 'other')
            snapshot = ss.get('project/code.py', 'project', 'other')
            self.assertEqual(snapshot['text'], body)
            self.assertTrue(ss.integrity(snapshot, 'other')['complete'])

    def test_incomplete_transfer_and_purge_do_not_restore_source(self):
        from imprint import purge
        self.store('context\n' * 30)
        frames = list(ss.export_parts('default'))
        config.register_workspace('other')
        importer = ss.Importer('other')
        bad = {**frames[0], 'parts': frames[0]['parts'] + 1}
        importer.feed(bad)
        with self.assertRaisesRegex(ValueError, 'Incomplete'):
            importer.finish()
        self.assertIsNone(ss.get('project/code.py', 'project', 'other'))
        purge.purge('project/code.py', kind='source', apply=True)
        self.assertIsNone(ss.get('project/code.py', 'project'))
        importer = ss.Importer('default')
        for frame in frames:
            importer.feed(frame)
        importer.finish()
        self.assertIsNone(ss.get('project/code.py', 'project'))

    def test_project_move_keeps_reconstructable_source(self):
        from imprint import projects
        text = 'Keep the rationale with its evidence.\n' * 10
        self.store(text)
        projects.move('project', 'renamed', apply=True)
        self.assertIsNone(ss.get('project/code.py', 'project'))
        moved = ss.get('project/code.py', 'renamed')
        self.assertEqual(moved['text'], text)
        self.assertTrue(ss.integrity(moved)['complete'])

    def test_missing_chunks_and_profile_changes_invalidate_skip_watermarks(self):
        source = 'conversation/test'
        self.store('durable evidence\n' * 30, source=source)
        vs.complete_conversation(source, 123., 3)
        self.assertEqual(vs.get_source_mtimes(learner_version=3, source_profile=sm.profile())[source], 123.)
        with patch.object(config, 'CHUNK_OVERLAP', config.CHUNK_OVERLAP + 1):
            self.assertEqual(vs.get_source_mtimes(learner_version=3, source_profile=sm.profile())[source], 0.)
        snapshot = ss.get(source, 'project')
        _, coll = vs._ensure_collection()
        self.client.delete(coll, qm.PointIdsList(points=[vs._point_uuid(snapshot['metadata']['manifest'][1]['id'])]))
        self.assertEqual(vs.get_source_mtimes(learner_version=3)[source], 0.)

    def test_explicit_memory_deletion_invalidates_full_source_archive(self):
        self.store('This content must not remain recoverable through the source reader.\n' * 10)
        snapshot = ss.get('project/code.py', 'project')
        self.assertTrue(vs.delete(snapshot['metadata']['manifest'][0]['id']))
        self.assertIsNone(ss.get('project/code.py', 'project'))

    def test_partial_topic_copy_cannot_reveal_unselected_source_text(self):
        from imprint.cli_migrate import migrate
        self.store('Public information\n' * 10 + 'UNSELECTED_PRIVATE_CONTEXT\n' * 10)
        snapshot = ss.get('project/code.py', 'project')
        _, coll = vs._ensure_collection()
        self.client.set_payload(coll, {'tags': {'topics': ['selected']}},
            points=[vs._point_uuid(snapshot['metadata']['manifest'][0]['id'])])
        migrate('default', 'curated', topic='selected', copy=True)
        self.assertIsNone(ss.get('project/code.py', 'project', 'curated'))
        self.assertIsNotNone(ss.get('project/code.py', 'project'))
        migrate('default', 'curated', source='project/code.py', copy=True)
        self.assertTrue(ss.integrity(ss.get('project/code.py', 'project', 'curated'), 'curated')['complete'])

    def test_single_file_ingest_keeps_tiny_empty_and_crlf_sources(self):
        from imprint import cli_index, workspace_policy
        workspace_policy.add_root(str(self.root))
        path = self.root / 'tiny.py'
        for text in ('x = 1\r\n', '  \r\n', ''):
            path.write_bytes(text.encode())
            with patch('imprint.progress.write_progress'), patch('imprint.progress.clear_progress'):
                cli_index.ingest_single_file(str(path), 'fixture')
            snapshot = ss.get('fixture/tiny.py', 'fixture')
            self.assertEqual(snapshot['text'], text)
            self.assertTrue(ss.integrity(snapshot)['complete'])

    def test_single_file_ingest_reports_reading_chunking_and_embedding(self):
        from imprint import cli_index, progress, workspace_policy
        workspace_policy.add_root(str(self.root))
        path = self.root / 'notes.md'
        path.write_text('\n\n'.join(f'Paragraph {i} about the release plan. ' * 20 for i in range(60)))
        output = io.StringIO()
        with patch('imprint.progress.write_progress', wraps=progress.write_progress) as record, \
                patch('sys.stdout', io.StringIO()), patch('sys.stderr', output):
            cli_index.ingest_single_file(str(path), 'fixture', batch_size=2)
        phases = [call.kwargs['phase'] for call in record.call_args_list]
        self.assertEqual(list(dict.fromkeys(phases)), ['reading', 'chunking', 'embedding'])
        embedding = record.call_args_list[-1]
        self.assertEqual(embedding.kwargs['unit'], 'chunks')
        self.assertGreater(embedding.args[2], 1)  # Its total is the number of chunks.
        self.assertIn('ingest: embedding', output.getvalue())
        self.assertIn('Indexing Complete', output.getvalue())
        self.assertIsNone(progress.read_progress())  # The record is dropped at the end.

    def test_trailing_whitespace_only_edit_updates_exact_reused_chunk(self):
        for text in ('x = 1\n', 'x = 1\r\n\r\n'):
            self.store(text)
            snapshot = ss.get('project/code.py', 'project')
            self.assertTrue(ss.integrity(snapshot)['complete'])
            self.assertEqual(sm.reconstruct(vs.get_chunks_by_source('project/code.py'), len(text)), text)

    def test_corrupt_or_oversized_transfers_fail_without_publishing(self):
        self.store('Original readable context.\n' * 10)
        original = ss.get('project/code.py', 'project')
        bad = {**original, 'metadata': {**original['metadata'], 'content_hash': 'wrong'}}
        with self.assertRaisesRegex(ValueError, 'checksum'):
            ss.publish(bad)
        self.assertEqual(ss.get('project/code.py', 'project'), original)
        with self.assertRaisesRegex(ValueError, 'oversized'):
            ss._inflate(zlib.compress(b'a' * 1024), limit=128)
        with self.assertRaisesRegex(ValueError, 'corrupt'):
            ss._inflate(zlib.compress(b'ok') + b'trailing')

    def test_stricter_export_redaction_preserves_original_source_freshness(self):
        self.store('secret-note\n' * 10)
        snapshot = ss.get('project/code.py', 'project')
        with patch('imprint.redaction.redact_all', side_effect=lambda xs: [x.replace('secret-note', '[REDACTED:secret:11]') for x in xs]):
            cleaned = ss.sanitize(snapshot)
        self.assertNotIn('secret-note', cleaned['text'])
        self.assertEqual(cleaned['metadata']['updated_at'], snapshot['metadata']['updated_at'])
        self.assertEqual(sm.reconstruct([{'content': r['prefix'] + cleaned['text'][r['start']:r['end']],
            'source_start': r['start'], 'source_end': r['end'], 'source_prefix': r['prefix']}
            for r in cleaned['metadata']['manifest']], len(cleaned['text'])), cleaned['text'])


class ConversationSourceTests(unittest.TestCase):
    def test_short_turns_markup_tool_evidence_and_analysis_are_retained(self):
        from imprint.conversation_source import prepare
        rows = [
            {'type': 'user', 'message': {'role': 'user', 'content': '<button>Pay</button>'}},
            {'type': 'assistant', 'message': {'content': [
                {'type': 'text', 'text': 'Yes.'}, {'type': 'thinking', 'thinking': 'Visible rationale: test before changing the handler.'},
                {'type': 'tool_use', 'name': 'test', 'id': 'call-1', 'input': {'cmd': 'run tests'}}]}},
            {'type': 'user', 'isMeta': True, 'message': {'role': 'user', 'content': 'Client context'}},
            {'type': 'user', 'message': {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'call-1', 'content': 'Tests passed: 12'}]}},
            {'type': 'user', 'message': {'role': 'user', 'content': 'OK'}},
            {'type': 'response_item', 'payload': {'type': 'reasoning', 'encrypted_content': 'opaque-data', 'summary': []}},
        ]
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'IMPRINT_DATA_DIR': temp}):
            path = Path(temp) / 'session.jsonl'; path.write_text('\n'.join(json.dumps(r) for r in rows))
            doc = prepare(str(path))
        for text in ('<button>Pay</button>', 'Yes.', 'Visible rationale', 'run tests', 'Tests passed: 12', 'OK', 'Client context'):
            self.assertIn(text, doc['text'])
        self.assertNotIn('opaque-data', doc['text'])
        self.assertEqual(doc['metadata']['omissions']['opaque_reasoning'], 1)
        self.assertIn('"role":"context"', doc['text'])
        self.assertEqual(sm.reconstruct(sm.records(doc), len(doc['text'])), doc['text'])

    def test_bytes_a_crash_never_wrote_are_counted_not_failed(self):
        # As a crash leaves them: a NUL-only line, then a record appended after
        # a NUL run on the same line (ADR 0088).
        from imprint.conversation_source import prepare
        first = json.dumps({'type': 'user', 'message': {'role': 'user', 'content': 'before the crash'}})
        after = json.dumps({'type': 'user', 'message': {'role': 'user', 'content': 'after the crash'}})
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'IMPRINT_DATA_DIR': temp}):
            path = Path(temp) / 'session.jsonl'
            path.write_text(first + '\n' + '\x00' * 900 + '\n' + '\x00' * 140 + after + '\n')
            doc = prepare(str(path))
        self.assertIn('before the crash', doc['text'])
        self.assertIn('after the crash', doc['text'])
        self.assertNotIn('\x00', doc['text'])
        self.assertEqual(doc['metadata']['omissions']['zero_filled_lines'], 2)
        self.assertEqual(doc['metadata']['event_count'], 2)

    def test_invalid_transcript_fails_instead_of_claiming_complete(self):
        from imprint.conversation_source import prepare
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'session.jsonl'; path.write_text('{bad json\n')
            with self.assertRaisesRegex(ValueError, 'Malformed'):
                prepare(str(path))
