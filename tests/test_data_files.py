"""Data files kept whole and found by a digest (ADR 0195)."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from qdrant_client import QdrantClient

from imprint import config, config_schema, data_files, long_events, retrieval, source_memory, vectorstore as vs

_REAL_RESOLVE = config_schema.resolve


def settings(**overrides):
    values = {'ingest.data_files': 'whole', 'ingest.data_file_min_kb': 64, 'ingest.doc_formats': 'json,csv',
              'tagger.zero_shot': False, 'tagger.llm': False, 'redact.privacy_filter': False,
              'associations.enabled': False}
    values.update({key.replace('__', '.'): value for key, value in overrides.items()})
    return lambda key: (values[key], 'config') if key in values else _REAL_RESOLVE(key)


def fixture(rows=900):
    """A floor-plan fixture of the kind that filled the audited store: one
    record per wall, each a few coordinates."""
    walls = [{'id': f'wall_{i}', 'layer': 'A-WALL', 'start': [i * 0.5, 3.115], 'end': [i * 0.5 + 4.2, 3.115],
              'note': 'origin' if i == 0 else 'door beside the column'} for i in range(rows)]
    return json.dumps({'units': 'm', 'walls': walls}, indent=2) + '\n'


class ReasonTests(unittest.TestCase):
    def reason(self, path, size, **overrides):
        with patch('imprint.config_schema.resolve', side_effect=settings(**overrides)):
            return data_files.reason(path, size)

    def test_folders_of_records_keep_files_whole_above_sixteen_kib(self):
        for path in ['src/floor/import/__tests__/fixtures/glo-terrace.json', 'output/regress/vlm/input_1_floor_38.json',
                     'training/data/brief-digests.json', 'packages/i18n/locales/es-ES.json',
                     'test/visual/baselines/home.json', 'C:\\work\\app\\testdata\\rows.csv']:
            with self.subTest(path=path):
                self.assertTrue(self.reason(path, 20_000))
                self.assertEqual(self.reason(path, 10_000), '')

    def test_any_large_data_file_is_kept_whole(self):
        self.assertEqual(self.reason('config/forms/layouts/OPC1Form.json', 224 * 1024), 'large')
        self.assertEqual(self.reason('perf/results.csv', 70 * 1024), 'large')
        self.assertEqual(self.reason('config/settings.json', 40 * 1024), '')

    def test_code_documents_and_chat_exports_keep_their_chunks(self):
        self.assertEqual(self.reason('fixtures/big.py', 900_000), '')
        self.assertEqual(self.reason('output/report.md', 900_000), '')
        self.assertEqual(self.reason('exports/conversations.json', 9_000_000), '')
        self.assertEqual(self.reason('exports/conversations-007.json', 9_000_000), '')

    def test_records_mode_keeps_every_file_record_by_record(self):
        self.assertEqual(self.reason('fixtures/glo.json', 9_000_000, ingest__data_files='records'), '')

    def test_a_stored_file_converts_when_its_form_differs(self):
        whole = {'doc_metadata': {'data_file': {'format': 'json'}}}
        chunked = {'doc_metadata': {'source_format': 'json_array'}}
        with patch('imprint.config_schema.resolve', side_effect=settings()):
            self.assertTrue(data_files.converts('fixtures/glo.json', 90_000, chunked))
            self.assertFalse(data_files.converts('fixtures/glo.json', 90_000, whole))
            self.assertFalse(data_files.converts('fixtures/glo.json', 90_000, None))
        with patch('imprint.config_schema.resolve', side_effect=settings(ingest__data_files='records')):
            self.assertTrue(data_files.converts('fixtures/glo.json', 90_000, whole))
            self.assertFalse(data_files.converts('fixtures/glo.json', 90_000, chunked))


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.data = patch.object(config, 'get_data_dir', return_value=Path(self.dir.name))
        self.data.start()
        self.addCleanup(self.data.stop)
        self.resolve = patch('imprint.config_schema.resolve', side_effect=settings())
        self.resolve.start()
        self.addCleanup(self.resolve.stop)
        self.tags = patch('imprint.tagger.build_payload_tags', return_value={'lang': 'json'})
        self.tags.start()
        self.addCleanup(self.tags.stop)

    def build(self, name, text):
        path = Path(self.dir.name) / name
        path.write_bytes(text.encode('utf-8'))
        size = path.stat().st_size
        return data_files.records(str(path), f'fixtures/{name}', 'app', f'app/fixtures/{name}',
                                  why='fixtures', size=size, mtime=1.0, pending=False, zero_shot=False)

    def test_pieces_rebuild_the_file_byte_for_byte(self):
        text = fixture(900).replace('\n', '\r\n')
        document, rows = self.build('walls.json', text)
        self.assertEqual(document['text'], text)
        self.assertEqual(source_memory.reconstruct(rows, len(text)), text)
        self.assertEqual([row['chunk_index'] for row in rows], list(range(len(rows))))
        self.assertIs(rows[0]['_source_document'], document)
        self.assertEqual(rows[0]['doc_metadata']['data_file']['pieces'], len(rows))
        self.assertTrue(all(row['type'] == 'data' and row['source_type'] == 'file' for row in rows))

    def test_a_file_longer_than_a_piece_is_split_at_lines_without_gaps(self):
        text = fixture(6000)
        _, rows = self.build('walls.json', text)
        self.assertGreater(len(rows), 1)
        self.assertTrue(all(row['source_end'] - row['source_start'] <= long_events.PIECE_MAX for row in rows))
        self.assertTrue(all(text[row['source_end'] - 1] == '\n' for row in rows))
        self.assertEqual(source_memory.reconstruct(rows, len(text)), text)

    def test_the_snapshot_accepts_the_pieces(self):
        from imprint import source_snapshots
        _, rows = self.build('walls.json', fixture(900))
        prepared = source_snapshots.prepare(rows[0]['_source_document'], rows, 'app/fixtures/walls.json', 'app')
        source_snapshots._validate(prepared['metadata'], prepared['text'])
        self.assertEqual(prepared['text'], fixture(900))

    def test_the_digest_describes_structure_and_a_first_record(self):
        _, rows = self.build('walls.json', fixture(900))
        head, data = data_files.split(rows[0]['content'])
        self.assertTrue(head.startswith('[fixtures/walls.json]\n--- {"kind": "data_file"'))
        self.assertIn('walls: array of 900 × {id: string, layer: string, start: array', head)
        self.assertIn('"id": "wall_0"', head)
        self.assertEqual(rows[0]['source_prefix'], head)
        self.assertEqual(data, fixture(900)[:len(data)])

    def test_a_csv_digest_names_its_columns(self):
        text = 'timestamp,endpoint,latency_ms\n' + ''.join(f'2024-12-06T11:14:{i % 60:02d},/api/order,{i}\n' for i in range(3000))
        _, rows = self.build('results.csv', text)
        head, _ = data_files.split(rows[0]['content'])
        self.assertIn('CSV of about 3,000 rows; columns: timestamp, endpoint, latency_ms', head)

    def test_secrets_are_redacted_before_the_digest_and_the_snapshot(self):
        token = 'ghp_' + 'a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8'
        text = json.dumps({'token': token, 'rows': [{'n': i} for i in range(2000)]})
        document, rows = self.build('creds.json', text)
        self.assertNotIn(token, document['text'])
        self.assertTrue(all(token not in row['content'] for row in rows))


class SearchTests(unittest.TestCase):
    def piece(self):
        with patch('imprint.config_schema.resolve', side_effect=settings()):
            text = fixture(2000)
            head = data_files.prefix('fixtures/glo-terrace.json', text, 'json', 'fixtures', len(text))
        return head + text

    def test_the_vector_is_computed_from_the_digest_and_a_sample(self):
        piece = self.piece()
        view = long_events.embedding_text(piece)
        self.assertTrue(view.startswith(data_files.split(piece)[0]))
        self.assertLessEqual(len(view), config.CHUNK_SIZE_PROSE + 500)

    def test_other_chunks_are_embedded_as_before(self):
        self.assertEqual(long_events.embedding_text('short'), 'short')
        self.assertIsNone(data_files.embedding_view('[app.py]\n--- {"kind": "tool_output"} ---\nx'))

    def test_common_words_in_the_data_do_not_match_but_the_digest_and_identifiers_do(self):
        row = {'content': self.piece(), 'source': 'app/fixtures/glo-terrace.json'}
        # Only the records after the first (outside the digest) say this.
        self.assertEqual(retrieval.lexical_score('door beside column', row), 0)
        # The path, the keys and the first record are the digest.
        self.assertGreater(retrieval.lexical_score('glo terrace fixture walls', row), 0)
        self.assertGreater(retrieval.lexical_score('layer note', row), 0)
        # An exact identifier anywhere in the data still finds the file.
        self.assertGreaterEqual(retrieval.lexical_score('wall_1999', row), 2)
        ordinary = {'content': 'the door beside the column', 'source': 'app/notes.md'}
        self.assertGreater(retrieval.lexical_score('door beside column', ordinary), 0)

    def test_folding_case_cannot_move_the_digest_boundary(self):
        text = json.dumps([{'straße': 'groß'} for _ in range(3000)])
        piece = data_files.prefix('fixtures/strasse.json', text, 'json', 'fixtures', len(text)) + text
        head, data = data_files.split(piece)
        self.assertEqual(data, text)
        self.assertIn('straße', head)


class IngestTests(unittest.TestCase):
    """A data file through `imprint ingest`/`refresh`, on an in-memory index."""

    def setUp(self):
        self.data = tempfile.TemporaryDirectory()
        self.addCleanup(self.data.cleanup)
        self.client = QdrantClient(':memory:')
        self.mode = {'value': 'whole'}
        resolve = lambda key: (self.mode['value'], 'config') if key == 'ingest.data_files' else settings()(key)
        self.patches = [patch.object(config, 'get_data_dir', return_value=Path(self.data.name)),
                        patch.object(vs, '_get_client', return_value=self.client),
                        patch.object(vs, '_wal_log'), patch.object(config, 'EMBEDDING_DIM', 3),
                        patch('imprint.embeddings.embed_documents_batch', side_effect=lambda texts, **_: [[1., 0., 0.] for _ in texts]),
                        patch('imprint.embeddings.embed_document', return_value=[1., 0., 0.]),
                        patch('imprint.embeddings.embed_query', return_value=[1., 0., 0.]),
                        patch('imprint.tagger.derive_zero_shot', return_value=[]),
                        patch('imprint.tagger.build_payload_tags', return_value={}),
                        patch('imprint.config_schema.resolve', side_effect=resolve),
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

    def chunks(self):
        client, coll = vs._ensure_collection()
        rows, _ = client.scroll(coll, limit=10_000, with_payload=['content', 'doc_metadata'])
        return [row.payload for row in rows]

    def test_a_fixture_is_kept_whole_restores_exactly_and_changes_form_with_the_setting(self):
        from imprint import cli_index, source_snapshots
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / 'tests' / 'fixtures'
            folder.mkdir(parents=True)
            text = fixture(900)
            (folder / 'walls.json').write_bytes(text.encode('utf-8'))
            (Path(directory) / 'app.py').write_text('def main():\n    return 1\n')
            run = lambda command: cli_index.main(command=command)
            with patch('sys.argv', ['index', directory, directory + ':app']), contextlib.redirect_stdout(io.StringIO()):
                run('ingest')
                whole = [row for row in self.chunks() if row['content'].startswith('[tests/fixtures/walls.json]')]
                self.assertEqual(len(whole), 1)
                self.assertEqual(whole[0]['doc_metadata']['data_file']['reason'], 'fixtures')
                self.assertEqual(source_snapshots.get('app/tests/fixtures/walls.json', 'app')['text'], text)

                self.mode['value'] = 'records'
                run('refresh')
                records = [row for row in self.chunks() if row['content'].startswith('[tests/fixtures/walls.json]')]
                self.assertGreater(len(records), 1)
                self.assertFalse(any(data_files.stored_whole(row) for row in records))

                self.mode['value'] = 'whole'
                run('refresh')
                again = [row for row in self.chunks() if row['content'].startswith('[tests/fixtures/walls.json]')]
                self.assertEqual(len(again), 1)
                self.assertEqual(source_snapshots.get('app/tests/fixtures/walls.json', 'app')['text'], text)
                self.assertTrue(any(row['content'].startswith('[app.py]') for row in self.chunks()))


if __name__ == '__main__':
    unittest.main()
