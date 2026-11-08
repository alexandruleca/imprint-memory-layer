"""The dashboard graph's scopes (imprint.cli_viz.build_graph_scope, ADR 0148)."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from imprint import cli_viz


def _project_of(filt):
    """The project name a project_filter() was built for, or None."""
    for cond in getattr(filt, 'should', None) or []:
        if cond.key == 'project':
            return cond.match.value
    return None


class FakeFacets:
    """Facet counts for a small workspace: memories as (project, topics)."""

    def __init__(self, memories):
        self.memories = memories
        self.calls = []

    def __call__(self, key, limit=100, filt=None, workspace=None):
        self.calls.append((key, _project_of(filt) if filt is not None else None))
        project = _project_of(filt) if filt is not None else None
        rows = [m for m in self.memories if project is None or m[0] == project]
        counts = {}
        for proj, topics in rows:
            for value in ([proj] if key == 'project' else topics if key == 'tags.topics' else []):
                counts[value] = counts.get(value, 0) + 1
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]


class RootScopeTests(unittest.TestCase):
    def setUp(self):
        titles = patch('imprint.source_snapshots.conversation_titles', return_value={})
        titles.start()
        self.addCleanup(titles.stop)

    def test_each_topic_links_to_its_three_largest_projects_with_one_facet_per_project(self):
        memories = (
            [('alpha', ['auth'])] * 5 + [('beta', ['auth'])] * 4 + [('gamma', ['auth'])] * 3
            + [('delta', ['auth'])] * 1 + [('beta', ['cache'])] * 2
        )
        facets = FakeFacets(memories)
        with patch.object(cli_viz, '_facet', facets):
            data = cli_viz.build_graph_scope('root')
        relates = {(e['source'], e['target']): e['weight'] for e in data['edges'] if e['kind'] == 'relates'}
        self.assertEqual(relates, {
            ('proj:alpha', 'topic:auth'): 5, ('proj:beta', 'topic:auth'): 4,
            ('proj:gamma', 'topic:auth'): 3, ('proj:beta', 'topic:cache'): 2,
        })
        # Two overview facets, then one topic facet per project: not one per topic.
        per_project = [c for c in facets.calls if c[0] == 'tags.topics' and c[1]]
        self.assertEqual(sorted(p for _, p in per_project), ['alpha', 'beta', 'delta', 'gamma'])
        self.assertEqual(len(facets.calls), 2 + 4)
        self.assertEqual(data['more'], [])

    def test_a_kind_listed_up_to_its_cap_is_reported_as_having_more(self):
        memories = [('alpha', [f't{i}']) for i in range(70)]
        with patch.object(cli_viz, '_facet', FakeFacets(memories)):
            data = cli_viz.build_graph_scope('root')
        self.assertEqual(sum(1 for n in data['nodes'] if n['kind'] == 'topic'), 60)
        self.assertEqual(data['more'], ['topic'])


class TopicScopeTests(unittest.TestCase):
    def test_the_topic_scope_counts_its_memories(self):
        client = SimpleNamespace(count=lambda coll, count_filter=None, exact=True: SimpleNamespace(count=42))
        with patch('imprint.source_snapshots.conversation_titles', return_value={}), \
             patch.object(cli_viz.vs, '_ensure_collection', return_value=(client, 'memories')), \
             patch.object(cli_viz, '_facet', return_value=[]):
            data = cli_viz.build_graph_scope('topic:auth')
        focus = next(n for n in data['nodes'] if n.get('focus'))
        self.assertEqual((focus['id'], focus['count']), ('topic:auth', 42))


class SourceLineageTests(unittest.TestCase):
    def test_a_long_source_is_read_page_by_page_and_shown_from_its_first_chunk(self):
        # Points come back in id order, not chunk order, 500 at a time.
        order = [(i * 7919) % 1200 for i in range(1200)]
        points = [SimpleNamespace(id=f'p{i}', payload={'_mid': f'm{i}', 'chunk_index': i, 'content': f'chunk {i} text here',
                                                        'source': 's.md', 'project': 'alpha', 'tags': {}}) for i in order]

        def scroll(coll, scroll_filter=None, limit=500, offset=None, **_):
            start = offset or 0
            end = start + limit
            return points[start:end], (end if end < len(points) else None)

        client = SimpleNamespace(scroll=scroll)
        with patch.object(cli_viz.vs, '_ensure_collection', return_value=(client, 'memories')), \
             patch('imprint.source_snapshots.conversation_titles', return_value={}):
            lineage = cli_viz.build_source_lineage('s.md')
            data = cli_viz.build_graph_scope('source:s.md')
        self.assertEqual(lineage['total'], 1200)
        self.assertEqual([c['chunk_index'] for c in lineage['chunks'][:3]], [0, 1, 2])
        chunks = sorted(n['chunk_index'] for n in data['nodes'] if n['kind'] == 'chunk')
        self.assertEqual(chunks, list(range(40)))
        self.assertIn('chunk', data['more'])


class GraphScopeCacheTests(unittest.TestCase):
    def setUp(self):
        cli_viz.reset_cache()
        self.addCleanup(cli_viz.reset_cache)

    def test_a_scope_is_built_once_until_the_data_changes(self):
        fingerprint = ['memories', 10, 100]
        built = []

        def build(scope, depth, workspace=None):
            built.append((scope, depth))
            return {'scope': scope, 'nodes': [], 'edges': [], 'center': '', 'more': []}

        with patch.object(cli_viz, 'data_fingerprint', side_effect=lambda: tuple(fingerprint)), \
             patch.object(cli_viz, 'build_graph_scope', side_effect=build), \
             patch.object(cli_viz.config, 'get_active_workspace', return_value='default'):
            cli_viz.get_graph_scope('root', 1)
            cli_viz.get_graph_scope('root', 1)
            cli_viz.get_graph_scope('root', 2)
            fingerprint[1] = 11  # a write grew the WAL
            cli_viz.get_graph_scope('root', 1)
        self.assertEqual(built, [('root', 1), ('root', 2), ('root', 1)])


if __name__ == '__main__':
    unittest.main()
