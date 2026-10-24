"""Fresh project views, moving memories between projects (ADR 0036), and
documents with nothing to index."""
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from qdrant_client import QdrantClient

from imprint import (cli_viz, config, device, extractors, imprint_graph as kg, projects, sync_ws,
                     vectorstore as vs, workspace_policy as policy)


class ProjectStoreTests(unittest.TestCase):
    """In-memory Qdrant with a real WAL in a temporary data directory."""

    def setUp(self):
        self.data = tempfile.TemporaryDirectory()
        self.addCleanup(self.data.cleanup)
        self.client = QdrantClient(':memory:')
        self.addCleanup(self.client.close)
        for item in [patch.object(config, 'get_data_dir', return_value=Path(self.data.name)),
                     patch.object(vs, '_get_client', return_value=self.client),
                     patch.object(config, 'EMBEDDING_DIM', 3),
                     patch('imprint.embeddings.embed_documents_batch',
                           side_effect=lambda texts: [[1., 0., 0.] for _ in texts]),
                     patch('imprint.embeddings.embed_document', return_value=[1., 0., 0.]),
                     patch('imprint.tagger.derive_zero_shot', return_value=[]),
                     patch.object(self.client, 'create_payload_index'),
                     patch.dict(os.environ, {'IMPRINT_WORKSPACE': ''})]:
            item.start()
            self.addCleanup(item.stop)
        self.addCleanup(kg._close_all)
        for clear in (vs._collections_ready.clear, vs._id_caches.clear, cli_viz.reset_cache):
            clear()
            self.addCleanup(clear)

    def records(self, texts, project='Desktop', source='Desktop/a.pdf'):
        return [{'content': text, 'project': project, 'source': source, 'source_type': 'file',
                 'chunk_index': i} for i, text in enumerate(texts)]

    def placed(self, workspace=None):
        client, coll = vs._ensure_collection(workspace)
        rows, _ = client.scroll(coll, limit=1000, with_payload=True)
        return sorted((row.payload['project'], row.payload['source'], row.payload['content']) for row in rows)

    def ids(self, workspace=None):
        client, coll = vs._ensure_collection(workspace)
        rows, _ = client.scroll(coll, limit=1000, with_payload=['_mid'])
        return sorted(row.payload['_mid'] for row in rows)

    def rules(self, workspace='default'):
        with policy._db() as conn:
            return projects._placement_rules(conn, workspace)


class FreshViewTests(ProjectStoreTests):
    def test_cached_views_follow_writes_made_by_other_processes(self):
        vs.store_batch(self.records(['first document']))
        self.assertEqual([p['name'] for p in cli_viz._get_overview()['projects']], ['Desktop'])
        # An ingest subprocess adds a project. Nothing tells this process.
        vs.store_batch(self.records(['notes'], project='fresh', source='fresh/notes.md'))
        self.assertEqual(sorted(p['name'] for p in cli_viz._get_overview()['projects']), ['Desktop', 'fresh'])
        self.assertEqual(cli_viz._get_project_detail('fresh')['count'], 1)
        # A payload-only update (retagging) changes no count; its WAL entry is the signal.
        self.assertEqual(cli_viz._get_project_detail('Desktop')['types'], [])
        _, coll = vs._ensure_collection()
        self.client.set_payload(coll, {'type': 'decision'}, points=[
            vs._point_uuid(vs._make_id('first document', 'Desktop', 'Desktop/a.pdf', 0))])
        vs._wal_log('retag', count=1)
        self.assertEqual(cli_viz._get_project_detail('Desktop')['types'], [{'name': 'decision', 'count': 1}])

    def test_unchanged_data_is_served_from_the_cache(self):
        vs.store_batch(self.records(['first document']))
        cli_viz._get_overview()
        with patch.object(cli_viz, 'build_overview') as build:
            cli_viz._get_overview()
            build.assert_not_called()


