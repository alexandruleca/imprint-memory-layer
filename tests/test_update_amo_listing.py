"""The AMO listing updater against a stand-in for addons.mozilla.org's v5 API (ADR 0203)."""
import importlib.util
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import struct
import threading
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('update_amo_listing', ROOT / 'scripts/update_amo_listing.py')
listing = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(listing)
STORE = ROOT / 'extensions/store'
ADDON = f'/api/v5/addons/addon/{listing.GECKO_ID}'


class FakeAMO:
    def __init__(self, categories=(), previews=(), throttle=0, held=None, policy=None, throttle_seconds=0):
        self.categories, self.previews, self.throttle = categories, list(previews), throttle
        self.throttle_seconds = throttle_seconds
        self.held, self.policy = dict(held or {}), policy
        self.patches, self.deleted, self.images, self.captions = {}, [], [], {}

    def handle(self, method, path, body, headers):
        assert headers.get('Authorization', '').startswith('JWT '), 'unsigned request'
        if (method, path) == ('GET', ADDON + '/'):
            previews = [p if isinstance(p, dict) else {'id': p, 'position': 0, 'caption': None} for p in self.previews]
            return 200, {'guid': listing.GECKO_ID, 'default_locale': 'en-US', 'categories': self.categories,
                         'previews': previews, **self.held}
        if (method, path) == ('GET', ADDON + '/eula_policy/'):
            return 200, {'eula': None, 'privacy_policy': self.policy}
        if method == 'PATCH' and path in (ADDON + '/', ADDON + '/eula_policy/'):
            self.patches[path] = json.loads(body)
            return 200, {}
        if method == 'DELETE' and path.startswith(ADDON + '/previews/'):
            self.deleted.append(int(path.rstrip('/').rsplit('/', 1)[1]))
            return 204, None
        if (method, path) == ('POST', ADDON + '/previews/'):
            assert headers.get('Content-Type', '').startswith('multipart/form-data; boundary=')
            assert b'name="image"; filename="' in body and b'Content-Type: image/png' in body
            position = int(body.split(b'name="position"\r\n\r\n', 1)[1].split(b'\r\n', 1)[0])
            self.images.append((position, body))
            return 201, {'id': 100 + position}
        if method == 'PATCH' and path.startswith(ADDON + '/previews/'):
            if self.throttle:
                self.throttle -= 1
                return 429, {'detail': f'Request was throttled. Expected available in {self.throttle_seconds} seconds.'}
            self.captions[int(path.rstrip('/').rsplit('/', 1)[1])] = json.loads(body)
            return 200, {}
        return 404, {'detail': f'unexpected {method} {path}'}


