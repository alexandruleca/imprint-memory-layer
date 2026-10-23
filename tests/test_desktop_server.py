"""Native service lifetime checks using disposable data and loopback ports."""

import io
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import urlopen

from imprint.desktop_server import watch_parent


ROOT = Path(__file__).resolve().parents[1]


class ParentWatchTests(unittest.TestCase):
    def test_eof_requests_shutdown_without_killing_finished_service(self):
        closed, finished = threading.Event(), threading.Event()
        finished.set()
        with patch('imprint.desktop_server.os._exit') as hard_exit:
            watch_parent(io.BytesIO(b''), closed, finished, grace_seconds=0)
        self.assertTrue(closed.is_set())
        hard_exit.assert_not_called()

    def test_stuck_service_exits_after_parent_disconnect(self):
        code = (
            'import sys, threading, time; '
            'from imprint.desktop_server import watch_parent; '
            'threading.Thread(target=watch_parent, args=(sys.stdin.buffer, '
            'threading.Event(), threading.Event(), .1), daemon=True).start(); '
            'print("ready", flush=True); time.sleep(30)'
        )
        proc = subprocess.Popen([sys.executable, '-c', code], cwd=ROOT,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        try:
            self.assertEqual(proc.stdout.readline().strip(), b'ready')
            proc.stdin.close()
            self.assertEqual(proc.wait(timeout=5), 0)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            proc.stdout.close()

    def test_blocked_pipe_reader_does_not_stall_dll_imports(self):
        # Windows serializes I/O on the pipe a thread is blocked reading; numpy
        # loads DLLs that query standard input, which must no longer be it.
        code = (
            'import sys, threading; '
            'from imprint.desktop_server import detach_lifetime_pipe; '
            'pipe = detach_lifetime_pipe(); '
            'threading.Thread(target=pipe.read, args=(1,), daemon=True).start(); '
            'import numpy; '
            'print("stdin", repr(sys.stdin.read()), flush=True)'
        )
        proc = subprocess.Popen([sys.executable, '-c', code], cwd=ROOT,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        try:
            line = []
            reader = threading.Thread(target=lambda: line.append(proc.stdout.readline()), daemon=True)
            reader.start()
            reader.join(60)
            self.assertEqual([l.strip() for l in line], [b"stdin ''"])
            proc.stdin.close()
            self.assertEqual(proc.wait(timeout=15), 0)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            proc.stdout.close()

    def test_real_api_stays_up_until_parent_pipe_closes(self):
        with tempfile.TemporaryDirectory(prefix='imprint desktop ') as data:
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            env = {**os.environ, 'IMPRINT_DATA_DIR': data, 'PYTHONPATH': str(ROOT)}
            with tempfile.TemporaryFile() as log:
                proc = subprocess.Popen(
                    [sys.executable, '-m', 'imprint.desktop_server', '--port', str(port)],
                    cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=log, stderr=log,
                )
                try:
                    deadline = time.monotonic() + 30
                    while True:
                        try:
                            with urlopen(f'http://127.0.0.1:{port}/api/ping', timeout=.5) as res:
                                self.assertEqual(res.status, 200)
                            break
                        except OSError:
                            if proc.poll() is not None or time.monotonic() > deadline:
                                log.seek(0)
                                self.fail(log.read().decode(errors='replace'))
                            time.sleep(.1)
                    # No UI heartbeat is needed while Electron holds the pipe.
                    self.assertIsNone(proc.poll())
                    proc.stdin.close()
                    self.assertEqual(proc.wait(timeout=15), 0)
                    with socket.socket() as sock:
                        self.assertNotEqual(sock.connect_ex(('127.0.0.1', port)), 0)
                finally:
                    if not proc.stdin.closed:
                        proc.stdin.close()
                    if proc.poll() is None:
                        proc.kill()
                        proc.wait()


if __name__ == '__main__':
    unittest.main()
