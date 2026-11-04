import json
import unittest

from imprint import chat_research
from imprint.chat_research import ResearchBudget, ResearchExhausted


class ResearchBudgetTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.
        self.budget = ResearchBudget(seconds=30, tokens=5000, patience=3, clock=lambda: self.now)

    def observe(self, text, writes=False):
        return self.budget.observe([(text, writes)])

    def test_cycles_across_different_queries_stop_without_new_evidence(self):
        self.assertTrue(self.observe('Source alpha.md contains the original architecture decision.'))
        self.assertTrue(self.observe('Source beta.md describes the deployment procedure.'))
        for text in ('Source alpha.md contains the original architecture decision.',
                     'Source beta.md describes the deployment procedure.',
                     'Source alpha.md contains the original architecture decision.'):
            self.assertFalse(self.observe(text))
        self.assertEqual(self.budget.reason(), 'no_progress')

    def test_new_evidence_resets_patience_even_after_many_rounds(self):
        for number in range(40):
            self.assertTrue(self.observe(f'Source file-{number}.md provides evidence number {number}.'))
            self.assertFalse(self.observe(f'Source file-{number}.md provides evidence number {number}.'))
            self.assertIsNone(self.budget.reason())
        self.assertEqual(self.budget.rounds, 80)

    def test_result_rank_scores_and_timestamps_are_not_progress(self):
        def result(score, rank):
            return f'[{rank}] id:abc sim:{score} | source:decision.md\nUse the local database for this project.'
        self.assertTrue(self.observe(result(.8, 1)))
        self.assertFalse(self.observe(result(.9, 2)))
        original = [{'id': 'a', 'content': 'Evidence alpha', 'timestamp': 1, 'similarity': .9},
                    {'id': 'b', 'content': 'Evidence beta', 'timestamp': 1, 'similarity': .8}]
        self.assertTrue(self.observe(json.dumps(original)))
        changed = [{**row, 'timestamp': 9, 'similarity': .6} for row in reversed(original)]
        self.assertFalse(self.observe(json.dumps(changed)))

    def test_recurring_different_errors_and_empty_queries_do_not_reset_progress(self):
        for result in ('[tool error] missing query', 'No results for query beta', '{"error":"bad request"}'):
            self.assertFalse(self.observe(result))
        self.assertEqual(self.budget.reason(), 'no_progress')

    def test_overlapping_passages_with_a_new_tail_count_as_progress(self):
        text = 'We use a local database and store every conversation with its original source and provenance.'
        self.assertTrue(self.observe(text))
        self.assertTrue(self.observe(text + ' An additional ADR explains project membership.'))
        self.assertFalse(self.observe(text))

    def test_writes_are_excluded_from_the_stagnation_heuristic(self):
        for _ in range(12):
            self.observe('Stored the requested note', writes=True)
            self.assertIsNone(self.budget.reason())

    def test_sampling_keeps_tail_changes_in_large_multiline_sources(self):
        prefix = '\n'.join(f'Source line {number} contains detailed reference material.' for number in range(1000))
        self.assertTrue(self.observe(prefix + '\nThe original final conclusion.'))
        self.assertTrue(self.observe(prefix + '\nA new final conclusion changes the architectural decision.'))

    def test_prompt_and_maximum_output_reservations_are_cumulative(self):
        self.budget.reserve(1500, 500)
        self.budget.reserve(1500, 500)
        self.assertEqual(self.budget.reserved, 4000)
        with self.assertRaises(ResearchExhausted) as stopped:
            self.budget.reserve(900, 200)
        self.assertEqual(stopped.exception.reason, 'tokens')
        self.assertEqual(self.budget.reserved, 4000)

    def test_time_stops_new_requests_without_starting_them(self):
        self.now += 30
        self.assertEqual(self.budget.reason(), 'time')
        with self.assertRaises(ResearchExhausted) as stopped:
            self.budget.reserve(1, 1)
        self.assertEqual(stopped.exception.reason, 'time')
        self.assertEqual(self.budget.reserved, 0)

    def test_zero_or_empty_results_are_not_evidence(self):
        for text in ('', '  ', 'No memories found.', 'No facts match this query.'):
            self.assertFalse(self.observe(text))



def exchange(*results):
    """A reply's research: a user question, then each (tool, args, result)."""
    messages = [{'role': 'user', 'content': 'question'}]
    for n, (name, args, result) in enumerate(results):
        messages.append({'role': 'assistant', 'content': '', 'tool_calls': [
            {'id': f'c{n}', 'type': 'function', 'function': {'name': name, 'arguments': args}}]})
        messages.append({'role': 'tool', 'tool_call_id': f'c{n}', 'content': result})
    return messages


class NotesTests(unittest.TestCase):
    def test_earlier_replies_empty_reads_and_errors_add_no_notes(self):
        messages = [{'role': 'user', 'content': 'old'},
                    {'role': 'tool', 'tool_call_id': 'old', 'content': 'An earlier reply found this.'}]
        messages += exchange(('kg_query', {'subject': 'x'}, 'No facts found.'),
                             ('search', {'query': 'x'}, 'Workspace: w. Search evidence; verify live sources before edits.\n'
                                                        'No relevant matches. Read the relevant files directly if needed.'),
                             ('file_chunks', {}, '[tool error] source is required'),
                             ('status', {}, '[research stopped] This call was not executed because the time budget ended.'))
        self.assertEqual(chat_research.notes(messages), [])

    def test_other_tools_become_one_note_named_by_their_subject(self):
        (note,) = chat_research.notes(exchange(('file_summary', '{"source": "api.py"}', 'api.py: 4 chunks, the HTTP routes.')))
        self.assertEqual((note.label, note.text), ('file_summary api.py', 'api.py: 4 chunks, the HTTP routes.'))

    def test_long_notes_end_at_a_word_and_the_digest_keeps_whole_notes_within_its_size(self):
        result = '\n'.join(f'[{n}] id:m{n} sim:0.5 | source:s{n}.md\n' + 'word ' * 400 for n in range(1, 30))
        found = chat_research.notes(exchange(('search', {}, result)), per_note=100)
        self.assertEqual(len(found), 29)
        self.assertTrue(all(len(note.text) <= 102 and note.text.endswith('word …') for note in found))
        text = chat_research.digest(found, max_chars=600)
        self.assertLessEqual(len(text), 600)
        self.assertTrue(text.startswith('[1] s1.md\n') and text.endswith('word …'))

    def test_sources_lists_only_the_notes_an_answer_cites(self):
        found = [chat_research.Note('a.md', 'A', 'a'), chat_research.Note('b.md', 'B', 'b')]
        self.assertEqual(chat_research.sources('Yes [2], see [2] and [7].', found), '- [2] b.md')
        self.assertEqual(chat_research.sources('No citations.', found), '')


if __name__ == '__main__':
    unittest.main()
