import json
import unittest
from unittest.mock import patch

from imprint import server
from imprint.mcp_output import compact_search, excerpt, expand_hint


class CompactEvidenceTests(unittest.TestCase):
    def test_expansion_hint_names_the_actual_mcp_argument(self):
        self.assertIn('get_memory(memory_id="b93a69214464b791")',
                      expand_hint('b93a69214464b791', 0, 100, 1000))

    def rows(self):
        return [{'id': f'memory-{i}', 'content': ('background ' * 300) + 'reconnect retry and cancellation ' * 20,
                 'similarity': .8, 'project': 'project', 'source': f'source-{i}.py', 'type': 'finding',
                 'source_hash': 'a' * 64, 'recorded_device': {'id': 'other-device', 'name': 'workstation'},
                 'path_context': {'original_source_path': '/foreign/project/code.py', 'path_status': 'unmapped'}}
                for i in range(10)]

    def test_excerpt_finds_late_query_evidence_and_preserves_original_text(self):
        content = 'unrelated background. ' * 300 + 'retry reconnect cancellation, then retry.'
        text, start, truncated = excerpt(content, 'reconnect cancellation retry', 200)
        self.assertIn('reconnect cancellation', text)
        self.assertGreater(start, 0)
        self.assertTrue(truncated)
        self.assertEqual(content[start:start + len(text)], text)

    def test_compact_budget_preserves_ids_provenance_and_continuation(self):
        rows = self.rows()
        for budget in (1200, 2000, 4000, 12000):
            text = compact_search(rows, 'reconnect cancellation', 'team', 0, 10, budget, .2)
            self.assertLessEqual(len(text), budget)
            self.assertIn('id:memory-0', text)
            self.assertIn('recorded on: workstation', text)
            self.assertIn('path status: unmapped', text)
            emitted = sum('id:memory-' + str(i) + ' ' in text for i in range(10))
            self.assertGreater(emitted, 0)
            self.assertIn(f'offset={emitted}', text)
            self.assertNotIn('graph_scope', text)

    def test_default_search_does_not_load_unrelated_session_context(self):
        with patch.object(server, '_validate_ws', return_value=('team', None)), \
             patch.object(server.vs, 'search', return_value=self.rows()[:5]) as search, \
             patch.object(server, 'wake_up') as wake:
            text = server.search('reconnect cancellation')
            wake.assert_not_called()
            self.assertEqual(search.call_args.kwargs['limit'], 5)
            self.assertLessEqual(len(text), 4000)
            self.assertIn('get_memory', text)

    def test_selected_memory_supports_lossless_content_pagination(self):
        content = 'x' * 12000 + 'end'
        with patch.object(server, '_validate_ws', return_value=('team', None)), \
             patch('imprint.cli_viz.get_memory', return_value={'id': 'id', 'content': content}):
            first = json.loads(server.get_memory('id'))
            self.assertEqual(len(first['content']), 4000)
            self.assertEqual(first['next_start'], 4000)
            rebuilt = ''
            start = 0
            while True:
                row = json.loads(server.get_memory('id', start=start))
                rebuilt += row['content']
                if row['next_start'] is None:
                    break
                start = row['next_start']
            self.assertEqual(rebuilt, content)


if __name__ == '__main__':
    unittest.main()
