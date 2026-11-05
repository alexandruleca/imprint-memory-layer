"""Core memories: user-approved statements every session receives (ADR 0129)."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from imprint import config, core_memories, sync_delta, sync_ws


class CoreMemoryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data = Path(directory.name)
        for patcher in (patch.object(config, 'get_data_dir', return_value=self.data),
                        patch.object(core_memories, '_device', return_value='device-a')):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_the_user_writes_active_ones_agents_only_propose(self):
        written = core_memories.add('Commit only when asked', 'instruction')
        self.assertEqual((written['state'], written['origin'], written['scope']), ('active', 'user', '*'))
        self.assertIsNotNone(written['approved_at'])
        proposed = core_memories.propose('Prefers concise answers', evidence=[{'memory_id': 'm1', 'quote': 'keep it short'}])
        self.assertEqual((proposed['state'], proposed['approved_at']), ('proposed', None))
        self.assertEqual(proposed['evidence'], [{'memory_id': 'm1', 'quote': 'keep it short'}])
        self.assertEqual([r['state'] for r in core_memories.list_all()], ['active', 'proposed'])

    def test_a_statement_already_held_is_not_proposed_again_even_after_retiring(self):
        held = core_memories.propose('Prefers  concise answers')
        self.assertIsNone(core_memories.propose('prefers concise ANSWERS'))
        core_memories.update(held['id'], state='retired')
        self.assertIsNone(core_memories.propose('Prefers concise answers'))

    def test_statements_are_short_and_redacted(self):
        self.assertIsNone(core_memories.propose('x' * (core_memories.MAX_TEXT + 1)))
        with self.assertRaisesRegex(ValueError, '1-600 characters'):
            core_memories.add('   ')
        secret = 'Use the token ghp_' + 'a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8' + ' for pushes'
        stored = core_memories.add(secret)
        self.assertNotIn('ghp_a1B2', stored['text'])
        self.assertIn('[REDACTED:', stored['text'])

    def test_approving_retiring_and_restoring(self):
        proposal = core_memories.propose('Answers in British English', kind='preference')
        approved = core_memories.update(proposal['id'], state='active', scope='shop')
        self.assertEqual((approved['state'], approved['scope']), ('active', 'shop'))
        self.assertIsNotNone(approved['approved_at'])
        self.assertEqual(core_memories.update(proposal['id'], state='retired')['state'], 'retired')
        self.assertEqual(core_memories.update(proposal['id'], state='active')['state'], 'active')
        with self.assertRaisesRegex(ValueError, 'cannot become a proposal'):
            core_memories.update(proposal['id'], state='proposed')
        with self.assertRaisesRegex(ValueError, 'kind'):
            core_memories.update(proposal['id'], kind='rule')
        with self.assertRaises(ValueError):
            core_memories.update(proposal['id'], scope=' padded ')
        with self.assertRaises(KeyError):
            core_memories.update('0' * 32, state='retired')

    def test_a_session_gets_the_global_ones_and_its_projects_within_its_budget(self):
        core_memories.add('Commit only when asked', 'instruction')
        core_memories.add('Use pnpm here', 'instruction', scope='shop')
        core_memories.add('Use cargo here', 'instruction', scope='engine')
        core_memories.update(core_memories.propose('Never shown: still a proposal')['id'], kind='fact')
        project = self.data / 'checkout' / 'web'
        project.mkdir(parents=True)
        (self.data / 'checkout' / 'package.json').write_text(json.dumps({'name': '@acme/shop'}))
        self.assertEqual(core_memories.session_projects(str(project)), {'web', 'shop', 'checkout'})
        self.assertEqual(core_memories.session_text(str(project)), '- Commit only when asked\n- Use pnpm here (in shop)')
        self.assertEqual(core_memories.session_text(None), '- Commit only when asked')
        for n in range(40):
            core_memories.add(f'Rule number {n} that the user wrote down for every session')
        text = core_memories.session_text(None, budget=300)
        self.assertLessEqual(len(text.rsplit('\n', 1)[0]), 300)
        self.assertRegex(text.splitlines()[-1], r"^- … \d+ more on the dashboard's Core memories page\.$")

    def test_sync_keeps_the_newest_change_of_each(self):
        mine = core_memories.add('Commit only when asked')
        newer = {**mine, 'text': 'Commit only when asked, on dev', 'updated_at': mine['updated_at'] + 10}
        older = {**mine, 'text': 'stale', 'updated_at': mine['updated_at'] - 10}
        theirs = {**mine, 'id': 'f' * 32, 'text': 'Prefers tabs', 'state': 'proposed', 'approved_at': None}
        broken = [{**mine, 'id': '../../x'}, {**mine, 'id': 'e' * 32, 'state': 'deleted'}, {'id': 'd' * 32}]
        self.assertEqual(core_memories.import_records([older]), {'inserted': 0, 'updated': 0, 'skipped': 1})
        self.assertEqual(core_memories.import_records([newer, theirs, *broken]), {'inserted': 1, 'updated': 1, 'skipped': 3})
        self.assertEqual(core_memories.get(mine['id'])['text'], 'Commit only when asked, on dev')
        self.assertEqual(core_memories.manifest(), {mine['id']: newer['updated_at'], 'f' * 32: theirs['updated_at']})
        self.assertEqual([r['id'] for r in core_memories.export(['f' * 32])], ['f' * 32])

    def test_core_memories_travel_only_to_a_device_that_offers_them(self):
        record = core_memories.add('Commit only when asked')
        chunks = list(sync_delta.export_chunks('ws', {'core': [record['id']]}))
        self.assertEqual(len(chunks), 1)
        frames = [json.loads(line) for line in chunks[0]]
        self.assertEqual(frames[0]['datasets'], {'memories': 0, 'facts': 0, 'core': 1})
        self.assertTrue(all(sync_ws._stream_line_ok(frame, index > 0) for index, frame in enumerate(frames)))
        # Received on another device, it is stored with the paired import.
        core_memories.update(record['id'], state='retired')
        buffer = self.data / 'stream.jsonl'
        buffer.write_text('\n'.join(chunks[0]))
        stats = sync_ws.process_import_buffer(str(buffer), 'ws')
        self.assertEqual(stats['core'], {'inserted': 0, 'updated': 0, 'skipped': 1})  # Retiring here is newer.
        self.assertEqual(core_memories.get(record['id'])['state'], 'retired')

    def test_a_peer_without_core_memories_is_sent_none(self):
        sent = []

        async def send(channel, workspace, keys, cancel):
            sent.append(keys)
            yield {'type': 'progress'}

        class Channel:
            async def send(self, frame):
                pass

            async def recv(self, timeout=None):
                return {'kind': 'manifest', 'data': sync_delta.encode({'memories': {}, 'facts': {}, 'sources': {}})}

        local = {'memories': {}, 'facts': {}, 'sources': {}, 'core': {'c' * 32: 1.0}}

        async def run(core):
            import asyncio
            async for _ in sync_delta.reconcile(Channel(), 'ws', local, provider=True, provider_sends=True,
                                                consumer_sends=False, cancel=asyncio.Event(), result={},
                                                extras=frozenset({'core'} if core else ())):
                pass

        import asyncio
        with patch.object(sync_delta, '_send', send):
            asyncio.run(run(False))
            asyncio.run(run(True))
        self.assertEqual([keys['core'] for keys in sent], [[], ['c' * 32]])


class ServerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data = Path(directory.name)
        for patcher in (patch.object(config, 'get_data_dir', return_value=self.data),
                        patch.object(core_memories, '_device', return_value='device-a')):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_the_mcp_instructions_carry_the_sessions_core_memories(self):
        from imprint import server
        self.assertEqual(server._with_core_memories('Base.\n'), 'Base.\n')
        core_memories.add('Commit only when asked', 'instruction')
        text = server._with_core_memories('Base.\n')
        self.assertTrue(text.startswith('Base.\nCore memories: the user approved these'))
        self.assertTrue(text.endswith('- Commit only when asked\n'))

    def test_a_stored_preference_is_proposed_for_the_user_to_approve(self):
        from imprint import server
        with patch.object(server.vs, 'store', return_value='abc123'), \
                patch.object(server, '_validate_ws', return_value=('default', None)):
            reply = server.store(content='The user prefers answers that lead with the result.', type='preference')
            plain = server.store(content='Retries are capped at three.', type='decision')
        self.assertIn('proposed as a core memory', reply)
        self.assertEqual(plain, 'Stored [abc123]')
        (proposal,) = core_memories.list_all()
        self.assertEqual((proposal['state'], proposal['evidence']), ('proposed', [{'memory_id': 'abc123'}]))


if __name__ == '__main__':
    unittest.main()
