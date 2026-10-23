import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from imprint import queue_lock


class LockSafetyTests(unittest.TestCase):
    def test_stale_cleanup_never_replaces_an_active_lock_inode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'queue.lock'
            descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                self.assertTrue(queue_lock._lock(descriptor, False))
                with patch.object(queue_lock, 'lock_path', return_value=path), patch.object(queue_lock, 'read_holder', return_value=None):
                    queue_lock.clear_stale()
                    self.assertTrue(path.exists())
                    challenger = os.open(path, os.O_RDWR)
                    try:
                        self.assertFalse(queue_lock._lock(challenger, False))
                    finally:
                        os.close(challenger)
            finally:
                queue_lock._unlock(descriptor)
                os.close(descriptor)
