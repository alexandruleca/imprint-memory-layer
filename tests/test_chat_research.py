import json
import unittest

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


if __name__ == '__main__':
    unittest.main()
