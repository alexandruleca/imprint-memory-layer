"""Live sync end to end: both sessions through a real WebSocket relay.

Storage is mocked; the relay records every frame so the tests can check
what an untrusted relay sees, and can alter, drop or replay frames.
"""
import argparse
import asyncio
import base64
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs

import websockets
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from imprint import cli_sync, config, sync_ws
from imprint.sync_crypto import KeyExchange
from imprint.sync_ws import REFUSED_PUSH, TAMPERED, SyncPolicy

BOTH, SEND, RECEIVE = (SyncPolicy.parse(direction) for direction in ('both', 'send', 'receive'))
SECRET = 'TOP-SECRET-MEMORY-CONTENT'
EXPORT = [json.dumps(line) for line in (
    {'kind': 'meta', 'datasets': {'memories': 1, 'facts': 0}, 'vectors': True},
    {'kind': 'batch', 'dataset': 'memories', 'seq': 1, 'records': [{'content': SECRET, 'vector': [0.5]}]},
    {'kind': 'done'},
)]
STATS = {'memories': {'inserted': 1, 'skipped': 0}, 'facts': {'inserted': 0, 'skipped': 0}}


class Relay:
    """Pairs provider and consumer by room, like cmd/relay.go, recording
    frames. ``alter(role, index, frame)`` may return a changed frame, None
    to drop it, or a list to send several."""

    def __init__(self, alter=None):
        self.alter = alter
        self.rooms: dict[str, dict] = {}
        self.frames: list[tuple[str, object]] = []
        self.counts = {'provider': 0, 'consumer': 0}

    async def handler(self, ws):
        room, _, query = ws.request.path.lstrip('/').partition('?')
        role = parse_qs(query)['role'][0]
        other_role = 'consumer' if role == 'provider' else 'provider'
        peers = self.rooms.setdefault(room, {})
        peers[role] = ws
        while other_role not in peers:
            await asyncio.sleep(0.005)
        other = peers[other_role]
        try:
            async for frame in ws:
                index = self.counts[role]
                self.counts[role] += 1
                out = self.alter(role, index, frame) if self.alter else frame
                for item in (out if isinstance(out, list) else [] if out is None else [out]):
                    self.frames.append((role, item))
                    await other.send(item)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            await other.close()


class LiveSyncTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data = Path(directory.name)
        self.export = Mock(return_value=EXPORT)
        self.received: list[str] = []

        def importer(path, workspace, on_progress=None):
            self.received.append((workspace, Path(path).read_text()))
            if on_progress:
                on_progress('memories', 1, 1)
            return STATS

        self.importer = Mock(side_effect=importer)
        for item in (patch.object(config, 'get_data_dir', return_value=self.data),
                     patch.dict(os.environ, {'IMPRINT_WORKSPACE': ''}),
                     patch.object(sync_ws, 'stream_export_lines', self.export),
                     patch.object(sync_ws, 'process_import_buffer', self.importer)):
            item.start()
            self.addCleanup(item.stop)
        config.register_workspace('team')
        self.identity = sync_ws.get_device_identity()

    def sync(self, provider=BOTH, consumer=BOTH, *, pin=None, decision='accept', send='accept', alter=None,
             workspace='team', buffer=None, consumer_side=None):
        """Serve 'team' and join it; ``send`` answers the joining side's
        push confirmation. Returns (provider events, consumer events, relay)."""
        relay = Relay(alter)

        async def run():
            async with serve(relay.handler, '127.0.0.1', 0) as server:
                endpoint = sync_ws.Relay('ws', f"127.0.0.1:{server.sockets[0].getsockname()[1]}")
                provider_events: list[dict] = []
                ready = asyncio.get_running_loop().create_future()

                async def provide():
                    room = {}
                    async for event in sync_ws.serve_session('team', provider, endpoint):
                        provider_events.append(event)
                        if event['type'] == 'room':
                            room = event
                        elif event.get('status') == 'waiting':
                            ready.set_result(room)
                        elif event['type'] == 'approval_required':
                            sync_ws.submit_approval(room['session_id'], decision)

                task = asyncio.create_task(provide())
                room = await asyncio.wait_for(ready, 5)
                if consumer_side is not None:
                    consumer_events = await consumer_side(endpoint, room)
                else:
                    session = sync_ws.receive_session(endpoint, room['room_id'], pin or room['pin'], workspace,
                                                      consumer, buffer)
                    consumer_events = []
                    async for event in session:
                        consumer_events.append(event)
                        if event['type'] == 'confirm_push':
                            sync_ws.submit_approval(consumer_events[0]['session_id'], send)
                await asyncio.wait_for(task, 5)
                return provider_events, consumer_events

        provider_events, consumer_events = asyncio.run(run())
        return provider_events, consumer_events, relay

    @staticmethod
    def kinds(events):
        return [event['type'] for event in events]

    # ── Encryption ────────────────────────────────────────────────

    def test_two_way_sync_is_end_to_end_encrypted(self):
        provider, consumer, relay = self.sync()
        self.assertEqual(consumer[-1], {'type': 'done', 'pull_stats': STATS, 'push_stats': STATS})
        self.assertIn('push_complete', self.kinds(provider))
        # Each side decrypted the other's export intact.
        self.assertEqual([workspace for workspace, _ in self.received], ['team', 'team'])
        for _, buffer in self.received:
            self.assertIn(SECRET, buffer)
        # The relay saw two clear key-exchange messages, then only ciphertext.
        text = [frame for _, frame in relay.frames if isinstance(frame, str)]
        self.assertEqual([json.loads(frame)['method'] for frame in text], ['PAKE', 'PAKE'])
        self.assertTrue(all(isinstance(frame, bytes) for _, frame in relay.frames[2:]))
        room = next(event for event in provider if event['type'] == 'room')
        wire = b''.join(frame.encode() if isinstance(frame, str) else frame for _, frame in relay.frames)
        for secret in (SECRET, room['pin'], self.identity['hostname'], self.identity['fingerprint'], 'team'):
            self.assertNotIn(secret.encode(), wire, secret)

    def test_joining_side_learns_who_serves_and_confirms_before_sending(self):
        _, consumer, _ = self.sync()
        served_by = next(event for event in consumer if event['type'] == 'peer_connected')
        self.assertEqual(served_by['fingerprint'], self.identity['fingerprint'])
        confirm = next(event for event in consumer if event['type'] == 'confirm_push')
        self.assertEqual((confirm['workspace'], confirm['hostname']), ('team', self.identity['hostname']))
        # Nothing is sent before the answer: the plan follows the confirmation.
        self.assertLess(self.kinds(consumer).index('confirm_push'), self.kinds(consumer).index('plan'))

    def test_declining_to_send_still_pulls(self):
        _, consumer, _ = self.sync(send='reject')
        self.assertIn({'type': 'plan', 'pull': True, 'push': False,
                       'skipped': ['push skipped (you chose not to send)']}, consumer)
        self.assertEqual(consumer[-1], {'type': 'done', 'pull_stats': STATS, 'push_stats': None})
        self.assertEqual(len(self.received), 1)  # only the joining side imported

    def test_declining_a_send_only_join_leaves_nothing_to_sync(self):
        _, consumer, _ = self.sync(consumer=SEND, send='reject')
        self.assertEqual(consumer[-1]['message'], 'nothing to sync: pull skipped (this machine is send-only), '
                                                  'push skipped (you chose not to send)')
        self.export.assert_not_called()

    def test_joining_receives_only_by_default(self):
        async def run():
            async with serve(Relay().handler, '127.0.0.1', 0) as server:
                endpoint = sync_ws.Relay('ws', f"127.0.0.1:{server.sockets[0].getsockname()[1]}")
                ready = asyncio.get_running_loop().create_future()

                async def provide():
                    room = {}
                    async for event in sync_ws.serve_session('team', BOTH, endpoint):
                        if event['type'] == 'room':
                            room = event
                        elif event.get('status') == 'waiting':
                            ready.set_result(room)
                        elif event['type'] == 'approval_required':
                            sync_ws.submit_approval(room['session_id'], 'accept')

                task = asyncio.create_task(provide())
                room = await ready
                events = [e async for e in sync_ws.receive_session(endpoint, room['room_id'], room['pin'], 'team')]
                await task
                return events

        events = asyncio.run(run())
        self.assertNotIn('confirm_push', self.kinds(events))
        self.assertIn({'type': 'plan', 'pull': True, 'push': False,
                       'skipped': ['push skipped (this machine is receive-only)']}, events)

    def test_wrong_pin_ends_the_session_before_any_data(self):
        provider, consumer, relay = self.sync(pin='WrongPin')
        self.assertEqual(provider[-1], {'type': 'error', 'message': 'Wrong PIN from peer'})
        self.assertEqual(consumer[-1], {'type': 'error', 'message': 'Handshake rejected: wrong PIN'})
        self.assertNotIn('approval_required', self.kinds(provider))
        self.export.assert_not_called()
        self.importer.assert_not_called()
        # Two key-exchange messages, the unreadable HELLO and the notice.
        self.assertEqual(len(relay.frames), 4)

    def test_rejected_device_receives_nothing(self):
        provider, consumer, _ = self.sync(decision='reject')
        self.assertEqual(consumer[-1], {'type': 'error', 'message': 'Handshake rejected: rejected by provider'})
        self.export.assert_not_called()

    def test_unknown_decision_is_a_rejection(self):
        _, consumer, _ = self.sync(decision='maybe')
        self.assertEqual(consumer[-1]['message'], 'Handshake rejected: rejected by provider')

    def test_trusted_device_is_accepted_without_a_prompt(self):
        with patch.object(sync_ws, 'load_trusted_devices', return_value={self.identity['fingerprint']: {}}):
            provider, consumer, _ = self.sync()
        self.assertIn('auto_accepted', self.kinds(provider))
        self.assertNotIn('approval_required', self.kinds(provider))
        self.assertEqual(consumer[-1]['type'], 'done')

    def test_trust_decision_is_remembered(self):
        provider, _, _ = self.sync(decision='trust')
        self.assertIn('trusted', self.kinds(provider))
        self.assertIn(self.identity['fingerprint'], sync_ws.load_trusted_devices())

    def test_altered_frame_stops_the_sync_and_stores_nothing(self):
        def flip(role, index, frame):
            # Provider frames: PAKE, grant, then the export stream.
            if role == 'provider' and index == 3:
                frame = bytearray(frame)
                frame[5] ^= 1
                return bytes(frame)
            return frame

        _, consumer, _ = self.sync(consumer=RECEIVE, alter=flip)
        self.assertEqual(consumer[-1], {'type': 'error', 'message': TAMPERED})
        self.importer.assert_not_called()

    def test_dropped_frame_is_detected(self):
        _, consumer, _ = self.sync(consumer=RECEIVE, alter=lambda role, index, frame:
                                   None if (role, index) == ('provider', 2) else frame)
        self.assertEqual(consumer[-1], {'type': 'error', 'message': TAMPERED})
        self.importer.assert_not_called()

    def test_replayed_frame_is_detected(self):
        # Consumer frames: PAKE, HELLO, pull request; replay the request.
        provider, _, _ = self.sync(consumer=RECEIVE, alter=lambda role, index, frame:
                                   [frame, frame] if (role, index) == ('consumer', 2) else frame)
        self.assertIn({'type': 'error', 'message': TAMPERED}, provider)

    def test_injected_plaintext_after_the_handshake_is_rejected(self):
        notice = json.dumps({'status': 200, 'body': {'pull': True, 'push': True}})
        _, consumer, _ = self.sync(alter=lambda role, index, frame:
                                   notice if (role, index) == ('provider', 1) else frame)
        self.assertEqual(consumer[-1]['message'], 'Handshake rejected: the other machine ended the handshake')
        self.importer.assert_not_called()
        self.export.assert_not_called()

    def test_relay_text_is_never_echoed(self):
        # A relay forging a notice gets fixed local text, never its own bytes on the terminal.
        hostile = json.dumps({'status': 403, 'body': '\x1b]52;c;Y3VybCB8IHNo\x07\x1b[1A', 'version': 2})
        _, consumer, _ = self.sync(alter=lambda role, index, frame:
                                   hostile if (role, index) == ('provider', 0) else frame)
        self.assertEqual(consumer[-1], {'type': 'error', 'message': 'Handshake rejected: wrong PIN'})

    # ── Hostile peers that know the PIN ───────────────────────────

    @staticmethod
    def raw_consumer(hello, *requests):
        """A protocol-2 consumer driven by hand: key exchange, the given HELLO,
        then each request. Returns the provider's replies."""
        async def side(endpoint, room):
            replies = []
            async with connect(endpoint.url(room['room_id'], 'consumer')) as ws:
                exchange = KeyExchange('consumer', room['pin'], room['room_id'])
                await ws.send(json.dumps({'method': 'PAKE', 'version': 2, 'msg': exchange.message}))
                cipher = exchange.finish(json.loads(await ws.recv())['msg'])
                await ws.send(cipher.seal(json.dumps(hello).encode()))
                try:
                    replies.append(json.loads(cipher.open(await ws.recv())))
                    for request in requests:
                        await ws.send(cipher.seal(json.dumps(request).encode()))
                        while True:
                            reply = json.loads(cipher.open(await ws.recv()))
                            replies.append(reply)
                            if 'status' in reply or reply.get('kind') == 'done':
                                break
                except websockets.exceptions.ConnectionClosed:
                    pass
            return replies
        return side

    def test_peer_identity_is_validated_and_sanitised(self):
        hello = {'method': 'HELLO', 'hostname': 'evil\x1b]52;c;abc\x07' + 'x' * 500, 'user': 'u', 'os': 'linux',
                 'fingerprint': 'deadbeef'}
        provider, _, _ = self.sync(consumer_side=self.raw_consumer(hello))
        asked = next(event for event in provider if event['type'] == 'approval_required')
        self.assertNotIn('\x1b', asked['hostname'])
        self.assertLessEqual(len(asked['hostname']), 128)
        for bad in ({'fingerprint': ['x']}, {'fingerprint': 'short'}, {'fingerprint': 'deadbeef', 'user': 7}):
            provider, _, _ = self.sync(consumer_side=self.raw_consumer({'method': 'HELLO', **bad}))
            self.assertEqual(provider[-1], {'type': 'error', 'message': 'Peer sent a malformed greeting'}, bad)
            self.assertNotIn('approval_required', self.kinds(provider))

    def test_each_phase_runs_once_per_session(self):
        hello = {'method': 'HELLO', 'hostname': 'h', 'user': 'u', 'os': 'linux', 'fingerprint': 'deadbeef'}
        pull = {'method': 'GET', 'path': '/sync/pull'}
        provider, replies, _ = self.sync(consumer_side=self.raw_consumer(hello, pull, pull))
        self.assertEqual(replies[-1]['status'], 409)
        self.assertEqual(provider[-1], {'type': 'error', 'message': 'Peer repeated a request; session ended'})
        self.export.assert_called_once_with('team')

    def test_memories_without_vectors_are_refused(self):
        self.export.return_value = [EXPORT[0], json.dumps({'kind': 'batch', 'dataset': 'memories', 'seq': 1,
                                                           'records': [{'content': 'no vector'}]}), EXPORT[2]]
        _, consumer, _ = self.sync(provider=SEND)
        self.assertEqual(consumer[-1], {'type': 'error', 'message': 'Pull failed: unexpected frame in data stream'})
        self.importer.assert_not_called()

    def test_malformed_stream_header_leaves_no_buffer(self):
        buffer = sync_ws.pending_buffer_path()
        self.export.return_value = [json.dumps({'kind': 'meta', 'datasets': {'memories': [1]}, 'vectors': True}),
                                    EXPORT[2]]
        _, consumer, _ = self.sync(provider=SEND, buffer=buffer)
        self.assertEqual(consumer[-1]['message'], 'Pull failed: unexpected frame in data stream')
        self.assertIsNone(sync_ws.pending_download(buffer))
        self.importer.assert_not_called()

    def test_unreadable_buffer_asks_for_fresh_instead_of_crashing(self):
        buffer = sync_ws.pending_buffer_path()
        buffer.write_text(json.dumps({'kind': 'meta', 'datasets': {'memories': [1]}}) + '\n')
        with self.assertRaisesRegex(ValueError, '--fresh'):
            sync_ws.pending_download(buffer)

    def test_concurrent_receiving_runs_never_share_a_download(self):
        config.register_workspace('other')

        def export(workspace):
            batch = {'kind': 'batch', 'dataset': 'memories', 'seq': 1,
                     'records': [{'content': f'FROM-{workspace}-' + 'x' * 200_000, 'vector': [0.5]}] * 3}
            return [EXPORT[0], json.dumps(batch), EXPORT[2]]

        self.export.side_effect = export
        relay, buffer = Relay(), sync_ws.pending_buffer_path()

        async def pair(endpoint, served, destination):
            ready = asyncio.get_running_loop().create_future()

            async def provide():
                room = {}
                async for event in sync_ws.serve_session(served, SEND, endpoint):
                    if event['type'] == 'room':
                        room = event
                    elif event.get('status') == 'waiting':
                        ready.set_result(room)
                    elif event['type'] == 'approval_required':
                        sync_ws.submit_approval(room['session_id'], 'accept')

            task = asyncio.create_task(provide())
            room = await ready
            events = [e async for e in sync_ws.receive_session(endpoint, room['room_id'], room['pin'], destination,
                                                               RECEIVE, buffer)]
            await task
            return events

        async def run():
            async with serve(relay.handler, '127.0.0.1', 0) as server:
                endpoint = sync_ws.Relay('ws', f"127.0.0.1:{server.sockets[0].getsockname()[1]}")
                return await asyncio.gather(pair(endpoint, 'team', 'dsta'), pair(endpoint, 'other', 'dstb'))

        for events in asyncio.run(run()):
            self.assertEqual(events[-1]['type'], 'done', events[-1])
        imported = dict(self.received)
        self.assertIn('FROM-team', imported['dsta'])
        self.assertNotIn('FROM-other', imported['dsta'])
        self.assertIn('FROM-other', imported['dstb'])
        self.assertNotIn('FROM-team', imported['dstb'])
        self.assertEqual(list(self.data.glob('sync-*.jsonl.tmp')), [])

    # ── Older peers ───────────────────────────────────────────────

    def test_older_consumer_is_told_to_update(self):
        async def legacy_consumer(endpoint, room):
            async with connect(endpoint.url(room['room_id'], 'consumer')) as ws:
                await ws.send(json.dumps({'method': 'HELLO', 'pin': room['pin'], 'fingerprint': 'deadbeef'}))
                return [json.loads(await ws.recv())]

        provider, (reply,), _ = self.sync(consumer_side=legacy_consumer)
        self.assertEqual(reply['status'], 426)
        self.assertIn('encrypted', reply['body'])
        self.assertIn('older Imprint', provider[-1]['message'])

    def test_consumer_tells_the_user_when_the_provider_is_older(self):
        relay = Relay()

        async def run():
            async with serve(relay.handler, '127.0.0.1', 0) as server:
                endpoint = sync_ws.Relay('ws', f"127.0.0.1:{server.sockets[0].getsockname()[1]}")

                async def legacy_provider():
                    async with connect(endpoint.url('room1', 'provider')) as ws:
                        await ws.recv()
                        await ws.send(json.dumps({'status': 400, 'body': 'handshake required'}))

                task = asyncio.create_task(legacy_provider())
                await asyncio.sleep(0.05)
                events = [e async for e in sync_ws.receive_session(endpoint, 'room1', 'PIN', 'team', BOTH)]
                await task
                return events

        events = asyncio.run(run())
        self.assertIn('older Imprint', events[-1]['message'])

    # ── Direction policy ──────────────────────────────────────────

    def test_send_only_provider_is_only_pulled_from(self):
        provider, consumer, _ = self.sync(provider=SEND)
        self.assertIn({'type': 'plan', 'pull': True, 'push': False,
                       'skipped': ['push skipped (the provider is send-only)']}, consumer)
        self.assertEqual(consumer[-1], {'type': 'done', 'pull_stats': STATS, 'push_stats': None})
        self.assertEqual(len(self.received), 1)  # only the consumer imported
        self.export.assert_called_once_with('team')

    def test_receive_only_provider_is_only_pushed_to(self):
        _, consumer, _ = self.sync(provider=RECEIVE)
        self.assertEqual(consumer[-1], {'type': 'done', 'pull_stats': None, 'push_stats': STATS})
        self.assertEqual(len(self.received), 1)  # only the provider imported

    def test_send_only_consumer_never_pulls(self):
        _, consumer, _ = self.sync(consumer=SEND)
        self.assertEqual(consumer[-1], {'type': 'done', 'pull_stats': None, 'push_stats': STATS})

    def test_nothing_to_sync_sends_no_requests(self):
        provider, consumer, _ = self.sync(provider=SEND, consumer=SEND)
        self.assertTrue(consumer[-1]['message'].startswith('nothing to sync: '), consumer[-1])
        self.export.assert_not_called()
        self.assertEqual(provider[-1], {'type': 'done'})

    def test_provider_refuses_a_request_outside_its_grant(self):
        # A consumer that ignores the grant still cannot push.
        with patch.object(sync_ws, 'plan_sync', return_value=sync_ws.SyncPlan(False, True, ())):
            provider, _, _ = self.sync(provider=SEND)
        self.assertIn({'type': 'refused', 'request': 'push', 'message': REFUSED_PUSH}, provider)
        self.assertEqual(self.received, [])

    def test_consumer_can_receive_into_a_new_workspace(self):
        _, consumer, _ = self.sync(provider=SEND, workspace='from-alice')
        self.assertIn({'type': 'workspace_created', 'workspace': 'from-alice'}, consumer)
        self.assertEqual(self.received[0][0], 'from-alice')
        self.assertEqual(config.get_active_workspace(), 'default')

    def test_send_only_consumer_creates_no_workspace(self):
        _, consumer, _ = self.sync(consumer=SEND, workspace='missing')
        self.assertIn('nothing to send', consumer[-1]['message'])
        self.assertNotIn('missing', config.get_known_workspaces())

    # ── Resumable downloads ───────────────────────────────────────

    def test_download_is_kept_when_storing_fails_and_resumes_later(self):
        buffer = sync_ws.pending_buffer_path()
        self.importer.side_effect = RuntimeError('qdrant unavailable')
        _, consumer, _ = self.sync(provider=SEND, buffer=buffer)
        self.assertIn('kept for the next run', consumer[-1]['message'])
        pending = sync_ws.pending_download(buffer)
        self.assertEqual((pending.workspace, pending.memories, pending.facts), ('team', 1, 0))
        if os.name == 'posix':  # Windows files carry no permission bits; the data directory's ACL applies
            self.assertEqual(buffer.stat().st_mode & 0o777, 0o600)

        self.importer.side_effect = lambda path, workspace, on_progress=None: STATS
        events = asyncio.run(self.collect(sync_ws.resume_session(pending)))
        self.assertEqual(events[-1], {'type': 'done', 'pull_stats': STATS, 'push_stats': None})
        self.assertFalse(buffer.exists())

    def test_successful_download_leaves_no_buffer(self):
        buffer = sync_ws.pending_buffer_path()
        self.sync(provider=SEND, buffer=buffer)
        self.assertIsNone(sync_ws.pending_download(buffer))
        self.assertEqual(list(self.data.glob('sync-*.jsonl.tmp')), [])

    def test_buffer_from_a_release_without_workspaces_belongs_to_the_active_one(self):
        buffer = sync_ws.pending_buffer_path()
        buffer.write_text(EXPORT[0] + '\n' + EXPORT[2] + '\n')
        self.assertEqual(sync_ws.pending_download(buffer).workspace, 'default')
        Path(str(buffer) + '.tmp').write_text('partial')
        self.assertEqual(len(sync_ws.discard_pending(buffer)), 2)
        self.assertIsNone(sync_ws.pending_download(buffer))

    @staticmethod
    async def collect(session):
        return [event async for event in session]

    # ── CLI front-end ─────────────────────────────────────────────

    def cli(self, *, pin=None, answer='y', send='y', direction='receive'):
        """Run `imprint sync serve` and `imprint sync <code>` in-process.
        ``answer`` answers the serving side's device prompt, ``send`` the
        joining side's push confirmation; ``pin`` replaces the PIN part of
        the shared code. Both print to one stdout, so the
        transcript is shared. Returns (serve exit, join exit, transcript)."""
        relay = Relay()

        async def answered(prompt):
            print(prompt)  # as input() would show it
            return send if prompt.lstrip().startswith('Send your workspace') else answer

        async def run():
            async with serve(relay.handler, '127.0.0.1', 0) as server:
                host = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
                out = io.StringIO()
                with patch.object(cli_sync, '_ask', side_effect=answered), contextlib.redirect_stdout(out):
                    serve_args = argparse.Namespace(workspace='team', relay=host, direction='both')
                    serving = asyncio.create_task(cli_sync._serve(serve_args))
                    shared = None
                    for _ in range(200):
                        await asyncio.sleep(0.01)
                        shared = next((line.split()[2] for line in out.getvalue().splitlines()
                                       if line.strip().startswith('imprint sync ws://')), None)
                        if shared:
                            break
                    if pin:
                        shared = shared.rsplit('-', 1)[0] + '-' + pin
                    join_args = argparse.Namespace(code=shared, fresh=False, workspace=None, direction=direction)
                    joined = await cli_sync._join(join_args)
                    served = await asyncio.wait_for(serving, 5)
                return served, joined, out.getvalue()

        return asyncio.run(run())

    def test_cli_two_way_sync(self):
        served, joined, out = self.cli(direction='both')
        self.assertEqual((served, joined), (0, 0))
        for line in ('Mode:        two-way', 'Incoming Sync Request', f"Connected to {self.identity['hostname']}",
                     "Send your workspace 'default' to", 'Merged peer data: 1 new memories',
                     'Merged remote → local: 1 new memories', 'Pushed local → remote: 1 new memories',
                     'Sync Complete', 'Connection closed.'):
            self.assertIn(line, out)

    def test_cli_rejection_and_wrong_pin_exit_nonzero(self):
        served, joined, out = self.cli(answer='n')
        self.assertEqual((served, joined), (1, 1))
        self.assertIn('rejected by provider', out)
        served, joined, out = self.cli(pin='WrongPin')
        self.assertEqual((served, joined), (1, 1))
        self.assertIn('Handshake rejected: wrong PIN', out)

    def test_cli_default_join_only_receives(self):
        served, joined, out = self.cli()
        self.assertEqual((served, joined), (0, 0))
        self.assertIn('Plan: pull only — push skipped (this machine is receive-only)', out)
        self.assertIn('add --two-way to also send', out)
        self.assertNotIn('Send your workspace', out)
        self.assertNotIn('Merged peer data', out)

    def test_cli_declining_the_push_still_pulls(self):
        served, joined, out = self.cli(direction='both', send='n')
        self.assertEqual((served, joined), (0, 0))
        self.assertIn('push skipped (you chose not to send)', out)
        self.assertIn('Merged remote → local', out)
        self.assertNotIn('Merged peer data', out)


