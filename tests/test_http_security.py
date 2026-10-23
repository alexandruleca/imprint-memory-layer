import unittest

from imprint.http_security import local_origin, local_host


class OriginTests(unittest.TestCase):
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
