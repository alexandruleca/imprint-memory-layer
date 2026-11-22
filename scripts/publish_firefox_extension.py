#!/usr/bin/env python3
"""Submit the Firefox add-on to its listing on addons.mozilla.org (ADR 0203).

Each stable release adds a listed version. Mozilla reviews it, and Firefox
updates installed copies once it is approved. The listing itself (summary,
categories, license, screenshots) is made once in the Developer Hub, as the
Chrome Web Store item was; until it exists this script says so and submits
nothing.

    AMO_JWT_ISSUER=user:… AMO_JWT_SECRET=… \\
        python scripts/publish_firefox_extension.py imprint-firefox-extension-unsigned.xpi

Uses AMO's v5 API with only the standard library: upload, wait for
validation, create the version. A version AMO already holds as listed is not
submitted again, so rerunning a release does not fail.
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sys
import time
import urllib.error
import urllib.request
import uuid as uuidlib
import zipfile

API = '/api/v5/addons'


def jwt(issuer, secret, now=None):
    """AMO's API token: HS256 over iss/jti/iat/exp, valid for a minute."""
    now = int(time.time() if now is None else now)
    encode = lambda value: base64.urlsafe_b64encode(value).rstrip(b'=')
    header = encode(json.dumps({'alg': 'HS256', 'typ': 'JWT'}, separators=(',', ':')).encode())
    claims = {'iss': issuer, 'jti': secrets.token_hex(16), 'iat': now, 'exp': now + 60}
    payload = encode(json.dumps(claims, separators=(',', ':')).encode())
    signature = encode(hmac.new(secret.encode(), header + b'.' + payload, hashlib.sha256).digest())
    return (header + b'.' + payload + b'.' + signature).decode()


def throttled_for(status, retry_after, detail):
    """Seconds to wait before retrying a throttled (HTTP 429) request, else None."""
    if status != 429:
        return None
    try:
        return max(0.0, float(retry_after)) + 1
    except (TypeError, ValueError):
        match = re.search(r'available in (\d+) second', detail)
        return int(match.group(1)) + 1 if match else 60.0


class Client:
    def __init__(self, issuer, secret, base='https://addons.mozilla.org', poll=10.0, timeout=1200.0):
        self.issuer, self.secret, self.base = issuer, secret, base.rstrip('/')
        self.poll, self.timeout = poll, timeout

    def request(self, method, path, body=None, content_type='application/json', raw=False):
        url = path if path.startswith('http') else self.base + path
        data = json.dumps(body).encode() if body is not None and content_type == 'application/json' else body
        deadline = time.monotonic() + self.timeout
        while True:
            # A fresh token each time: one lives a minute, and a throttled request may wait longer.
            request = urllib.request.Request(url, data=data, method=method, headers={
                'Authorization': 'JWT ' + jwt(self.issuer, self.secret), 'Accept': 'application/json',
                **({'Content-Type': content_type} if data is not None else {})})
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    payload = response.read()
                return payload if raw else json.loads(payload or b'null')
            except urllib.error.HTTPError as error:
                with error:
                    detail = error.read().decode('utf-8', 'replace')[:2000]
                    wait = throttled_for(error.code, error.headers.get('Retry-After'), detail)
                if wait is None:
                    raise RuntimeError(f'{method} {path}: HTTP {error.code}: {detail}') from None
                if time.monotonic() + wait > deadline:
                    raise RuntimeError(f'addons.mozilla.org throttled {method} {path} for {wait / 60:.0f} more minutes; '
                                       'run this again after that') from None
                print(f'addons.mozilla.org throttled {method} {path}; retrying in {wait:.0f}s')
                time.sleep(wait)

    def exists(self, path):
        try:
            return self.request('GET', path)
        except RuntimeError as error:
            if ': HTTP 404:' in str(error):
                return None
            raise

    def wait(self, path, done, what):
        deadline = time.monotonic() + self.timeout
        while True:
            value = self.request('GET', path)
            if done(value):
                return value
            if time.monotonic() > deadline:
                raise RuntimeError(f'Timed out waiting for {what}: {json.dumps(value)[:500]}')
            time.sleep(self.poll)

    def upload(self, xpi):
        boundary = uuidlib.uuid4().hex
        body = b''.join([
            f'--{boundary}\r\nContent-Disposition: form-data; name="channel"\r\n\r\nlisted\r\n'.encode(),
            f'--{boundary}\r\nContent-Disposition: form-data; name="upload"; filename="{xpi.name}"\r\n'
            'Content-Type: application/x-xpinstall\r\n\r\n'.encode(),
            xpi.read_bytes(), f'\r\n--{boundary}--\r\n'.encode()])
        return self.request('POST', f'{API}/upload/', body, f'multipart/form-data; boundary={boundary}')


def file_status(version):
    status = ((version or {}).get('file') or {}).get('status')
    if status == 'disabled':
        raise RuntimeError(f'AMO rejected or disabled version {version.get("version")}')
    return status


def submit(client, source):
    source = Path(source)
    with zipfile.ZipFile(source) as archive:
        manifest = json.loads(archive.read('manifest.json'))
    guid = manifest['browser_specific_settings']['gecko']['id']
    number = manifest['version']
    addon = f'{API}/addon/{guid}'
    version = client.exists(f'{addon}/versions/v{number}/')
    if version is not None:
        if version.get('channel') != 'listed':
            raise RuntimeError(f'AMO already signed {number} as {version.get("channel")}; '
                               'it takes each version number once, so release a new version')
        status = file_status(version)
        print(f'addons.mozilla.org already has {guid} {number} (file {status})')
        return status
    # Authors see the channels an add-on has versions in; the listing is made by hand once.
    details = client.exists(f'{addon}/')
    if details is None or 'listed' not in details.get('channels', ['listed']):
        print(f'::warning::{guid} has no listing on addons.mozilla.org yet, so {number} was not submitted. '
              'Submit this file once in the Developer Hub (the add-on → Upload New Version → On this site) '
              'and complete the listing; later stable releases are submitted automatically.')
        return None
    print(f'Submitting {guid} {number} to its addons.mozilla.org listing')
    upload = client.upload(source)
    upload = client.wait(f'{API}/upload/{upload["uuid"]}/', lambda value: value.get('processed'), 'validation')
    if not upload.get('valid'):
        messages = [m.get('message', '') for m in (upload.get('validation') or {}).get('messages', [])
                    if m.get('type') == 'error']
        raise RuntimeError('AMO validation failed: ' + '; '.join(messages or ['no message']))
    # The license and listing carry over from the previous listed version.
    version = client.request('POST', f'{addon}/versions/', {'upload': upload['uuid']})
    status = file_status(version)
    print(f'Submitted {guid} {number} for review (file {status}); Firefox updates users once it is approved')
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source', type=Path)
    args = parser.parse_args()
    issuer, secret = os.environ.get('AMO_JWT_ISSUER'), os.environ.get('AMO_JWT_SECRET')
    if not issuer or not secret:
        sys.exit('Set AMO_JWT_ISSUER and AMO_JWT_SECRET (addons.mozilla.org → Developer Hub → Manage API Keys).')
    client = Client(issuer, secret, os.environ.get('AMO_BASE_URL', 'https://addons.mozilla.org'))
    submit(client, args.source)


if __name__ == '__main__':
    main()