class ListingTests(unittest.TestCase):
    def serve(self, amo):
        class Handler(BaseHTTPRequestHandler):
            def do(self):
                body = self.rfile.read(int(self.headers.get('Content-Length') or 0))
                try:
                    code, payload = amo.handle(self.command, self.path, body, self.headers)
                except AssertionError as error:
                    code, payload = 500, {'detail': str(error)}
                data = b'' if payload is None else json.dumps(payload).encode()
                self.send_response(code)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            do_GET = do_POST = do_PATCH = do_DELETE = do
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return listing.Client('user:1:2', 'not-a-real-secret', f'http://127.0.0.1:{server.server_port}', poll=0, timeout=5)

    def update(self, amo, **options):
        with mock.patch('sys.stdout', io.StringIO()) as output:
            listing.update(self.serve(amo), **options)
        return output.getvalue()

    def test_a_listing_without_screenshots_gets_its_text_and_every_screenshot_with_its_caption(self):
        amo = FakeAMO()
        self.update(amo)
        data = json.loads((STORE / 'amo-listing.json').read_text(encoding='utf-8'))
        patch = amo.patches[ADDON + '/']
        self.assertEqual(patch['summary'], {'en-US': data['summary']})
        self.assertEqual(patch['description'], {'en-US': (STORE / 'amo-description.md').read_text(encoding='utf-8').strip()})
        self.assertEqual(patch['categories'], data['categories'])
        self.assertEqual(patch['homepage'], {'en-US': data['homepage']})
        self.assertEqual(amo.patches[ADDON + '/eula_policy/'],
                         {'privacy_policy': {'en-US': (STORE / 'amo-privacy-policy.md').read_text(encoding='utf-8').strip()}})
        self.assertEqual([position for position, _ in amo.images], list(range(len(data['screenshots']))))
        for (position, body), shot in zip(amo.images, data['screenshots']):
            self.assertIn((STORE / shot['file']).read_bytes(), body)
            self.assertEqual(amo.captions[100 + position], {'caption': {'en-US': shot['caption']}, 'position': position})

    def test_categories_follow_the_shape_the_addon_reads_back(self):
        amo = FakeAMO(categories={'firefox': ['other']}, previews=[1])
        self.update(amo)
        self.assertEqual(amo.patches[ADDON + '/']['categories'], {'firefox': ['bookmarks', 'web-development']})

    def test_a_partial_upload_is_completed_and_captions_set_without_uploading_again(self):
        data = json.loads((STORE / 'amo-listing.json').read_text(encoding='utf-8'))
        first = data['screenshots'][0]['caption']
        amo = FakeAMO(previews=[{'id': 7, 'position': 0, 'caption': None}])
        self.assertIn('4 uploaded, 1 kept', self.update(amo))
        self.assertEqual(amo.deleted, [])
        self.assertEqual(amo.captions[7], {'caption': {'en-US': first}, 'position': 0})
        self.assertEqual([position for position, _ in amo.images], [1, 2, 3, 4])
        # A complete listing needs no writes at all.
        done = [{'id': 10 + i, 'position': i, 'caption': {'en-US': shot['caption']}} for i, shot in enumerate(data['screenshots'])]
        amo = FakeAMO(previews=done)
        self.assertIn('0 uploaded, 5 kept', self.update(amo))
        self.assertEqual((amo.images, amo.captions, amo.deleted), ([], {}, []))

    def test_replacing_deletes_every_screenshot_and_uploads_the_listing(self):
        amo = FakeAMO(previews=[7, 8])
        self.update(amo, replace_screenshots=True)
        self.assertEqual(amo.deleted, [7, 8])
        self.assertEqual(len(amo.images), 5)

    def test_a_throttled_request_waits_and_retries(self):
        amo = FakeAMO(throttle=2)
        output = self.update(amo)
        self.assertEqual(output.count('throttled PATCH'), 2)
        self.assertEqual(len(amo.captions), 5)

    def test_a_long_throttle_stops_the_run_saying_when_to_run_it_again(self):
        amo = FakeAMO(throttle=1, throttle_seconds=2403)
        with self.assertRaisesRegex(RuntimeError, 'throttled PATCH .* for 40 more minutes; run this again after that'):
            self.update(amo)

    def test_text_amo_already_holds_is_not_written_again(self):
        data = json.loads((STORE / 'amo-listing.json').read_text(encoding='utf-8'))
        description = (STORE / 'amo-description.md').read_text(encoding='utf-8')
        # As AMO might return it: rendered, links rewritten through its redirector.
        rendered = '<p>' + description.replace('**', '').replace('[Imprint app](https://imprintmcp.alexandruleca.com/)',
                   '<a href="https://outgoing.prod.mozaws.net/v1/abc/https%3A//imprintmcp">Imprint app</a>') + '</p>'
        policy = (STORE / 'amo-privacy-policy.md').read_text(encoding='utf-8').replace('→', '&rarr;')
        held = {'summary': {'en-US': data['summary']}, 'description': {'en-US': rendered},
                'homepage': {'url': {'en-US': data['homepage']}, 'outgoing': {'en-US': 'https://outgoing.prod.mozaws.net/x'}},
                'support_url': {'url': {'en-US': data['support_url']}, 'outgoing': {'en-US': 'https://outgoing.prod.mozaws.net/y'}}}
        amo = FakeAMO(categories=data['categories'], held=held, policy={'en-US': policy}, previews=[1, 2, 3, 4, 5])
        self.assertIn('already current', self.update(amo))
        self.assertEqual(amo.patches, {})
        # A changed summary is the only field written.
        held['summary'] = {'en-US': 'An older summary'}
        amo = FakeAMO(categories=data['categories'], held=held, policy={'en-US': policy}, previews=[1, 2, 3, 4, 5])
        self.assertIn('summary updated', self.update(amo))
        self.assertEqual(amo.patches, {ADDON + '/': {'summary': {'en-US': data['summary']}}})

    def test_the_listing_files_fit_amo(self):
        data = json.loads((STORE / 'amo-listing.json').read_text(encoding='utf-8'))
        self.assertLessEqual(len(data['summary']), 250)
        self.assertLessEqual(len(data['categories']), 2)
        for shot in data['screenshots']:
            with self.subTest(shot['file']):
                header = (STORE / shot['file']).read_bytes()[:24]
                self.assertEqual(header[:8], b'\x89PNG\r\n\x1a\n')
                self.assertEqual(struct.unpack('>II', header[16:24]), (1280, 800))


if __name__ == '__main__':
    unittest.main()
