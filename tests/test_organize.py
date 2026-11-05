"""Organize: reviewed placement proposals, project profiles and core memory
candidates (ADR 0130)."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from qdrant_client import QdrantClient

from imprint import cli_viz, config, core_memories, imprint_graph as kg, organize, projects, vectorstore as vs
from imprint import workspace_policy as policy


class Fixture(unittest.TestCase):
    """In-memory Qdrant in a temporary data directory, as ProjectStoreTests."""

    def setUp(self):
        self.data = tempfile.TemporaryDirectory()
        self.addCleanup(self.data.cleanup)
        self.client = QdrantClient(':memory:')
        self.addCleanup(self.client.close)
        for item in [patch.object(config, 'get_data_dir', return_value=Path(self.data.name)),
                     patch.object(vs, '_get_client', return_value=self.client),
                     patch.object(config, 'EMBEDDING_DIM', 3),
                     patch('imprint.embeddings.embed_documents_batch',
                           side_effect=lambda texts: [[1., 0., 0.] for _ in texts]),
                     patch('imprint.embeddings.embed_document', return_value=[1., 0., 0.]),
                     patch('imprint.tagger.derive_zero_shot', return_value=[]),
                     patch.object(self.client, 'create_payload_index'),
                     patch.object(core_memories, '_device', return_value='device-a'),
                     patch.dict(os.environ, {'IMPRINT_WORKSPACE': ''})]:
            item.start()
            self.addCleanup(item.stop)
        self.addCleanup(kg._close_all)
        for clear in (vs._collections_ready.clear, vs._id_caches.clear, cli_viz.reset_cache):
            clear()
            self.addCleanup(clear)
        # Two real projects, and memories in catch-alls.
        vs.store_batch([
            {'content': 'The checkout-service books orders through Nexus.', 'project': 'checkout-service', 'source': 'docs/booking.md', 'type': 'architecture'},
            {'content': 'Render the voice avatar with WebGL.', 'project': 'echo-ui', 'source': 'src/avatar.ts', 'type': 'architecture'},
            {'content': 'Notes from the call: checkout-service must retry bookings twice.', 'project': 'Desktop', 'source': 'Desktop/call.txt'},
            {'content': 'A grocery list: milk, eggs, bread and butter for the week.', 'project': 'Desktop', 'source': 'Desktop/list.txt'},
            {'content': 'Decided to cap retries in checkout-service at three.', 'project': '', 'source': 'note/retries'},
        ])

    def placed(self):
        client, coll = vs._ensure_collection()
        rows, _ = client.scroll(coll, limit=1000, with_payload=True)
        return {(row.payload['source'], row.payload['project']) for row in rows}

    def pending(self):
        return {(p['origin'], p['source']): p for p in organize.review()['proposals'] if p['status'] == 'pending'}



class OrganizeTests(Fixture):
    def test_identifier_matches_are_proposed_and_nothing_moves_until_accepted(self):
        summary = organize.run(use_model=False)
        self.assertEqual((summary['model'], summary['pending'], summary['profiles']), (False, 2, 0))
        proposals = self.pending()
        self.assertEqual(set(proposals), {('Desktop', 'Desktop/call.txt'), ('', 'note/retries')})
        call = proposals[('Desktop', 'Desktop/call.txt')]
        self.assertEqual((call['target'], call['method'], call['chunks']), ('checkout-service', 'name', 1))
        self.assertIn('checkout-service', call['evidence'][0]['quote'])
        self.assertIn(('Desktop/call.txt', 'Desktop'), self.placed())  # Proposed, not moved.

    def test_accepting_moves_with_a_rule_even_from_no_project_and_rejecting_sticks(self):
        organize.run(use_model=False)
        workspace = organize.review()['workspace']
        result = organize.decide(workspace, [{'origin': '', 'source': 'note/retries'}], accept=True)
        self.assertEqual(result, {'decided': 1, 'failed': []})
        self.assertIn(('note/retries', 'checkout-service'), self.placed())
        with policy._db() as conn:
            self.assertEqual(projects._placement_rules(conn, workspace)[('', 'note/retries')], 'checkout-service')
        organize.decide(workspace, [{'origin': 'Desktop', 'source': 'Desktop/call.txt'}], accept=False)
        organize.run(use_model=False)
        statuses = {(p['origin'], p['source']): p['status'] for p in organize.review()['proposals']}
        self.assertEqual(statuses, {('', 'note/retries'): 'accepted', ('Desktop', 'Desktop/call.txt'): 'rejected'})
        self.assertEqual(organize.decide(workspace, [{'origin': 'Desktop', 'source': 'Desktop/call.txt'}], True)['failed'][0]['error'],
                         'no pending proposal')

    def test_plain_word_projects_need_more_than_their_name_and_folders_are_catch_alls(self):
        vs.store_batch([
            {'content': 'The client renders invoices.', 'project': 'client', 'source': 'src/client.ts'},
            {'content': 'Mostly code reviews today; the client asked for invoices.', 'project': 'cloud-ai-convos', 'source': 'chat/1'},
            {'content': 'Old notes on checkout-service retries.', 'project': 'Code', 'source': 'Code/notes.md'},
        ])
        self.assertTrue(organize._identifier('engine-3d') and organize._identifier('nextApp'))
        self.assertFalse(organize._identifier('client') or organize._identifier('Code'))
        self.assertNotIn('Code', organize._candidates(organize._workspace()))
        organize.run(use_model=False)
        proposals = self.pending()
        self.assertNotIn(('cloud-ai-convos', 'chat/1'), proposals)  # Only the plain word "client".
        self.assertEqual(proposals[('Code', 'Code/notes.md')]['target'], 'checkout-service')

    def test_no_project_as_a_whole_is_not_moved(self):
        with self.assertRaisesRegex(ValueError, 'project name'):
            projects.move('', 'checkout-service', None, apply=False)
        preview = projects.move('', 'checkout-service', ['note/retries'], apply=False)
        self.assertEqual((preview['chunks'], preview['sources']), (1, 1))

    def test_the_model_writes_profiles_and_places_only_with_a_quote_from_the_source(self):
        answers = []

        def ask(llm, prompt, schema, max_tokens):
            if 'profile' in schema['properties']:
                project = prompt.split('"')[1]
                return {'profile': f'{project} is a project in this workspace with its own code.', 'topics': ['booking']}
            if 'statement' in schema['properties']:
                return {}
            if 'grocery' in prompt:
                answers.append('grocery')
                return {'project': 'echo-ui', 'quote': 'not in the source at all, so refused'}
            return {'project': '', 'quote': ''}

        with patch.object(organize, '_model', return_value=object()), patch.object(organize, '_ask', side_effect=ask):
            summary = organize.run()
        self.assertEqual((summary['model'], summary['profiles']), (True, 2))
        profiles = {p['project']: p['text'] for p in organize.review()['profiles']}
        self.assertEqual(set(profiles), {'checkout-service', 'echo-ui'})
        self.assertTrue(profiles['echo-ui'].startswith('Project profile: echo-ui\n\necho-ui is a project'))
        self.assertIn(('profile/echo-ui', 'echo-ui'), self.placed())
        self.assertEqual(answers, ['grocery'])  # Asked, but its quote was not the source's.
        self.assertNotIn(('Desktop', 'Desktop/list.txt'), self.pending())
        # Rewriting a profile replaces it.
        with patch.object(organize, '_model', return_value=object()), patch.object(organize, '_ask', side_effect=ask):
            organize.run()
        self.assertEqual(sum(1 for source, _ in self.placed() if source == 'profile/echo-ui'), 1)

    def test_a_model_placement_needs_the_quote_in_the_source(self):
        def ask(llm, prompt, schema, max_tokens):
            if 'grocery' in prompt and 'project' in schema['properties']:
                return {'project': 'echo-ui', 'quote': 'A grocery list: milk, eggs'}
            return {}

        with patch.object(organize, '_model', return_value=object()), patch.object(organize, '_ask', side_effect=ask):
            organize.run()
        proposal = self.pending()[('Desktop', 'Desktop/list.txt')]
        self.assertEqual((proposal['target'], proposal['method'], proposal['evidence']),
                         ('echo-ui', 'local-model', [{'quote': 'A grocery list: milk, eggs'}]))

    def test_preferences_become_core_memory_proposals(self):
        vs.store(content='Prefers answers that lead with the result.', project='checkout-service', type='preference',
                 source='note/style')
        vs.store(content='The user said: keep commits small. ' + 'Context. ' * 60, project='', type='preference',
                 source='note/commits')
        organize.run(use_model=False)
        self.assertEqual([(m['text'], m['origin'], m['state']) for m in core_memories.list_all()],
                         [('Prefers answers that lead with the result.', 'organize', 'proposed')])

        def ask(llm, prompt, schema, max_tokens):
            if 'statement' in schema['properties'] and 'keep commits small' in prompt:
                return {'statement': 'Keeps commits small', 'quote': 'keep commits small'}
            return {}

        with patch.object(organize, '_model', return_value=object()), patch.object(organize, '_ask', side_effect=ask):
            organize.run()
        texts = {m['text']: m['evidence'] for m in core_memories.list_all()}
        self.assertEqual(texts['Keeps commits small'][0]['quote'], 'keep commits small')


class ConfidenceTests(Fixture):
    """Strong evidence moves by itself, weaker waits (ADR 0133)."""

    def setUp(self):
        super().setUp()
        vs.store_batch([{'content': 'Retry plan for checkout-service: checkout-service books twice, then gives up.',
                         'project': 'Desktop', 'source': 'Desktop/retry.txt'},
                        {'content': 'Compare checkout-service with echo-ui and the old checkout-service flow.',
                         'project': 'Desktop', 'source': 'Desktop/compare.txt'}])
        self.workspace = organize._workspace()

    def statuses(self):
        return {p['source']: (p['status'], p['strength']) for p in organize.review()['proposals']}

    def test_a_strong_match_moves_by_itself_and_can_be_undone(self):
        summary = organize.run(use_model=False)
        self.assertEqual(summary['moved'], 1)
        statuses = self.statuses()
        self.assertEqual(statuses['Desktop/retry.txt'], ('auto', 'strong'))
        # Named once, or next to another project: review.
        self.assertEqual(statuses['Desktop/call.txt'], ('pending', 'weak'))
        self.assertEqual(statuses['Desktop/compare.txt'], ('pending', 'weak'))
        self.assertIn(('Desktop/retry.txt', 'checkout-service'), self.placed())
        with policy._db() as conn:
            self.assertEqual(projects._placement_rules(conn, self.workspace)[('Desktop', 'Desktop/retry.txt')],
                             'checkout-service')
        self.assertEqual(organize.undo(self.workspace, [{'origin': 'Desktop', 'source': 'Desktop/retry.txt'}]),
                         {'undone': 1, 'failed': []})
        self.assertIn(('Desktop/retry.txt', 'Desktop'), self.placed())
        self.assertEqual(self.statuses()['Desktop/retry.txt'][0], 'rejected')
        organize.run(use_model=False)
        self.assertIn(('Desktop/retry.txt', 'Desktop'), self.placed())  # Not moved again.
        self.assertEqual(organize.undo(self.workspace, [{'origin': 'Desktop', 'source': 'Desktop/call.txt'}])['failed'][0]['error'],
                         'not moved by Organize')

    def test_two_named_projects_or_auto_move_off_wait_for_review(self):
        vs.store_batch([{'content': 'Port the engine-3d renderer into checkout-service, engine-3d first.',
                         'project': 'engine-3d', 'source': 'src/engine.c'},
                        {'content': 'engine-3d and checkout-service, engine-3d and checkout-service.',
                         'project': 'Desktop', 'source': 'Desktop/both.txt'}])
        with patch.dict(os.environ, {'IMPRINT_ORGANIZE_AUTO_MOVE': '0'}):
            self.assertEqual(organize.run(use_model=False)['moved'], 0)
        statuses = self.statuses()
        self.assertEqual(statuses['Desktop/retry.txt'], ('pending', 'strong'))
        self.assertEqual(statuses['Desktop/both.txt'], ('pending', 'weak'))

    def test_a_source_that_names_its_project_only_deep_inside_waits(self):
        # An export file bundling many conversations: the first is unrelated.
        bundle = 'Weather in Prague in January, packing lists and recipes. ' * 1000
        vs.store_batch([{'content': bundle + ' Then checkout-service, and checkout-service again.',
                         'project': 'Desktop', 'source': 'Desktop/export.json'}])
        organize.run(use_model=False)
        self.assertEqual(self.statuses()['Desktop/export.json'], ('pending', 'weak'))

    def test_a_registered_folder_is_strong_evidence(self):
        folder = Path(self.data.name) / 'echo'
        folder.mkdir()
        policy.map_project('echo-ui', str(folder))
        vs.store(content=f'Opened {folder.resolve()}/src/avatar.ts to fix the avatar.', project='Desktop',
                 source='Desktop/avatar.txt')
        organize.run(use_model=False)
        self.assertEqual(self.statuses()['Desktop/avatar.txt'], ('auto', 'strong'))
        self.assertIn(('Desktop/avatar.txt', 'echo-ui'), self.placed())


class BackgroundTests(Fixture):
    """Organize in the queue's idle slot: only what changed, and it steps
    aside (ADR 0132)."""

    def setUp(self):
        super().setUp()
        patcher = patch.dict(os.environ, {'IMPRINT_ORGANIZE_BACKGROUND': '1'})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_pass_runs_only_when_something_changed(self):
        self.assertEqual(organize.background_due(), 1)
        with patch.object(organize, '_model_available', return_value=False):
            self.assertEqual(organize.background(lambda: False), 0)
            self.assertEqual(organize.background_due(), 0)
            self.assertEqual(len(self.pending()), 2)
            self.assertEqual(organize.review()['background']['enabled'], True)
            vs.store(content='Another checkout-service note for later.', project='Desktop', source='Desktop/later.txt')
            self.assertEqual(organize.background_due(), 1)
        with patch.dict(os.environ, {'IMPRINT_ORGANIZE_BACKGROUND': '0'}):
            self.assertEqual(organize.background_due(), 0)

    def test_a_waiting_job_stops_the_pass_and_the_next_one_resumes(self):
        from imprint.tag_backlog import YIELDED
        with patch.object(organize, '_model_available', return_value=False):
            self.assertEqual(organize.background(lambda: True), YIELDED)
            self.assertEqual(organize.background_due(), 1)  # Not recorded as done.
            self.assertEqual(organize.background(lambda: False), 0)
        self.assertEqual(len(self.pending()), 2)

    def test_the_model_is_asked_once_per_source_profile_and_preference(self):
        vs.store(content='Prefers answers that lead with the result.', project='', type='preference', source='note/style')
        asked = []

        def ask(llm, prompt, schema, max_tokens):
            asked.append(next(iter(schema['properties'])))
            if 'profile' in schema['properties']:
                return {'profile': 'A project in this workspace with its own code and notes.', 'topics': []}
            return {}

        with patch.object(organize, '_model_available', return_value=True), \
                patch.object(organize, '_model', return_value=object()), \
                patch.object(organize, '_ask', side_effect=ask):
            self.assertEqual(organize.background(lambda: False), 0)
            first = sorted(asked)
            self.assertEqual(first.count('profile'), 2)
            self.assertIn('statement', first)
            asked.clear()
            # Its own profiles changed what it watches: one more pass finds
            # nothing for the model, and then nothing is due.
            self.assertEqual(organize.background(lambda: False), 0)
            self.assertEqual(organize.background_due(), 0)
            with organize._db() as conn:
                conn.execute('DELETE FROM passes')
            self.assertEqual(organize.background(lambda: False), 0)
        self.assertEqual(asked, [])

    def test_a_pass_with_nothing_for_the_model_does_not_load_it(self):
        with patch.object(organize, '_model_available', return_value=True), \
                patch.object(organize, '_model', side_effect=AssertionError('loaded')), \
                patch.object(organize, '_stale', return_value=[]):
            vs.store_batch([{'content': 'A note that names no project at all.', 'project': 'Desktop',
                             'source': 'Desktop/plain.txt'}])
            with organize._db() as conn:  # Every source already looked at with the model.
                for group in organize.catch_all_sources(organize._workspace()):
                    text, _ = organize._source_text(group, organize._workspace())
                    fingerprint = organize.hashlib.sha256(organize.json.dumps(
                        [text, sorted(organize._candidates(organize._workspace())), True]).encode()).hexdigest()
                    organize._write(conn, workspace=organize._workspace(), origin=group['origin'],
                                    source=group['source'], status='none', fingerprint=fingerprint, chunks=1)
            self.assertEqual(organize.background(lambda: False), 0)

    def test_the_idle_slot_runs_organize_after_tagging(self):
        from imprint import queue, tag_backlog
        with patch.object(organize, '_model_available', return_value=False), \
                patch.object(tag_backlog, 'deferring', return_value=False), \
                patch.object(queue, 'foreground_waiting', return_value=False):
            self.assertEqual(queue._work_total(), 1)
            self.assertEqual(tag_backlog._organize(), 0)
            self.assertEqual(queue._work_total(), 0)
            state = queue.background_state()
        self.assertEqual((state['enabled'], state['tagging'], state['organize']['enabled']), (True, False, True))


class ReopenTests(Fixture):
    """Decisions return to review on request: the dashboard's undo and its
    Decided tab (ADR 0136)."""

    def setUp(self):
        super().setUp()
        organize.run(use_model=False)
        self.workspace = organize._workspace()

    def statuses(self):
        return {p['source']: (p['status'], p['decided_at']) for p in organize.review()['proposals']}

    def rules(self):
        with policy._db() as conn:
            return projects._placement_rules(conn, self.workspace)

    def test_reopening_a_move_takes_it_back_and_a_keep_only_reopens(self):
        organize.decide(self.workspace, [{'origin': '', 'source': 'note/retries'}], accept=True)
        organize.decide(self.workspace, [{'origin': 'Desktop', 'source': 'Desktop/call.txt'}], accept=False)
        self.assertIn(('note/retries', 'checkout-service'), self.placed())
        result = organize.reopen(self.workspace, [{'origin': '', 'source': 'note/retries'},
                                                  {'origin': 'Desktop', 'source': 'Desktop/call.txt'}])
        self.assertEqual(result, {'reopened': 2, 'failed': []})
        self.assertNotIn(('', 'note/retries'), self.rules())  # The rule is gone,
        self.assertIn(('note/retries', ''), self.placed())  # and the memories are back.
        self.assertIn(('Desktop/call.txt', 'Desktop'), self.placed())
        self.assertEqual(self.statuses(), {'note/retries': ('pending', None), 'Desktop/call.txt': ('pending', None)})
        # A later run leaves the reopened proposals waiting, and they can be decided again.
        organize.run(use_model=False)
        self.assertEqual(set(self.pending()), {('', 'note/retries'), ('Desktop', 'Desktop/call.txt')})
        self.assertEqual(organize.decide(self.workspace, [{'origin': '', 'source': 'note/retries'}], True)['decided'], 1)
        self.assertIn(('note/retries', 'checkout-service'), self.placed())

    def test_moves_organize_made_and_pending_proposals_are_refused(self):
        vs.store_batch([{'content': 'Retry plan: checkout-service books twice, then checkout-service gives up.',
                         'project': 'Desktop', 'source': 'Desktop/retry.txt'}])
        organize.run(use_model=False)
        self.assertEqual(self.statuses()['Desktop/retry.txt'][0], 'auto')
        result = organize.reopen(self.workspace, [{'origin': 'Desktop', 'source': 'Desktop/retry.txt'},
                                                  {'origin': 'Desktop', 'source': 'Desktop/call.txt'},
                                                  {'origin': 'Desktop', 'source': 'Desktop/none.txt'}])
        self.assertEqual(result['reopened'], 0)
        self.assertEqual([f['error'] for f in result['failed']],
                         ['moved by Organize itself; move it back instead', 'not decided yet', 'no decided proposal'])
        self.assertEqual(self.statuses()['Desktop/retry.txt'][0], 'auto')
        self.assertIn(('Desktop/retry.txt', 'checkout-service'), self.placed())
        with self.assertRaisesRegex(ValueError, 'at least one'):
            organize.reopen(self.workspace, [])

    def test_a_move_back_and_its_undo_end_as_the_users_move(self):
        """Undoing Move back: reopen the rejection, then move it."""
        vs.store_batch([{'content': 'Retry plan: checkout-service books twice, then checkout-service gives up.',
                         'project': 'Desktop', 'source': 'Desktop/retry.txt'}])
        organize.run(use_model=False)
        item = [{'origin': 'Desktop', 'source': 'Desktop/retry.txt'}]
        organize.undo(self.workspace, item)
        self.assertEqual(organize.reopen(self.workspace, item)['reopened'], 1)
        organize.decide(self.workspace, item, accept=True)
        self.assertEqual(self.statuses()['Desktop/retry.txt'][0], 'accepted')
        self.assertEqual(self.rules()[('Desktop', 'Desktop/retry.txt')], 'checkout-service')

    def test_a_running_job_refuses_taking_a_move_back(self):
        organize.decide(self.workspace, [{'origin': '', 'source': 'note/retries'}], accept=True)
        with patch('imprint.queue_lock.acquire', return_value=None):
            result = organize.reopen(self.workspace, [{'origin': '', 'source': 'note/retries'}])
        self.assertEqual(result['reopened'], 0)
        self.assertIn('ingestion or maintenance job is running', result['failed'][0]['error'])
        self.assertEqual(self.statuses()['note/retries'][0], 'accepted')
        self.assertIn(('note/retries', 'checkout-service'), self.placed())

    def test_a_reopen_within_one_clock_tick_still_reaches_the_peer(self):
        config.register_workspace('peer')
        item = [{'origin': 'Desktop', 'source': 'Desktop/call.txt'}]
        with patch('time.time', return_value=1000.0):
            organize.decide(self.workspace, item, accept=False)
            organize.import_records(organize.export(self.workspace, organize.manifest(self.workspace)), 'peer')
            before = organize.manifest(self.workspace)
            organize.reopen(self.workspace, item)
            after = organize.manifest(self.workspace)
            stats = organize.import_records(organize.export(self.workspace, after), 'peer')
        key = next(k for k in after if 'Desktop/call.txt' in k)
        self.assertGreater(after[key], before[key])
        self.assertEqual(stats['updated'], 1)
        self.assertEqual({p['source']: p['status'] for p in organize.review('peer')['proposals']}['Desktop/call.txt'],
                         'pending')

    def test_proposals_carry_their_conversations_title(self):
        with patch('imprint.source_snapshots.conversation_titles',
                   return_value={'note/retries': 'How many retries does checkout get?'}) as titles:
            proposals = {p['source']: p['title'] for p in organize.review()['proposals']}
        titles.assert_called_once_with(self.workspace)
        self.assertEqual(proposals, {'note/retries': 'How many retries does checkout get?', 'Desktop/call.txt': ''})

    def test_the_dashboard_reopens_through_the_api(self):
        from fastapi.testclient import TestClient
        from imprint.api import app
        client = TestClient(app, base_url='http://127.0.0.1:8420', client=('127.0.0.1', 1234))
        organize.decide(self.workspace, [{'origin': 'Desktop', 'source': 'Desktop/call.txt'}], accept=False)
        reply = client.post('/api/organize/reopen', json={'workspace': self.workspace,
                                                          'items': [{'origin': 'Desktop', 'source': 'Desktop/call.txt'}]})
        self.assertEqual(reply.json(), {'reopened': 1, 'failed': []})
        self.assertEqual(client.post('/api/organize/reopen', json={'items': []}).status_code, 400)
        refused = client.post('/api/organize/reopen', json={'workspace': self.workspace, 'items': [{'source': 1}]})
        self.assertEqual((refused.status_code, refused.json()['error']), (400, 'Each proposal is an origin and a source'))


class ProposalSyncTests(Fixture):
    """Proposals and decisions reach paired devices; the newest wins (ADR 0134)."""

    def test_proposals_travel_and_the_newer_decision_wins(self):
        organize.run(use_model=False)
        workspace = organize._workspace()
        config.register_workspace('peer')
        manifest = organize.manifest(workspace)
        records = organize.export(workspace, manifest)
        self.assertEqual(len(records), len(manifest))
        self.assertEqual(organize.import_records(records, 'peer'), {'inserted': len(records), 'updated': 0, 'skipped': 0})
        self.assertEqual(organize.import_records(records, 'peer')['skipped'], len(records))
        self.assertEqual(organize.manifest('peer'), manifest)
        organize.decide(workspace, [{'origin': 'Desktop', 'source': 'Desktop/call.txt'}], accept=False)
        newer = organize.export(workspace, organize.manifest(workspace))
        self.assertEqual(organize.import_records(newer, 'peer')['updated'], 1)
        peer = {p['source']: p['status'] for p in organize.review('peer')['proposals']}
        self.assertEqual(peer['Desktop/call.txt'], 'rejected')

    def test_a_decision_within_one_clock_tick_still_reaches_the_peer(self):
        config.register_workspace('peer')
        workspace = organize._workspace()
        with patch('time.time', return_value=1000.0):
            organize.run(use_model=False)
            organize.import_records(organize.export(workspace, organize.manifest(workspace)), 'peer')
            organize.decide(workspace, [{'origin': 'Desktop', 'source': 'Desktop/call.txt'}], accept=False)
            stats = organize.import_records(organize.export(workspace, organize.manifest(workspace)), 'peer')
        self.assertEqual(stats['updated'], 1)

    def test_a_peers_proposal_is_checked_and_redacted(self):
        good = {'origin': 'Desktop', 'source': 'Desktop/x.txt', 'target': 'checkout-service', 'method': 'name',
                'evidence': [{'quote': 'token ghp_' + 'a' * 36}], 'preview': 'key ghp_' + 'b' * 36, 'chunks': 1,
                'status': 'pending', 'fingerprint': '', 'created_at': 1.0, 'decided_at': None, 'updated_at': 2.0,
                'strength': 'weak'}
        bad = [{**good, 'status': 'done'}, {**good, 'target': ' x'}, {**good, 'source': ''},
               {**good, 'evidence': 'quote'}, {**good, 'updated_at': None}, {**good, 'fingerprint': 'zz'}, 'text']
        self.assertEqual(organize.import_records([good, *bad], organize._workspace()),
                         {'inserted': 1, 'updated': 0, 'skipped': len(bad)})
        stored = organize.review()['proposals'][0]
        self.assertNotIn('ghp_', stored['preview'] + stored['evidence'][0]['quote'])


if __name__ == '__main__':
    unittest.main()
