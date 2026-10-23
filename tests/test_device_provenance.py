import concurrent.futures
import json
import os
from pathlib import Path
import sqlite3
from contextlib import closing
import tempfile
import unittest
from unittest.mock import patch

import test_indexing_retrieval as indexing
from imprint import config, device, imprint_graph as kg, source_state, workspace_policy as policy, vectorstore as vs

A = {'id': 'aaaaaaaa', 'name': 'laptop-A', 'os': 'windows'}
B = {'id': 'bbbbbbbb', 'name': 'desktop-B', 'os': 'linux'}


class DevicePathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.patch = patch.object(config, 'get_data_dir', return_value=self.root / 'data')
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.env = patch.dict(os.environ, {'IMPRINT_WORKSPACE': ''})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.checkout = self.root / 'checkout-B'
        self.checkout.mkdir()
        (self.checkout / 'app.py').write_text('shared implementation')

    def test_identity_is_stable_under_concurrent_initialization_and_reuses_sync_id(self):
        with concurrent.futures.ThreadPoolExecutor(12) as pool:
            identities = list(pool.map(lambda _: device.current()['id'], range(40)))
        self.assertEqual(len(set(identities)), 1)
        (self.root / 'data' / 'device_id.txt').write_text('deadbeef\n')
        self.assertEqual(device.current()['id'], 'deadbeef')

    def test_foreign_windows_path_uses_explicit_local_mapping_without_rewriting_origin(self):
        record = device.prepare({'source_path': r'C:\users\alice\project\app.py', 'source_type': 'file',
                                 'source': 'project/app.py', 'project': 'project', 'recorded_device': A,
                                 'source_device': A, 'source_path_local': True}, imported=True)
        with patch.object(device, 'current', return_value=B):
            self.assertFalse(record['source_path_local'])
            self.assertEqual(device.context(record, cwd=self.checkout)['path_status'], 'foreign-unmapped')
            policy.map_project('project', str(self.checkout))
            view = device.context(record, cwd=self.checkout)
            self.assertEqual(view['path_status'], 'foreign-mapped')
            self.assertEqual(view['local_source_path'], str((self.checkout / 'app.py').resolve()))
            self.assertEqual(view['recorded_device'], A)
            self.assertEqual(view['original_source_path'], record['source_path'])
            self.assertEqual(device.context(record, cwd=self.root / 'different-project')['local_source_path'], '')

    def test_relative_traversal_cannot_escape_mapping(self):
        policy.map_project('project', str(self.checkout))
        record = {'source_path': '/foreign/secret.py', 'source_relative_path': '../secret.py',
                  'source_device': A, 'project': 'project'}
        self.assertEqual(device.context(record, cwd=self.root)['local_source_path'], '')

    def test_foreign_and_unknown_paths_never_trigger_local_missing_source_pruning(self):
        missing = self.checkout / 'gone.py'
        with patch.object(device, 'current', return_value=B):
            states = {'foreign': {'source_path': str(missing), 'source_device': A, 'source_path_local': True},
                      'unknown': {'source_path': str(missing)},
                      'local': {'source_path': str(missing), 'source_device': B, 'source_path_local': True}}
            self.assertEqual(source_state.missing_sources(states, [str(self.checkout)]), ['local'])

    def test_legacy_import_is_unknown_not_relabelled_as_receiver(self):
        with patch.object(device, 'current', return_value=B):
            record = device.prepare({'content': 'old shared conversation', 'source_path': '/legacy/path.py'}, imported=True)
        self.assertEqual(record['recorded_device'], device.UNKNOWN)
        self.assertFalse(record['source_path_local'])


class DeviceTransferTests(unittest.TestCase):
    setUp = indexing.StorageTests.setUp
    cleanup = indexing.StorageTests.cleanup
    records = indexing.StorageTests.records

    def test_shared_conversations_vectors_and_facts_keep_origin_and_are_searchable_on_receiver(self):
        from imprint.sync_ws import stream_export_lines, process_import_buffer
        self.addCleanup(kg._close_all)
        config.register_workspace('received')
        with patch.object(device, 'current', return_value=A):
            mid = vs.store('We chose local storage so shared project knowledge works offline.', project='project', source='conversation/session', type='decision')
            kg.add('project', 'uses', 'local storage', source='conversation/session')
            lines = stream_export_lines('default')
        buffer = Path(self.data_directory.name) / 'sync.jsonl'
        buffer.write_text('\n'.join(lines))
        with patch.object(device, 'current', return_value=B):
            stats = process_import_buffer(str(buffer), 'received')
            self.assertEqual(stats['memories']['inserted'], 1)
            rows = vs.search('local storage', workspace='received')
            self.assertEqual(rows[0]['id'], mid)
            self.assertEqual(rows[0]['recorded_device'], A)
            self.assertEqual(rows[0]['path_context']['path_status'], 'no-file-path')
            self.assertEqual(kg.query(workspace='received')[0]['recorded_device'], A)

    def test_graph_additive_migration_preserves_legacy_facts(self):
        self.addCleanup(kg._close_all)
        path = config.graph_db_path()
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("CREATE TABLE facts (id INTEGER PRIMARY KEY, subject TEXT, predicate TEXT, object TEXT, valid_from REAL, ended REAL, source TEXT)")
            conn.execute("INSERT INTO facts VALUES (1, 'old', 'uses', 'local', 1, NULL, '')")
        with patch.object(device, 'current', return_value=B):
            kg.add('new', 'uses', 'portable context')
        facts = kg.query()
        self.assertEqual(len(facts), 2)
        self.assertEqual(facts[0]['recorded_device'], B)
        self.assertEqual(facts[1]['recorded_device'], {})
