"""URL ingestion honours robots.txt (ingest.url_respect_robots) and caps
downloads at ingest.max_doc_size_mb."""
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from imprint import extractors
from imprint.extractors import url as url_ext
from emulation import slack


class Site(BaseHTTPRequestHandler):
    """Serves ``routes``: path -> (status, body, headers); records each request."""
    routes: dict = {}
    seen: list = []

    def do_GET(self):
        self.seen.append(self.path)
        status, body, headers = self.routes.get(self.path, (404, b'missing', {}))
        self.send_response(status)
        headers = {'Content-Type': 'text/plain', 'Content-Length': str(len(body)), **headers}
        for name, value in headers.items():
            if value is not None:
                self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class SiteTests(unittest.TestCase):
    """A local site with a robots.txt and a clean robots cache."""
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {'IMPRINT_DATA_DIR': temp.name, 'IMPRINT_INGEST_URL_TIMEOUT': str(round(slack(10)))})
        env.start(); self.addCleanup(env.stop)
        Site.routes = {'/robots.txt': (200, b'User-agent: *\nDisallow: /private\n', {}),
                       '/public': (200, b'public page text', {}),
                       '/private': (200, b'private page text', {})}
        Site.seen = []
        server = ThreadingHTTPServer(('127.0.0.1', 0), Site)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.base = f'http://127.0.0.1:{server.server_address[1]}'
        url_ext._ROBOTS.clear()
        self.addCleanup(url_ext._ROBOTS.clear)
        # Pages come back as their raw text; extraction itself is not under test.
        extract = patch.object(url_ext, 'dispatch_by_mime',
                               side_effect=lambda mime, data, source_url='': [extractors.ExtractedDoc(data.decode())])
        extract.start(); self.addCleanup(extract.stop)

    def text(self, path):
        return url_ext.fetch(self.base + path)[0].text


class RobotsTests(SiteTests):
    def test_disallowed_url_is_refused_without_being_fetched(self):
        with self.assertRaisesRegex(url_ext.RobotsDisallowed, r'robots.txt at .* disallows .*/private'):
            url_ext.fetch(self.base + '/private')
        self.assertEqual(Site.seen, ['/robots.txt'])

    def test_allowed_url_is_fetched_and_robots_read_once_per_site(self):
        self.assertIn('public page text', self.text('/public'))
        self.assertIn('public page text', self.text('/public'))
        self.assertEqual(Site.seen, ['/robots.txt', '/public', '/public'])

    def test_rules_match_the_configured_user_agent(self):
        Site.routes['/robots.txt'] = (200, b'User-agent: mybot\nDisallow: /\n', {})
        self.assertIn('public page text', self.text('/public'))
        url_ext._ROBOTS.clear()
        with patch.dict(os.environ, {'IMPRINT_INGEST_URL_USER_AGENT': 'mybot/2.0'}):
            with self.assertRaises(url_ext.RobotsDisallowed):
                url_ext.fetch(self.base + '/public')

    def test_setting_off_skips_robots_entirely(self):
        with patch.dict(os.environ, {'IMPRINT_INGEST_URL_RESPECT_ROBOTS': 'false'}):
            self.assertIn('private page text', self.text('/private'))
        self.assertEqual(Site.seen, ['/private'])

    def test_missing_robots_allows_everything(self):
        del Site.routes['/robots.txt']
        self.assertIn('private page text', self.text('/private'))

    def test_failing_robots_refuses_the_site(self):
        Site.routes['/robots.txt'] = (503, b'down', {})
        with self.assertRaisesRegex(url_ext.RobotsDisallowed, 'HTTP 503; refusing the site'):
            url_ext.fetch(self.base + '/public')
        self.assertEqual(Site.seen, ['/robots.txt'])

    def test_non_http_urls_never_fetch_robots(self):
        self.assertEqual(url_ext.robots_refusal('file:///etc/hosts'), '')
        self.assertEqual(Site.seen, [])

    def test_followed_links_skip_disallowed_pages(self):
        page = b'<a href="/public">a</a> <a href="/private">b</a>'
        Site.routes['/index'] = (200, page, {'Content-Type': 'text/html'})
        self.assertEqual(url_ext.extract_links(self.base + '/index'), [self.base + '/public'])

    def test_ingest_and_refresh_report_the_refusal_and_keep_prior_content(self):
        from imprint import cli_ingest_url
        url = self.base + '/private'
        known = {url: {'project': 'docs', 'etag': 'old'}}
        with patch.object(cli_ingest_url.vs, 'replace_source') as replace:
            count, status = cli_ingest_url.ingest_one(url, 'docs', known, force=True)
            self.assertEqual(url_ext.head_check(url), {})  # refresh's change check sends nothing
        self.assertEqual(count, 0)
        self.assertIn('disallows', status)
        replace.assert_not_called()
        self.assertNotIn('/private', Site.seen)


class DownloadSizeTests(SiteTests):
    """A download over ingest.max_doc_size_mb fails before extraction."""
    def setUp(self):
        super().setUp()
        env = patch.dict(os.environ, {'IMPRINT_INGEST_MAX_DOC_MB': '1'})
        env.start(); self.addCleanup(env.stop)
        big = b'x' * (1024 * 1024 + 1)
        Site.routes['/declared'] = (200, big, {})
        Site.routes['/streamed'] = (200, big, {'Content-Length': None, 'Connection': 'close'})

    def test_oversize_downloads_are_refused_before_extraction(self):
        with patch.object(url_ext, 'dispatch_by_mime') as extract:
            for path in ('/declared', '/streamed'):
                with self.assertRaisesRegex(extractors.DocumentTooLarge, r'exceeds ingest.max_doc_size_mb \(1 MiB\)'):
                    url_ext.fetch(self.base + path)
        extract.assert_not_called()


if __name__ == '__main__':
    unittest.main()
