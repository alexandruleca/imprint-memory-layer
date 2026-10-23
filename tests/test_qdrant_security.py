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
                finally:
                    options['stdout'].close()

    def test_ipv6_health_probe_uses_bracketed_address(self):
        with patch.object(runner, 'QDRANT_HOST', '::1'), \
                patch.object(runner.urllib.request, 'urlopen') as open_url:
            open_url.return_value.__enter__.return_value.status = 200
            self.assertTrue(runner.is_running())
            open_url.assert_called_once_with(f'http://[::1]:{runner.QDRANT_PORT}/readyz', timeout=1.0)
