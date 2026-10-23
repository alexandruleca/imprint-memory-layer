"""WIP browser capture identities and local, workspace-bound pairing grants."""
from contextlib import contextmanager
import argparse
import hashlib
import json
import os
import re
import secrets
import sqlite3
import time
from urllib.parse import urlsplit, urlunsplit

from . import config
from .workspace_policy import workspace_name

MAX_REQUEST_BYTES = 1_000_000
EXTENSION_ID = re.compile(r'[a-p]{32}')


def extension_origin(extension_id):
    if not isinstance(extension_id, str) or not EXTENSION_ID.fullmatch(extension_id):
        raise ValueError('Expected a Chrome extension ID (32 letters a–p)')
    return 'chrome-extension://' + extension_id


@contextmanager
def _db():
    directory = config.get_data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'browser-pairings.sqlite3'
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(descriptor)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('CREATE TABLE IF NOT EXISTS pairings '
                     '(extension_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, '
                     'workspace TEXT NOT NULL, created_at REAL NOT NULL)')
        with conn:
            yield conn
    finally:
        conn.close()


def token_hash(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def pair(extension_id, workspace=None):
    extension_origin(extension_id)
    workspace = workspace_name(workspace)
    token = secrets.token_urlsafe(32)
    with _db() as conn:
        conn.execute('INSERT OR REPLACE INTO pairings VALUES (?, ?, ?, ?)',
                     (extension_id, token_hash(token), workspace, time.time()))
    return token


def grant(extension_id):
    extension_origin(extension_id)
    # Reading an unpaired service should not create local state.
    if not (config.get_data_dir() / 'browser-pairings.sqlite3').is_file():
        return None
    with _db() as conn:
        row = conn.execute('SELECT * FROM pairings WHERE extension_id=?', (extension_id,)).fetchone()
    return dict(row) if row else None


def revoke(extension_id):
    extension_origin(extension_id)
    with _db() as conn:
        conn.execute('DELETE FROM pairings WHERE extension_id=?', (extension_id,))


def _text(value, label, limit, minimum=1):
    if not isinstance(value, str) or '\x00' in value or not minimum <= len(value.strip()) <= limit:
        raise ValueError(f'{label} must contain {minimum}–{limit} characters')
    return value.strip()


def capture_url(value):
    value = _text(value, 'URL', 8192)
    parsed = urlsplit(value)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or any(c.isspace() for c in value) or '\\' in value):
        raise ValueError('Capture requires an HTTP(S) URL without embedded credentials')
    parsed.port  # Reject malformed ports.
    return urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path or '/', parsed.query, parsed.fragment))


def normalize_capture(body):
    if not isinstance(body, dict):
        raise ValueError('Expected a capture object')
    project = _text(body.get('project', 'browser'), 'Project', 128)
    documents = body.get('documents')
    if not isinstance(documents, list) or not 1 <= len(documents) <= 50:
        raise ValueError('Expected 1–50 captured documents')
    normalized = []
    for doc in documents:
        if not isinstance(doc, dict):
            raise ValueError('Expected a document object')
        kind = doc.get('kind')
        if kind not in ('page', 'selection', 'gmail'):
            raise ValueError('Unsupported capture kind')
        url = capture_url(doc.get('url'))
        title = _text(doc.get('title'), 'Title', 1000)
        text = _text(doc.get('text'), 'Content', 800_000, minimum=10)
        account = ''
        message_id = ''
        if urlsplit(url).hostname == 'mail.google.com':
            if kind == 'page':
                raise ValueError('Gmail requires message extraction or selected text')
            account = _text(doc.get('account'), 'Gmail account email', 320).casefold()
            if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', account):
                raise ValueError('Enter the Gmail account email in extension settings')
        elif kind == 'gmail':
            raise ValueError('Gmail messages must come from mail.google.com')
        if kind == 'gmail':
            message_id = _text(doc.get('message_id'), 'Gmail message ID', 128)
            if not re.fullmatch(r'[A-Za-z0-9:_-]+', message_id):
                raise ValueError('Invalid Gmail message ID')
        identity = [url] if kind == 'page' else (
            [account, message_id] if kind == 'gmail' else [url, account, text])
        source_key = f'browser:{kind}:' + token_hash(json.dumps(identity, ensure_ascii=False))
        normalized.append(dict(kind=kind, url=url, title=title, text=text,
                               account=account, message_id=message_id, source_key=source_key))
    result = {'project': project, 'documents': normalized}
    if len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > MAX_REQUEST_BYTES:
        raise ValueError('Capture exceeds 1 MB; select a smaller passage')
    if len({doc['source_key'] for doc in normalized}) != len(normalized):
        raise ValueError('Duplicate document identities in capture')
    return result


def main():
    parser = argparse.ArgumentParser(description='Pair the WIP Chrome extension with this local Imprint installation')
    parser.add_argument('extension_id')
    parser.add_argument('--workspace', help='Existing workspace to grant (default: active workspace)')
    parser.add_argument('--revoke', action='store_true')
    args = parser.parse_args()
    try:
        if args.revoke:
            revoke(args.extension_id)
            print('Browser pairing revoked. Indexed memories remain.')
        else:
            token = pair(args.extension_id, args.workspace)
            print('Paste this token into the extension settings. Re-pairing rotates the token:')
            print(token)
    except ValueError as error:
        parser.error(str(error))


if __name__ == '__main__':
    main()
