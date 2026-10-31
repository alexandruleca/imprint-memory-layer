"""Paired sync sends only what the other device lacks, in chunks (ADR 0116)."""
import asyncio
import io
import json
import sqlite3
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from imprint import imprint_graph, source_snapshots, sync_delta, sync_ws, vectorstore


def stream_ok(lines):
    """Every line passes the receiver's check, in a stream's order."""
    frames = [json.loads(line) for line in lines]
    after_meta = False
    for frame in frames:
        if not sync_ws._stream_line_ok(frame, after_meta):
            return False
        after_meta = True
    return frames[-1]['kind'] == 'done'


class WantedTests(unittest.TestCase):
    def test_missing_and_newer_records_are_sent_older_and_equal_ones_are_not(self):
        local = {'memories': {'new': 0.0, 'retagged': 5.0, 'older': 1.0, 'same': 2.0},
                 'facts': {'f1': 0.0, 'f2': 0.0}, 'sources': {'s1': 10.0}}
        remote = {'memories': {'retagged': 3.0, 'older': 4.0, 'same': 2.0},
                  'facts': {'f2': 0.0}, 'sources': {'s1': 10.0}}
        self.assertEqual(sync_delta.wanted(local, remote),
                         {'memories': ['new', 'retagged'], 'facts': ['f1'], 'sources': []})
        # The other side then lacks nothing newer than this one holds: no ping-pong.
        self.assertEqual(sync_delta.wanted(remote, local), {'memories': ['older'], 'facts': [], 'sources': []})

    def test_digest_ignores_order_and_follows_versions(self):
        a = {'memories': {'x': 1.0, 'y': 2.0}, 'facts': {}, 'sources': {}}
        b = {'memories': {'y': 2.0, 'x': 1.0}, 'facts': {}, 'sources': {}}
        self.assertEqual(sync_delta.digest(a), sync_delta.digest(b))
        b['memories']['x'] = 3.0
        self.assertNotEqual(sync_delta.digest(a), sync_delta.digest(b))


class ManifestCodingTests(unittest.TestCase):
    def test_round_trip(self):
        local = {'memories': {'m' * 16: 1.5}, 'facts': {'["s","p","o",0]': 0.0}, 'sources': {'["p","s"]': 9.0}}
        self.assertEqual(sync_delta.decode(sync_delta.encode(local)), local)

    def test_malformed_or_oversized_manifests_are_refused(self):
        for data in (None, 'not base64!', sync_delta.encode({'memories': {'x': 'new'}}),
                     sync_delta.encode({'memories': ['x']}), sync_delta.encode(['memories'])):
            with self.assertRaisesRegex(ValueError, 'manifest'):
                sync_delta.decode(data)
        with patch.object(sync_delta, 'MANIFEST_BYTES', 64), self.assertRaisesRegex(ValueError, 'oversized'):
            sync_delta.decode(sync_delta.encode({'memories': {str(i): 0.0 for i in range(100)}}))


