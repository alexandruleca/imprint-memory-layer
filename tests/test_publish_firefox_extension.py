"""The Firefox submitter against a stand-in for addons.mozilla.org's v5 API (ADRs 0203, 0207)."""
import base64
import hashlib
import hmac
import importlib.util
import io
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('publish_firefox', ROOT / 'scripts/publish_firefox_extension.py')
submitter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(submitter)
PACKAGE = importlib.util.spec_from_file_location('chrome_package_for_amo', ROOT / 'scripts/package_chrome_extension.py')
package = importlib.util.module_from_spec(PACKAGE)
PACKAGE.loader.exec_module(package)

ISSUER, SECRET = 'user:12345:67', 'not-a-real-secret'


class FakeAMO:
    """Answers the calls the submitter makes; each validation poll advances one step."""

    def __init__(self, channels=('unlisted', 'listed'), existing=None, valid=True, status='unreviewed', throttle=()):
        self.channels, self.existing, self.valid, self.status = channels, existing, valid, status
        self.throttle = list(throttle)
        self.calls, self.uploads, self.polls = [], [], 0

    def handle(self, method, path, body, headers):
        token = headers.get('Authorization', '').removeprefix('JWT ')
        header, claims, signature = token.split('.')
        expected = base64.urlsafe_b64encode(hmac.new(SECRET.encode(), f'{header}.{claims}'.encode(), hashlib.sha256).digest()).rstrip(b'=')
        assert hmac.compare_digest(expected.decode(), signature), 'bad JWT signature'
        claims = json.loads(base64.urlsafe_b64decode(claims + '=' * (-len(claims) % 4)))
        assert claims['iss'] == ISSUER and claims['exp'] - claims['iat'] == 60 and claims['jti']
        self.calls.append((method, path))
        addon = f'/api/v5/addons/addon/{package.GECKO_ID}'
        if (method, path) == ('GET', addon + '/versions/v0.57.1/'):
            return (200, self.existing) if self.existing else (404, {'detail': 'Not found.'})
        if (method, path) == ('GET', addon + '/'):
            if self.channels is None:
                return 404, {'detail': 'Not found.'}
            return 200, {'guid': package.GECKO_ID, 'channels': list(self.channels)}
        if (method, path) == ('POST', '/api/v5/addons/upload/'):
            self.uploads.append(body)
            return 201, {'uuid': 'u1', 'processed': False}
        if (method, path) == ('GET', '/api/v5/addons/upload/u1/'):
            self.polls += 1
            if self.polls < 2:
                return 200, {'uuid': 'u1', 'processed': False}
            messages = [] if self.valid else [{'type': 'error', 'message': 'Duplicate add-on ID found.'},
                                              {'type': 'warning', 'message': 'Unimportant'}]
            return 200, {'uuid': 'u1', 'processed': True, 'valid': self.valid, 'validation': {'messages': messages}}
        if (method, path) == ('POST', addon + '/versions/'):
            if self.throttle:
                # AMO's throttle answer, as v0.62.0 and v0.62.1 got it: the wait is only in the detail.
                return 429, {'detail': f'Request was throttled. Expected available in {self.throttle.pop(0)} seconds.'}
            assert json.loads(body) == {'upload': 'u1'}
            return 201, {'id': 7, 'version': '0.57.1', 'channel': 'listed', 'file': {'status': self.status}}
        return 404, {'detail': f'unexpected {method} {path}'}


class SubmitterTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.unsigned = package.package(ROOT / 'extensions/chrome', self.root / 'unsigned.xpi', browser='firefox', version='v0.57.1')

    def serve(self, amo):
        class Handler(BaseHTTPRequestHandler):
            def do(self):
                body = self.rfile.read(int(self.headers.get('Content-Length') or 0))
                try:
                    code, payload = amo.handle(self.command, self.path, body, self.headers)
                except AssertionError as error:
                    code, payload = 500, {'detail': str(error)}
                data = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            do_GET = do_POST = do
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        amo.base = f'http://127.0.0.1:{server.server_port}'
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return submitter.Client(ISSUER, SECRET, amo.base, poll=0, timeout=5)

    def submit(self, amo):
        output = io.StringIO()
        with mock.patch('sys.stdout', output):
            status = submitter.submit(self.serve(amo), self.unsigned)
        return status, output.getvalue()

    def test_a_release_adds_a_listed_version_for_review(self):
        amo = FakeAMO()
        status, output = self.submit(amo)
        self.assertEqual(status, 'unreviewed')
        self.assertIn(('POST', f'/api/v5/addons/addon/{package.GECKO_ID}/versions/'), amo.calls)
        upload = amo.uploads[0]
        self.assertIn(b'name="channel"\r\n\r\nlisted', upload)
        self.assertIn(self.unsigned.read_bytes(), upload)
        self.assertIn('Firefox updates users once it is approved', output)

    def test_a_version_already_listed_is_not_submitted_again(self):
        amo = FakeAMO(existing={'version': '0.57.1', 'channel': 'listed', 'file': {'status': 'public'}})
        self.assertEqual(self.submit(amo)[0], 'public')
        self.assertEqual(amo.uploads, [])

    def test_without_a_listing_nothing_is_submitted_and_the_run_says_how_to_make_one(self):
        for channels in (('unlisted',), None):
            with self.subTest(channels):
                amo = FakeAMO(channels=channels)
                status, output = self.submit(amo)
                self.assertIsNone(status)
                self.assertEqual(amo.uploads, [])
                self.assertIn('::warning::', output)
                self.assertIn('On this site', output)

    def test_failures_raise(self):
        for amo, message in ((FakeAMO(valid=False), 'Duplicate add-on ID found'),
                             (FakeAMO(status='disabled'), 'rejected or disabled'),
                             (FakeAMO(existing={'version': '0.57.1', 'channel': 'unlisted', 'file': {'status': 'public'}}),
                              'already holds 0.57.1 as unlisted')):
            with self.subTest(message), self.assertRaisesRegex(RuntimeError, message):
                self.submit(amo)

    def test_a_short_throttle_is_waited_out(self):
        amo = FakeAMO(throttle=[3])
        with mock.patch.object(submitter.time, 'sleep') as sleep:
            status, _ = self.submit(amo)
        self.assertEqual(status, 'unreviewed')
        sleep.assert_any_call(4)
        self.assertEqual(amo.calls.count(('POST', f'/api/v5/addons/addon/{package.GECKO_ID}/versions/')), 2)

    def test_a_long_throttle_warns_instead_of_failing_the_release(self):
        amo = FakeAMO(throttle=[6480])
        with self.assertRaisesRegex(submitter.Throttled, 'for 108 more minutes'):
            self.submit(amo)
        amo.throttle = [6480]
        argv = ['publish_firefox_extension.py', str(self.unsigned)]
        env = {'AMO_JWT_ISSUER': ISSUER, 'AMO_JWT_SECRET': SECRET, 'AMO_BASE_URL': amo.base}
        with mock.patch.object(submitter.sys, 'argv', argv), mock.patch.dict(os.environ, env), \
                mock.patch('builtins.print') as printed:
            submitter.main()
        self.assertIn('::warning::', printed.call_args.args[0])
        self.assertIn('0.57.1 was not submitted', printed.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
