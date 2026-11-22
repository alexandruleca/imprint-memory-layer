"""Synced memories keep the time they were first stored (ADR 0200), and
upkeep brings what is stored up to date in the idle slot (ADR 0201)."""
import contextlib
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from qdrant_client import QdrantClient, models as qm

import test_indexing_retrieval as indexing
from imprint import config, device, imprint_graph as kg, queue, tag_backlog, upkeep
from imprint import source_snapshots as ss, vectorstore as vs

A = {'id': 'aaaaaaaa', 'name': 'laptop-A', 'os': 'windows'}
B = {'id': 'bbbbbbbb', 'name': 'desktop-B', 'os': 'linux'}
OCTOBER = 1790000000.0   # 2026-09-21
NOVEMBER = 1794000000.0  # 2026-11-06


class FirstStoredTests(unittest.TestCase):
    def test_the_origin_s_record_time_then_the_sender_s_never_the_future(self):
        now = 1800000000.0
        self.assertEqual(vs.first_stored({'recorded_at': OCTOBER, 'timestamp': NOVEMBER}, now), OCTOBER)
        self.assertEqual(vs.first_stored({'recorded_at': None, 'timestamp': NOVEMBER}, now), NOVEMBER)
        self.assertEqual(vs.first_stored({'recorded_at': now + 3600}, now), now)
        for broken in ({}, {'recorded_at': 'yesterday'}, {'timestamp': float('nan')}, {'recorded_at': True},
                       {'recorded_at': 0, 'timestamp': -5}):
            with self.subTest(record=broken):
                self.assertEqual(vs.first_stored(broken, now), now)


class SyncKeepsTheTimeTests(unittest.TestCase):
    setUp = indexing.StorageTests.setUp
    cleanup = indexing.StorageTests.cleanup

    def payload(self, mid, workspace):
        client, coll = vs._ensure_collection(workspace)
        (point,) = client.retrieve(coll, [vs._point_uuid(mid)], with_payload=True)
        return point.payload

    def test_a_received_memory_is_dated_when_its_origin_stored_it(self):
        from imprint.sync_ws import process_import_buffer, stream_export_lines
        self.addCleanup(kg._close_all)
        config.register_workspace('received')
        with patch.object(device, 'current', return_value=A):
            kept = vs.store('We chose local storage so the app works offline.', project='p', source='s1')
            relayed = vs.store('The relay forwards encrypted frames only.', project='p', source='s2')
            legacy = vs.store('A note from before provenance was recorded.', project='p', source='s3')
        client, coll = vs._ensure_collection()
        # As A holds them: recorded in October; one A itself received in
        # November (the old bug), one from before recorded_at existed.
        client.set_payload(coll, {'timestamp': OCTOBER, 'recorded_at': OCTOBER}, [vs._point_uuid(kept)])
        client.set_payload(coll, {'timestamp': NOVEMBER, 'recorded_at': OCTOBER}, [vs._point_uuid(relayed)])
        client.set_payload(coll, {'timestamp': OCTOBER}, [vs._point_uuid(legacy)])
        client.delete_payload(coll, ['recorded_at'], [vs._point_uuid(legacy)])
        buffer = Path(self.data_directory.name) / 'sync.jsonl'
        buffer.write_text('\n'.join(stream_export_lines('default')))
        with patch.object(device, 'current', return_value=B):
            stats = process_import_buffer(str(buffer), 'received')
        self.assertEqual(stats['memories']['inserted'], 3)
        self.assertEqual(self.payload(kept, 'received')['timestamp'], OCTOBER)
        self.assertEqual(self.payload(relayed, 'received')['timestamp'], OCTOBER)
        self.assertEqual(self.payload(legacy, 'received')['timestamp'], OCTOBER)
        self.assertEqual(self.payload(legacy, 'received')['recorded_at'], OCTOBER)


