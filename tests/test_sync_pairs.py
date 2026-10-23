"""Paired sync through a real untrusted relay, with isolated device profiles."""
import asyncio
import contextvars
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
from websockets.asyncio.server import serve

from imprint import config, sync_pairs, sync_ws
from imprint.sync_pair_lock import owner
from test_sync_live import Relay, EXPORT, STATS


class ReconnectingRelay(Relay):
    async def handler(self, ws):
        room = ws.request.path.lstrip('/').partition('?')[0]
        try:
            await super().handler(ws)
        finally:
            peers = self.rooms.get(room, {})
            if ws in peers.values():
                self.rooms.pop(room, None)


class PairedSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.side = contextvars.ContextVar('device', default='a')
        self.dirs = {side: Path(self.temp.name) / side for side in ('a', 'b')}
        self.identities = {side: {'fingerprint': side * 8, 'hostname': 'device-' + side,
                                  'user': side, 'os': 'linux'} for side in ('a', 'b')}
        self.imports = []
        self.export = Mock(return_value=EXPORT)
        self.digests = {'a': 'a' * 64, 'b': 'b' * 64}

        def importer(path, workspace, on_progress=None):
            self.imports.append((self.side.get(), workspace, Path(path).read_text()))
            return STATS

        patches = [patch.object(config, 'get_data_dir', side_effect=lambda: self.dirs[self.side.get()]),
                   patch.dict(os.environ, {'IMPRINT_WORKSPACE': ''}),
                   patch.object(sync_ws, 'get_device_identity', side_effect=lambda: self.identities[self.side.get()]),
                   patch.object(sync_ws, 'stream_export_lines', self.export),
                   patch.object(sync_ws, 'process_import_buffer', side_effect=importer),
                   patch.object(sync_pairs, 'digest', side_effect=lambda ws: self.digests[self.side.get()])]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        for side in ('a', 'b'):
            token = self.side.set(side)
            config.register_workspace('from-' + side)
            self.side.reset(token)

    async def at(self, side, fn):
        token = self.side.set(side)
        try:
            return await fn()
        finally:
            self.side.reset(token)

    async def initial(self, endpoint, *, remember_a=True, remember_b=True, direction='both', send='accept'):
        ready = asyncio.get_running_loop().create_future()
        provider_events, consumer_events = [], []

        async def provider():
            room = None
            async for event in sync_ws.serve_session('from-a', relay=endpoint, remember=remember_a):
                provider_events.append(event)
                if event['type'] == 'room':
                    room = event
                elif event.get('status') == 'waiting':
                    ready.set_result(room)
                elif event['type'] == 'approval_required':
                    sync_ws.submit_approval(room['session_id'], 'accept')

        task = asyncio.create_task(self.at('a', provider))
        room = await asyncio.wait_for(ready, 5)

        async def consumer():
            session_id = None
            async for event in sync_ws.receive_session(endpoint, room['room_id'], room['pin'], 'from-b',
                                                       sync_ws.SyncPolicy.parse(direction), remember=remember_b):
                consumer_events.append(event)
                if event['type'] == 'session':
                    session_id = event['session_id']
                elif event['type'] == 'confirm_push':
                    sync_ws.submit_approval(session_id, send)

        await asyncio.wait_for(self.at('b', consumer), 10)
        await asyncio.wait_for(task, 10)
        self.assertFalse([e for e in provider_events + consumer_events if e['type'] == 'error'],
                         provider_events + consumer_events)
        return provider_events, consumer_events

    async def repeat(self):
        async def attempt():
            await sync_pairs._attempt(sync_pairs.pairs()[0])
        a = asyncio.create_task(self.at('a', attempt))
        await asyncio.sleep(.02)
        b = asyncio.create_task(self.at('b', attempt))
        await asyncio.wait_for(asyncio.gather(a, b), 10)

    def scenario(self, fn):
        async def run():
            relay = ReconnectingRelay()
            async with serve(relay.handler, '127.0.0.1', 0) as server:
                endpoint = sync_ws.Relay('ws', f"127.0.0.1:{server.sockets[0].getsockname()[1]}")
                await fn(endpoint, relay)
        asyncio.run(run())

    def test_mutual_opt_in_and_reconnect_without_approval_or_llm(self):
        async def scenario(endpoint, relay):
            await self.initial(endpoint)
            pair = sync_pairs.pairs()[0]
            self.assertEqual(pair['workspace'], 'from-a')
            self.assertEqual(pair['remote_workspace'], 'from-b')
            self.assertNotIn('secret', sync_pairs.pairs(public=True)[0])
            self.assertNotIn('room', sync_pairs.pairs(public=True)[0])
            self.imports.clear()
            await self.repeat()
            self.assertEqual([(s, w) for s, w, _ in self.imports], [('b', 'from-b'), ('a', 'from-a')])
            self.assertIsNotNone(sync_pairs.pairs()[0]['last_sync'])
            self.imports.clear()
            await self.repeat()  # unchanged signatures: control only
            self.assertEqual(self.imports, [])
            # A failed import/checkpoint on just one endpoint must make BOTH
            # endpoints retry, rather than one closing an apparently idle room.
            sync_pairs.update(pair['id'], local_digest=None)
            await self.repeat()
            self.assertEqual(len(self.imports), 2)
            self.imports.clear()
            self.digests['a'] = 'c' * 64
            await self.repeat()
            self.assertEqual(len(self.imports), 2)
            wire = b''.join(f.encode() if isinstance(f, str) else f for _, f in relay.frames)
            for secret in (pair['secret'], pair['peer']['fingerprint'], 'from-a', 'from-b'):
                self.assertNotIn(secret.encode(), wire)
        self.scenario(scenario)

    def test_one_time_consent_does_not_pair_devices(self):
        for a, b in ((False, True), (True, False), (False, False)):
            async def scenario(endpoint, relay):
                await self.initial(endpoint, remember_a=a, remember_b=b)
                self.assertEqual(sync_pairs.pairs(), [])
                token = self.side.set('b')
                try:
                    self.assertEqual(sync_pairs.pairs(), [])
                finally:
                    self.side.reset(token)
            self.scenario(scenario)

    def test_receive_only_and_declined_push_cannot_widen_on_reconnect(self):
        for direction, decision in (('receive', 'accept'), ('both', 'reject')):
            async def scenario(endpoint, relay):
                await self.initial(endpoint, direction=direction, send=decision)
                self.assertEqual(sync_pairs.pairs()[0]['direction'], 'send')
                self.imports.clear()
                await self.repeat()
                self.assertEqual([(s, w) for s, w, _ in self.imports], [('b', 'from-b')])
            self.scenario(scenario)

    def test_pause_on_either_endpoint_stops_data_and_can_resume(self):
        async def scenario(endpoint, relay):
            await self.initial(endpoint)
            token = self.side.set('b')
            pair_id = sync_pairs.pairs()[0]['id']
            sync_pairs.set_enabled(pair_id, False)
            self.side.reset(token)
            self.imports.clear()
            await self.repeat()
            self.assertEqual(self.imports, [])
            token = self.side.set('b')
            sync_pairs.set_enabled(pair_id, True)
            self.side.reset(token)
            await self.repeat()
            self.assertEqual(len(self.imports), 2)
        self.scenario(scenario)

    def test_forget_requires_confirmation_and_deletes_both_capabilities_after_ack(self):
        async def scenario(endpoint, relay):
            await self.initial(endpoint)
            pair = sync_pairs.pairs()[0]
            sync_ws.save_trusted_device(pair['peer'])
            with self.assertRaisesRegex(ValueError, 'confirmation'):
                sync_pairs.forget(pair['id'])
            sync_pairs.forget(pair['id'], confirmed=True)
            self.assertEqual(sync_pairs.get(pair['id'])['state'], 'forget_pending')
            self.assertNotIn(pair['peer']['fingerprint'], sync_ws.load_trusted_devices())
            with self.assertRaises(ValueError):
                sync_pairs.set_enabled(pair['id'], True)
            self.imports.clear()
            await self.repeat()
            self.assertEqual(self.imports, [])
            self.assertEqual(sync_pairs.pairs(), [])
            token = self.side.set('b')
            self.assertEqual(sync_pairs.pairs(), [])
            self.side.reset(token)
        self.scenario(scenario)

    def test_wrong_device_identity_or_workspace_does_not_transfer(self):
        for field, value in (('remote_workspace', 'wrong'),):
            async def scenario(endpoint, relay):
                await self.initial(endpoint)
                pair = sync_pairs.pairs()[0]
                sync_pairs.update(pair['id'], **{field: value})
                self.imports.clear()
                with self.assertRaises(Exception):
                    await self.repeat()
                self.assertEqual(self.imports, [])
            self.scenario(scenario)

    def test_forget_does_not_need_an_index_or_an_existing_workspace(self):
        async def scenario(endpoint, relay):
            await self.initial(endpoint)
            pair = sync_pairs.pairs()[0]
            sync_pairs.forget(pair['id'], confirmed=True)
            self.imports.clear()
            with patch.object(config, 'get_known_workspaces', return_value=[]), \
                 patch.object(sync_pairs, 'digest', side_effect=RuntimeError('index unavailable')) as read:
                await self.repeat()
                read.assert_not_called()
            self.assertFalse(sync_pairs.pairs())
            self.assertFalse(self.imports)
        self.scenario(scenario)

    def test_credentials_are_private_and_lock_has_one_owner(self):
        sync_pairs.pairs()
        path = self.dirs['a'] / 'sync-pairs.sqlite3'
        if os.name != 'nt':
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with owner() as first:
            self.assertTrue(first)
            with owner() as second:
                self.assertFalse(second)
        with owner() as again:
            self.assertTrue(again)

    def test_worker_observes_persisted_pause_and_releases_ownership_on_shutdown(self):
        record = sync_pairs.offer('from-a', self.identities['b'], sync_ws.SyncPolicy(), 'from-b', sync_ws.parse_relay(None))
        sync_pairs.save(record)
        starts, cancelled = [], []

        async def attempt(pair):
            starts.append((pair['enabled'], pair['state']))
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.append(pair['enabled'])
                raise

        async def run():
            with patch.object(sync_pairs, '_attempt', side_effect=attempt):
                task = asyncio.create_task(sync_pairs.watch(interval=.02, tick=.01))
                try:
                    for _ in range(100):
                        if starts:
                            break
                        await asyncio.sleep(.01)
                    # Simulate a separate CLI process: update the database only.
                    sync_pairs.update(record['id'], enabled=False)
                    for _ in range(100):
                        if (False, 'active') in starts:
                            break
                        await asyncio.sleep(.01)
                    self.assertIn(True, cancelled)
                    self.assertIn((False, 'active'), starts)
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            self.assertFalse(sync_pairs._running)
        asyncio.run(run())
        with owner() as acquired:
            self.assertTrue(acquired)

    def test_forget_covers_all_workspaces_with_the_same_device(self):
        one = sync_pairs.offer('from-a', self.identities['b'], sync_ws.SyncPolicy(), 'from-b', sync_ws.parse_relay(None))
        two = sync_pairs.offer('other', self.identities['b'], sync_ws.SyncPolicy(), 'other', sync_ws.parse_relay(None))
        sync_pairs.save(one)
        sync_pairs.save(two)
        sync_pairs.forget(one['id'], confirmed=True)
        self.assertEqual([p['state'] for p in sync_pairs.pairs()], ['forget_pending'] * 2)

    def test_http_controls_validate_confirmation_and_do_not_expose_credentials(self):
        from imprint.api import app
        peer = self.identities['b']
        record = sync_pairs.offer('from-a', peer, sync_ws.SyncPolicy(), 'from-b', sync_ws.parse_relay(None))
        sync_pairs.save(record)

        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:8420') as client:
                response = await client.get('/api/sync/peers')
                self.assertNotIn(record['secret'], response.text)
                self.assertNotIn(record['room'], response.text)
                for body in ({'action': 'forget'}, {'action': 'forget', 'confirm': 'yes'},
                             {'action': 'enabled', 'enabled': 'false'}, ['bad']):
                    response = await client.post('/api/sync/peers/' + record['id'], json=body)
                    self.assertEqual(response.status_code, 400)
                response = await client.post('/api/sync/peers/' + record['id'], json={'action': 'forget', 'confirm': True})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['peers'][0]['state'], 'forget_pending')
        asyncio.run(run())


if __name__ == '__main__':
    unittest.main()
