"""Native service lifetime checks using disposable data and loopback ports."""

import io
import json
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
from urllib.request import Request, urlopen

from imprint.desktop_server import watch_parent

from emulation import REASON as EMULATED, emulated


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

    def serve(self, data, log):
        """Start the real desktop service; return it and its port once it answers."""
        if emulated():
            self.skipTest(EMULATED)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        env = {**os.environ, 'IMPRINT_DATA_DIR': data, 'PYTHONPATH': str(ROOT)}
        proc = subprocess.Popen(
            [sys.executable, '-m', 'imprint.desktop_server', '--port', str(port)],
            cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=log, stderr=log,
        )
        self.addCleanup(self.reap, proc)
        deadline = time.monotonic() + 30
        while True:
            try:
                with urlopen(f'http://127.0.0.1:{port}/api/ping', timeout=.5) as res:
                    self.assertEqual(res.status, 200)
                return proc, port
            except OSError:
                if proc.poll() is not None or time.monotonic() > deadline:
                    log.seek(0)
                    self.fail(log.read().decode(errors='replace'))
                time.sleep(.1)

    @staticmethod
    def reap(proc):
        if not proc.stdin.closed:
            proc.stdin.close()
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    def test_real_api_stays_up_until_parent_pipe_closes(self):
        with tempfile.TemporaryDirectory(prefix='imprint desktop ') as data, tempfile.TemporaryFile() as log:
            proc, port = self.serve(data, log)
            # Every launcher's server registers itself while it serves.
            registration = Path(data) / 'imprint_ui.json'
            self.assertEqual(json.loads(registration.read_text()), {'pid': proc.pid, 'port': port})
            # No UI heartbeat is needed while Electron holds the pipe.
            self.assertIsNone(proc.poll())
            proc.stdin.close()
            self.assertEqual(proc.wait(timeout=15), 0)
            with socket.socket() as sock:
                self.assertNotEqual(sock.connect_ex(('127.0.0.1', port)), 0)
            self.assertFalse(registration.exists())

    def test_restart_request_exits_for_the_owner_to_start_it_again(self):
        from imprint.api import RESTART_EXIT_CODE
        with tempfile.TemporaryDirectory(prefix='imprint desktop ') as data, tempfile.TemporaryFile() as log:
            proc, port = self.serve(data, log)
            request = Request(f'http://127.0.0.1:{port}/api/restart', method='POST')
            with urlopen(request, timeout=5) as res:
                self.assertEqual(json.load(res), {'restarting': True})
            # Electron still holds the pipe: the exit status asks it to restart.
            self.assertEqual(proc.wait(timeout=15), RESTART_EXIT_CODE)
            self.assertFalse((Path(data) / 'imprint_ui.json').exists())

    def test_service_waits_while_setup_changes_the_runtime(self):
        # Setup holds runtime.lock while it replaces the venv's packages (ADR 0084).
        from imprint import queue_lock
        with tempfile.TemporaryDirectory(prefix='imprint desktop ') as data, tempfile.TemporaryFile() as log:
            fd = os.open(os.path.join(data, 'runtime.lock'), os.O_RDWR | os.O_CREAT)
            release = threading.Timer(3, queue_lock._unlock, args=(fd,))
            try:
                self.assertTrue(queue_lock._lock(fd, False))
                release.start()
                started = time.monotonic()
                proc, _ = self.serve(data, log)
                self.assertGreaterEqual(time.monotonic() - started, 3)
                # Windows deletes no file that a live process, or this one,
                # still holds, and the directory goes when this block ends.
                self.reap(proc)
            finally:
                release.cancel()
                os.close(fd)


class RestartEndpointTests(unittest.TestCase):
    def test_stopping_never_waits_on_clients_indefinitely(self):
        # A Windows client reset can leave a connection uvicorn believes open.
        from imprint import api
        server = api.DashboardServer(port=0)
        self.addCleanup(setattr, api.app.state, 'server', None)
        self.assertEqual(server.config.timeout_graceful_shutdown, api.GRACEFUL_SHUTDOWN_SECONDS)

    def test_an_app_served_without_a_dashboard_server_cannot_restart(self):
        from imprint import api
        with patch.object(api.app.state, 'server', None):
            response = api.api_restart()
        self.assertEqual((response.status_code, json.loads(response.body)),
                         (409, {'error': 'This server was not started by Imprint'}))


if __name__ == '__main__':
    unittest.main()