class MoveTests(ProjectStoreTests):
    def setUp(self):
        super().setUp()
        vs.replace_source(self.records(['scanned form', 'second page']), 'Desktop/a.pdf', 'Desktop')
        vs.replace_source(self.records(['unrelated'], source='Desktop/b.pdf'), 'Desktop/b.pdf', 'Desktop')

    def test_preview_changes_nothing(self):
        before = self.placed()
        preview = projects.move('Desktop', 'branding', ['Desktop/a.pdf'])
        self.assertEqual((preview['chunks'], preview['sources'], preview['target_exists'], preview['applied']),
                         (2, 1, False, False))
        self.assertEqual((self.placed(), self.rules()), (before, {}))

    def test_moved_source_stays_through_refresh_without_reembedding(self):
        kg.add('form', 'mentions', 'rotation', source='Desktop/a.pdf')
        projects.move('Desktop', 'branding', ['Desktop/a.pdf'], workspace='default', apply=True)
        moved = [('Desktop', 'Desktop/b.pdf', 'unrelated'), ('branding', 'Desktop/a.pdf', 'scanned form'),
                 ('branding', 'Desktop/a.pdf', 'second page')]
        self.assertEqual(self.placed(), moved)
        # Refresh names the file's project from the directory again.
        with patch('imprint.embeddings.embed_documents_batch') as encode:
            vs.replace_source(self.records(['scanned form', 'second page']), 'Desktop/a.pdf', 'Desktop')
            encode.assert_not_called()
        self.assertEqual(self.placed(), moved)
        vs.replace_source(self.records(['edited form']), 'Desktop/a.pdf', 'Desktop')
        self.assertEqual(self.placed(), [moved[0], ('branding', 'Desktop/a.pdf', 'edited form')])
        # Graph facts reference the unchanged source key.
        self.assertEqual(kg.query(subject='form')[0]['source'], 'Desktop/a.pdf')

    def test_each_device_keeps_its_placement_through_sync(self):
        config.register_workspace('peer')
        self.sync('default', 'peer')
        projects.move('Desktop', 'branding', ['Desktop/a.pdf'], workspace='default', apply=True)
        mover = self.placed()
        # The peer has no rule: the moved copies dedupe with its own.
        self.sync('default', 'peer')
        self.assertEqual(self.placed('peer'), [('Desktop', 'Desktop/a.pdf', 'scanned form'),
                                               ('Desktop', 'Desktop/a.pdf', 'second page'),
                                               ('Desktop', 'Desktop/b.pdf', 'unrelated')])
        # Its old copies do not undo the move here.
        self.sync('peer', 'default')
        self.assertEqual(self.placed(), mover)
        # The same move on the peer gives both devices the same IDs.
        projects.move('Desktop', 'branding', ['Desktop/a.pdf'], workspace='peer', apply=True)
        self.assertEqual(self.ids('peer'), self.ids())
        self.sync('peer', 'default')
        self.assertEqual(self.placed(), mover)

    def sync(self, source, target):
        buffer = Path(self.data.name) / 'sync-buffer.jsonl'
        buffer.write_text('\n'.join(sync_ws.stream_export_lines(source)))
        sync_ws.process_import_buffer(str(buffer), target)

    def test_merging_a_project_keeps_note_ids_and_places_later_writes(self):
        note = 'A decision about the scanned forms'
        vs.store(note, project='Desktop', type='decision')
        vs.store_batch(self.records(['handbook'], project='Documents', source='Documents/handbook.md'))
        result = projects.move('Desktop', 'Documents', workspace='default', apply=True)
        self.assertEqual((result['whole'], result['target_exists'], result['chunks']), (True, True, 4))
        self.assertEqual({project for project, _, _ in self.placed()}, {'Documents'})
        count = len(self.placed())
        # A note keeps its kind of ID, so storing it again under its old name dedupes.
        self.assertEqual(vs.store(note, project='Desktop', type='decision'), vs._make_id(note, 'Documents', ''))
        vs.store_batch(self.records(['new scan'], source='Desktop/new.pdf'))
        self.assertEqual(len(self.placed()), count + 1)
        self.assertIn(('Documents', 'Desktop/new.pdf', 'new scan'), self.placed())

    def test_moving_back_leaves_no_rule(self):
        projects.move('Desktop', 'branding', ['Desktop/a.pdf'], workspace='default', apply=True)
        projects.move('branding', 'Desktop', ['Desktop/a.pdf'], workspace='default', apply=True)
        self.assertEqual(self.rules(), {})
        projects.move('Desktop', 'Archive', workspace='default', apply=True)
        projects.move('Archive', 'Desktop', workspace='default', apply=True)
        self.assertEqual(self.rules(), {('Archive', ''): 'Desktop'})
        self.assertEqual({project for project, _, _ in self.placed()}, {'Desktop'})

    def test_source_moved_out_of_a_merged_project_follows_its_origin(self):
        projects.move('Desktop', 'Documents', workspace='default', apply=True)
        projects.move('Documents', 'branding', ['Desktop/a.pdf'], workspace='default', apply=True)
        vs.replace_source(self.records(['scanned form', 'second page']), 'Desktop/a.pdf', 'Desktop')
        self.assertEqual(self.placed(), [('Documents', 'Desktop/b.pdf', 'unrelated'),
                                         ('branding', 'Desktop/a.pdf', 'scanned form'),
                                         ('branding', 'Desktop/a.pdf', 'second page')])

    def test_http_apply_requires_the_previewed_workspace(self):
        import asyncio
        import httpx
        from imprint.api import app

        async def requests():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url='http://127.0.0.1:8420') as client:
                body = {'project': 'Desktop', 'target': 'branding', 'sources': ['Desktop/a.pdf']}
                preview = await client.post('/api/projects/move', json=body)
                self.assertEqual((preview.json()['chunks'], preview.json()['applied']), (2, False))
                for refused in ({**body, 'apply': True}, {**body, 'apply': 'true', 'workspace': 'default'},
                                {**body, 'sources': 'Desktop/a.pdf'}, {**body, 'target': 'Desktop'}):
                    self.assertEqual((await client.post('/api/projects/move', json=refused)).status_code, 400)
                applied = await client.post('/api/projects/move', json={**body, 'apply': True, 'workspace': 'default'})
                self.assertEqual(applied.json()['applied'], True)
        asyncio.run(requests())
        self.assertIn(('branding', 'Desktop/a.pdf', 'scanned form'), self.placed())

    def test_invalid_moves_are_refused(self):
        for args in [('Desktop', 'Desktop', None), ('Desktop', ' padded', None), ('Desktop', 'x', [])]:
            with self.assertRaises(ValueError):
                projects.move(*args)


