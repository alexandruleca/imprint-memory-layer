"""The Firefox signer against a stand-in for addons.mozilla.org's v5 API (ADRs 0193, 0206)."""
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
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('sign_firefox', ROOT / 'scripts/sign_firefox_extension.py')
signer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(signer)
PACKAGE = importlib.util.spec_from_file_location('chrome_package_for_signing', ROOT / 'scripts/package_chrome_extension.py')
package = importlib.util.module_from_spec(PACKAGE)
PACKAGE.loader.exec_module(package)

ISSUER, SECRET = 'user:12345:67', 'not-a-real-secret'


def archive(names):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as zipped:
        for name in names:
            zipped.writestr(name, 'x')
    return buffer.getvalue()


class FakeAMO:
    """Answers the calls the signer makes; each poll advances one step."""

    def __init__(self, exists=False, signed=False, valid=True, signature=True, throttle=(), channel='unlisted'):
        self.addon_exists, self.signed, self.valid = exists, signed, valid
        self.throttle, self.channel = list(throttle), channel
        self.download = archive(['manifest.json'] + (['META-INF/mozilla.rsa'] if signature else []))
        self.calls, self.uploads, self.polls = [], [], 0
        self.version_created = signed

    def handle(self, method, path, body, headers):
        token = headers.get('Authorization', '').removeprefix('JWT ')
        header, claims, signature = token.split('.')
        expected = base64.urlsafe_b64encode(hmac.new(SECRET.encode(), f'{header}.{claims}'.encode(), hashlib.sha256).digest()).rstrip(b'=')
        assert hmac.compare_digest(expected.decode(), signature), 'bad JWT signature'
        claims = json.loads(base64.urlsafe_b64decode(claims + '=' * (-len(claims) % 4)))
        assert claims['iss'] == ISSUER and claims['exp'] - claims['iat'] == 60 and claims['jti']
        self.calls.append((method, path))
        addon = f'/api/v5/addons/addon/{package.GECKO_ID}'
        if (method, path) == ('POST', '/api/v5/addons/upload/'):
            if self.throttle:
                # AMO's throttle answer: the wait is only in the detail.
                return 429, {'detail': f'Request was throttled. Expected available in {self.throttle.pop(0)} seconds.'}
            self.uploads.append(body)
            return 201, {'uuid': 'u1', 'processed': False}
        if (method, path) == ('GET', '/api/v5/addons/upload/u1/'):
            self.polls += 1
            if self.polls < 2:
                return 200, {'uuid': 'u1', 'processed': False}
            messages = [] if self.valid else [{'type': 'error', 'message': 'Duplicate add-on ID found.'},
                                              {'type': 'warning', 'message': 'Unimportant'}]
            return 200, {'uuid': 'u1', 'processed': True, 'valid': self.valid, 'validation': {'messages': messages}}
        if (method, path) == ('GET', addon + '/'):
            return (200, {'guid': package.GECKO_ID}) if self.addon_exists else (404, {'detail': 'Not found.'})
        if (method, path) == ('PUT', addon + '/'):
            assert json.loads(body) == {'version': {'upload': 'u1'}}
            self.addon_exists = self.version_created = True
            return 201, {'guid': package.GECKO_ID, 'current_version': None}
        if (method, path) == ('POST', addon + '/versions/'):
            assert json.loads(body) == {'upload': 'u1'}
            self.version_created = True
            return 201, {'id': 7, 'version': '0.57.1', 'file': {'status': 'unreviewed'}}
        if (method, path) == ('GET', addon + '/versions/v0.57.1/'):
            if not self.version_created:
                return 404, {'detail': 'Not found.'}
            status = 'public' if self.signed else 'unreviewed'
            self.signed = True
            return 200, {'id': 7, 'version': '0.57.1', 'channel': self.channel,
                         'file': {'status': status, 'url': self.base + '/downloads/signed.xpi'}}
        if (method, path) == ('GET', '/downloads/signed.xpi'):
            return 200, self.download
        return 404, {'detail': f'unexpected {method} {path}'}


