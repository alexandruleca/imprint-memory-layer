#!/usr/bin/env python3
"""Submit the capture extension to its Chrome Web Store item (ADR 0202).

    CWS_SERVICE_ACCOUNT_JSON='{"client_email": …}' CWS_PUBLISHER_ID=… \\
        python scripts/publish_chrome_extension.py dist/imprint-chrome-web-store.zip

Uses the Chrome Web Store API v2 with the standard library, and the openssl
command for the service account's RS256 token: read the item's status, upload
the package, wait while the store processes it, and submit it for review with
DEFAULT_PUBLISH, so it goes live once approved and Chrome updates every copy.

A version the store already holds is not uploaded again, so a rerun does not
fail. While an earlier version still waits for review, nothing is uploaded:
cancelling that review on every release could keep any version from ever
passing. Rerun the job after the review, or the next release submits.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

# The listing: chromewebstore.google.com/detail/imprint-capture/<ITEM_ID>
ITEM_ID = 'ammleledmkghliflljeapomaaccnhmme'
SCOPE = 'https://www.googleapis.com/auth/chromewebstore'
TOKEN_URI = 'https://oauth2.googleapis.com/token'


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def openssl_sign(private_key, data):
    """RS256 (RSASSA-PKCS1-v1_5 over SHA-256) with the openssl command."""
    with tempfile.TemporaryDirectory() as directory:
        key = Path(directory) / 'key.pem'
        descriptor = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as file:
            file.write(private_key)
        result = subprocess.run(['openssl', 'dgst', '-sha256', '-sign', str(key)],
                                input=data, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError('openssl could not sign with the service account key: '
                           + result.stderr.decode('utf-8', 'replace').strip())
    return result.stdout


def assertion(account, now=None, sign=openssl_sign):
    """The service account's signed JWT that Google exchanges for an access token."""
    now = int(time.time() if now is None else now)
    header = {'alg': 'RS256', 'typ': 'JWT'}
    if account.get('private_key_id'):
        header['kid'] = account['private_key_id']
    claims = {'iss': account['client_email'], 'scope': SCOPE,
              'aud': account.get('token_uri') or TOKEN_URI, 'iat': now, 'exp': now + 3600}
    signing_input = '.'.join(b64(json.dumps(part, separators=(',', ':')).encode()) for part in (header, claims))
    return signing_input + '.' + b64(sign(account['private_key'], signing_input.encode()))


def http(method, url, data=None, headers=None):
    request = urllib.request.Request(url, data=data, method=method, headers={'Accept': 'application/json', **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            return json.loads(response.read() or b'null')
    except urllib.error.HTTPError as error:
        with error:
            detail = error.read().decode('utf-8', 'replace')[:2000]
        raise RuntimeError(f'{method} {url}: HTTP {error.code}: {detail}') from None


class Client:
    def __init__(self, account, publisher, item=ITEM_ID, base='https://chromewebstore.googleapis.com',
                 sign=openssl_sign, poll=10.0, timeout=600.0):
        self.account, self.base, self.sign = account, base.rstrip('/'), sign
        self.name = f'publishers/{publisher}/items/{item}'
        self.poll, self.timeout, self.token = poll, timeout, None

    def request(self, method, path, body=None, content_type='application/json'):
        if self.token is None:
            form = urllib.parse.urlencode({'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
                                           'assertion': assertion(self.account, sign=self.sign)}).encode()
            response = http('POST', self.account.get('token_uri') or TOKEN_URI, form,
                            {'Content-Type': 'application/x-www-form-urlencoded'})
            self.token = response['access_token']
        data = json.dumps(body).encode() if content_type == 'application/json' and body is not None else body
        headers = {'Authorization': f'Bearer {self.token}', **({'Content-Type': content_type} if data is not None else {})}
        return http(method, self.base + path, data, headers)

    def status(self):
        return self.request('GET', f'/v2/{self.name}:fetchStatus')

    def upload(self, package):
        return self.request('POST', f'/upload/v2/{self.name}:upload', Path(package).read_bytes(), 'application/zip')

    def publish(self):
        return self.request('POST', f'/v2/{self.name}:publish', {'publishType': 'DEFAULT_PUBLISH'})


def versions(revision):
    return {channel.get('crxVersion') for channel in (revision or {}).get('distributionChannels') or []}


def submit(client, package):
    with zipfile.ZipFile(package) as archive:
        version = json.loads(archive.read('manifest.json'))['version']
    status = client.status()
    published, submitted = status.get('publishedItemRevisionStatus'), status.get('submittedItemRevisionStatus')
    if version in versions(published):
        print(f'Chrome Web Store already publishes {version}')
        return 'PUBLISHED'
    if version in versions(submitted):
        state = submitted.get('state')
        if state in ('REJECTED', 'CANCELLED'):
            raise RuntimeError(f'The Chrome Web Store {state.lower()} {version}; release a new version')
        print(f'Chrome Web Store already has {version}: {state}')
        return state
    if (submitted or {}).get('state') == 'PENDING_REVIEW':
        waiting = ', '.join(sorted(filter(None, versions(submitted)))) or 'an earlier version'
        print(f'::warning::The Chrome Web Store is still reviewing {waiting}, so {version} was not submitted. '
              'Rerun this job once that review ends, or the next release submits its own version.')
        return 'WAITING'
    print(f'Uploading {version} to the Chrome Web Store')
    upload = client.upload(package)
    state = upload.get('uploadState')
    deadline = time.monotonic() + client.timeout
    while state in ('IN_PROGRESS', 'UPLOAD_IN_PROGRESS'):
        if time.monotonic() > deadline:
            raise RuntimeError('Timed out waiting for the Chrome Web Store to process the upload')
        time.sleep(client.poll)
        state = client.status().get('lastAsyncUploadState')
    if state != 'SUCCEEDED':
        raise RuntimeError(f'The Chrome Web Store did not accept the upload: {json.dumps(upload)[:1000]} (state {state})')
    if upload.get('crxVersion') not in (None, version):
        raise RuntimeError(f'The Chrome Web Store read version {upload["crxVersion"]}, not {version}')
    result = client.publish()
    for warning in (result.get('warningInfo') or {}).get('warnings') or []:
        print(f'::warning::Chrome Web Store: {warning.get("reason")}: {warning.get("description")}')
    state = result.get('state')
    print(f'Submitted {version} to the Chrome Web Store: {state}')
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('package', type=Path)
    args = parser.parse_args()
    account, publisher = os.environ.get('CWS_SERVICE_ACCOUNT_JSON'), os.environ.get('CWS_PUBLISHER_ID')
    if not account or not publisher:
        sys.exit('Set CWS_SERVICE_ACCOUNT_JSON (the service account key) and CWS_PUBLISHER_ID '
                 '(Chrome Web Store Developer Dashboard → Publisher → Settings).')
    client = Client(json.loads(account), publisher, os.environ.get('CWS_ITEM_ID') or ITEM_ID)
    submit(client, args.package)


if __name__ == '__main__':
    main()
