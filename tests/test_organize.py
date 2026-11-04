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


class OrganizeTests(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
