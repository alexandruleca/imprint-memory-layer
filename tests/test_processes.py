import os
import subprocess
import sys
import unittest

from imprint.processes import is_alive


class ProcessTests(unittest.TestCase):
    def test_probe_never_terminates_live_process(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        try:
            for _ in range(3):
                self.assertTrue(is_alive(child.pid))
                self.assertIsNone(child.poll())
        finally:
            child.terminate()
            child.wait(timeout=10)
        self.assertFalse(is_alive(child.pid))

    def test_current_and_invalid_pid(self):
        self.assertTrue(is_alive(os.getpid()))
        for pid in (None, 0, -1):
            self.assertFalse(is_alive(pid))