class SignerTests(unittest.TestCase):
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
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(code)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            do_GET = do_POST = do_PUT = do
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        amo.base = f'http://127.0.0.1:{server.server_port}'
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return signer.Client(ISSUER, SECRET, amo.base, poll=0, timeout=5)

    def test_the_first_release_creates_the_unlisted_addon_and_downloads_it_signed(self):
        amo = FakeAMO()
        output = signer.sign(self.serve(amo), self.unsigned, self.root / 'signed.xpi')
        self.assertEqual(output.read_bytes(), amo.download)
        self.assertIn(('PUT', f'/api/v5/addons/addon/{package.GECKO_ID}/'), amo.calls)
        upload = amo.uploads[0]
        self.assertIn(b'name="channel"\r\n\r\nunlisted', upload)
        self.assertIn(self.unsigned.read_bytes(), upload)

    def test_a_later_release_adds_a_version(self):
        amo = FakeAMO(exists=True)
        signer.sign(self.serve(amo), self.unsigned, self.root / 'signed.xpi')
        self.assertIn(('POST', f'/api/v5/addons/addon/{package.GECKO_ID}/versions/'), amo.calls)
        self.assertNotIn(('PUT', f'/api/v5/addons/addon/{package.GECKO_ID}/'), amo.calls)

    def test_a_version_already_signed_is_downloaded_without_uploading_again(self):
        amo = FakeAMO(exists=True, signed=True)
        signer.sign(self.serve(amo), self.unsigned, self.root / 'signed.xpi')
        self.assertEqual(amo.uploads, [])

    def test_failures_stop_without_writing_the_output(self):
        for amo, message in ((FakeAMO(valid=False), 'Duplicate add-on ID found'),
                             (FakeAMO(signature=False), 'no Mozilla signature')):
            with self.subTest(message), self.assertRaisesRegex(RuntimeError, message):
                signer.sign(self.serve(amo), self.unsigned, self.root / 'signed.xpi')
            self.assertFalse((self.root / 'signed.xpi').exists())

    def test_a_number_amo_holds_as_listed_is_refused(self):
        amo = FakeAMO(exists=True, signed=True, channel='listed')
        with self.assertRaisesRegex(RuntimeError, 'already holds 0.57.1 as listed'):
            signer.sign(self.serve(amo), self.unsigned, self.root / 'signed.xpi')
        self.assertEqual(amo.uploads, [])

    def test_a_short_throttle_is_waited_out(self):
        amo = FakeAMO(exists=True, throttle=[3])
        client = self.serve(amo)
        with mock.patch.object(signer.time, 'sleep') as sleep:
            output = signer.sign(client, self.unsigned, self.root / 'signed.xpi')
        self.assertEqual(output.read_bytes(), amo.download)
        sleep.assert_any_call(4)
        self.assertEqual(len(amo.uploads), 1)

    def test_a_long_throttle_leaves_the_release_unsigned_instead_of_failing_it(self):
        amo = FakeAMO(exists=True, throttle=[4000])
        client = self.serve(amo)
        with self.assertRaisesRegex(signer.Throttled, 'for 67 more minutes'):
            signer.sign(client, self.unsigned, self.root / 'signed.xpi')
        amo.throttle = [4000]
        argv = ['sign_firefox_extension.py', str(self.unsigned), str(self.root / 'signed.xpi')]
        env = {'AMO_JWT_ISSUER': ISSUER, 'AMO_JWT_SECRET': SECRET, 'AMO_BASE_URL': amo.base}
        with mock.patch.object(signer.sys, 'argv', argv), mock.patch.dict(os.environ, env), \
                mock.patch('builtins.print') as printed:
            signer.main()
        self.assertIn('::warning::', printed.call_args.args[0])
        self.assertIn('only the unsigned Firefox add-on', printed.call_args.args[0])
        self.assertFalse((self.root / 'signed.xpi').exists())
        self.assertEqual(amo.uploads, [])


if __name__ == '__main__':
    unittest.main()
