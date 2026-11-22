"""One name per topic and the topic map (ADRs 0198, 0199)."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from qdrant_client import QdrantClient, models

from imprint import cli_viz, config, topics, vectorstore as vs


class KeyTests(unittest.TestCase):
    def test_spellings_of_one_topic_share_a_key(self):
        for group in (['unit-tests', 'unit-test', 'unittest', 'Unit_Tests', 'unit tests'],
                      ['health-checks', 'health-check', 'healthcheck'],
                      ['entity-schema', 'entity-schemas'], ['dependencies', 'dependency'],
                      ['processes', 'process'], ['search-indexes', 'search-index']):
            with self.subTest(group=group):
                self.assertEqual({topics.key(name) for name in group}, {topics.key(group[0])})

    def test_different_topics_keep_different_keys(self):
        for a, b in (('status', 'state'), ('analysis', 'analyst'), ('class', 'classic'),
                     ('unit-testing', 'unit-tests'), ('ios', 'io'), ('build', 'building')):
            with self.subTest(a=a, b=b):
                self.assertNotEqual(topics.key(a), topics.key(b))

    def test_the_most_used_spelling_names_a_key_and_ties_go_to_the_shorter(self):
        vocabulary = topics.Vocabulary.from_counts([('unit-test', 3), ('unit-tests', 7), ('unittest', 1),
                                                    ('healthcheck', 2), ('health-check', 2)])
        self.assertEqual(vocabulary.named('Unit_Test'), 'unit-tests')
        self.assertEqual(vocabulary.count[topics.key('unittest')], 11)
        self.assertEqual(vocabulary.spellings[topics.key('unit-test')], ['unit-tests', 'unit-test', 'unittest'])
        self.assertEqual(vocabulary.named('health-checks'), 'healthcheck')
        self.assertEqual(vocabulary.named('never-seen'), 'never-seen')


class StrengthAndGroupTests(unittest.TestCase):
    def test_strength_is_zero_for_independent_topics_and_one_for_inseparable_ones(self):
        self.assertAlmostEqual(topics.strength(25, 50, 50, 100), 0.0)
        self.assertAlmostEqual(topics.strength(10, 10, 10, 100), 1.0)
        self.assertEqual(topics.strength(0, 10, 10, 100), 0.0)
        self.assertEqual(topics.strength(5, 0, 10, 100), 0.0)
        self.assertTrue(0 < topics.strength(4, 10, 7, 23) < 1)

    def test_groups_form_around_their_largest_topic(self):
        names = ['tests', 'mocks', 'asserts', 'ui', 'forms', 'state', 'lonely']
        links = {('mocks', 'tests'): .6, ('asserts', 'tests'): .5, ('asserts', 'mocks'): .4,
                 ('forms', 'ui'): .6, ('state', 'ui'): .5, ('forms', 'state'): .4, ('tests', 'ui'): .05}
        sizes = {'tests': 10, 'mocks': 7, 'asserts': 8, 'ui': 9, 'forms': 6, 'state': 7, 'lonely': 3}
        hubs = topics.communities(names, links, sizes)
        self.assertEqual({n: hubs[n] for n in ('tests', 'mocks', 'asserts')}, dict.fromkeys(('tests', 'mocks', 'asserts'), 'tests'))
        self.assertEqual({n: hubs[n] for n in ('ui', 'forms', 'state')}, dict.fromkeys(('ui', 'forms', 'state'), 'ui'))
        self.assertEqual(hubs['lonely'], 'lonely')
        self.assertEqual(topics.communities(list(reversed(names)), links, sizes), hubs)


class UnopenedStoreTests(unittest.TestCase):
    """Naming helpers never start or reach a store this process did not open."""

    def setUp(self):
        topics.reset()
        self.addCleanup(topics.reset)
        vs._collections_ready.clear()

    def test_merge_and_canonical_work_from_their_input_alone(self):
        with patch.object(vs, '_ensure_collection', side_effect=AssertionError('store reached')), \
             patch.object(vs, '_get_client', side_effect=AssertionError('store reached')):
            self.assertEqual(topics.merge([('unit-tests', 5), ('unit-test', 2), ('mocking', 3)]),
                             [('unit-tests', 7), ('mocking', 3)])
            self.assertEqual(topics.canonical(['unit-test', 'mocking', 'mocking']), ['unit-test', 'mocking'])
            self.assertEqual(topics.spellings('unit-test'), ['unit-test'])


MEMORIES = (
    [('app', ['unit-tests', 'mocking'])] * 4 + [('app', ['unit-test', 'assertions'])] * 3
    + [('app', ['mocking', 'assertions'])] * 3 + [('lib', ['unit-tests', 'assertions'])] * 2
    + [('web', ['state-management', 'components'])] * 4 + [('web', ['components', 'forms'])] * 3
    + [('web', ['state-management', 'forms'])] * 3 + [('app', ['unit-tests', 'components'])] * 1
)


class StoreTests(unittest.TestCase):
    """Two groups of topics, one spelled two ways, joined by one memory."""

    def setUp(self):
        self.data = tempfile.TemporaryDirectory()
        self.addCleanup(self.data.cleanup)
        self.client = QdrantClient(':memory:')
        self.patches = [patch.object(config, 'get_data_dir', return_value=Path(self.data.name)),
                        patch.object(vs, '_get_client', return_value=self.client),
                        patch.object(vs, '_wal_log'), patch.object(config, 'EMBEDDING_DIM', 3),
                        patch.object(self.client, 'create_payload_index'),
                        patch('imprint.source_snapshots.conversation_titles', return_value={})]
        for item in self.patches:
            item.start()
        vs._collections_ready.clear()
        vs._id_caches.clear()
        topics.reset()
        cli_viz.reset_cache()
        self.addCleanup(self.cleanup)
        client, coll = vs._ensure_collection()
        client.upsert(coll, points=[
            models.PointStruct(id=index + 1, vector={config.QDRANT_VECTOR_NAME: [1., 0., 0.]},
                               payload={'project': project, 'source': f'{project}/f{index}.py', 'type': 'finding',
                                        'content': f'memory {index}', 'tags': {'topics': tags}})
            for index, (project, tags) in enumerate(MEMORIES)])

    def cleanup(self):
        vs._collections_ready.clear()
        vs._id_caches.clear()
        topics.reset()
        cli_viz.reset_cache()
        for item in reversed(self.patches):
            item.stop()
        self.client.close()

    def test_new_tags_take_the_stored_name_and_filters_match_every_spelling(self):
        self.assertEqual(topics.canonical(['unit-test', 'UnitTests', 'mocking', 'brand-new']),
                         ['unit-tests', 'mocking', 'brand-new'])
        client, coll = vs._ensure_collection()
        matched = client.count(coll, count_filter=models.Filter(must=[topics.condition('unit-test')]), exact=True).count
        self.assertEqual(matched, 10)

    def test_the_tagger_writes_the_stored_name(self):
        from imprint import tagger
        reply = ('{"type": "pattern", "domains": ["testing"], "topics": ["unit-test", "mocks"], '
                 '"lang": "python", "layer": "tests", "kind": "test"}')
        # The registry keeps its SQLite file open, which Windows will not delete.
        with patch('imprint.taxonomy.record_usage'):
            result = tagger._classification_result(reply, None)
        self.assertEqual(result['topics'], ['unit-tests', 'mocks'])

    def test_the_topic_map_links_and_groups_topics_without_projects(self):
        data = cli_viz.build_graph_scope('root', lens='topics')
        nodes = {n['id']: n for n in data['nodes']}
        self.assertEqual({n['kind'] for n in nodes.values()}, {'topic'})
        self.assertNotIn('topic:unit-test', nodes)
        self.assertEqual(nodes['topic:unit-tests']['count'], 10)
        self.assertTrue(all(e['kind'] == 'related' for e in data['edges']))
        pairs = {frozenset((e['source'], e['target'])) for e in data['edges']}
        self.assertIn(frozenset(('topic:unit-tests', 'topic:mocking')), pairs)
        self.assertIn(frozenset(('topic:forms', 'topic:state-management')), pairs)
        # One memory carries both groups' topics: too few to link them.
        self.assertNotIn(frozenset(('topic:unit-tests', 'topic:components')), pairs)
        for e in data['edges']:
            self.assertTrue(0 < e['strength'] <= 1 and e['count'] >= topics.MIN_TOGETHER and 1 <= e['weight'] <= 10)
        hubs = sorted(n['label'] for n in nodes.values() if n['hub'])
        self.assertEqual(hubs, ['components', 'unit-tests'])
        self.assertEqual(nodes['topic:mocking']['group'], 'topic:unit-tests')
        self.assertEqual(nodes['topic:forms']['group'], 'topic:components')
        self.assertEqual(nodes['topic:mocking']['color'], nodes['topic:unit-tests']['color'])
        self.assertEqual(nodes['topic:unit-tests']['projects'][0], {'name': 'app', 'count': 8})
        self.assertEqual(data['more'], [])

    def test_the_project_root_counts_every_spelling_under_one_topic(self):
        data = cli_viz.build_graph_scope('root')
        nodes = {n['id']: n for n in data['nodes']}
        self.assertIn('proj:app', nodes)
        self.assertNotIn('topic:unit-test', nodes)
        self.assertEqual(nodes['topic:unit-tests']['count'], 10)
        self.assertEqual({e['kind'] for e in data['edges']}, {'relates'})

    def test_a_topic_scope_reaches_its_related_topics_from_any_spelling(self):
        data = cli_viz.build_graph_scope('topic:unit-test')
        focus = next(n for n in data['nodes'] if n.get('focus'))
        self.assertEqual(focus['count'], 10)
        related = {e['target'] for e in data['edges'] if e['kind'] == 'related'}
        self.assertEqual(related, {'topic:mocking', 'topic:assertions'})

    def test_the_topics_page_counts_every_spelling_under_one_name(self):
        overview = cli_viz.build_topic_overview()
        counts = {t['name']: t['count'] for t in overview['topics']}
        self.assertEqual(counts['unit-tests'], 10)
        self.assertNotIn('unit-test', counts)
        self.assertEqual(overview['topic_count'], 6)
        detail = cli_viz.build_topic_detail('unit-test')
        self.assertEqual(detail['total'], 10)

    def test_the_lens_is_part_of_the_cache_key_and_unknown_lenses_are_refused(self):
        with patch.object(cli_viz.config, 'get_active_workspace', return_value='default'):
            plain = cli_viz.get_graph_scope('root', 1)
            lensed = cli_viz.get_graph_scope('root', 1, 'topics')
        self.assertTrue(any(n['kind'] == 'project' for n in plain['nodes']))
        self.assertFalse(any(n['kind'] == 'project' for n in lensed['nodes']))
        with self.assertRaises(ValueError):
            cli_viz.build_graph_scope('root', lens='nonsense')

    def test_the_mcp_tool_marks_hubs_and_refuses_unknown_lenses(self):
        from imprint import server
        with patch.object(server, '_validate_ws', return_value=(None, None)):
            text = server.graph_scope('root', lens='topics')
            self.assertIn('[topic:unit-tests] unit-tests  (10)  [hub]', text)
            self.assertIn('Error', server.graph_scope('root', lens='nonsense'))
            self.assertIn('graph_scope("root", lens="topics")', server.graph_scope('root'))

    def test_the_api_reports_the_lens(self):
        from imprint import api
        with patch.object(api, 'get_graph_scope', return_value={'scope': 'root', 'nodes': [], 'edges': []}) as built:
            self.assertEqual(api.api_graph('root', 1, 'topics')['lens'], 'topics')
            self.assertNotIn('lens', api.api_graph('root', 1, ''))
        self.assertEqual(built.call_args_list[0].args, ('root', 1, 'topics'))


if __name__ == '__main__':
    unittest.main()