class Store(unittest.TestCase):
    """An in-memory store, a data folder and this device's identity."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / 'data'
        self.data.mkdir()
        env = patch.dict(os.environ, {'IMPRINT_DATA_DIR': str(self.data), 'IMPRINT_WORKSPACE': 'default',
                                      'IMPRINT_LLM_TAGS': '0', 'IMPRINT_REDACT_PRIVACY_FILTER': 'false'})
        env.start(); self.addCleanup(env.stop)
        for key in ('IMPRINT_UPKEEP_BACKGROUND', 'IMPRINT_INGEST_DATA_FILES', 'IMPRINT_INGEST_DATA_FILE_MIN_KB'):
            os.environ.pop(key, None)
        self.client = QdrantClient(':memory:')
        self.addCleanup(self.client.close)
        for item in (patch.object(vs, '_get_client', return_value=self.client),
                     patch.object(vs, '_wal_log'),
                     patch.object(self.client, 'create_payload_index'),
                     patch.object(config, 'EMBEDDING_DIM', 3),
                     patch('imprint.embeddings.embed_documents_batch', side_effect=lambda xs, **_: [[1., 0., 0.] for _ in xs]),
                     patch('imprint.embeddings.embed_document', return_value=[1., 0., 0.]),
                     patch('imprint.tagger.derive_zero_shot', return_value=[]),
                     patch('imprint.tagger.build_payload_tags', return_value={}),
                     patch.object(upkeep, '_lower_priority', side_effect=AssertionError('priority lowered in a test'))):
            item.start(); self.addCleanup(item.stop)
        vs._collections_ready.clear(); vs._id_caches.clear()
        self.addCleanup(vs._collections_ready.clear)
        self.addCleanup(vs._id_caches.clear)
        upkeep.reset(); self.addCleanup(upkeep.reset)
        self.me = device.current()['id']

    def paired(self):
        """As a device that paired with another: it may hold synced memories."""
        import sqlite3
        conn = sqlite3.connect(self.data / 'sync-pairs.sqlite3')
        with conn:
            conn.execute('CREATE TABLE pairs (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
            conn.execute("INSERT INTO pairs VALUES ('p1', '{}')")
        conn.close()

    def run_upkeep(self, *args, **kwargs):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = upkeep.run(*args, **kwargs)
        return code, out.getvalue()

    def ledger(self, step, workspace='default'):
        return upkeep.read().get('workspaces', {}).get(workspace, {}).get(step, {})


class TimestampStepTests(Store):
    def setUp(self):
        super().setUp()
        self.paired()
        client, coll = vs._ensure_collection()
        now = time.time()
        peer = {'id': 'peer0001', 'name': 'peer', 'os': 'linux'}
        me = device.current()
        rows = {  # name: (recorded_device, recorded_at, timestamp)
            'late-1': (peer, OCTOBER, NOVEMBER), 'late-2': (peer, OCTOBER + 60, NOVEMBER),
            'late-3': (peer, OCTOBER + 120, NOVEMBER), 'on-time': (peer, OCTOBER, OCTOBER + 5),
            'mine': (me, OCTOBER, NOVEMBER), 'before-provenance': (None, None, NOVEMBER),
            # A peer whose clock runs a day ahead.
            'ahead': (peer, now + 86400, now + 2 * 86400),
        }
        client.upsert(coll, points=[qm.PointStruct(
            id=index + 1, vector={config.QDRANT_VECTOR_NAME: [1., 0., 0.]},
            payload={'content': name, 'project': 'p', 'source': name, 'timestamp': stamp,
                     **({'recorded_device': origin} if origin else {}),
                     **({'recorded_at': recorded} if recorded else {})})
            for index, (name, (origin, recorded, stamp)) in enumerate(rows.items())])

    def stamps(self):
        client, coll = vs._ensure_collection()
        points, _ = client.scroll(coll, limit=100, with_payload=['content', 'timestamp'])
        return {p.payload['content']: p.payload['timestamp'] for p in points}

    def test_received_memories_take_back_their_origin_s_time_once(self):
        self.assertIn(('default', 'timestamps'), upkeep.due(fresh=True))
        code, out = self.run_upkeep()
        self.assertEqual(code, 0, out)
        stamps = self.stamps()
        self.assertEqual((stamps['late-1'], stamps['late-2'], stamps['late-3']), (OCTOBER, OCTOBER + 60, OCTOBER + 120))
        self.assertEqual(stamps['on-time'], OCTOBER + 5)       # within a minute: left alone
        self.assertEqual(stamps['mine'], NOVEMBER)             # recorded here
        self.assertEqual(stamps['before-provenance'], NOVEMBER)
        self.assertLessEqual(stamps['ahead'], time.time())      # a clock ahead never dates the future
        self.assertIn('4 memories from paired devices dated', out)
        self.assertEqual((self.ledger('timestamps')['version'], self.ledger('timestamps')['fixed']), (1, 4))
        self.assertNotIn(('default', 'timestamps'), upkeep.due(fresh=True))

    def test_it_resumes_where_a_queued_job_stopped_it(self):
        asked = []

        def stop():
            asked.append(1)
            return len(asked) == 2  # after the first page

        with patch.object(upkeep, '_PAGE', 2):
            code, _ = self.run_upkeep(stop)
            self.assertEqual(code, tag_backlog.YIELDED)
            cursor = self.ledger('timestamps')['cursor']
            self.assertIsNotNone(cursor)
            self.assertNotIn('version', self.ledger('timestamps'))
            # One page of two done: late-1 and late-2.
            self.assertEqual(sum(stamp == NOVEMBER for stamp in self.stamps().values()), 3)
            code, _ = self.run_upkeep()
        self.assertEqual(code, 0)
        self.assertEqual(self.ledger('timestamps')['fixed'], 4)
        self.assertEqual(self.stamps()['late-3'], OCTOBER + 120)

    def test_writes_go_in_small_requests(self):
        writes = []
        real = self.client.batch_update_points

        def counted(collection, operations, **options):
            writes.append(sum(len(op.set_payload.points) for op in operations))
            return real(collection, operations, **options)

        with patch.object(upkeep, '_WRITE', 2), patch.object(self.client, 'batch_update_points', side_effect=counted):
            code, out = self.run_upkeep()
        self.assertEqual(code, 0, out)
        self.assertEqual(writes, [2, 2])  # four memories to date, two per request
        self.assertEqual(self.ledger('timestamps')['fixed'], 4)

    def test_it_steps_aside_between_writes_and_passes_over_what_it_dated(self):
        asked = []
        with patch.object(upkeep, '_WRITE', 2):
            # Asked at the page's start, then before its second write.
            code, _ = self.run_upkeep(lambda: asked.append(1) or len(asked) == 2)
            self.assertEqual(code, tag_backlog.YIELDED)
            self.assertEqual(self.ledger('timestamps')['fixed'], 2)
            self.assertIsNone(self.ledger('timestamps').get('cursor'))  # the page is read again
            code, _ = self.run_upkeep()
        self.assertEqual(code, 0)
        self.assertEqual(self.ledger('timestamps')['fixed'], 4)
        stamps = self.stamps()
        self.assertEqual((stamps['late-1'], stamps['late-3']), (OCTOBER, OCTOBER + 120))

    def test_a_store_too_busy_to_answer_ends_the_run_and_only_repeated_timeouts_fail(self):
        import httpx
        from qdrant_client.http.exceptions import ResponseHandlingException

        def busy(workspace, entry, run):
            entry['cursor'] = 'half-way'
            run.save()
            raise ResponseHandlingException(httpx.ReadTimeout('timed out'))

        step = upkeep.Step('timestamps', 'a step the store is too busy for', upkeep._timestamps_due, busy)
        with patch.dict(upkeep._BY_NAME, {'timestamps': step}):
            for attempt in range(1, upkeep.TIMEOUTS_BEFORE_FAILING):
                code, out = self.run_upkeep()
                self.assertEqual(code, 0, out)
                self.assertIn('too busy to answer in time (timed out); the next idle run goes on', out)
                self.assertNotIn('failed', out)
                self.assertNotIn('data files', out)  # the run ends there
                self.assertEqual(self.ledger('timestamps')['timeouts'], attempt)
                self.assertEqual(self.ledger('timestamps')['cursor'], 'half-way')
            code, out = self.run_upkeep()
            self.assertEqual(code, 1)
            self.assertIn('failed: timed out', out)
        # A run that gets through starts the count again.
        through = upkeep.Step('timestamps', 'a step that gets through', upkeep._timestamps_due, lambda *_: True)
        with patch.dict(upkeep._BY_NAME, {'timestamps': through}):
            code, out = self.run_upkeep()
        self.assertEqual(code, 0, out)
        self.assertNotIn('timeouts', self.ledger('timestamps'))

    def test_other_errors_still_fail_at_once(self):
        def broken(workspace, entry, run):
            raise ValueError('no such field')

        step = upkeep.Step('timestamps', 'a broken step', upkeep._timestamps_due, broken)
        with patch.dict(upkeep._BY_NAME, {'timestamps': step}):
            code, out = self.run_upkeep()
        self.assertEqual(code, 1)
        self.assertIn('failed: no such field', out)
        self.assertNotIn('timeouts', self.ledger('timestamps'))

    def test_a_dry_run_changes_nothing(self):
        before = self.stamps()
        code, out = self.run_upkeep(dry_run=True)
        self.assertEqual(code, 0)
        self.assertIn('would date 4 memories', out)
        self.assertEqual(self.stamps(), before)
        self.assertEqual(upkeep.read(), {})

    def test_a_workspace_without_a_collection_is_settled_without_creating_one(self):
        config.register_workspace('empty')
        self.assertIn(('empty', 'timestamps'), upkeep.due(fresh=True))
        self.run_upkeep()
        self.assertFalse(self.client.collection_exists(config.collection_name('empty')))
        self.assertEqual(upkeep.due(fresh=True), [])


def records(n):
    return json.dumps([{'id': i, 'room': f'Room {i}', 'polygon': [[i, 0], [i + 1, 0], [i + 1, 1], [i, 1]],
                        'tags': ['floor', 'import', 'fixture']} for i in range(n)], indent=2)


class DataFileStepTests(Store):
    def setUp(self):
        super().setUp()
        self.folder = Path(self.temp.name) / 'app'
        (self.folder / 'fixtures').mkdir(parents=True)
        (self.folder / 'fixtures' / 'plan.json').write_text(records(300), encoding='utf-8')
        (self.folder / 'small.json').write_text('{"name": "app"}', encoding='utf-8')
        (self.folder / 'main.py').write_text('def main():\n    return 1\n', encoding='utf-8')
        # Indexed by a release that kept data files as record chunks.
        self.index(imprint_ingest_data_files='records')
        self.source = 'app/fixtures/plan.json'

    def index(self, **env):
        from imprint import cli_index
        with patch('sys.argv', ['index', str(self.folder), f'{self.folder}:app']), \
                patch.dict(os.environ, {key.upper(): value for key, value in env.items()}), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            cli_index.main(command='ingest')

    def chunks(self, source):
        _, coll = vs._ensure_collection()
        rows, _ = self.client.scroll(coll, scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key='source', match=qm.MatchValue(value=source))]), limit=1000)
        return rows

    def head(self, source):
        return next(h for h in ss.list_files() if h['source'] == source)

    def test_a_data_file_held_as_records_is_kept_whole_without_a_refresh(self):
        before = len(self.chunks(self.source))
        self.assertGreater(before, 3)
        text = (self.folder / 'fixtures' / 'plan.json').read_bytes().decode('utf-8')
        self.assertIn(('default', 'data-files'), upkeep.due(fresh=True))
        code, out = self.run_upkeep()
        self.assertEqual(code, 0, out)
        after = self.chunks(self.source)
        self.assertEqual(len(after), 1)
        self.assertEqual(after[0].payload['doc_metadata']['data_file']['reason'], 'fixtures')
        self.assertEqual(self.head(self.source)['data_format'], 'json')
        self.assertEqual(ss.get(self.source, 'app')['text'], text)  # restorable byte for byte
        self.assertIn(f'{self.source}: {before:,} chunks -> 1, kept whole', out)
        # Small data files and code stay as they were.
        self.assertEqual(self.head('app/small.json')['data_format'], '')
        self.assertTrue(self.chunks('app/main.py'))
        self.assertEqual(upkeep.due(fresh=True), [])

    def test_a_dry_run_names_the_files_and_changes_nothing(self):
        before = len(self.chunks(self.source))
        code, out = self.run_upkeep(dry_run=True)
        self.assertEqual(code, 0)
        self.assertIn(f'would keep whole: {self.source} ({before:,} chunks now)', out)
        self.assertEqual(len(self.chunks(self.source)), before)
        self.assertEqual(upkeep.read(), {})

    def test_a_file_in_a_stopped_wsl_distribution_waits_for_it(self):
        before = len(self.chunks(self.source))
        with patch.object(upkeep, '_distribution', return_value='Ubuntu-26.04'), \
                patch.object(upkeep, '_running', return_value=set()):
            code, out = self.run_upkeep()
            self.assertEqual(code, 0)
            self.assertEqual(len(self.chunks(self.source)), before)
            self.assertIn('wait until WSL runs it', out)
            self.assertEqual(self.ledger('data-files')['waiting'], ['Ubuntu-26.04'])
            self.assertNotIn(('default', 'data-files'), upkeep.due(fresh=True))
        with patch.object(upkeep, '_distribution', return_value='Ubuntu-26.04'), \
                patch.object(upkeep, '_running', return_value={'ubuntu-26.04'}):
            self.assertIn(('default', 'data-files'), upkeep.due(fresh=True))
            self.run_upkeep()
        self.assertEqual(len(self.chunks(self.source)), 1)

    def test_a_file_gone_from_disk_is_left_alone_and_tried_again_after_a_week(self):
        before = len(self.chunks(self.source))
        (self.folder / 'fixtures' / 'plan.json').unlink()
        code, out = self.run_upkeep()
        self.assertEqual(code, 0)
        self.assertIn('left as it is', out)
        self.assertEqual(len(self.chunks(self.source)), before)
        self.assertIn('not on disk', self.ledger('data-files')['skipped'][self.source]['why'])
        self.assertEqual(upkeep.due(fresh=True), [])
        later = time.time() + (upkeep.RETRY_DAYS + 1) * 86400
        with patch('imprint.upkeep.time.time', return_value=later):
            self.assertIn(('default', 'data-files'), upkeep.due(fresh=True))

    def test_lowering_the_size_setting_looks_again(self):
        self.run_upkeep()
        self.assertEqual(upkeep.due(fresh=True), [])
        with patch.dict(os.environ, {'IMPRINT_INGEST_DATA_FILE_MIN_KB': '1'}):
            self.assertIn(('default', 'data-files'), upkeep.due(fresh=True))

    def test_another_device_s_files_are_left_to_it(self):
        with patch.object(device, 'current', return_value=B):
            upkeep.reset()
            code, out = self.run_upkeep()
        self.assertEqual(code, 0)
        self.assertGreater(len(self.chunks(self.source)), 1)
        self.assertNotIn('chunks ->', out)


class BackgroundTests(Store):
    def setUp(self):
        super().setUp()
        self.paired()
        queue._init_db()
        queue._pending_cache.update(at=0.0, value=None, organize_at=0.0, upkeep_at=0.0)
        self.addCleanup(queue._pending_cache.update, at=0.0, value=None, organize_at=0.0, upkeep_at=0.0)

    def test_the_idle_slot_runs_upkeep_last_and_counts_it_as_work(self):
        vs._ensure_collection()
        with patch.object(tag_backlog, 'deferring', return_value=False), \
                patch.object(queue, 'foreground_waiting', return_value=False), \
                patch.object(queue, '_organizing', return_value=False):
            self.assertEqual(queue._work_total(), 1)  # timestamps, once; no data file is indexed
            state = queue.background_state()
            self.assertEqual((state['enabled'], state['upkeep']), (True, {'enabled': True, 'due': 1}))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(tag_backlog._upkeep(), 0)
            upkeep.reset()
            self.assertEqual(queue._work_total(), 0)
        with patch.dict(os.environ, {'IMPRINT_UPKEEP_BACKGROUND': 'false'}):
            self.assertEqual(upkeep.due_count(), 0)
            self.assertEqual(tag_backlog._upkeep(), 0)

    def test_it_steps_aside_for_a_queued_job_and_rests_between_pieces(self):
        vs._ensure_collection()
        with patch.object(queue, 'foreground_waiting', return_value=True), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(tag_backlog._upkeep(), tag_backlog.YIELDED)
        run = upkeep.Pass(lambda: False, background=True)
        started = time.monotonic()
        run.rest(0.2)
        self.assertGreaterEqual(time.monotonic() - started, 0.19)
        self.assertLess(time.monotonic() - started, 1.0)
        foreground = upkeep.Pass(lambda: False)
        started = time.monotonic()
        foreground.rest(5)
        self.assertLess(time.monotonic() - started, 0.1)

    def test_its_share_of_a_run_ends_it_and_the_next_goes_on(self):
        vs._ensure_collection()
        def out_of_time(workspace, entry, run):
            # The share runs out while this step works. (Python 3.12's
            # monotonic clock on Windows ticks every ~16 ms, so a tiny
            # budget alone may not have passed yet.)
            run.deadline = time.monotonic() - 1
            return not run.halt()

        step = upkeep.Step('timestamps', 'a step that never finishes', upkeep._timestamps_due, out_of_time)
        with patch.dict(upkeep._BY_NAME, {'timestamps': step}), patch.object(upkeep.time, 'sleep'):
            code, out = self.run_upkeep(budget=600, background=True)
        self.assertEqual(code, 0)
        self.assertIn('next idle run goes on', out)
        self.assertNotIn('data files', out)  # the rest waits for the next run

    def test_only_the_job_s_own_process_lowers_its_priority(self):
        vs._ensure_collection()
        with patch.object(upkeep, '_lower_priority') as lowered, contextlib.redirect_stdout(io.StringIO()), \
                patch.object(queue, 'foreground_waiting', return_value=False):
            tag_backlog._upkeep()
            lowered.assert_not_called()
            upkeep.reset()
            config.register_workspace('second')
            with patch.object(tag_backlog, '_JOB_PROCESS', True):
                tag_backlog._upkeep()
            lowered.assert_called_once()

    def test_checking_what_is_due_never_reaches_the_store(self):
        with patch.object(vs, '_get_client', side_effect=AssertionError('store reached')), \
                patch.object(vs, '_ensure_collection', side_effect=AssertionError('store reached')):
            self.assertEqual(upkeep.due(fresh=True), [('default', 'timestamps')])

    def test_a_fresh_store_has_nothing_due(self):
        import sqlite3
        conn = sqlite3.connect(self.data / 'sync-pairs.sqlite3')
        with conn:
            conn.execute('DELETE FROM pairs')  # forgotten, or created empty by the API
        conn.close()
        upkeep._wal_scan.clear()
        self.assertEqual(upkeep.due(fresh=True), [])
        self.assertEqual(queue._work_total(), 0)
        # An unpaired device that received a share-code sync says so in its write log.
        (self.data / 'wal.jsonl').write_text('{"ts": 1, "op": "store", "count": 1}\n', encoding='utf-8')
        self.assertEqual(upkeep.due(fresh=True), [])
        with open(self.data / 'wal.jsonl', 'a', encoding='utf-8') as log:
            log.write('{"ts": 2, "op": "store_batch_precomputed", "count": 3}\n')
        self.assertEqual(upkeep.due(fresh=True), [('default', 'timestamps')])


class CommandTests(unittest.TestCase):
    def test_the_dashboard_queues_it_with_its_dry_run(self):
        from imprint import commands
        self.assertIn('upkeep', commands.ALLOWED)
        self.assertEqual(queue.build_command_args('upkeep', {'dry_run': True}), ['--dry-run'])
        self.assertEqual(queue.build_command_args('upkeep', {}), [])

    def test_the_cli_refuses_unknown_arguments(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(upkeep.main(['--force']), 2)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(upkeep.main(['--help']), 0)
        self.assertIn('imprint upkeep [--dry-run]', out.getvalue())


if __name__ == '__main__':
    unittest.main()
