"""The Chrome Web Store publisher against a stand-in for Google's token endpoint and the v2 API (ADR 0202)."""
import base64
import importlib.util
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest import mock
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('publish_chrome', ROOT / 'scripts/publish_chrome_extension.py')
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)
PACKAGE = importlib.util.spec_from_file_location('chrome_package_for_store', ROOT / 'scripts/package_chrome_extension.py')
package = importlib.util.module_from_spec(PACKAGE)
PACKAGE.loader.exec_module(package)

PUBLISHER = 'pub-123'
ITEM = f'/v2/publishers/{PUBLISHER}/items/{publisher.ITEM_ID}'


def fake_sign(key, data):
    return b'signed:' + data


def decode(part):
    return json.loads(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4)))


class FakeStore:
    """Google's token endpoint and the store; status() reports the configured revisions."""

    def __init__(self, published=None, submitted=None, upload_state='SUCCEEDED', async_states=()):
        self.published, self.submitted = published, submitted
        self.upload_state, self.async_states = upload_state, list(async_states)
        self.calls, self.uploads, self.publishes, self.tokens = [], [], [], 0

    @staticmethod
    def revision(state, version):
        return {'state': state, 'distributionChannels': [{'deployPercentage': 100, 'crxVersion': version}]}

    def handle(self, method, path, body, headers):
        if path == '/token':
            form = urllib.parse.parse_qs(body.decode())
            assert form['grant_type'] == ['urn:ietf:params:oauth:grant-type:jwt-bearer']
            header, claims, signature = form['assertion'][0].split('.')
            claims_decoded = decode(claims)
            assert decode(header) == {'alg': 'RS256', 'typ': 'JWT', 'kid': 'key-1'}
            assert claims_decoded['iss'] == 'ci@example.iam.gserviceaccount.com'
            assert claims_decoded['scope'] == publisher.SCOPE and claims_decoded['aud'] == self.base + '/token'
            assert claims_decoded['exp'] - claims_decoded['iat'] == 3600
            assert base64.urlsafe_b64decode(signature + '==') == b'signed:' + f'{header}.{claims}'.encode()
            self.tokens += 1
            return 200, {'access_token': 'token-1', 'expires_in': 3600}
        assert headers.get('Authorization') == 'Bearer token-1', 'missing bearer token'
        self.calls.append((method, path))
        if (method, path) == ('GET', ITEM + ':fetchStatus'):
            status = {'itemId': publisher.ITEM_ID}
            if self.published:
                status['publishedItemRevisionStatus'] = self.published
            if self.submitted:
                status['submittedItemRevisionStatus'] = self.submitted
            if self.uploads and self.async_states:
                status['lastAsyncUploadState'] = self.async_states.pop(0)
            return 200, status
        if (method, path) == ('POST', '/upload' + ITEM + ':upload'):
            assert headers.get('Content-Type') == 'application/zip'
            self.uploads.append(body)
            done = self.upload_state == 'SUCCEEDED'
            return 200, {'itemId': publisher.ITEM_ID, 'uploadState': self.upload_state, **({'crxVersion': '0.57.1'} if done else {})}
        if (method, path) == ('POST', ITEM + ':publish'):
            self.publishes.append(json.loads(body))
            self.submitted = self.revision('PENDING_REVIEW', '0.57.1')
            return 200, {'itemId': publisher.ITEM_ID, 'state': 'PENDING_REVIEW',
                         'warningInfo': {'warnings': [{'reason': 'NOTE', 'description': 'Just so you know'}]}}
        return 404, {'error': f'unexpected {method} {path}'}


class PublisherTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.zip = package.package(ROOT / 'extensions/chrome', self.root / 'store.zip', browser='chrome-web-store', version='v0.57.1')

    def serve(self, store):
        class Handler(BaseHTTPRequestHandler):
            def do(self):
                body = self.rfile.read(int(self.headers.get('Content-Length') or 0))
                try:
                    code, payload = store.handle(self.command, self.path, body, self.headers)
                except AssertionError as error:
                    code, payload = 500, {'error': str(error)}
                data = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            do_GET = do_POST = do
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        store.base = f'http://127.0.0.1:{server.server_port}'
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        account = {'client_email': 'ci@example.iam.gserviceaccount.com', 'private_key': 'unused',
                   'private_key_id': 'key-1', 'token_uri': store.base + '/token'}
        return publisher.Client(account, PUBLISHER, base=store.base, sign=fake_sign, poll=0, timeout=5)

    def submit(self, store):
        output = io.StringIO()
        with mock.patch('sys.stdout', output):
            state = publisher.submit(self.serve(store), self.zip)
        return state, output.getvalue()

    def test_a_new_version_is_uploaded_and_submitted_to_go_live_once_approved(self):
        store = FakeStore(published=FakeStore.revision('PUBLISHED', '0.56.0'))
        state, output = self.submit(store)
        self.assertEqual(state, 'PENDING_REVIEW')
        self.assertEqual(store.uploads, [self.zip.read_bytes()])
        self.assertEqual(store.publishes, [{'publishType': 'DEFAULT_PUBLISH'}])
        self.assertEqual(store.tokens, 1)
        self.assertIn('::warning::Chrome Web Store: NOTE: Just so you know', output)

    def test_an_upload_the_store_processes_later_is_awaited(self):
        store = FakeStore(upload_state='IN_PROGRESS', async_states=['IN_PROGRESS', 'SUCCEEDED'])
        self.assertEqual(self.submit(store)[0], 'PENDING_REVIEW')
        self.assertEqual(len(store.publishes), 1)

    def test_a_version_the_store_already_holds_is_not_uploaded_again(self):
        for published, submitted, expected in (
                (FakeStore.revision('PUBLISHED', '0.57.1'), None, 'PUBLISHED'),
                (FakeStore.revision('PUBLISHED', '0.56.0'), FakeStore.revision('PENDING_REVIEW', '0.57.1'), 'PENDING_REVIEW')):
            with self.subTest(expected):
                store = FakeStore(published=published, submitted=submitted)
                self.assertEqual(self.submit(store)[0], expected)
                self.assertEqual((store.uploads, store.publishes), ([], []))

    def test_an_earlier_version_in_review_is_left_to_finish(self):
        store = FakeStore(submitted=FakeStore.revision('PENDING_REVIEW', '0.56.0'))
        state, output = self.submit(store)
        self.assertEqual(state, 'WAITING')
        self.assertEqual((store.uploads, store.publishes), ([], []))
        self.assertIn('::warning::The Chrome Web Store is still reviewing 0.56.0', output)

    def test_failures_stop_before_publishing(self):
        for store, message in ((FakeStore(upload_state='FAILED'), 'did not accept the upload'),
                               (FakeStore(upload_state='IN_PROGRESS', async_states=['FAILED']), 'state FAILED'),
                               (FakeStore(submitted=FakeStore.revision('REJECTED', '0.57.1')), 'rejected 0.57.1')):
            with self.subTest(message), self.assertRaisesRegex(RuntimeError, message):
                self.submit(store)
            self.assertEqual(store.publishes, [])

    @unittest.skipUnless(shutil.which('openssl'), 'needs the openssl command')
    def test_openssl_signs_rs256_that_the_public_key_verifies(self):
        key = self.root / 'key.pem'
        subprocess.run(['openssl', 'genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:2048', '-out', str(key)],
                       check=True, capture_output=True)
        public = self.root / 'public.pem'
        subprocess.run(['openssl', 'pkey', '-in', str(key), '-pubout', '-out', str(public)], check=True, capture_output=True)
        token = publisher.assertion({'client_email': 'ci@example.iam.gserviceaccount.com', 'private_key': key.read_text()}, now=1000)
        signing_input, signature = token.rsplit('.', 1)
        (self.root / 'signature').write_bytes(base64.urlsafe_b64decode(signature + '=' * (-len(signature) % 4)))
        verified = subprocess.run(['openssl', 'dgst', '-sha256', '-verify', str(public), '-signature', str(self.root / 'signature')],
                                  input=signing_input.encode(), capture_output=True)
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
        self.assertEqual(decode(signing_input.split('.')[1])['exp'], 4600)


if __name__ == '__main__':
    unittest.main()
