import http.client
import http.server
import threading
import unittest
from unittest.mock import patch

import httpx

from imprint.http_security import local_origin, local_host, loopback_address


class OriginTests(unittest.TestCase):
    def test_peer_requires_a_literal_loopback_ip(self):
        for address in ('127.0.0.1', '127.0.0.2', '::1', '::ffff:127.0.0.1'):
            self.assertTrue(loopback_address(address), address)
        for address in ('0.0.0.0', '::', '192.168.1.2', '203.0.113.1',
                        '::ffff:192.168.1.2', 'localhost', 'attacker.example', ''):
            self.assertFalse(loopback_address(address), address)

    def test_host_rejects_rebinding_and_malformed_addresses(self):
        for host in ('127.0.0.1:8420', 'localhost:3100', '[::1]:8420'):
            self.assertTrue(local_host(host))
        for host in ('attacker.example', '127.0.0.1.evil.test', 'localhost/path',
                     'localhost?host=foo', 'user@localhost', '', 'localhost:bad'):
            self.assertFalse(local_host(host), host)
    def test_loopback_dashboard_and_dev_server(self):
        for origin in ('http://127.0.0.1:8420', 'http://localhost:3000', 'http://[::1]:8420'):
            self.assertTrue(local_origin(origin), origin)

    def test_remote_and_lookalike_origins(self):
        for origin in ('null', 'http://localhost.evil.test', 'http://127.0.0.10',
                       'https://example.com', 'file:///tmp/app', 'http://user@localhost',
                       'http://localhost:bad', 'http://localhost:3000/path'):
            self.assertFalse(local_origin(origin), origin)


class APIBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, peer, path='/api/ping', headers=None, method='GET'):
        from imprint.api import app
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=peer),
            base_url='http://127.0.0.1:8420',
        ) as client:
            return await client.request(method, path, headers=headers)

    async def test_local_connections_and_browser_boundaries(self):
        for address in ('127.0.0.1', '::1', '::ffff:127.0.0.1'):
            self.assertEqual((await self.request((address, 1234))).status_code, 200)
        for headers in ({'Host': 'evil.example'}, {'Origin': 'https://evil.example'},
                        {'Origin': 'null'}, {'Origin': ''}):
            self.assertEqual((await self.request(('127.0.0.1', 1234), headers=headers)).status_code, 403)

    async def test_remote_peers_cannot_spoof_local_headers_on_any_route(self):
        headers = {'Host': 'localhost:8420', 'Origin': 'http://localhost:8420',
                   'X-Forwarded-For': '127.0.0.1', 'Forwarded': 'for=127.0.0.1'}
        for peer in (('192.168.1.2', 1234), ('203.0.113.1', 1234), None):
            for method, path in (('GET', '/'), ('GET', '/docs'), ('GET', '/api/ping'),
                                 ('POST', '/api/commands/ingest'), ('OPTIONS', '/api/ping')):
                response = await self.request(peer, path=path, headers=headers, method=method)
                self.assertEqual(response.status_code, 403, (peer, method, path))

    async def test_container_exception_is_explicit_and_preserves_browser_checks(self):
        from imprint.api import app
        with patch.object(app.state, 'container_bind', True):
            peer = ('172.18.0.1', 1234)
            self.assertEqual((await self.request(peer)).status_code, 200)
            self.assertEqual((await self.request(peer, headers={'Host': 'evil.example'})).status_code, 403)
            self.assertEqual((await self.request(peer, headers={'Origin': 'https://evil.example'})).status_code, 403)

    async def test_supported_launcher_ignores_proxy_header_configuration(self):
        from imprint.api import DashboardServer, app, main
        with patch('sys.argv', ['imprint.api', '--dev', '--no-browser']), \
                patch.dict('os.environ', {'FORWARDED_ALLOW_IPS': '*'}), \
                patch.object(app.state, 'server', None), \
                patch.object(DashboardServer, 'run', autospec=True) as run:
            main()
        config = run.call_args.args[0].config
        self.assertFalse(config.proxy_headers)
        self.assertEqual(config.host, '127.0.0.1')
        self.assertFalse(app.state.container_bind)

    async def test_public_launcher_binds_are_rejected(self):
        from imprint.api import DashboardServer, main
        for host in ('0.0.0.0', '::', '192.168.1.2'):
            with patch('sys.argv', ['imprint.api', '--host', host, '--dev', '--no-browser']), \
                    patch.object(DashboardServer, 'run') as run:
                with self.assertRaises(SystemExit) as error:
                    main()
                self.assertEqual(error.exception.code, 2)
                run.assert_not_called()


class LegacyGraphBoundaryTests(unittest.TestCase):
    def test_rebinding_and_cross_site_requests_are_rejected_before_handlers(self):
        from imprint.cli_viz import VizHandler
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), VizHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch('imprint.cli_viz.chat_store.create_session') as create:
                for method in ('GET', 'POST', 'DELETE', 'OPTIONS'):
                    for headers in ({'Host': 'evil.example'}, {'Origin': 'https://evil.example'},
                                    {'Origin': 'null'}, {'Origin': ''}):
                        connection = http.client.HTTPConnection(*server.server_address, timeout=3)
                        try:
                            connection.request(method, '/api/chat/sessions', headers=headers)
                            self.assertEqual(connection.getresponse().status, 403)
                        finally:
                            connection.close()
                create.assert_not_called()
            connection = http.client.HTTPConnection(*server.server_address, timeout=3)
            try:
                connection.request('GET', '/')
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertIsNone(response.getheader('Access-Control-Allow-Origin'))
                self.assertIn(b'<html', response.read())
            finally:
                connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
