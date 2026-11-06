"""Core memories: recorded statements about the user, and the guidelines
every session receives once the user approves them (ADR 0129, ADR 0143)."""
import json
import os
import sqlite3
import tempfile
from contextlib import closing
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

    def test_the_user_writes_guidelines_agents_record_and_organize_proposes(self):
        written = core_memories.add('Commit only when asked', 'instruction')
        self.assertEqual((written['state'], written['origin'], written['scope'], written['tier']),
                         ('active', 'user', '*', 'guideline'))
        self.assertIsNotNone(written['approved_at'])
        recorded = core_memories.record('Prefers concise answers', evidence=[{'memory_id': 'm1', 'quote': 'keep it short'}])
        self.assertEqual((recorded['state'], recorded['tier'], recorded['approved_at']), ('active', 'record', None))
        self.assertEqual(recorded['evidence'], [{'memory_id': 'm1', 'quote': 'keep it short'}])
        proposed = core_memories.propose_guideline('Keep answers concise', covers=[recorded['id'], 'not-an-id'])
        self.assertEqual((proposed['state'], proposed['tier'], proposed['covers']), ('proposed', 'guideline', [recorded['id']]))
        self.assertEqual([(r['tier'], r['state']) for r in core_memories.list_all()],
                         [('record', 'active'), ('guideline', 'active'), ('guideline', 'proposed')])
        made = core_memories.add('Keep every answer short', covers=[recorded['id']])
        self.assertEqual(made['covers'], [recorded['id']])

    def test_a_statement_already_held_is_not_recorded_or_proposed_again_even_after_retiring(self):
        held = core_memories.record('Prefers  concise answers')
        self.assertIsNone(core_memories.record('prefers concise ANSWERS'))
        core_memories.update(held['id'], state='retired')
        self.assertIsNone(core_memories.record('Prefers concise answers'))
        # A guideline may say what a record says.
        guideline = core_memories.propose_guideline('Prefers concise answers')
        self.assertIsNotNone(guideline)
        core_memories.update(guideline['id'], state='retired')
        self.assertIsNone(core_memories.propose_guideline('prefers concise answers'))

    def test_a_record_is_kept_or_retired_never_proposed(self):
        recorded = core_memories.record('Prefers tabs')
        self.assertEqual(core_memories.update(recorded['id'], state='retired')['state'], 'retired')
        self.assertEqual(core_memories.update(recorded['id'], state='active')['state'], 'active')
        with self.assertRaisesRegex(ValueError, 'never proposed'):
            core_memories.update(recorded['id'], state='proposed')
        self.assertEqual(core_memories.update(recorded['id'], text='Prefers tabs over spaces')['text'], 'Prefers tabs over spaces')

    def test_statements_are_short_and_redacted(self):
        self.assertIsNone(core_memories.record('x' * (core_memories.MAX_TEXT + 1)))
        self.assertIsNone(core_memories.propose_guideline('x' * (core_memories.MAX_TEXT + 1)))
        with self.assertRaisesRegex(ValueError, '1-600 characters'):
            core_memories.add('   ')
        secret = 'Use the token ghp_' + 'a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8' + ' for pushes'
        stored = core_memories.add(secret)
        self.assertNotIn('ghp_a1B2', stored['text'])
        self.assertIn('[REDACTED:', stored['text'])

    def test_approving_retiring_and_restoring(self):
        proposal = core_memories.propose_guideline('Answers in British English', kind='preference')
        approved = core_memories.update(proposal['id'], state='active', scope='shop')
        self.assertEqual((approved['state'], approved['scope']), ('active', 'shop'))
        self.assertIsNotNone(approved['approved_at'])
        self.assertEqual(core_memories.update(proposal['id'], state='retired')['state'], 'retired')
        self.assertEqual(core_memories.update(proposal['id'], state='active')['state'], 'active')
        written = core_memories.add('Commit only when asked')
        with self.assertRaisesRegex(ValueError, 'cannot become a proposal'):
            core_memories.update(written['id'], state='proposed')
        with self.assertRaisesRegex(ValueError, 'kind'):
            core_memories.update(proposal['id'], kind='rule')
        with self.assertRaises(ValueError):
            core_memories.update(proposal['id'], scope=' padded ')
        with self.assertRaises(KeyError):
            core_memories.update('0' * 32, state='retired')

    def test_an_approval_or_rejection_is_undone_back_to_a_proposal(self):
        """The dashboard's undo (ADR 0136): a proposal returns to proposed,
        unapproved, and each change is stamped after the last one."""
        proposal = core_memories.propose_guideline('Prefers small pull requests', origin='organize')
        with patch('time.time', return_value=1000.0):
            approved = core_memories.update(proposal['id'], state='active')
            undone = core_memories.update(proposal['id'], state='proposed')
        self.assertEqual((approved['state'], approved['approved_at']), ('active', approved['updated_at']))
        self.assertEqual((undone['state'], undone['approved_at']), ('proposed', None))
        self.assertGreater(undone['updated_at'], approved['updated_at'])  # Within one clock tick.
        self.assertEqual(core_memories.session_text(None), '')
        core_memories.update(proposal['id'], state='retired')
        self.assertEqual(core_memories.update(proposal['id'], state='proposed')['state'], 'proposed')
        self.assertEqual(core_memories.get(proposal['id'])['approved_at'], None)
        written = core_memories.add('Commit only when asked')
        core_memories.update(written['id'], state='retired')
        with self.assertRaisesRegex(ValueError, 'you wrote cannot become a proposal'):
            core_memories.update(written['id'], state='proposed')
        self.assertEqual(core_memories.get(written['id'])['state'], 'retired')

    def test_approving_a_rewrite_retires_what_it_amends_and_undoing_restores_it(self):
        original = core_memories.propose_guideline('Keep recipes gluten-free')
        core_memories.update(original['id'], state='active')
        rewrite = core_memories.propose_guideline('Keep recipes gluten-free and lactose-free', amends=original['id'])
        self.assertEqual(rewrite['amends'], original['id'])
        with self.assertRaisesRegex(ValueError, 'amends'):
            core_memories.propose_guideline('Something else', amends='../x')
        core_memories.update(rewrite['id'], state='active')
        self.assertEqual(core_memories.get(original['id'])['state'], 'retired')
        self.assertEqual(core_memories.session_text(None), '- Keep recipes gluten-free and lactose-free')
        core_memories.update(rewrite['id'], state='proposed')  # The dashboard's undo.
        self.assertEqual(core_memories.get(original['id'])['state'], 'active')
        self.assertEqual(core_memories.session_text(None), '- Keep recipes gluten-free')
        # Rejecting a rewrite leaves the guideline it amends alone.
        core_memories.update(rewrite['id'], state='retired')
        core_memories.update(rewrite['id'], state='proposed')
        self.assertEqual(core_memories.get(original['id'])['state'], 'active')

    def test_records_never_reach_a_session(self):
        core_memories.record('Asks about baby sleep')
        core_memories.update(core_memories.record('Prefers tabs')['id'], state='active')
        self.assertEqual(core_memories.session_text(None), '')
        self.assertEqual(core_memories.used_budget(), 0)

    def test_older_stores_become_tiers_without_new_stamps(self):
        """What the user wrote becomes a guideline, the rest records, and a
        record is no proposal; every device moves its rows the same way."""
        # Closed, not only committed: Windows cannot remove an open database.
        with closing(sqlite3.connect(self.data / 'core-memories.sqlite3')) as conn, conn:
            conn.execute('CREATE TABLE core (id TEXT PRIMARY KEY, text TEXT NOT NULL, kind TEXT NOT NULL, '
                         'scope TEXT NOT NULL, state TEXT NOT NULL, evidence TEXT NOT NULL, origin TEXT NOT NULL, '
                         'created_at REAL NOT NULL, updated_at REAL NOT NULL, approved_at REAL, device TEXT NOT NULL)')
            for n, (origin, state) in enumerate((('user', 'active'), ('organize', 'active'), ('agent', 'proposed'),
                                                 ('organize', 'retired'))):
                conn.execute('INSERT INTO core VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                             (str(n) * 32, f'Statement {n}', 'preference', '*', state, '[]', origin, n, n, None, 'd'))
        core_memories._READY.clear()
        rows = {r['id'][0]: (r['tier'], r['state'], r['updated_at']) for r in core_memories.list_all()}
        self.assertEqual(rows, {'0': ('guideline', 'active', 0), '1': ('record', 'active', 1),
                                '2': ('record', 'active', 2), '3': ('record', 'retired', 3)})
        self.assertEqual(core_memories.session_text(None), '- Statement 0')

    def test_a_session_gets_the_global_ones_and_its_projects_within_its_budget(self):
        core_memories.add('Commit only when asked', 'instruction')
        core_memories.add('Use pnpm here', 'instruction', scope='shop')
        core_memories.add('Use cargo here', 'instruction', scope='engine')
        core_memories.update(core_memories.propose_guideline('Never shown: still a proposal')['id'], kind='fact')
        project = self.data / 'checkout' / 'web'
        project.mkdir(parents=True)
        (self.data / 'checkout' / 'package.json').write_text(json.dumps({'name': '@acme/shop'}))
        self.assertEqual(core_memories.session_projects(str(project)), {'web', 'shop', 'checkout'})
        self.assertEqual(core_memories.session_text(str(project)), '- Commit only when asked\n- Use pnpm here (in shop)')
        self.assertEqual(core_memories.session_text(None), '- Commit only when asked')
        for n in range(40):
            core_memories.add(f'Rule number {n} that the user wrote down for every session')
        text = core_memories.session_text(None, budget=75)
        shown = text.splitlines()[:-1]
        self.assertLessEqual(sum(core_memories.tokens(len(line) + 1) for line in shown), 75)
        self.assertRegex(text.splitlines()[-1], r"^- … \d+ more on the dashboard's Core memories page\.$")
        self.assertEqual(core_memories.SESSION_BUDGET, 3000)
        self.assertEqual(core_memories.tokens(9), 3)

    def test_sync_keeps_the_newest_change_of_each(self):
        mine = core_memories.add('Commit only when asked')
        newer = {**mine, 'text': 'Commit only when asked, on dev', 'updated_at': mine['updated_at'] + 10}
        older = {**mine, 'text': 'stale', 'updated_at': mine['updated_at'] - 10}
        theirs = {**mine, 'id': 'f' * 32, 'text': 'Prefers tabs', 'state': 'proposed', 'approved_at': None}
        broken = [{**mine, 'id': '../../x'}, {**mine, 'id': 'e' * 32, 'state': 'deleted'}, {'id': 'd' * 32},
                  {**mine, 'id': 'b' * 32, 'amends': '../x'}]
        self.assertEqual(core_memories.import_records([older]), {'inserted': 0, 'updated': 0, 'skipped': 1})
        self.assertEqual(core_memories.import_records([newer, theirs, *broken]), {'inserted': 1, 'updated': 1, 'skipped': 4})
        self.assertEqual(core_memories.get(mine['id'])['text'], 'Commit only when asked, on dev')
        self.assertEqual(core_memories.manifest(), {mine['id']: newer['updated_at'], 'f' * 32: theirs['updated_at']})
        self.assertEqual([r['id'] for r in core_memories.export(['f' * 32])], ['f' * 32])

    def test_records_from_a_device_before_tiers_take_their_tier_from_their_origin(self):
        base = {'kind': 'preference', 'scope': '*', 'evidence': [], 'created_at': 1.0, 'updated_at': 1.0,
                'approved_at': None, 'device': 'old'}
        older = [{**base, 'id': 'a' * 32, 'text': 'Written there', 'state': 'active', 'origin': 'user'},
                 {**base, 'id': 'b' * 32, 'text': 'Proposed there', 'state': 'proposed', 'origin': 'organize'}]
        newer = [{**base, 'id': 'c' * 32, 'text': 'A rewrite', 'state': 'proposed', 'origin': 'organize',
                  'tier': 'guideline', 'amends': 'a' * 32, 'covers': ['b' * 32, 'junk'],
                  'conflicts': [{'id': 'a' * 32, 'reason': ' Changed ', 'resolution': 'nonsense'}, 'junk']}]
        self.assertEqual(core_memories.import_records(older + newer), {'inserted': 3, 'updated': 0, 'skipped': 0})
        got = {r['id'][0]: (r['tier'], r['state'], r['amends'], r['covers']) for r in core_memories.list_all()}
        self.assertEqual(got, {'a': ('guideline', 'active', '', []), 'b': ('record', 'active', '', []),
                               'c': ('guideline', 'proposed', 'a' * 32, ['b' * 32])})
        self.assertEqual(core_memories.get('c' * 32)['conflicts'], [{'id': 'a' * 32, 'reason': 'Changed', 'resolution': ''}])

    def test_core_memories_go_only_to_a_peer_that_tells_the_tiers_apart(self):
        self.assertIn('core', sync_delta.extras_of({'core': True, 'core_tiers': True}))
        self.assertNotIn('core', sync_delta.extras_of({'core': True}))
        self.assertTrue(sync_delta.extras_offered()['core_tiers'])

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
        self.assertTrue(text.startswith("Base.\n\n# The user's guidelines\nThe user approved these"))
        self.assertIn('search them when a task needs that detail', text)
        self.assertTrue(text.endswith('- Commit only when asked\n'))
        spoken = core_memories.guidance(750, searchable=False)
        self.assertNotIn('search', spoken)
        self.assertIn('- Commit only when asked', spoken)

    def test_the_dashboard_returns_a_proposal_to_proposed_but_not_one_the_user_wrote(self):
        from fastapi.testclient import TestClient
        from imprint.api import app
        client = TestClient(app, base_url='http://127.0.0.1:8420', client=('127.0.0.1', 1234))
        proposal = core_memories.propose_guideline('Prefers small pull requests')
        written = core_memories.add('Commit only when asked')
        recorded = core_memories.record('Asks about pull requests')
        made = client.post('/api/core', json={'text': 'Keep pull requests small', 'covers': [recorded['id']]})
        self.assertEqual((made.status_code, made.json()['tier'], made.json()['covers']), (200, 'guideline', [recorded['id']]))
        self.assertEqual(client.post('/api/core', json={'text': 'x', 'covers': 'nope'}).status_code, 400)
        body = client.get('/api/core').json()
        self.assertEqual((body['budget'], body['used']), (3000, core_memories.tokens(len('Commit only when asked') + 3)
                                                          + core_memories.tokens(len('Keep pull requests small') + 3)))
        self.assertEqual(client.patch(f"/api/core/{proposal['id']}", json={'state': 'active'}).json()['state'], 'active')
        undone = client.patch(f"/api/core/{proposal['id']}", json={'state': 'proposed'})
        self.assertEqual((undone.status_code, undone.json()['state'], undone.json()['approved_at']), (200, 'proposed', None))
        older, newer = core_memories.record('Prefers tea'), core_memories.record('Prefers coffee now')
        core_memories.flag_conflict(newer['id'], older['id'], 'Switched to coffee')
        settled = client.post(f"/api/core/{newer['id']}/resolve", json={'keep': 'newer'})
        self.assertEqual({r['id']: r['state'] for r in settled.json()['changed']},
                         {newer['id']: 'active', older['id']: 'retired'})
        self.assertEqual(client.post(f"/api/core/{newer['id']}/resolve", json={'keep': 'x'}).status_code, 400)
        self.assertEqual(client.post(f"/api/core/{'0' * 32}/resolve", json={'keep': 'both'}).status_code, 404)
        refused = client.patch(f"/api/core/{written['id']}", json={'state': 'proposed'})
        self.assertEqual(refused.status_code, 400)
        self.assertIn('cannot become a proposal', refused.json()['error'])

    def test_a_stored_preference_is_recorded_not_given_to_sessions(self):
        from imprint import server
        with patch.object(server.vs, 'store', return_value='abc123'), \
                patch.object(server, '_validate_ws', return_value=('default', None)):
            reply = server.store(content='The user prefers answers that lead with the result.', type='preference')
            plain = server.store(content='Retries are capped at three.', type='decision')
        self.assertIn('recorded as a core memory', reply)
        self.assertEqual(plain, 'Stored [abc123]')
        (recorded,) = core_memories.list_all()
        self.assertEqual((recorded['tier'], recorded['state'], recorded['evidence']),
                         ('record', 'active', [{'memory_id': 'abc123'}]))
        self.assertEqual(core_memories.session_text(None), '')


if __name__ == '__main__':
    unittest.main()
