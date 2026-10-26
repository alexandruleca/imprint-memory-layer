"""recall: batched reads, budgets, workspace scope and the optional local model."""
import asyncio
import json
import os
import threading
import time
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from imprint import recall, server


def memory(mid, text='', similarity=.7, project='alpha', source=None):
    return {'id': mid, 'content': text or f'{mid} reconnect retry cancellation ' * 40, 'similarity': similarity,
            'project': project, 'type': 'decision', 'source': source or f'src/{mid}.py', 'source_hash': 'b' * 64,
            'recorded_device': {'id': 'dev', 'name': 'laptop'},
            'path_context': {'original_source_path': f'/repo/src/{mid}.py', 'path_status': 'mapped'}}


class Store:
    """The data layer under the read tools, recording each call's workspace."""

    def __init__(self):
        self.workspaces = []
        self.search_results = {}

    def search(self, query, **kw):
        self.workspaces.append(kw['workspace'])
        return [dict(row) for row in self.search_results.get(query, [memory('m1'), memory('m2')])]

    def get_memory(self, memory_id, workspace=None):
        self.workspaces.append(workspace)
        if memory_id == 'missing':
            return {'error': 'not found'}
        row = memory(memory_id, 'full text of ' + memory_id + ' ' + 'detail ' * 600)
        del row['similarity']
        return row

    def query(self, subject='', predicate='', limit=20, workspace=None):
        self.workspaces.append(workspace)
        return [{'id': 7, 'subject': subject or 'api', 'predicate': 'uses', 'object': 'qdrant', 'ended': None,
                 'source': 'notes.md', 'recorded_device': {'name': 'laptop'}}]

    def list_sources(self, project='', lang='', layer='', limit=50, workspace=None):
        self.workspaces.append(workspace)
        return [('src/a.py', 3), ('src/b.py', 1)]

    def facet_counts(self, key, limit=10, workspace=None):
        self.workspaces.append(workspace)
        return [('alpha', 4), ('beta', 2)]

    def patches(self, stack):
        stack.enter_context(patch.object(server, '_validate_ws', return_value=('team', None)))
        stack.enter_context(patch.object(server.vs, 'search', side_effect=self.search))
        stack.enter_context(patch('imprint.cli_viz.get_memory', side_effect=self.get_memory))
        stack.enter_context(patch.object(server.kg, 'query', side_effect=self.query))
        stack.enter_context(patch.object(server.vs, 'list_sources', side_effect=self.list_sources))
        stack.enter_context(patch.object(server.vs, 'facet_counts', side_effect=self.facet_counts))
        stack.enter_context(patch.dict(os.environ, {'IMPRINT_RECALL_LLM': '0'}))


