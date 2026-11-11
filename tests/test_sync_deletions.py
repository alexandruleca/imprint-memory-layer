"""Deletions that reach paired devices (ADR 0176): what is recorded, what is
asked of the other device, what it deletes, and what it refuses to import."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from qdrant_client import QdrantClient, models

from imprint import config, sync_deletions, vectorstore as vs


class DeletionRecordTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        patcher = patch.object(config, 'get_data_dir', return_value=Path(directory.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_deletions_are_kept_per_workspace_with_the_later_time_until_retention(self):
        sync_deletions.record('work', ['m1', 'm2'], at=100.0)
        sync_deletions.record('work', {'m1': 50.0, 'm3': time.time()})
        sync_deletions.record('home', ['m1'], at=time.time())
        held = sync_deletions.held('work')
        self.assertEqual(sorted(held), ['m3'])  # 100 and 50 are past retention
        sync_deletions.record('work', {'m1': time.time() - 10})
        sync_deletions.record('work', {'m1': time.time() - 100})  # an older time does not win
        self.assertGreater(sync_deletions.held('work')['m1'], time.time() - 20)
        self.assertEqual(set(sync_deletions.held('home')), {'m1'})

    def test_only_keys_the_other_device_holds_are_named_and_never_what_is_held_here_again(self):
        now = time.time()
        sync_deletions.record('work', {'gone': now, 'secret': now, 'again': now})
        local = {'memories': {'again': 0.0, 'mine': 0.0}}
        remote = {'memories': {'gone': 0.0, 'again': 0.0, 'theirs': 0.0}}
        asked = sync_deletions.doomed('work', local, remote)
        # "secret" was never on the other device: its ID is not sent there.
        self.assertEqual(asked, {'memories': {'gone': now}})

    def test_incoming_memories_older_than_a_deletion_are_refused(self):
        sync_deletions.record('work', {'old': time.time()})
        deleted = sync_deletions.held('work')['old']
        records = [{'id': 'old', 'recorded_at': deleted - 60}, {'id': 'old-again', 'recorded_at': deleted - 60},
                   {'id': 'old', 'recorded_at': deleted + 60}]
        self.assertEqual(sync_deletions.blocked('work', records[:2]), {'old'})
        # Made again after the deletion, it is welcome.
        self.assertEqual(sync_deletions.blocked('work', records[2:]), set())
        self.assertEqual(sync_deletions.blocked('home', records), set())


class StoreCase(unittest.TestCase):
    """A real (in-memory) Qdrant collection, as the storage tests use."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.client = QdrantClient(':memory:')
        patches = [patch.object(config, 'get_data_dir', return_value=Path(directory.name)),
                   patch.object(vs, '_get_client', return_value=self.client),
                   patch.object(vs, '_wal_log'), patch.object(config, 'EMBEDDING_DIM', 3),
                   patch('imprint.embeddings.embed_documents_batch', side_effect=lambda texts: [[1., 0., 0.] for _ in texts]),
                   patch('imprint.embeddings.embed_document', return_value=[1., 0., 0.]),
                   patch('imprint.embeddings.embed_query', return_value=[1., 0., 0.]),
                   patch('imprint.tagger.derive_zero_shot', return_value=[]),
                   patch.object(self.client, 'create_payload_index')]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        vs._collections_ready.clear()
        vs._id_caches.clear()
        self.addCleanup(vs._collections_ready.clear)
        self.addCleanup(vs._id_caches.clear)
        self.workspace = config.get_active_workspace()

    def store(self, texts, source='project/app.py', project='project'):
        records = [{'content': text, 'project': project, 'source': source, 'source_type': 'file', 'chunk_index': i}
                   for i, text in enumerate(texts)]
        vs.store_batch(records)
        return self.ids(source)

    def ids(self, source=None):
        client, coll = vs._ensure_collection()
        points, _ = client.scroll(coll, limit=100, with_payload=['_mid', 'source'])
        return sorted(p.payload['_mid'] for p in points if source is None or p.payload['source'] == source)

    def from_elsewhere(self, mids):
        client, coll = vs._ensure_collection()
        client.set_payload(coll, {'recorded_device': {'id': 'otherdevice', 'name': 'other', 'os': 'linux'}},
                           points=[vs._point_uuid(mid) for mid in mids])

    def recorded(self):
        return set(sync_deletions.held(self.workspace))


