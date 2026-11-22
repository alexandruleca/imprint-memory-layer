import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from imprint import qdrant_runner as runner


class QdrantBoundaryTests(unittest.TestCase):
    def test_nonlocal_managed_hosts_fail_before_probe_download_or_spawn(self):
        for host in ('0.0.0.0', '::', '192.168.1.2', 'example.com', 'localhost.evil.test'):
            with self.subTest(host=host), patch.object(runner, 'QDRANT_HOST', host), \
                    patch.object(runner, 'DISABLE_SPAWN', False), \
                    patch.object(runner, 'is_running', return_value=True) as probe, \
                    patch.object(runner, '_find_or_download_binary') as download, \
                    patch.object(runner.subprocess, 'Popen') as spawn:
                with self.assertRaisesRegex(ValueError, 'loopback'):
                    runner.ensure_running()
                with self.assertRaisesRegex(ValueError, 'loopback'):
                    runner._spawn(Path('qdrant'))
                probe.assert_not_called()
                download.assert_not_called()
                spawn.assert_not_called()

    def test_external_connections_require_explicit_byo_mode(self):
        with patch.object(runner, 'QDRANT_HOST', '192.168.1.2'), \
                patch.object(runner, 'DISABLE_SPAWN', True), \
                patch.object(runner, 'is_running', return_value=True):
            self.assertEqual(runner.ensure_running(), ('192.168.1.2', runner.QDRANT_PORT))

    def test_managed_daemon_overrides_unsafe_inherited_settings(self):
        for host, bind in (('127.0.0.1', '127.0.0.1'), ('localhost', '127.0.0.1'), ('::1', '::1')):
            with self.subTest(host=host), tempfile.TemporaryDirectory() as directory, \
                    patch.dict(os.environ, {'IMPRINT_DATA_DIR': directory,
                                           'QDRANT__SERVICE__HOST': '0.0.0.0',
                                           'QDRANT__CLUSTER__ENABLED': 'true',
                                           'QDRANT__SERVICE__ENABLE_CORS': 'true'}), \
                    patch.object(runner, 'QDRANT_HOST', host), \
                    patch.object(runner.subprocess, 'Popen') as spawn:
                spawn.return_value.pid = 1234
                runner._spawn(Path('qdrant'))
                options = spawn.call_args.kwargs
                try:
                    env = options['env']
                    self.assertEqual(env['QDRANT__SERVICE__HOST'], bind)
                    self.assertEqual(env['QDRANT__CLUSTER__ENABLED'], 'false')
                    self.assertEqual(env['QDRANT__SERVICE__ENABLE_CORS'], 'false')
                    self.assertEqual(env['QDRANT__SERVICE__HTTP_PORT'], str(runner.QDRANT_PORT))
                    self.assertEqual(env['QDRANT__SERVICE__GRPC_PORT'], str(runner.QDRANT_GRPC_PORT))
                    # Every HTTP request at info filled a log with 1.26 GB in three days (ADR 0204).
                    self.assertEqual(env['QDRANT__LOG_LEVEL'], 'info,actix_web=warn')
                finally:
                    options['stdout'].close()

    def test_ipv6_health_probe_uses_bracketed_address(self):
        with patch.object(runner, 'QDRANT_HOST', '::1'), \
                patch.object(runner.urllib.request, 'urlopen') as open_url:
            open_url.return_value.__enter__.return_value.status = 200
            self.assertTrue(runner.is_running())
            open_url.assert_called_once_with(f'http://[::1]:{runner.QDRANT_PORT}/readyz', timeout=1.0)


class QdrantLogTests(unittest.TestCase):
    """qdrant.log stays bounded (ADR 0204)."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        env = patch.dict(os.environ, {'IMPRINT_DATA_DIR': directory.name})
        env.start()
        self.addCleanup(env.stop)
        self.log = Path(directory.name) / 'qdrant.log'

    def fill(self, lines):
        self.log.write_bytes(b''.join(b'line %08d of the old log\n' % i for i in range(lines)))

    def test_a_log_past_the_limit_keeps_its_last_lines_and_starts_again(self):
        with patch.object(runner, 'LOG_LIMIT', 4096), patch.object(runner, 'LOG_KEEP', 1000):
            self.fill(1000)
            self.assertTrue(runner._trim_log(running=False))
            kept = self.log.with_name('qdrant.log.1').read_bytes()
            self.assertLessEqual(len(kept), 1000)
            self.assertTrue(kept.startswith(b'line '), kept[:40])
            self.assertTrue(kept.endswith(b'line 00000999 of the old log\n'))
            self.assertEqual(self.log.stat().st_size, 0)
            self.fill(10)
            self.assertFalse(runner._trim_log(running=False))  # under the limit
            self.assertEqual(self.log.stat().st_size, 10 * 29)

    def test_a_running_daemon_appending_continues_at_the_new_end(self):
        with patch.object(runner, 'LOG_LIMIT', 4096), patch.object(runner, 'LOG_KEEP', 1000), \
                patch.object(runner.sys, 'platform', 'linux'):
            self.fill(1000)
            with open(self.log, 'ab', buffering=0) as daemon:
                if os.name == 'nt':
                    self.skipTest('Windows keeps a handle position of its own')
                self.assertTrue(runner._trim_log(running=True))
                daemon.write(b'after the trim\n')
            self.assertEqual(self.log.read_bytes(), b'after the trim\n')

    def test_on_windows_a_running_daemon_s_log_waits_for_the_next_start(self):
        with patch.object(runner, 'LOG_LIMIT', 4096), patch.object(runner.sys, 'platform', 'win32'):
            self.fill(1000)
            self.assertFalse(runner._trim_log(running=True))
            self.assertEqual(self.log.stat().st_size, 29000)
            self.assertTrue(runner._trim_log(running=False))

    def test_starting_trims_and_connecting_trims_a_managed_daemon_s_log(self):
        with patch.object(runner, '_trim_log') as trim, patch.object(runner, 'QDRANT_HOST', '127.0.0.1'), \
                patch.object(runner, 'is_running', return_value=True):
            with patch.object(runner, 'DISABLE_SPAWN', False):
                runner.ensure_running()
            trim.assert_called_once_with(running=True)
            with patch.object(runner, 'DISABLE_SPAWN', True):
                runner.ensure_running()  # someone else's server: its log is theirs
            trim.assert_called_once()
            with patch.object(runner.subprocess, 'Popen') as spawn:
                spawn.return_value.pid = 1234
                runner._spawn(Path('qdrant'))
                spawn.call_args.kwargs['stdout'].close()
            trim.assert_called_with(running=False)