class RelayAddressTests(unittest.TestCase):
    def test_relay_forms(self):
        cases = {
            '': ('wss', sync_ws.DEFAULT_RELAY_HOST),
            'sync.example.com': ('wss', 'sync.example.com'),
            'wss://sync.example.com/': ('wss', 'sync.example.com'),
            'https://sync.example.com': ('wss', 'sync.example.com'),
            'ws://sync.example.com:8430': ('ws', 'sync.example.com:8430'),
            'localhost:8430': ('ws', 'localhost:8430'),
            '127.0.0.1:8430': ('ws', '127.0.0.1:8430'),
            '[::1]': ('ws', '[::1]'),
            'localhost.evil.com:8430': ('wss', 'localhost.evil.com:8430'),
            '127.0.0.1.evil.com': ('wss', '127.0.0.1.evil.com'),
        }
        for raw, expected in cases.items():
            relay = sync_ws.parse_relay(raw)
            self.assertEqual((relay.scheme, relay.host), expected, raw)
        for bad in ('bad host', 'host/path', 'user@host'):
            with self.assertRaises(ValueError):
                sync_ws.parse_relay(bad)

    def test_share_code_forms_and_what_the_provider_shows(self):
        code = sync_ws.share_code('7f3a9c21', 'Ab3xY9Kq')
        self.assertEqual(code, '7f3a9c21-Ab3xY9Kq')
        cases = {
            code: ('wss', sync_ws.DEFAULT_RELAY_HOST),
            f'sync.example.com/{code}': ('wss', 'sync.example.com'),
            f'wss://sync.example.com/{code}': ('wss', 'sync.example.com'),
            f'ws://localhost:8430/{code}': ('ws', 'localhost:8430'),
            f'127.0.0.1:8430/{code}': ('ws', '127.0.0.1:8430'),
        }
        for raw, expected in cases.items():
            relay, room, pin = sync_ws.parse_code(raw)
            self.assertEqual((relay.scheme, relay.host, room, pin), (*expected, '7f3a9c21', 'Ab3xY9Kq'), raw)
            self.assertEqual(sync_ws.parse_code(relay.target(code)), (relay, room, pin))
        self.assertEqual(sync_ws.parse_relay('').target(code), code)
        # A room ID alone — the shape older releases used — carries no PIN and is refused.
        for bad in ('', '7f3a9c21', 'host/7f3a9c21', 'host/', f'host/{code}?x=1', f'a/b/{code}', '7f3a9c21-short',
                    '7f3a9c21-Ab3x Y9Kq', '7f3a-9c21-Ab3xY9Kq'):
            with self.assertRaises(ValueError, msg=bad):
                sync_ws.parse_code(bad)