class LocalDeletionTests(StoreCase):
    def test_the_users_deletion_of_any_memory_is_recorded(self):
        first, second = self.store(['alpha chunk', 'beta chunk'])
        self.from_elsewhere([second])
        self.assertTrue(vs.delete(first))
        self.assertTrue(vs.delete(second))
        self.assertEqual(self.recorded(), {first, second})

    def test_a_source_deletion_records_all_own_or_none_as_asked(self):
        mine, theirs = self.store(['first part', 'second part'], source='recording/r1')
        self.from_elsewhere([theirs])
        vs.delete_by_source('recording/r1', propagate='own')
        self.assertEqual(self.recorded(), {mine})
        kept = self.store(['a file that is gone'], source='project/gone.py')
        vs.delete_by_source('project/gone.py')  # A file gone from disk: nothing travels.
        self.assertTrue(kept and not set(kept) & self.recorded())
        everything = self.store(['meeting part one', 'meeting part two'], source='recording/r2')
        self.from_elsewhere(everything[:1])
        vs.delete_by_source('recording/r2', propagate='all')
        self.assertTrue(set(everything) <= self.recorded())
        with self.assertRaises(ValueError):
            vs._propagate(None, [], 'some')

    def test_a_new_version_of_a_source_records_only_the_replaced_chunks_this_device_recorded(self):
        old = self.store(['version one first', 'version one second'], source='project/app.py')
        self.from_elsewhere(old[1:])
        vs.replace_source([{'content': 'version two', 'project': 'project', 'source': 'project/app.py',
                            'source_type': 'file', 'chunk_index': 0}], 'project/app.py', 'project')
        self.assertEqual(self.recorded(), {old[0]})


class ApplyTests(StoreCase):
    def test_copies_older_than_the_deletion_go_and_the_deletion_travels_on(self):
        old, newer = self.store(['deleted over there', 'made again here later'])
        client, coll = vs._ensure_collection()
        now = time.time()
        client.set_payload(coll, {'recorded_at': now - 3600}, points=[vs._point_uuid(old)])
        client.set_payload(coll, {'recorded_at': now + 60}, points=[vs._point_uuid(newer)])
        local = {'memories': {old: 0.0, newer: 0.0}}
        gone = sync_deletions.apply(self.workspace, {'memories': {old: now, newer: now, 'unknown': now}}, local)
        self.assertEqual(gone, {'memories': [old]})
        self.assertEqual(self.ids(), [newer])
        # Recorded with the other device's time, so it reaches devices beyond.
        self.assertEqual(sync_deletions.held(self.workspace), {old: now})

    def test_a_request_to_delete_more_than_half_a_workspace_is_refused_whole(self):
        local = {'memories': {f'm{i}': 0.0 for i in range(500)}}
        asked = {'memories': {f'm{i}': time.time() for i in range(300)}}
        with patch.object(vs, 'delete_memories') as delete:
            with self.assertRaises(sync_deletions.MassDeletion) as refused:
                sync_deletions.apply(self.workspace, asked, local)
        delete.assert_not_called()
        self.assertEqual((refused.exception.asked, refused.exception.held), (300, 500))
        self.assertIn('kept', str(refused.exception))


class ImportGuardTests(StoreCase):
    def test_a_sync_import_skips_memories_this_device_deleted(self):
        from imprint import sync_ws
        sync_deletions.record(self.workspace, {'d' * 16: time.time()})
        record = lambda mid, at: {'id': mid, 'content': f'text {mid}', 'project': 'p', 'source': 'p/x.md',
                                  'vector': [1., 0., 0.], 'recorded_at': at}  # noqa: E731
        stream = Path(tempfile.mkdtemp()) / 'stream.jsonl'
        self.addCleanup(lambda: stream.unlink(missing_ok=True))
        stream.write_text('\n'.join(json.dumps(frame) for frame in [
            {'kind': 'meta', 'datasets': {'memories': 2}},
            {'kind': 'batch', 'dataset': 'memories', 'records': [record('d' * 16, 1.0), record('e' * 16, 1.0)]},
            {'kind': 'done'}]))
        with patch.object(vs, 'store_batch_precomputed', return_value=(1, 0, 0)) as store:
            stats = sync_ws.process_import_buffer(str(stream), self.workspace)
        self.assertEqual([p['id'] for p in store.call_args.args[0]], ['e' * 16])
        self.assertEqual(stats['memories'], {'inserted': 1, 'updated': 0, 'skipped': 1})


if __name__ == '__main__':
    unittest.main()