class StreamTests(unittest.TestCase):
    def test_source_snapshot_frames_are_part_of_a_stream(self):
        frame = {'kind': 'source_snapshot', 'transfer': 'abc', 'part': 0, 'parts': 1, 'data': 'eHg='}
        self.assertTrue(sync_ws._stream_line_ok(frame, True))
        self.assertFalse(sync_ws._stream_line_ok(frame, False))  # not before the meta line
        for broken in ({**frame, 'part': '0'}, {**frame, 'data': None}, {k: v for k, v in frame.items() if k != 'transfer'}):
            self.assertFalse(sync_ws._stream_line_ok(broken, True))

    def test_a_stream_that_stops_fails_instead_of_waiting_forever(self):
        class Silent:
            async def recv(self, timeout=None):
                await asyncio.wait_for(asyncio.Event().wait(), timeout)

        async def receive():
            async for _ in sync_ws._receive_stream(Silent(), io.StringIO(), 'pull', asyncio.Event()):
                pass
        with patch.object(sync_ws, 'STREAM_WAIT', .05), self.assertRaisesRegex(RuntimeError, 'sent nothing'):
            asyncio.run(receive())

    def test_chunks_are_complete_streams_of_bounded_size(self):
        payloads = {f'{i:016x}': {'_mid': f'{i:016x}', 'content': f'memory {i}', '_vector': [0.1, 0.2]}
                    for i in range(5)}

        def retrieve(mids, fields, workspace, with_vectors=False):
            self.assertTrue(with_vectors)
            return [payloads[mid] for mid in mids if mid in payloads]

        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute('CREATE TABLE facts (subject, predicate, object, valid_from, ended, source, recorded_device)')
        conn.executemany('INSERT INTO facts VALUES (?, ?, ?, ?, NULL, "", "{}")',
                         [('a', 'knows', 'b', 1), ('c', 'knows', 'd', 2)])

        @contextmanager
        def connection(workspace):
            yield conn

        snapshot = {'project': 'p', 'source': 's', 'updated_at': 1.0}
        parts = [{'kind': 'source_snapshot', 'transfer': 't', 'part': i, 'parts': 2, 'data': 'eA=='} for i in range(2)]
        keys = {'memories': sorted(payloads) + ['ffffffffffffffff'],  # one went away meanwhile
                'facts': [sync_delta._fact_key({'subject': 'c', 'predicate': 'knows', 'object': 'd', 'valid_from': 2})],
                'sources': [sync_delta._source_key(snapshot)]}
        with patch.object(sync_delta, 'CHUNK_RECORDS', 2), \
             patch.object(vectorstore, 'retrieve_payloads', side_effect=retrieve), \
             patch.object(imprint_graph, '_connection', connection), \
             patch.object(source_snapshots, 'list_metadata', return_value=[snapshot, {**snapshot, 'source': 'other'}]), \
             patch.object(source_snapshots, 'export_parts', return_value=iter(parts)) as export, \
             patch('imprint.config.embedding_identity', return_value='profile'):
            chunks = list(sync_delta.export_chunks('ws', keys))
        self.assertEqual(len(chunks), 5)  # 2 + 2 + 1 memories, 1 fact chunk, 1 snapshot
        self.assertTrue(all(stream_ok(chunk) for chunk in chunks))
        memories = [r['id'] for chunk in chunks[:3] for r in json.loads(chunk[1])['records']]
        self.assertEqual(memories, sorted(payloads))
        self.assertEqual(json.loads(chunks[3][1])['records'][0]['subject'], 'c')
        self.assertEqual([json.loads(line)['kind'] for line in chunks[4]],
                         ['meta', 'source_snapshot', 'source_snapshot', 'done'])
        export.assert_called_once_with('ws', [snapshot])


class ManifestTests(unittest.TestCase):
    def test_lists_what_sync_can_change_without_vectors(self):
        def scroll(fields, workspace=None, with_vectors=False):
            self.assertFalse(with_vectors)
            return [{'_mid': 'm1', 'tagged_at': 7.5}, {'_mid': 'm2'}, {'content': 'no id'}]

        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute('CREATE TABLE facts (subject, predicate, object, valid_from)')
        conn.execute("INSERT INTO facts VALUES ('a', 'knows', 'b', 1)")

        @contextmanager
        def connection(workspace):
            yield conn

        with patch.object(vectorstore, '_scroll_all', side_effect=scroll), \
             patch.object(imprint_graph, '_connection', connection), \
             patch.object(source_snapshots, 'list_metadata',
                          return_value=[{'project': 'p', 'source': 's', 'updated_at': 3}]):
            local = sync_delta.manifest('ws')
        self.assertEqual(local, {'memories': {'m1': 7.5, 'm2': 0.0},
                                 'facts': {'["a", "knows", "b", 1]': 0.0},
                                 'sources': {'["p", "s"]': 3.0}})


if __name__ == '__main__':
    unittest.main()