class KeyExchangeTests(unittest.TestCase):
    def pair(self, consumer_pin='Ab3xY9Kq', provider_pin='Ab3xY9Kq', consumer_room='r1', provider_room='r1'):
        consumer = KeyExchange('consumer', consumer_pin, consumer_room)
        provider = KeyExchange('provider', provider_pin, provider_room)
        return consumer.finish(provider.message), provider.finish(consumer.message)

    def test_keys_agree_only_for_the_same_pin_and_room(self):
        from imprint.sync_crypto import FrameError
        consumer, provider = self.pair()
        self.assertEqual(provider.open(consumer.seal(b'hello')), b'hello')
        self.assertEqual(consumer.open(provider.seal(b'back')), b'back')
        for kwargs in ({'provider_pin': 'Ab3xY9Kx'}, {'provider_room': 'r2'}):
            consumer, provider = self.pair(**kwargs)
            with self.assertRaises(FrameError):
                provider.open(consumer.seal(b'hello'))

    def test_directions_use_separate_keys(self):
        from imprint.sync_crypto import FrameError
        consumer, _ = self.pair()
        with self.assertRaises(FrameError):
            consumer.open(consumer.seal(b'reflected'))

    def test_malformed_messages_are_rejected(self):
        from imprint.sync_crypto import KeyExchangeError
        off_curve = base64.b64encode(b'A' + b'\xff' * 32).decode()
        for message in (None, '', '!!', off_curve, KeyExchange('provider', 'pin', 'r').message):
            with self.assertRaises(KeyExchangeError):
                KeyExchange('provider', 'pin', 'r').finish(message)


if __name__ == '__main__':
    unittest.main()