class MovedPathHintTests(ProjectStoreTests):
    def test_moved_file_hint_resolves_under_its_origin_mapping(self):
        root = Path(self.data.name) / 'home'
        for folder in ('Desktop', 'branding'):
            (root / folder).mkdir(parents=True)
            (root / folder / 'a.pdf').write_text(folder)
        policy.add_root(str(root))
        policy.map_project('Desktop', str(root / 'Desktop'))
        policy.map_project('branding', str(root / 'branding'))
        vs.replace_source([{**record, 'source_path': str(root / 'Desktop' / 'a.pdf')}
                           for record in self.records(['scanned form'])], 'Desktop/a.pdf', 'Desktop')
        projects.move('Desktop', 'branding', ['Desktop/a.pdf'], workspace='default', apply=True)
        expected = (root / 'Desktop' / 'a.pdf').resolve()
        with patch('os.getcwd', return_value=str(root)):
            summary = vs.get_source_summary('Desktop/a.pdf')
            chunk = vs.get_chunks_by_source('Desktop/a.pdf')[0]
        self.assertEqual(summary['project'], 'branding')
        for context in (summary['path_context'], chunk['path_context']):
            self.assertEqual(context['path_status'], 'local')
            self.assertEqual(Path(context['local_source_path']), expected)


class OriginPathTests(unittest.TestCase):
    def test_moved_file_resolves_under_the_project_it_was_indexed_in(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for folder in ('Desktop', 'branding'):
                (root / folder).mkdir()
                (root / folder / 'a.pdf').write_text(folder)
            with patch.object(config, 'get_data_dir', return_value=root / 'data'), \
                 patch.dict(os.environ, {'IMPRINT_WORKSPACE': ''}):
                policy.add_root(str(root))
                policy.map_project('Desktop', str(root / 'Desktop'))
                policy.map_project('branding', str(root / 'branding'))
                record = device.prepare({'source_path': str(root / 'Desktop' / 'a.pdf'), 'source_type': 'file',
                                         'source': 'Desktop/a.pdf', 'project': 'Desktop'})
                moved = {**record, 'project': 'branding', 'origin_project': 'Desktop'}
                view = device.context(moved, cwd=root)
        self.assertEqual(view['path_status'], 'local')
        self.assertEqual(Path(view['local_source_path']), (root / 'Desktop' / 'a.pdf').resolve())


class NoTextDocumentTests(ProjectStoreTests):
    def setUp(self):
        super().setUp()
        self.folder = Path(self.data.name) / 'Desktop'
        self.folder.mkdir()
        (self.folder / 'scan.pdf').write_bytes(b'%PDF scanned pages')
        (self.folder / 'text.pdf').write_bytes(b'%PDF with a text layer')
        self.ocr = False
        self.unreadable = {'scan.pdf'}
        self.extract = patch('imprint.extractors.dispatch_by_ext', side_effect=lambda path: [extractors.ExtractedDoc(
            text='' if Path(path).name in self.unreadable else f'readable text of {Path(path).name}',
            chunk_mode='prose')])
        for item in [self.extract,
                     patch('imprint.config_schema.resolve', side_effect=self.setting),
                     patch('imprint.chunker.chunk_file',
                           side_effect=lambda text, *args, **kwargs: [(text, 0)] if text.strip() else []),
                     patch('imprint.tagger.build_payload_tags', return_value={}),
                     patch('shutil.which', return_value=None)]:
            item.start()
            self.addCleanup(item.stop)

    def setting(self, key):
        values = {'ingest.doc_formats': 'pdf', 'ingest.ocr_enabled': self.ocr, 'ingest.ocr_lang': 'eng',
                  'ingest.use_kreuzberg': False, 'ingest.max_file_bytes': 2_000_000}
        return values.get(key, False), 'default'

    def ingest(self, *args):
        from imprint import cli_index
        output = io.StringIO()
        argv = ['index', *args] if args else ['index', str(self.folder), f'{self.folder}:Desktop']
        with patch('sys.argv', argv), contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            cli_index.main(command='refresh')
        return output.getvalue()

    def test_scanned_pdf_is_skipped_with_a_reason_and_not_retried(self):
        output = self.ingest()
        self.assertIn('Indexing Complete', output)
        self.assertIn('scan.pdf: no text layer', output)
        self.assertIn('ingest.ocr_enabled true', output)
        self.assertEqual(self.placed(), [('Desktop', 'Desktop/text.pdf', 'readable text of text.pdf')])
        extract = extractors.dispatch_by_ext
        extract.reset_mock()
        self.assertIn('No text:  1', self.ingest())
        extract.assert_not_called()
        # Enabling OCR can change the result, so the document is read again.
        self.ocr = True
        self.assertIn('OCR cannot run', self.ingest())
        self.assertEqual([Path(call.args[0]).name for call in extract.call_args_list], ['scan.pdf'])

    def test_document_that_loses_its_text_keeps_its_earlier_chunks(self):
        self.ingest()
        (self.folder / 'text.pdf').write_bytes(b'%PDF rescanned without a text layer')
        self.unreadable.add('text.pdf')
        output = self.ingest()
        self.assertIn('text.pdf: no text layer (scanned or image-only). OCR is off', output)
        self.assertIn('kept the chunks of its earlier version', output)
        self.assertIn(('Desktop', 'Desktop/text.pdf', 'readable text of text.pdf'), self.placed())

    def test_single_scanned_file_is_nothing_to_index(self):
        output = self.ingest('--file', str(self.folder / 'scan.pdf'), '--project', 'Desktop')
        self.assertIn('Nothing to index:', output)
        self.assertEqual(self.placed(), [])


if __name__ == '__main__':
    unittest.main()
