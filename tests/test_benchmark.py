"""Measurement regressions: failures, accounting, pairing and ranking units."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from benchmark.analysis import aggregate, interval, metrics, paired_runs, percentile, retrieval_metrics
from benchmark.corpus import passages


class BenchmarkStatisticsTests(unittest.TestCase):
    def result(self):
        return {'subtype': 'success', 'modelUsage': {
            'primary': {'inputTokens': 10, 'outputTokens': 20, 'cacheCreationInputTokens': 30, 'cacheReadInputTokens': 40, 'costUSD': .2},
            'secondary': {'inputTokens': 1, 'outputTokens': 2, 'cacheCreationInputTokens': 3, 'cacheReadInputTokens': 4, 'costUSD': .02}},
            'total_cost_usd': .22, 'num_turns': 2, 'duration_ms': 100}

    def trial(self, pid, mode, repeat, tokens, status='success'):
        return {'prompt_id': pid, 'mode': mode, 'repeat': repeat, 'status': status,
                'metrics': {'total_tokens': tokens, 'cost': tokens/1000}}

    def test_all_models_and_all_cache_token_kinds_are_counted(self):
        value = metrics(self.result())
        self.assertEqual(value['total_tokens'], 110)
        self.assertEqual(sum(value[k] for k in ('input_tokens','output_tokens','cache_create','cache_read')), 110)
        self.assertEqual(value['cost'], .22)

    def test_usage_fallback_requires_cost_and_actual_usage(self):
        self.assertEqual(metrics({'subtype':'success', 'usage':{'input_tokens':10,'output_tokens':5}, 'total_cost_usd':0})['total_tokens'], 15)
        for result in ({'subtype':'success'}, {'subtype':'success','usage':{}}, {'subtype':'success','usage':{'input_tokens':10,'output_tokens':5}}):
            with self.assertRaises(ValueError):
                metrics(result)

    def test_failed_or_nonfinite_usage_cannot_look_like_savings(self):
        for key, value in [('is_error', True), ('subtype', 'error_max_budget_usd')]:
            result = self.result(); result[key] = value
            with self.assertRaises(ValueError): metrics(result)
        result = self.result(); result['total_cost_usd'] = float('nan')
        with self.assertRaises(ValueError): metrics(result)

    def test_pairing_excludes_both_sides_of_a_failed_or_missing_pair(self):
        rows = [self.trial('a','off',0,100), self.trial('a','on',0,50),
                self.trial('a','off',1,100), self.trial('a','on',1,0,'failed'), self.trial('b','off',0,200)]
        pairs = paired_runs(rows)
        self.assertEqual(len(pairs),1)
        self.assertEqual(aggregate(pairs)['tokenChangePct'],-50)
        with self.assertRaises(ValueError): paired_runs(rows+[copy.deepcopy(rows[0])])

    def test_aggregate_sums_trials_instead_of_medians_or_average_percentages(self):
        rows = [self.trial('a',mode,rep,tokens) for rep,values in enumerate([(10,20),(100,50),(1000,500)])
                for mode,tokens in zip(('off','on'),values)]
        result = aggregate(paired_runs(rows))
        self.assertEqual(result['tokensOff'],1110)
        self.assertEqual(result['tokensOn'],570)
        self.assertAlmostEqual(result['tokenChangePct'],(570/1110-1)*100)

    def test_bootstrap_is_paired_and_deterministic(self):
        rows=[self.trial(pid,mode,rep,tokens) for pid in ('a','b') for rep in range(3) for mode,tokens in [('off',100),('on',50)]]
        pairs=paired_runs(rows)
        self.assertEqual(interval(pairs,'total_tokens'),[-50,-50])
        self.assertEqual(interval(pairs,'total_tokens'),interval(pairs,'total_tokens'))
        self.assertIsNone(interval([], 'total_tokens'))

    def test_percentiles_interpolate_and_absent_data_stays_unknown(self):
        self.assertEqual(percentile([0,100],.95),95)
        self.assertIsNone(percentile([],.5))
        self.assertIsNone(aggregate([])['tokenChangePct'])

    def test_duplicate_chunks_do_not_inflate_source_recall_or_ndcg(self):
        score=retrieval_metrics(['a','a','a','b'],{'a':3,'b':2},3)
        self.assertEqual(score['recall'],.5)
        self.assertLess(score['ndcg'],1)
        self.assertEqual(score['reciprocal_rank'],1)
        self.assertEqual(retrieval_metrics(['x','b'],{'b':3},10)['reciprocal_rank'],.5)
        self.assertIsNone(retrieval_metrics(['x'],{},5)['recall'])

    def test_passages_are_deterministic_bounded_and_keep_long_lines(self):
        text='z'*6000+'\n'+'next line\n'*200
        first=list(passages(text))
        self.assertEqual(first,list(passages(text)))
        self.assertTrue(all(0<len(row)<=1980 for row in first))
        self.assertTrue(any('next line' in row for row in first))

    def test_public_fixture_labels_reference_actual_snapshot_files(self):
        root=Path(__file__).resolve().parents[1]
        from benchmark.corpus import snapshot
        with tempfile.TemporaryDirectory() as directory:
            corpus=snapshot(root,'HEAD',directory)
            available={file['path'] for file in corpus['files']}
            for case in json.loads((root/'benchmark/retrieval.json').read_text()):
                self.assertTrue(case.get('relevance',{}).keys()<=available,case['id'])

    def test_cli_trace_requires_connection_and_preserves_accounting_and_isolation(self):
        from benchmark.run import trial
        with tempfile.TemporaryDirectory() as directory:
            raw=Path(directory)
            state={'snapshot':directory,'env':{'IMPRINT_DATA_DIR':directory},'python':'/venv/bin/python'}
            prompt={'id':'fixture','category':'information','prompt':'A question','checks':['answer']}
            args=SimpleNamespace(model='fixture',trial_budget=.5,timeout=10)
            def fake(argv, **kwargs):
                self.assertNotIn('--safe-mode',argv)
                self.assertIn('--restricted',argv)
                settings=json.loads(argv[argv.index('--settings')+1])
                self.assertFalse(settings['autoMemoryEnabled'])
                config=json.loads(argv[argv.index('--mcp-config')+1])
                self.assertEqual(config['mcpServers']['imprint']['command'],'/venv/bin/python')
                events=[{'type':'system','subtype':'init','model':'fixture','tools':['Read','mcp__imprint__search'],
                         'mcp_servers':[{'name':'imprint','status':'connected'}]},
                        {'type':'assistant','message':{'content':[{'type':'tool_use','name':'mcp__imprint__search'}]}},
                        {**self.result(),'type':'result','result':'An answer'}]
                kwargs['stdout'].write('\n'.join(json.dumps(e) for e in events))
                return 0,'','',False
            with patch('benchmark.run.run_process',side_effect=fake):
                row=trial(raw,prompt,'on',0,args,state)
            self.assertEqual(row['status'],'success')
            self.assertEqual(row['mcpCalls'],1)
            self.assertEqual(row['metrics']['total_tokens'],110)
            self.assertEqual(row['referenceCoverage'],1)
            self.assertIn('recordedAt',row)


if __name__=='__main__':
    unittest.main()
