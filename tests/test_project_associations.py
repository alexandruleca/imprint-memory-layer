import json
import os
import sqlite3
from unittest.mock import patch

from imprint import project_associations as pa, source_snapshots as ss, vectorstore as vs, workspace_policy as policy
from imprint import config, projects, conversation_source
from test_source_snapshots import SourceFixture


class ProjectAssociationTests(SourceFixture):
    def test_database_connections_close_after_commit_and_rollback(self):
        with pa._db() as committed:
            committed.execute('CREATE TABLE close_test (value TEXT)')
            committed.execute("INSERT INTO close_test VALUES ('kept')")
        with self.assertRaises(sqlite3.ProgrammingError):
            committed.execute('SELECT 1')
        with self.assertRaisesRegex(RuntimeError, 'rollback'):
            with pa._db() as rolled_back:
                rolled_back.execute("INSERT INTO close_test VALUES ('removed')")
                raise RuntimeError('rollback')
        with self.assertRaises(sqlite3.ProgrammingError):
            rolled_back.execute('SELECT 1')
        with pa._db() as check:
            self.assertEqual([row[0] for row in check.execute('SELECT value FROM close_test')], ['kept'])

    def seed(self):
        self.store('first known repository', source='alpha/file', project='alpha-project')
        self.store('second known repository', source='beta/file', project='beta-project')

    def test_full_source_multi_membership_no_duplicate_and_reader(self):
        from imprint.cli_viz import build_overview, build_project_detail, build_source_read
        self.seed()
        text = 'generic information ' * 800 + '\nWorking on alpha-project and beta-project together.\n'
        self.store(text, source='conversation/shared', project='unassigned')
        links = pa.review()['associations']
        self.assertEqual({row['target'] for row in links}, {'alpha-project', 'beta-project'})
        self.assertTrue(all(row['status'] == 'suggested' for row in links))
        self.assertTrue(all(row['evidence'][0]['start'] > 10000 for row in links))
        count = vs.status()['total_memories']
        for target in ('alpha-project', 'beta-project'):
            pa.decide('conversation/shared', 'unassigned', target, 'accepted', 'default')
            self.assertIn('conversation/shared', dict(vs.list_sources(project=target)))
            read = build_source_read('conversation/shared', target)
            self.assertTrue(read['available'])
            self.assertEqual(read['project'], 'unassigned')
        self.assertEqual(vs.status()['total_memories'], count)
        self.assertEqual(build_project_detail('alpha-project')['count'], len(vs.get_chunks_by_source('conversation/shared')) + 1)
        overview = build_overview()
        self.assertEqual(overview['total'], count)
        self.assertEqual({p['name'] for p in overview['projects']}, {'alpha-project', 'beta-project', 'unassigned'})

    def test_manual_accept_reject_survive_refresh_and_moves(self):
        self.seed()
        text = 'alpha-project and beta-project need to share their integration.'
        self.store(text, source='conversation/shared', project='unassigned')
        pa.decide('conversation/shared', 'unassigned', 'alpha-project', 'accepted', 'default')
        pa.decide('conversation/shared', 'unassigned', 'beta-project', 'rejected', 'default')
        self.store(text + '\nnew content without any further evidence', source='conversation/shared', project='unassigned')
        links = pa.review()['associations']
        self.assertEqual({row['target']: row['status'] for row in links}, {'alpha-project': 'accepted', 'beta-project': 'rejected'})
        self.assertTrue(all(row['stale'] and not row['evidence'] for row in links))
        projects.move('unassigned', 'archive', workspace='default', apply=True)
        self.assertIn('conversation/shared', dict(vs.list_sources(project='alpha-project')))
        self.assertTrue(all(row['project'] == 'archive' for row in pa.review()['associations']))
        vs.delete_by_source('conversation/shared', project='archive')
        self.assertEqual(pa.review()['associations'], [])

    def test_inference_replaced_but_manual_membership_is_local_on_write(self):
        self.seed()
        self.store('Mention alpha-project here.', source='shared', project='unassigned')
        pa.decide('shared', 'unassigned', 'alpha-project', 'accepted', 'default')
        rows = self.rows('Replacement text', source='shared', project='unassigned')
        for row in rows:
            row['related_projects'] = ['unauthorized-peer-project']
        vs.replace_source(rows, 'shared', 'unassigned')
        client, coll = vs._ensure_collection()
        points, _ = client.scroll(coll, scroll_filter=pa.project_filter('alpha-project'), limit=100)
        self.assertTrue(any(p.payload['source'] == 'shared' for p in points))
        points, _ = client.scroll(coll, scroll_filter=pa.project_filter('unauthorized-peer-project'), limit=100)
        self.assertEqual(points, [])

    def test_auto_accept_only_path_and_clears_obsolete_inference(self):
        self.seed()
        snapshot_text = 'See /srv/alpha/repo/src/main.py; beta-project is discussed too.'
        self.store(snapshot_text, source='shared', project='unassigned')
        snapshot = ss.get('shared', 'unassigned')
        with patch.dict(os.environ, {'IMPRINT_ASSOCIATIONS_AUTO_PATHS': 'true'}):
            pa.analyze(snapshot, candidates={'alpha-project': '/srv/alpha/repo', 'beta-project': ''}, force=True)
        links = pa.review()['associations']
        self.assertEqual({row['target']: row['status'] for row in links}, {'alpha-project': 'accepted', 'beta-project': 'suggested'})
        self.store('No project mentioned any longer.', source='shared', project='unassigned')
        self.assertEqual(pa.review()['associations'], [])
        self.assertNotIn('shared', dict(vs.list_sources(project='alpha-project')))

    def test_injected_context_is_not_identity_evidence(self):
        self.seed()
        path = self.root / 'transcript.jsonl'
        path.write_text(json.dumps({'type': 'user', 'message': {'role': 'user', 'content': 'hello'}}) + '\n')
        snapshot = {'metadata': {'kind': 'conversation'}, 'text':
            '--- {"event":0,"role":"context","kind":"message"} ---\nalpha-project\n'
            '--- {"event":1,"role":"tool","kind":"tool_output"} ---\nbeta-project\n'
            '--- {"event":2,"role":"user","kind":"message"} ---\nLet us discuss something else.\n'}
        self.assertEqual(pa._matches(snapshot, {'alpha-project': '', 'beta-project': ''}), {})

    def test_workspace_isolation_and_explicit_inbox(self):
        config.register_workspace('other')
        with patch('imprint.transcripts.cwds', return_value=iter([])):
            self.assertFalse(policy.transcript_allowed('unused', 'default'))
        with patch.dict(os.environ, {'IMPRINT_UNASSIGNED_WORKSPACE': 'default', 'IMPRINT_CONVERSATION_INBOX_WORKSPACE': ''}), \
             patch('imprint.transcripts.cwds', return_value=iter([])):
            # Agent workspace fallback must not silently enable client imports.
            self.assertFalse(policy.transcript_allowed('unused', 'default'))
        with patch.dict(os.environ, {'IMPRINT_CONVERSATION_INBOX_WORKSPACE': 'default'}):
            with patch('imprint.transcripts.cwds', side_effect=lambda path: iter([])):
                self.assertTrue(policy.transcript_allowed('unused', 'default'))
                self.assertFalse(policy.transcript_allowed('unused', 'other'))
            other_root = self.root / 'other'; other_root.mkdir()
            policy.add_root(str(other_root), 'other')
            with patch('imprint.transcripts.cwds', return_value=iter([str(other_root)])):
                self.assertFalse(policy.transcript_allowed('unused', 'default'))
            with patch('imprint.transcripts.cwds', return_value=iter(['relative/path'])):
                self.assertFalse(policy.transcript_allowed('unused', 'default'))
        self.store('Some text', source='shared', project='unassigned')
        pa.decide('shared', 'unassigned', 'private-project', 'accepted', 'default')
        self.assertEqual(pa.accepted('other'), {})
        self.assertEqual(pa.review('other')['associations'], [])

    def test_manifest_naming_preserves_legacy_and_no_cwd_import_uses_inbox(self):
        from imprint.cli_conversations import derive_project, index_transcript
        self.assertEqual(derive_project('/home/me/code/personal/my-project'), 'my-project')
        folder = self.root / 'repositories' / 'demo'; folder.mkdir(parents=True)
        (folder / 'package.json').write_text('{"name":"manifest-name"}')
        self.assertEqual(derive_project(str(folder)), 'manifest-name')
        transcript = self.root / 'abcd1234.jsonl'
        transcript.write_text(json.dumps({'type': 'user', 'message': {'role': 'user', 'content': 'hello there'}}) + '\n')
        with patch.dict(os.environ, {'IMPRINT_CONVERSATION_INBOX_WORKSPACE': 'default'}):
            with patch('imprint.transcripts.cwds', side_effect=lambda path: iter([])), patch('imprint.tagger.build_payload_tags', return_value={}):
                index_transcript(str(transcript))
        self.assertIsNotNone(ss.get('conversation/abcd1234', 'unassigned'))

    def test_model_quotes_must_be_verbatim_and_never_auto_accepted(self):
        self.seed()
        self.store('A discussion about domain-specific inventory rules.', source='shared', project='unassigned')
        fake = self.root / 'helper.gguf'; fake.touch()
        llm = object()
        with patch('imprint.tagger._resolve_tagger_model_path', return_value=(fake, None)), \
             patch('imprint.tagger._load_tagger_model', return_value=(llm, None)), \
             patch('imprint.local_llm.fit_messages'), \
             patch('imprint.local_llm.complete', return_value=json.dumps({'matches': [
                 {'project': 'alpha-project', 'quote': 'domain-specific inventory rules'},
                 {'project': 'beta-project', 'quote': 'This quote is invented by the model'},
                 {'project': 'arbitrary-project', 'quote': 'domain-specific inventory rules'}]})):
            pa.analyze(ss.get('shared', 'unassigned'), use_model=True, force=True)
        links = pa.review()['associations']
        self.assertEqual([(row['target'], row['status']) for row in links], [('alpha-project', 'suggested')])
        # A metadata/unchanged-source refresh must not discard an expensive
        # model suggestion. Changed source text must invalidate it.
        self.store('A discussion about domain-specific inventory rules.', source='shared', project='unassigned')
        self.assertEqual(len(pa.review()['associations']), 1)
        self.store('A different discussion.', source='shared', project='unassigned')
        self.assertEqual(pa.review()['associations'], [])

    def test_api_requires_workspace_and_reads_linked_sources_as_canonical(self):
        import asyncio
        import httpx
        from imprint.api import app
        self.store('Some text about alpha-project.', source='shared', project='unassigned')
        async def requests():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:8420') as client:
                body = {'source': 'shared', 'project': 'unassigned', 'target': 'alpha-project', 'status': 'accepted'}
                self.assertEqual((await client.post('/api/projects/associations', json=body)).status_code, 400)
                response = await client.post('/api/projects/associations', json={**body, 'workspace': 'default'})
                self.assertEqual(response.status_code, 200)
                listing = (await client.get('/api/sources', params={'project': 'alpha-project'})).json()
                self.assertEqual(listing['sources'][0]['project'], 'unassigned')
                self.assertTrue(listing['sources'][0]['linked'])
                read = (await client.get('/api/sources/read', params={'source': 'shared', 'project': 'alpha-project'})).json()
                self.assertEqual(read['project'], 'unassigned')
        asyncio.run(requests())

    def test_scan_queue_dispatch_and_dedupe_keep_model_choice(self):
        from imprint import queue
        argv = queue._build_argv('associate-projects', {'model': True})
        self.assertEqual(argv[1:], ['-m', 'imprint.project_associations', '--model'])
        self.assertNotEqual(queue.dedupe_key('associate-projects', {'_workspace': 'default'}),
                            queue.dedupe_key('associate-projects', {'_workspace': 'default', 'model': True}))

    def test_primary_move_with_suggestions_disabled_keeps_local_links(self):
        self.store('A shared source.', source='shared', project='unassigned')
        pa.decide('shared', 'unassigned', 'target', 'accepted', 'default')
        with patch.dict(os.environ, {'IMPRINT_ASSOCIATIONS_ENABLED': 'false'}):
            projects.move('unassigned', 'target', workspace='default', apply=True)
        client, coll = vs._ensure_collection()
        points, _ = client.scroll(coll, limit=100)
        self.assertTrue(points)
        self.assertTrue(all(point.payload['related_projects'] == [] for point in points))
        self.assertEqual(dict(pa.project_counts())['target'], len(points))
        projects.move('target', 'archive', workspace='default', apply=True)
        self.assertIn('shared', dict(vs.list_sources(project='target')))

    def test_deleting_one_chunk_invalidates_source_links_on_surviving_chunks(self):
        self.store('Some evidence to split into many chunks.\n' * 8, source='shared', project='unassigned')
        pa.decide('shared', 'unassigned', 'target', 'accepted', 'default')
        chunks = vs.get_chunks_by_source('shared', project='unassigned')
        self.assertGreater(len(chunks), 1)
        self.assertTrue(vs.delete(chunks[0]['id']))
        self.assertEqual(pa.accepted(), {})
        self.assertEqual(vs.list_sources(project='target'), [])
        self.assertTrue(vs.get_chunks_by_source('shared', project='unassigned'))
