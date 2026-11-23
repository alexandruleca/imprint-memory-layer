#!/usr/bin/env python3
"""Sign the Firefox add-on through addons.mozilla.org as an unlisted add-on.

Mozilla signs unlisted add-ons automatically; the signed .xpi installs in any
Firefox and is attached to the GitHub release, not listed on AMO (ADRs 0193, 0206).

    AMO_JWT_ISSUER=user:… AMO_JWT_SECRET=… \\
        python scripts/sign_firefox_extension.py unsigned.xpi signed.xpi

Uses AMO's v5 API with only the standard library: upload, wait for
validation, create the add-on or a new version of it, wait for the signature,
download. A version AMO has already signed is downloaded again, so rerunning a
release does not fail on the duplicate. AMO throttles uploads; a short throttle
is waited out, and a longer one leaves the release with only the unsigned
add-on instead of failing it.
"""
import argparse
import base64
import hashlib
import hmac
import io
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


class Throttled(RuntimeError):
    """addons.mozilla.org asked for a longer wait than the run has left."""


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
                    raise Throttled(f'addons.mozilla.org throttled {method} {path} for {wait / 60:.0f} more minutes') from None
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
            f'--{boundary}\r\nContent-Disposition: form-data; name="channel"\r\n\r\nunlisted\r\n'.encode(),
            f'--{boundary}\r\nContent-Disposition: form-data; name="upload"; filename="{xpi.name}"\r\n'
            'Content-Type: application/x-xpinstall\r\n\r\n'.encode(),
            xpi.read_bytes(), f'\r\n--{boundary}--\r\n'.encode()])
        return self.request('POST', f'{API}/upload/', body, f'multipart/form-data; boundary={boundary}')


def signed_file(version):
    file = (version or {}).get('file') or {}
    if file.get('status') == 'disabled':
        raise RuntimeError(f'AMO rejected version {version.get("version")}: its file is disabled')
    return file.get('url') if file.get('status') == 'public' and file.get('url') else None


def sign(client, source, output):
    source, output = Path(source), Path(output)
    with zipfile.ZipFile(source) as archive:
        manifest = json.loads(archive.read('manifest.json'))
    guid = manifest['browser_specific_settings']['gecko']['id']
    number = manifest['version']
    addon = f'{API}/addon/{guid}'
    print(f'Signing {guid} {number} as an unlisted add-on')
    version = client.exists(f'{addon}/versions/v{number}/')
    if version is not None and version.get('channel', 'unlisted') != 'unlisted':
        raise RuntimeError(f'AMO already holds {number} as {version["channel"]}; '
                           'it takes each version number once, so release a new version')
    if version is None:
        upload = client.upload(source)
        upload = client.wait(f'{API}/upload/{upload["uuid"]}/', lambda value: value.get('processed'), 'validation')
        if not upload.get('valid'):
            messages = [m.get('message', '') for m in (upload.get('validation') or {}).get('messages', [])
                        if m.get('type') == 'error']
            raise RuntimeError('AMO validation failed: ' + '; '.join(messages or ['no message']))
        # The first upload creates the add-on under its manifest ID; later ones add versions.
        if client.exists(f'{addon}/') is None:
            client.request('PUT', f'{addon}/', {'version': {'upload': upload['uuid']}})
        else:
            client.request('POST', f'{addon}/versions/', {'upload': upload['uuid']})
    version = client.wait(f'{addon}/versions/v{number}/', signed_file, 'the signature')
    data = client.request('GET', signed_file(version), raw=True)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if 'META-INF/mozilla.rsa' not in archive.namelist() and 'META-INF/cose.sig' not in archive.namelist():
            raise RuntimeError('The downloaded add-on carries no Mozilla signature')
    output.parent.mkdir(parents=True, exist_ok=True)
    staged = output.with_name(output.name + '.part')
    staged.write_bytes(data)
    staged.replace(output)
    print(f'Signed add-on: {output}')
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    issuer, secret = os.environ.get('AMO_JWT_ISSUER'), os.environ.get('AMO_JWT_SECRET')
    if not issuer or not secret:
        sys.exit('Set AMO_JWT_ISSUER and AMO_JWT_SECRET (addons.mozilla.org → Developer Hub → Manage API Keys).')
    client = Client(issuer, secret, os.environ.get('AMO_BASE_URL', 'https://addons.mozilla.org'))
    try:
        sign(client, args.source, args.output)
    except Throttled as error:
        # A release does not wait hours for AMO: it carries the unsigned add-on, and the
        # website keeps offering the newest release that has a signed one (ADR 0206).
        print(f'::warning::{error}, so this release carries only the unsigned Firefox add-on.')


if __name__ == '__main__':
    main()
