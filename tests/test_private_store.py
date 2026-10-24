import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from imprint import config, private_store, sync_pairs
from imprint.sync_ws import SyncPolicy


class PrivateStoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        patcher = patch.object(config, 'get_data_dir', return_value=self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.path = self.dir / 'sync-pairs.sqlite3'

    def test_pairing_store_cut_by_a_trailing_ctrl_z_is_repaired(self):
        # Each automatic-sync update replaces the pairing's row with a new
        # rowid. Once the index's last entry, at the end of the file, points at
        # row 26 (0x1A), the Windows C runtime dropped that byte when the file
        # was opened read-write in text mode.
        record = sync_pairs.offer('default', {'fingerprint': 'ab12', 'hostname': 'Outland.local'},
                                  SyncPolicy.parse('both'), 'default', 'wss://relay.example')
        sync_pairs.save(record)
        for n in range(200):
            if self.path.read_bytes()[-1] == 0x1A:
                break
            sync_pairs.update(record['id'], last_sync=float(n))
        self.assertEqual(self.path.read_bytes()[-1], 0x1A)
        os.truncate(self.path, self.path.stat().st_size - 1)
        with sqlite3.connect(self.path) as damaged, self.assertRaisesRegex(sqlite3.DatabaseError, 'malformed'):
            damaged.execute('SELECT body FROM pairs ORDER BY id').fetchall()

        peers = sync_pairs.pairs(public=True)
        self.assertEqual([peer['peer']['hostname'] for peer in peers], ['Outland.local'])
        self.assertEqual(self.path.stat().st_size % 4096, 0)
        with sqlite3.connect(self.path) as repaired:
            self.assertEqual(repaired.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_store_files_are_opened_in_binary_mode(self):
        flags = []
        real_open = os.open

        def recording_open(path, flag, mode=0o777):
            flags.append(flag)
            return real_open(path, flag & ~0x8000, mode)

        with patch.object(os, 'O_BINARY', 0x8000, create=True), patch.object(os, 'open', recording_open):
            private_store.connect(self.path, timeout=1).close()
        self.assertTrue(flags and flags[0] & 0x8000)

    def test_a_store_that_cannot_be_repaired_is_reported_with_its_path(self):
        self.path.write_bytes(b'not a database' * 400)
        with self.assertRaises(sqlite3.DatabaseError) as raised:
            sync_pairs.pairs()
        self.assertIn(str(self.path), str(raised.exception))
        self.assertIn('pair again', str(raised.exception))


if __name__ == '__main__':
    unittest.main()
