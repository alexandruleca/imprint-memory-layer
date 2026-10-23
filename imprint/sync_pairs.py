"""Local, workspace-scoped capabilities for explicitly paired sync devices.

The relay remains a ciphertext forwarder. Pair credentials never leave this
device except inside the initial PIN-authenticated channel. SQLite transactions
serialize CLI/dashboard changes; no secrets are returned by the public API.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import secrets
import re
import sqlite3
import time
from contextlib import contextmanager

from . import config


@contextmanager
def _database():
    directory = config.get_data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'sync-pairs.sqlite3'
    # Create privately before sqlite opens it; existing credentials stay private.
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    if os.name != 'nt':
        os.chmod(path, 0o600)
    conn = sqlite3.connect(path, timeout=15)
    try:
        conn.execute('PRAGMA secure_delete = ON')
        conn.execute('CREATE TABLE IF NOT EXISTS pairs (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
        with conn:
            yield conn
    finally:
        conn.close()


def _all(conn):
    return [json.loads(row[0]) for row in conn.execute('SELECT body FROM pairs ORDER BY id')]


def pairs(*, public=False):
    with _database() as conn:
        records = _all(conn)
    if public:
        return [{k: v for k, v in record.items() if k not in {'secret', 'room', 'local_digest', 'remote_digest'}}
                for record in records]
    return records


def get(pair_id):
    with _database() as conn:
        row = conn.execute('SELECT body FROM pairs WHERE id = ?', (pair_id,)).fetchone()
    if row is None:
        raise ValueError('Unknown pairing')
    return json.loads(row[0])


def _put(conn, record):
    conn.execute('INSERT OR REPLACE INTO pairs VALUES (?, ?)', (record['id'], json.dumps(record)))


def save(record):
    with _database() as conn:
        conn.execute('BEGIN IMMEDIATE')
        for old in _all(conn):
            if (old['peer']['fingerprint'], old['workspace'], old['remote_workspace']) == (
                    record['peer']['fingerprint'], record['workspace'], record['remote_workspace']):
                conn.execute('DELETE FROM pairs WHERE id = ?', (old['id'],))
        _put(conn, record)


def update(pair_id, **changes):
    with _database() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT body FROM pairs WHERE id = ?', (pair_id,)).fetchone()
        if row is None:
            raise ValueError('Unknown pairing')
        record = json.loads(row[0])
        record.update(changes)
        _put(conn, record)
    return record


def offer(workspace, peer, policy, remote_workspace, relay):
    """An independent high-entropy capability, never the reusable share PIN."""
    return {'id': secrets.token_hex(16), 'room': 'paired_' + secrets.token_hex(16),
            'secret': secrets.token_hex(32), 'workspace': workspace, 'peer': peer,
            'remote_workspace': remote_workspace, 'relay': str(relay),
            'direction': 'both' if policy.send and policy.receive else 'send' if policy.send else 'receive',
            'enabled': True, 'state': 'active', 'created_at': time.time(),
            'last_seen': None, 'last_sync': None, 'last_error': ''}


async def accept_pair(channel, request, workspace, peer, policy, relay):
    from . import sync_ws
    remote_workspace = request.get('workspace')
    if (not isinstance(remote_workspace, str) or config.validate_workspace_name(remote_workspace)
            or type(request.get('pull')) is not bool or type(request.get('push')) is not bool
            or not (request['pull'] or request['push'])
            or request['pull'] and not policy.send or request['push'] and not policy.receive
            or peer['fingerprint'] == sync_ws.get_device_identity()['fingerprint']):
        raise ValueError('Invalid automatic sync scope')
    approved = sync_ws.SyncPolicy(send=request['pull'], receive=request['push'])
    record = offer(workspace, peer, approved, remote_workspace, relay)
    await channel.send({'status': 200, 'pair': {k: record[k] for k in ('id', 'room', 'secret', 'workspace')}})
    ack = await channel.recv(timeout=30)
    if ack.get('pair_ack') != record['id']:
        raise ValueError('Pairing acknowledgement missing')
    save(record)
    await channel.send({'paired': record['id']})
    return record['id']


async def request_pair(channel, workspace, peer, plan, relay):
    from . import sync_ws
    if peer['fingerprint'] == sync_ws.get_device_identity()['fingerprint']:
        raise ValueError('Cannot pair this device with itself')
    await channel.send({'path': '/sync/pair', 'workspace': workspace, 'pull': plan.pull, 'push': plan.push})
    response = await channel.recv(timeout=30)
    remote = response.get('pair')
    if (response.get('status') != 200 or not isinstance(remote, dict)
            or not re.fullmatch(r'[a-f0-9]{32}', str(remote.get('id', '')))
            or not re.fullmatch(r'paired_[a-f0-9]{32}', str(remote.get('room', '')))
            or not re.fullmatch(r'[a-f0-9]{64}', str(remote.get('secret', '')))
            or not isinstance(remote.get('workspace'), str)
            or config.validate_workspace_name(remote['workspace'])):
        raise ValueError('Invalid automatic sync offer')
    local = offer(workspace, peer, sync_ws.SyncPolicy(send=plan.push, receive=plan.pull), remote['workspace'], relay)
    local.update({key: remote[key] for key in ('id', 'room', 'secret')})
    # Persist before acknowledging so a disconnect cannot leave only the
    # provider holding the new capability. An incomplete handshake can be
    # explicitly forgotten/re-paired; it never grants a wider direction.
    save(local)
    await channel.send({'pair_ack': local['id']})
    if (await channel.recv(timeout=30)).get('paired') != local['id']:
        raise ValueError('Pairing was not confirmed by the other device')
    return local['id']


def set_enabled(pair_id, enabled):
    if type(enabled) is not bool:
        raise ValueError('enabled must be a boolean')
    record = get(pair_id)
    if record['state'] != 'active':
        raise ValueError('A forgotten device must be paired again with a new code')
    update(pair_id, enabled=enabled, last_error='')
    cancel_peer(record['peer']['fingerprint'])


def _untrust(fingerprint):
    # This legacy file contains identifiers, not pair secrets. Remove its old
    # approval shortcut as well as the new automatic capability.
    from . import sync_ws
    trusted = sync_ws.load_trusted_devices()
    if fingerprint in trusted:
        trusted.pop(fingerprint)
        (config.get_data_dir() / 'trusted_devices.json').write_text(json.dumps(list(trusted.values()), indent=2))


def forget(pair_id, *, confirmed=False):
    if confirmed is not True:
        raise ValueError('Explicit confirmation is required to forget this device on both sides')
    fingerprint = get(pair_id)['peer']['fingerprint']
    with _database() as conn:
        conn.execute('BEGIN IMMEDIATE')
        for record in _all(conn):
            if record['peer']['fingerprint'] == fingerprint:
                record.update(state='forget_pending', enabled=False, last_error='')
                _put(conn, record)
    _untrust(fingerprint)
    cancel_peer(fingerprint)


def remove_peer(fingerprint):
    with _database() as conn:
        conn.execute('BEGIN IMMEDIATE')
        for record in _all(conn):
            if record['peer']['fingerprint'] == fingerprint:
                conn.execute('DELETE FROM pairs WHERE id = ?', (record['id'],))
    _untrust(fingerprint)
    cancel_peer(fingerprint)


def digest(workspace):
    """Hash stable exported records; transfer only when an endpoint changes.

    Hashing scans the workspace on the local machine, including vectors. It
    uses no embedding model/LLM. Sort record hashes so scroll order is irrelevant.
    """
    from .sync_ws import stream_export_lines
    records = []
    for line in stream_export_lines(workspace):
        frame = json.loads(line)
        for record in frame.get('records', []):
            encoded = json.dumps([frame['dataset'], record], sort_keys=True, separators=(',', ':')).encode()
            records.append(hashlib.sha256(encoded).digest())
    return hashlib.sha256(b''.join(sorted(records))).hexdigest()


async def exchange(channel, pair, peer, *, provider):
    """Authenticate identity and scope, exchange pause/revocation before data."""
    pair = get(pair['id'])  # Management changes override the worker's snapshot.
    if peer['fingerprint'] != pair['peer']['fingerprint']:
        raise ValueError('Paired device identity changed; pair again explicitly')
    active = pair['state'] == 'active' and pair['enabled']
    local = {'pair_id': pair['id'], 'state': pair['state'], 'enabled': active,
             'workspace': pair['workspace'], 'digest': await asyncio.to_thread(digest, pair['workspace']) if active else None}
    if provider:
        await channel.send(local)
        remote = await channel.recv(timeout=60)
    else:
        remote = await channel.recv(timeout=60)
        await channel.send(local)
    if remote.get('pair_id') != pair['id'] or remote.get('workspace') != pair['remote_workspace']:
        raise ValueError('Paired workspace or capability mismatch')
    if pair['state'] == 'forget_pending' or remote.get('state') == 'forget_pending':
        # Exchange an authenticated acknowledgement before deleting credentials.
        await channel.send({'forgotten': pair['id']})
        reply = await channel.recv(timeout=30)
        if reply.get('forgotten') != pair['id']:
            raise ValueError('Forget acknowledgement missing')
        remove_peer(peer['fingerprint'])
        return False
    update(pair['id'], last_seen=time.time(), last_error='')
    if not active or remote.get('enabled') is not True or remote.get('state') != 'active':
        return False
    remote_digest = remote.get('digest')
    if not isinstance(remote_digest, str) or len(remote_digest) != 64:
        raise ValueError('Malformed paired workspace digest')
    pair['_local_digest'], pair['_remote_digest'] = local['digest'], remote_digest
    changed = local['digest'] != pair.get('local_digest') or remote_digest != pair.get('remote_digest')
    return pair if changed else False


_running: dict[str, tuple[asyncio.Task, str]] = {}


def cancel_peer(fingerprint):
    # API and watch each notice persisted changes. Cancel immediately in this
    # process too; other processes notice on their next management tick.
    try:
        current = asyncio.current_task()
    except RuntimeError:
        current = None
    for task, peer_id in list(_running.values()):
        if peer_id == fingerprint and task is not current:
            task.cancel()


async def _attempt(pair):
    from . import sync_ws
    identity = sync_ws.get_device_identity()
    policy = sync_ws.SyncPolicy.parse(pair['direction'])
    if pair['state'] == 'active' and pair['workspace'] not in config.get_known_workspaces():
        raise ValueError('Paired workspace no longer exists; forget or explicitly pair a workspace again')
    relay = sync_ws.parse_relay(pair['relay'])
    if identity['fingerprint'] == pair['peer']['fingerprint']:
        raise ValueError('Cannot sync a device with itself')
    provider = identity['fingerprint'] < pair['peer']['fingerprint']
    session = (sync_ws.serve_session(pair['workspace'], policy, relay, pairing=pair) if provider else
               sync_ws.receive_session(relay, pair['room'], pair['secret'], pair['workspace'], policy, pairing=pair))
    errors = []
    async for event in session:
        if event['type'] in ('error', 'warning'):
            errors.append(event.get('message', 'Sync failed'))
        if event['type'] == 'done' and not errors:
            fresh = get(pair['id'])
            if fresh['state'] == 'active' and fresh['enabled']:
                # Values captured BEFORE the transfer avoid hiding concurrent
                # writes. Imports may cause one extra deduplicating next pass.
                update(pair['id'], last_sync=time.time(), last_error='',
                       local_digest=pair.get('_local_digest'), remote_digest=pair.get('_remote_digest'))
    if errors:
        raise RuntimeError(errors[-1])


async def watch(*, interval=60, tick=2):
    """Run while the dashboard/service is alive. No real client setup needed.

    A process-wide OS file lock prevents CLI and API workers in this data
    directory from occupying the same relay roles. Other processes still expose
    controls, and the owner picks them up from SQLite every management tick.
    """
    from .sync_pair_lock import owner
    with owner() as acquired:
        if not acquired:
            return
        due, versions = {}, {}
        try:
            while True:
                records = {p['id']: p for p in pairs()}
                for pair_id, (task, _) in list(_running.items()):
                    record = records.get(pair_id)
                    version = None if record is None else (record['enabled'], record['state'])
                    if version != versions.get(pair_id):
                        task.cancel()
                    if task.done():
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            task.result()
                        _running.pop(pair_id, None)
                        due[pair_id] = time.monotonic() + interval
                        if version != versions.get(pair_id):
                            due[pair_id] = 0
                for pair_id, record in records.items():
                    if (record['enabled'], record['state']) != versions.get(pair_id) and pair_id not in _running:
                        due[pair_id] = 0
                    if pair_id in _running or time.monotonic() < due.get(pair_id, 0):
                        continue
                    versions[pair_id] = (record['enabled'], record['state'])

                    async def run(record=record):
                        try:
                            async with asyncio.timeout(600):
                                await _attempt(record)
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            with contextlib.suppress(ValueError):
                                update(record['id'], last_error=type(exc).__name__ + ': ' + str(exc)[:240])

                    task = asyncio.create_task(run())
                    _running[pair_id] = (task, record['peer']['fingerprint'])
                await asyncio.sleep(tick)
        finally:
            tasks = [task for task, _ in _running.values()]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            _running.clear()


async def run():
    """Take ownership when another dashboard/CLI owner exits."""
    while True:
        await watch()
        await asyncio.sleep(2)