class RecallTestCase(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.stack = ExitStack()
        self.store.patches(self.stack)
        self.addCleanup(lambda: self.stack.close())  # the stack a test ends with

    def model(self, *replies, delay=0.0):
        """A local model answering each request with the next reply."""
        replies = list(replies)
        calls = []

        def generate(provider, prompt, max_tokens, schema, deadline):
            calls.append({'prompt': prompt, 'schema': schema})
            if delay:
                time.sleep(delay)
            reply = replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply
        self.stack.enter_context(patch.object(recall, 'local_model', return_value=('local', '')))
        self.stack.enter_context(patch.object(recall, '_generate', side_effect=generate))
        return calls


class PlanValidationTests(unittest.TestCase):
    def test_steps_take_the_tools_own_arguments_and_reject_everything_else(self):
        steps, errors = recall.validate_plan([
            {'tool': 'search', 'args': {'query': 'retry', 'limit': '3'}},
            {'tool': 'store', 'args': {'content': 'x'}},
            {'tool': 'search', 'args': {'query': ''}},
            {'tool': 'kg_query', 'args': {'subject': 'api', 'workspace': 'other'}},
            {'tool': 'search', 'args': {'query': 'retry', 'limit': 3}},
            {'tool': 'search', 'args': {'query': 'retry', 'detail': 'full'}},
            {'tool': 'search', 'args': {'query': 'retry', 'mode': 'fuzzy'}},
            'search retry',
        ])
        self.assertEqual([(tool, args['query'], args['limit']) for tool, args in steps], [('search', 'retry', 3)])
        self.assertEqual(len(errors), 6)
        self.assertIn('step 2', errors[0])
        self.assertIn('workspace', errors[2])
        self.assertIn('detail', errors[3])
        self.assertEqual(recall.validate_plan('search retry'), ([], ['plan must be a list of steps']))

    def test_every_read_tool_and_only_read_tools_can_be_planned(self):
        self.assertEqual(set(recall.READS), {'search', 'get_memory', 'neighbors', 'file_chunks', 'file_summary',
                                             'list_sources', 'kg_query'})
        for tool, names in recall.PLANNER_ARGS.items():
            self.assertLessEqual(set(names), set(recall.ARGUMENTS[tool].model_fields))
            self.assertNotIn('workspace', recall.ARGUMENTS[tool].model_fields)
        schema = json.dumps(recall._plan_schema())
        for absent in ('workspace', 'store', 'delete', 'kg_edit', '"type"', 'maxLength'):
            self.assertNotIn(absent, schema.replace('"type": "', ''))


class BatchTests(RecallTestCase):
    def test_invalid_caller_plan_runs_nothing(self):
        text = server.recall('q', plan=[{'tool': 'search', 'args': {'query': 'a'}}, {'tool': 'delete', 'args': {}}])
        self.assertTrue(text.startswith('Error: invalid plan: step 2'))
        self.assertEqual(self.store.workspaces, [])
        too_many = [{'tool': 'search', 'args': {'query': str(i)}} for i in range(recall.MAX_STEPS + 1)]
        self.assertIn('at most 8', server.recall('q', plan=too_many))
        self.assertIn('Error', server.recall('q', plan=[]))
        self.assertIn('Error', server.recall('q', max_chars=20000))

    def test_independent_steps_run_concurrently(self):
        barrier = threading.Barrier(2, timeout=5)

        def search(query, **kw):
            barrier.wait()  # deadlocks unless both searches run at once
            return [memory('m-' + query)]
        with patch.object(server.vs, 'search', side_effect=search):
            text = server.recall('q', plan=[{'tool': 'search', 'args': {'query': 'a'}},
                                            {'tool': 'search', 'args': {'query': 'b'}}])
        self.assertIn('1. search(query="a") → 1 result', text)
        self.assertIn('2. search(query="b") → 1 result', text)
        self.assertNotIn('error', text)

    def test_results_merge_by_id_keeping_the_fullest_content(self):
        text = server.recall('detail', max_chars=12000, plan=[
            {'tool': 'search', 'args': {'query': 'retry'}},
            {'tool': 'get_memory', 'args': {'memory_id': 'm1'}},
            {'tool': 'get_memory', 'args': {'memory_id': 'missing'}},
            {'tool': 'kg_query', 'args': {'subject': 'api'}},
            {'tool': 'list_sources', 'args': {'project': 'alpha'}},
        ])
        self.assertEqual(text.count('id:m1 '), 1)
        self.assertIn('full text of m1', text)
        self.assertIn('2. get_memory(memory_id="m1") → 1 result', text)
        self.assertIn('3. get_memory(memory_id="missing") → no results', text)
        self.assertIn('fact:7 api → uses → qdrant', text)
        self.assertIn('file:src/a.py | chunks:3', text)
        self.assertIn('recorded on: laptop', text)
        self.assertIn('path status: mapped', text)
        # Each step's best result comes before any step's second result.
        self.assertLess(text.index('fact:7'), text.index('id:m2 '))

    def test_file_and_neighbor_reads_join_the_same_evidence(self):
        chunks = [{**memory('m1', 'chunk zero of the file ' * 30), 'chunk_index': 0},
                  {**memory('c1', 'chunk one of the file ' * 30), 'chunk_index': 1}]
        for chunk in chunks:
            del chunk['similarity']
        summary = {'source': 'src/m1.py', 'project': 'alpha', 'chunk_count': 2, 'first_chunk_preview': 'chunk zero',
                   'tags': {'lang': 'python', 'layer': 'api', 'domain': ['retry']}}
        neighbors = {'source': 'm1', 'neighbors': [{'id': 'n1', 'similarity': .5, 'project': 'beta', 'type': 'pattern',
                                                    'source': 'lib/n1.py', 'content': 'neighbor preview'}]}
        with patch.object(server.vs, 'get_chunks_by_source', return_value=chunks) as read, \
             patch.object(server.vs, 'get_source_summary', return_value=summary), \
             patch('imprint.cli_viz.get_neighbors', return_value=neighbors) as near:
            text = server.recall('file', max_chars=12000, plan=[
                {'tool': 'search', 'args': {'query': 'retry'}},
                {'tool': 'file_chunks', 'args': {'source': 'src/m1.py', 'start': 0, 'end': 1}},
                {'tool': 'file_summary', 'args': {'source': 'src/m1.py'}},
                {'tool': 'neighbors', 'args': {'id': 'chunk:m1', 'k': 3}}])
        self.assertEqual(read.call_args.kwargs['workspace'], 'team')
        self.assertEqual(near.call_args.args[0], 'm1')
        self.assertEqual(text.count('id:m1 '), 1)
        self.assertIn('id:c1', text)
        self.assertIn('file:src/m1.py | chunks:2 | project:alpha | #python #api #retry', text)
        self.assertIn('id:n1 sim:0.500 | project:beta', text)
        self.assertIn('2. file_chunks(source="src/m1.py", end=1) → 2 results', text)
        with patch.object(server.vs, 'get_chunks_by_source') as read:
            text = server.recall('file', plan=[{'tool': 'file_chunks', 'args': {'source': 'x', 'start': 5, 'end': 2}}])
        self.assertIn('error: end must be greater than or equal to start', text)
        read.assert_not_called()

    def test_a_failing_step_does_not_hide_the_others(self):
        with patch.object(server.kg, 'query', side_effect=RuntimeError('graph offline')):
            text = server.recall('q', plan=[{'tool': 'kg_query', 'args': {'subject': 'api'}},
                                            {'tool': 'search', 'args': {'query': 'retry'}}])
        self.assertIn('1. kg_query(subject="api") → error: graph offline', text)
        self.assertIn('id:m1 ', text)

    def test_one_budget_bounds_every_step_and_counts_hidden_results(self):
        self.store.search_results = {q: [memory(f'{q}{i}') for i in range(8)] for q in 'abcd'}
        plan = [{'tool': 'search', 'args': {'query': q}} for q in 'abcd']
        for budget in (1200, 2500, 4000, 8000, 12000):
            text = server.recall('retry', plan=plan, max_chars=budget)
            self.assertLessEqual(len(text), budget)
            shown = text.count(' sim:0.700')
            self.assertGreater(shown, 0)
            if shown < 32:
                self.assertIn(f'{32 - shown} more results not shown', text)
        wide = server.recall('retry', plan=plan[:1], max_chars=12000)
        self.assertIn('id:a0 ', wide)
        self.assertNotIn('more results not shown', wide)
        for block in wide.split('\n\n')[1:-1]:
            excerpt = block.split('\n')[1]
            self.assertGreaterEqual(len(excerpt), recall.MIN_WIDTH)

    def test_without_a_plan_or_model_recall_is_exactly_search(self):
        self.assertEqual(server.recall('reconnect retry'), server.search('reconnect retry'))
        self.assertEqual(server.recall('reconnect retry', max_chars=2000),
                         server.search('reconnect retry', max_chars=2000))

    def test_steps_and_the_planner_stay_in_the_recall_workspace(self):
        calls = self.model(json.dumps({'steps': [
            {'tool': 'kg_query', 'args': {'subject': 'api'}},
            {'tool': 'search', 'args': {'query': 'x', 'workspace': 'other'}}]}), 'Uses qdrant [1].')
        with patch.object(server, '_validate_ws', return_value=('alpha', None)):
            text = server.recall('what does api use', workspace='alpha')
            server.recall('q', workspace='alpha', plan=[{'tool': 'search', 'args': {'query': 'a'}},
                                                         {'tool': 'list_sources', 'args': {}}], condense=False)
        self.assertEqual(set(self.store.workspaces), {'alpha'})
        self.assertIn('Ignored 1 invalid step', text)
        self.assertIn('alpha, beta', calls[0]['prompt'])
        rejected = server.recall('q', plan=[{'tool': 'search', 'args': {'query': 'a', 'workspace': 'other'}}])
        self.assertIn('Extra inputs are not permitted', rejected)


class LocalModelTests(RecallTestCase):
    def test_the_model_adds_steps_to_the_search_and_answers_with_citations(self):
        calls = self.model(json.dumps({'steps': [{'tool': 'kg_query', 'args': {'subject': 'api'}},
                                                 {'tool': 'search', 'args': {'query': 'what does api use'}}]}),
                           'The api uses qdrant [2], see also [1].')
        text = server.recall('what does api use')
        self.assertIn('Plan (local model,', text)
        self.assertIn('1. search(query="what does api use") → 2 results', text)
        self.assertIn('2. kg_query(subject="api") → 1 result', text)
        self.assertNotIn('3.', text.split('Answer')[0])  # the model's copy of the baseline is dropped
        self.assertIn('The api uses qdrant [2], see also [1].', text)
        self.assertIn('[2] fact:7', text)
        self.assertIn('Also consulted: [3] m2', text)
        self.assertNotIn('reconnect retry cancellation', text)  # excerpts stay out of the answer view
        self.assertLessEqual(len(text), 4000)
        self.assertIsNotNone(calls[0]['schema'])
        self.assertIsNone(calls[1]['schema'])
        self.assertIn('[1] id:m1', calls[1]['prompt'])

    def test_a_given_plan_is_condensed_unless_the_caller_opts_out(self):
        self.model('Retry is decided [1].')
        plan = [{'tool': 'search', 'args': {'query': 'retry'}}]
        self.assertIn('Retry is decided [1].', server.recall('retry?', plan=plan))
        evidence = server.recall('retry?', plan=plan, condense=False)
        self.assertIn('Recall evidence', evidence)
        self.assertNotIn('Answer', evidence)

    def test_planning_timeout_returns_the_search_with_a_note(self):
        self.model('{"steps": []}', delay=3)
        with patch.dict(os.environ, {'IMPRINT_RECALL_LLM_TIMEOUT': '1'}):
            started = time.monotonic()
            text = server.recall('reconnect retry')
        self.assertLess(time.monotonic() - started, 2.5)
        note = 'Local model could not plan (timed out after 1 s); ran the question as one search.\n'
        self.assertTrue(text.startswith(note))
        self.assertEqual(text[len(note):], recall.compact_search(
            server.search_rows('reconnect retry', workspace='team'), 'reconnect retry', 'team', 0, 5,
            4000 - len(note), server.RELEVANCE_THRESHOLD))

    def test_invalid_plans_fall_back_to_the_search(self):
        for reply in ('not json', '{"steps": [{"tool": "delete", "args": {"memory_id": "m1"}}]}',
                      '{"plan": []}', RuntimeError('local model m.gguf is not downloaded yet')):
            self.model(reply)
            text = server.recall('reconnect retry')
            self.assertTrue(text.startswith('Local model could not plan ('), text[:120])
            self.assertIn('ran the question as one search', text)
            self.assertIn('id:m1 ', text)
            self.stack.close()
            self.stack = ExitStack()
            self.store.patches(self.stack)

    def test_answer_timeout_or_untraceable_answer_returns_the_evidence(self):
        plan = [{'tool': 'search', 'args': {'query': 'retry'}}]
        for reply, reason in [('Retry is decided.', 'did not cite'), ('Retry [9].', 'did not cite'),
                              (TimeoutError(), 'timed out after 30 s')]:
            self.model(reply)
            text = server.recall('retry?', plan=plan)
            self.assertIn(f'Local model did not answer (', text)
            self.assertIn(reason, text)
            self.assertIn('Recall evidence', text)
            self.assertIn('reconnect retry cancellation', text)
            self.assertLessEqual(len(text), 4000)

    def test_the_deadline_bounds_the_whole_recall(self):
        self.model(json.dumps({'steps': [{'tool': 'kg_query', 'args': {'subject': 'api'}}]}), 'late [1]', delay=0.7)
        with patch.dict(os.environ, {'IMPRINT_RECALL_LLM_TIMEOUT': '1'}):
            started = time.monotonic()
            text = server.recall('what does api use')
        self.assertLess(time.monotonic() - started, 2)
        self.assertIn('Plan (local model,', text)
        self.assertIn('did not answer (timed out after 1 s)', text)


class LocalInferenceSwitchTests(unittest.TestCase):
    def setting(self, **env):
        base = {'IMPRINT_RECALL_LLM': '1', 'IMPRINT_LLM_TAGGER_BASE_URL': ''}
        return patch.dict(os.environ, {**base, **env})

    def test_only_a_model_on_this_computer_counts(self):
        with patch.dict(os.environ, {'IMPRINT_RECALL_LLM': '0', 'IMPRINT_LLM_TAGGER_PROVIDER': 'local'}):
            self.assertEqual(recall.local_model(), ('', ''))
        with self.setting(IMPRINT_LLM_TAGGER_PROVIDER='local'):
            self.assertEqual(recall.local_model(), ('local', ''))
        for provider, url in [('ollama', ''), ('vllm', 'http://127.0.0.1:9000/v1'), ('ollama', 'http://[::1]:11434/v1')]:
            with self.setting(IMPRINT_LLM_TAGGER_PROVIDER=provider, IMPRINT_LLM_TAGGER_BASE_URL=url):
                self.assertEqual(recall.local_model(), (provider, ''))
        for provider, url in [('ollama', 'http://192.168.1.5:11434/v1'), ('vllm', 'http://gpu.example:8000/v1'),
                              ('anthropic', ''), ('openai', ''), ('gemini', '')]:
            with self.setting(IMPRINT_LLM_TAGGER_PROVIDER=provider, IMPRINT_LLM_TAGGER_BASE_URL=url):
                found, note = recall.local_model()
                self.assertEqual(found, '')
                self.assertIn('not a model on this computer', note)

    def test_a_remote_provider_never_receives_the_question(self):
        store = Store()
        with ExitStack() as stack, self.setting(IMPRINT_LLM_TAGGER_PROVIDER='anthropic'):
            store.patches(stack)
            stack.enter_context(patch.dict(os.environ, {'IMPRINT_RECALL_LLM': '1'}))
            generate = stack.enter_context(patch.object(recall, '_generate'))
            text = server.recall('reconnect retry')
            server.recall('reconnect retry', plan=[{'tool': 'search', 'args': {'query': 'a'}}])
        generate.assert_not_called()
        self.assertTrue(text.startswith("recall.llm is on, but tagger.llm_provider 'anthropic'"))


class RegistrationTests(unittest.TestCase):
    def test_recall_is_a_read_only_tool_beside_the_unchanged_ones(self):
        tools = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
        self.assertEqual(len(tools), 19)  # read_source joined in ADR 0081
        self.assertIn('recall', tools)
        self.assertTrue(tools['recall'].annotations.read_only_hint)
        self.assertFalse(tools['recall'].annotations.destructive_hint)
        self.assertEqual(set(tools['recall'].parameters['properties']),
                         {'question', 'plan', 'max_chars', 'condense', 'workspace'})
        self.assertIn('one call', tools['recall'].description.lower())
        for name in recall.READS:
            self.assertIn(name, tools)


if __name__ == '__main__':
    unittest.main()
