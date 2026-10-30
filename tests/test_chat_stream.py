import json
import os
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from imprint import chat


class ProvisioningTests(unittest.TestCase):
    """The first local chat downloads the model; every installation already
    has the binding (ADR 0086)."""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.model = self.tmp / 'model.gguf'
        replacements = {
            'llama_cpp': None, '_llm': None, '_provisioning': None,
            '_is_remote_provider': MagicMock(return_value=False),
            '_resolve_local_model_path': MagicMock(return_value=(self.model, None)),
            '_models_dir': MagicMock(return_value=self.tmp),
        }
        for patcher in [patch.object(chat, name, value) for name, value in replacements.items()] + [llama_binding()]:
            patcher.start()
            self.addCleanup(patcher.stop)

    def download(self, done=None):
        """A download that reports half, then all, of the model and writes it."""
        def run(progress_cb=None):
            progress_cb(5, 10)
            if done:
                done.wait(5)
            progress_cb(10, 10)
            self.model.write_bytes(b'gguf')
            return self.model, None
        return run

    def test_first_local_chat_downloads_the_model_then_answers(self):
        llm = LocalModel(['Hello.'])
        with patch.object(chat, 'download_model', side_effect=self.download()), \
             patch.object(chat, 'load_model', return_value=(llm, None)), \
             patch.dict(os.environ, {'IMPRINT_CHAT_TOOL_FORMAT': 'none'}):
            events = list(chat.stream_reply([], 'hi'))
        kinds = [event['type'] for event in events]
        self.assertEqual(kinds, ['download_start', 'download_progress', 'download_progress', 'download_complete',
                                 'load_start', 'token', 'assistant_message', 'done'])
        self.assertEqual((events[1]['downloaded'], events[1]['total']), (5, 10))

    def test_concurrent_chats_follow_one_download(self):
        release = threading.Event()
        with patch.object(chat, 'download_model', side_effect=self.download(release)) as download:
            first, second = chat._provision(), chat._provision()
            self.assertIs(first, second)
            seen = []
            follower = threading.Thread(target=lambda: seen.extend(second.follow()))
            follower.start()
            release.set()
            events = list(first.follow())
            follower.join(5)
        download.assert_called_once()
        self.assertEqual(events, seen)
        self.assertEqual([e['type'] for e in events], ['download_start', 'download_progress', 'download_progress', 'download_complete'])
        self.assertIsNone(first.error)

    def test_a_failed_download_ends_the_chat_with_its_error(self):
        with patch.object(chat, 'download_model', return_value=(None, 'download failed: connection reset by peer')):
            events = list(chat.stream_reply([], 'hi'))
        self.assertEqual(events[-2], {'type': 'error', 'error': 'download failed: connection reset by peer'})
        self.assertEqual(events[-1], {'type': 'done'})
        # A later chat retries instead of replaying the failed job.
        failed = chat._provisioning
        with patch.object(chat, '_run_provisioning'):
            self.assertIsNot(chat._provision(), failed)

    def test_a_binding_that_does_not_load_is_a_damaged_installation(self):
        self.model.write_bytes(b'gguf')
        with patch.object(chat, 'llama_cpp', None), patch.object(chat, '_llama', return_value=None), \
             patch.object(chat, '_llama_import_error', 'libllama.so: cannot open shared object file'):
            status = chat.status()
            events = list(chat.stream_reply([], 'hi'))
        self.assertFalse(status['installed'])
        self.assertIn('libllama.so', status['error'])
        self.assertIn('imprint bootstrap', events[-2]['error'])
        self.assertEqual(events[-1], {'type': 'done'})

    def test_status_reports_a_missing_model_without_an_error(self):
        status = chat.status()
        self.assertFalse(status['model_ready'])
        self.assertIsNone(status['error'])

    def test_chat_is_always_available_even_with_a_stale_chat_enabled(self):
        """chat.enabled was removed; a config.json still holding it loads and changes nothing."""
        from imprint import config_schema
        (self.tmp / 'config.json').write_text(json.dumps({'chat': {'enabled': False, 'provider': 'local'}}))
        with patch.dict(os.environ, {'IMPRINT_DATA_DIR': str(self.tmp)}):
            self.assertNotIn('chat.enabled', [row[0] for row in config_schema.resolve_all()])
            self.assertEqual(config_schema.resolve('chat.provider'), ('local', 'config'))
            status = chat.status()
        self.assertNotIn('enabled', status)
        self.assertEqual(status['provider'], 'local')


def text_chunk(content):
    """A streamed OpenAI-compatible chunk carrying reply text."""
    return types.SimpleNamespace(choices=[types.SimpleNamespace(
        delta=types.SimpleNamespace(content=content, tool_calls=None))])


def call_chunk(index, name=None, arguments=None, id=None):
    """A streamed OpenAI-compatible tool_call delta."""
    piece = types.SimpleNamespace(index=index, id=id, function=types.SimpleNamespace(name=name, arguments=arguments))
    return types.SimpleNamespace(choices=[types.SimpleNamespace(
        delta=types.SimpleNamespace(content=None, tool_calls=[piece]))])


class ProviderStream:
    """A provider's streamed response, closed when the request ends."""
    def __init__(self, chunks):
        self.chunks, self.closed = chunks, False

    def __iter__(self):
        return iter(self.chunks)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True


def remote_provider(*turns):
    """Patches for a remote provider whose successive requests stream these chunks."""
    client = MagicMock()
    client.streams = [ProviderStream(chunks) for chunks in turns]
    client.chat.completions.create.side_effect = client.streams
    return client, [patch.object(chat, '_is_remote_provider', return_value=True),
                    patch.object(chat, '_get_chat_provider', return_value='ollama'),
                    patch.object(chat, '_get_openai_client', return_value=client)]


EOG = -1
# The llama_cpp names local_llm.generate uses, for a stand-in binding.
def llama_binding():
    """Patch in a stand-in llama_cpp binding: end-of-generation and grammars."""
    module = types.ModuleType('llama_cpp')
    module.llama_vocab_is_eog = lambda vocab, token: token == EOG
    module.LlamaGrammar = types.SimpleNamespace(from_json_schema=lambda schema, verbose: ('grammar', json.loads(schema)))
    return patch.dict(sys.modules, {'llama_cpp': module})


class LocalModel:
    """A loaded GGUF whose successive generations stream these pieces, one
    token each. Its template renders the messages and tools as JSON."""
    def __init__(self, *turns, eos_token='<eos>'):
        self.turns, self.requests = iter(turns), []
        self._model = types.SimpleNamespace(vocab='vocab')
        self._imprint_formatter = lambda messages, tools=None: types.SimpleNamespace(
            prompt=json.dumps({'messages': messages, 'tools': tools}))
        self._imprint_formatter.eos_token = eos_token

    def tokenize(self, text, **kwargs):
        self.prompt = json.loads(text)
        return [0]

    def n_ctx(self):
        return 8192

    def generate(self, tokens, **sampling):
        self.requests.append({**self.prompt, **sampling})
        self.pieces = [piece if isinstance(piece, bytes) else piece.encode() for piece in next(self.turns)]
        yield from range(len(self.pieces))
        yield EOG

    def detokenize(self, tokens, special=False):
        assert special, 'special tokens are decoded as text'
        return self.pieces[tokens[0]]


def tool(name, run):
    """The registered tool with its implementation replaced."""
    return {name: chat.TOOLS[name]._replace(run=run)}


class RegistryTests(unittest.TestCase):
    def test_every_schema_matches_the_function_that_runs(self):
        import inspect
        for name, entry in chat.TOOLS.items():
            signature = inspect.signature(entry.run)
            signature.bind(workspace='', **{key: None for key in entry.params})
            signature.bind(workspace='', **{key: None for key in entry.required})
            self.assertLessEqual(set(entry.required), set(entry.params), name)


class LocalProtocolTests(unittest.TestCase):
    def _reply(self, model, prior=(), tools=None, tool_format='qwen-xml'):
        with patch.object(chat, '_is_remote_provider', return_value=False), \
             patch.object(chat, '_llama', return_value=object()), \
             patch.object(chat, '_model_missing', return_value=False), \
             patch.object(chat, '_llm', model), \
             patch.object(chat, 'load_model', return_value=(model, None)), \
             patch.dict(os.environ, {'IMPRINT_CHAT_TOOL_FORMAT': tool_format}), \
             patch.dict(chat.TOOLS, tools or {}), llama_binding():
            return list(chat.stream_reply(list(prior), 'hi'))

    def test_calls_in_template_markup_run_with_schema_typed_arguments(self):
        run = Mock(return_value='chunk two')
        model = LocalModel(['Let me look.\n\n<tool', '_call>\n<function=file_chunks>\n<parameter=source>\nsrc/a.py\n'
                            '</parameter>\n<parameter=start>\n2\n</parameter>\n</function>\n</tool_call>'],
                           ['Done.'])
        events = self._reply(model, tools=tool('file_chunks', run))
        self.assertEqual([e['text'] for e in events if e['type'] == 'token'], ['Let me look.\n\n', 'Done.'])
        self.assertIn({'type': 'tool_call', 'name': 'file_chunks', 'args': {'source': 'src/a.py', 'start': 2}}, events)
        self.assertIn({'type': 'tool_result', 'name': 'file_chunks', 'result': 'chunk two'}, events)
        run.assert_called_once_with(workspace='', source='src/a.py', start=2)
        first, second = model.requests
        self.assertEqual(first['tools'], chat._tool_schemas(False))
        self.assertIn('# Strategy guide', first['messages'][0]['content'])
        # The template renders the call and its result from native messages.
        self.assertEqual(second['messages'][-2:], [
            {'role': 'assistant', 'content': 'Let me look.\n\n', 'tool_calls': [{'id': 'call00000', 'type': 'function',
             'function': {'name': 'file_chunks', 'arguments': {'source': 'src/a.py', 'start': 2}}}]},
            {'role': 'tool', 'tool_call_id': 'call00000', 'content': 'chunk two'},
        ])
        self.assertEqual(events[-2], {'type': 'assistant_message', 'text': 'Let me look.\nDone.'})

    def test_every_call_of_a_turn_runs_and_strings_stay_verbatim(self):
        store, ingest = Mock(return_value='stored'), Mock(return_value='fetched')
        note = 'First line.\n\n    indented: 5\ntrue'
        model = LocalModel([f'<tool_call>\n<function=store>\n<parameter=content>\n{note}\n</parameter>\n</function>\n</tool_call>\n'
                            '<tool_call>\n<function=ingest_url>\n<parameter=url>\nhttps://example.com\n</parameter>\n'
                            '<parameter=force>\ntrue\n</parameter>\n</function>\n</tool_call>'], ['Saved both.'])
        events = self._reply(model, tools={**tool('store', store), **tool('ingest_url', ingest)})
        store.assert_called_once_with(workspace='', content=note)
        ingest.assert_called_once_with(workspace='', url='https://example.com', force=True)
        self.assertEqual([e['type'] for e in events if e['type'] not in ('assistant_message', 'done')],
                         ['tool_call', 'tool_result', 'tool_call', 'tool_result', 'token'])
        self.assertEqual([m['role'] for m in model.requests[1]['messages'][-3:]], ['assistant', 'tool', 'tool'])

    def test_a_value_of_the_wrong_type_is_the_result_the_model_reads(self):
        chunks = Mock()
        model = LocalModel(['<tool_call>\n<function=file_chunks>\n<parameter=source>\na.py\n</parameter>\n'
                            '<parameter=start>\nfirst\n</parameter>\n</function>\n</tool_call>'], ['Sorry.'])
        events = self._reply(model, tools=tool('file_chunks', chunks))
        chunks.assert_not_called()
        self.assertIn({'type': 'tool_result', 'name': 'file_chunks',
                       'result': '[tool error] bad args for file_chunks: start must be integer'}, events)

    def test_a_missing_required_argument_is_the_result_the_model_reads(self):
        search = Mock()
        model = LocalModel(['<tool_call>\n<function=search>\n<parameter=limit>\n10\n</parameter>\n</function>\n</tool_call>'],
                           ['Sorry.'])
        events = self._reply(model, tools=tool('search', search))
        search.assert_not_called()
        error = '[tool error] bad args for search: missing required query'
        self.assertIn({'type': 'tool_result', 'name': 'search', 'result': error}, events)
        self.assertEqual(model.requests[1]['messages'][-1], {'role': 'tool', 'tool_call_id': 'call00000', 'content': error})

    def test_json_inside_tool_call_tags_is_not_the_native_protocol(self):
        search = Mock()
        raw = '<tool_call>{"name": "search", "query": "alex", "limit": 10}</tool_call>'
        events = self._reply(LocalModel([raw]), tools=tool('search', search))
        search.assert_not_called()
        self.assertEqual(''.join(e['text'] for e in events if e['type'] == 'token'), raw)

    def test_held_back_text_that_is_not_a_tag_still_streams(self):
        events = self._reply(LocalModel(['1 <', ' 2']))
        self.assertEqual([e['text'] for e in events if e['type'] == 'token'], ['1 ', '< 2'])

    def test_mistral_calls_open_with_a_special_token(self):
        status = Mock(return_value='7 memories')
        model = LocalModel(['[TOOL_CALLS]', ' [{"name": "status", ', '"arguments": {}}]'], ['Seven.'])
        events = self._reply(model, tools=tool('status', status), tool_format='mistral')
        status.assert_called_once_with(workspace='')
        self.assertEqual([e['text'] for e in events if e['type'] == 'token'], ['Seven.'])
        # Mistral's template requires call ids of nine alphanumerics.
        self.assertEqual(model.requests[1]['messages'][-1]['tool_call_id'], 'call00000')

    def test_llama3_streams_unless_the_reply_opens_as_a_call(self):
        search = Mock(return_value='Alex maintains Imprint.')
        model = LocalModel(['<|python_tag|>', '{"name": "search", ', '"parameters": {"query": "alex", "limit": "10"}}'],
                           ['Use ', '{x}', ' here.'])
        events = self._reply(model, tools=tool('search', search), tool_format='llama3')
        # A number written as a string is typed by the schema, as the history records it.
        search.assert_called_once_with(workspace='', query='alex', limit=10)
        self.assertIn({'type': 'tool_call', 'name': 'search', 'args': {'query': 'alex', 'limit': 10}}, events)
        self.assertEqual(model.requests[1]['messages'][-2]['tool_calls'][0]['function']['arguments'],
                         {'query': 'alex', 'limit': 10})
        self.assertEqual([e['text'] for e in events if e['type'] == 'token'], ['Use ', '{x}', ' here.'])

    def test_without_a_format_auto_chats_without_tools_and_says_so_once(self):
        model = LocalModel(['<tool_call>{"name": "search"}</tool_call>'], ['Hello again.'])
        events = self._reply(model, tool_format='auto')
        notice = [e for e in events if e['type'] == 'notice']
        self.assertEqual(len(notice), 1)
        self.assertIn('chat.tool_format', notice[0]['text'])
        self.assertIsNone(model.requests[0]['tools'])
        self.assertNotIn('# Strategy guide', model.requests[0]['messages'][0]['content'])
        self.assertEqual(events[-2]['text'], '<tool_call>{"name": "search"}</tool_call>')
        later = self._reply(model, prior=[{'role': 'user', 'content': 'hi'}, {'role': 'assistant', 'content': 'x'}],
                            tool_format='auto')
        self.assertNotIn('notice', [e['type'] for e in later])

    def test_none_chats_without_tools_and_without_a_notice(self):
        model = LocalModel(['Hello.'])
        events = self._reply(model, tool_format='none')
        self.assertNotIn('notice', [e['type'] for e in events])
        self.assertIsNone(model.requests[0]['tools'])

    def test_stored_tool_exchanges_replay_as_native_messages(self):
        model = LocalModel(['Still Alex.'])
        prior = [{'role': 'user', 'content': 'who?'},
                 {'role': 'tool', 'content': 'Alex wrote it', 'tool_name': 'search', 'tool_args': {'query': 'author'}},
                 {'role': 'assistant', 'content': 'Alex.'}]
        self._reply(model, prior)
        self.assertEqual(model.requests[0]['messages'][1:], [
            {'role': 'user', 'content': 'who?'},
            {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'hist00001', 'type': 'function',
             'function': {'name': 'search', 'arguments': {'query': 'author'}}}]},
            {'role': 'tool', 'tool_call_id': 'hist00001', 'content': 'Alex wrote it'},
            {'role': 'assistant', 'content': 'Alex.'},
            {'role': 'user', 'content': 'hi'},
        ])


    def test_local_budget_finishes_with_no_schemas_and_suppresses_further_calls(self):
        run = Mock(return_value='Source project.md: the project uses local storage.')
        write = Mock()
        model = LocalModel(['<tool_call><function=status></function></tool_call>'],
                           ['<tool_call><function=store><parameter=content>blocked</parameter></function></tool_call>'])
        with patch.dict(os.environ, {'IMPRINT_CHAT_RESEARCH_TOKENS': '1025'}), patch.object(chat, '_prompt_tokens', return_value=1):
            events = self._reply(model, tools={**tool('status', run), **tool('store', write)})
        run.assert_called_once()
        write.assert_not_called()
        self.assertIsNone(model.requests[-1]['tools'])
        self.assertNotIn('error', [event['type'] for event in events])
        output = ''.join(event['text'] for event in events if event['type'] == 'token')
        self.assertNotIn('<tool_call>', output)
        self.assertIn('project.md', output)


class RemoteProtocolTests(unittest.TestCase):
    def _reply(self, *turns, tools=None, prior=()):
        client, patches = remote_provider(*turns)
        with patches[0], patches[1], patches[2], patch.dict(chat.TOOLS, tools or {}):
            events = list(chat.stream_reply(list(prior), 'hi'))
        return events, [c.kwargs for c in client.chat.completions.create.call_args_list]

    def test_streamed_tool_call_deltas_run_and_return_as_tool_messages(self):
        search = Mock(return_value='Alex is a developer')
        events, requests = self._reply(
            [text_chunk('Checking. '), call_chunk(0, 'search', '{"que', id='call_a'), call_chunk(0, arguments='ry": "alex"}')],
            [text_chunk('Alex '), text_chunk('develops.')],
            tools=tool('search', search))
        search.assert_called_once_with(workspace='', query='alex')
        self.assertEqual([e['text'] for e in events if e['type'] == 'token'], ['Checking. ', 'Alex ', 'develops.'])
        self.assertIn({'type': 'tool_call', 'name': 'search', 'args': {'query': 'alex'}}, events)
        self.assertEqual(requests[0]['tools'], chat._tool_schemas(False))
        self.assertNotIn('stop', requests[0])
        self.assertEqual(requests[1]['messages'][-2:], [
            {'role': 'assistant', 'content': 'Checking. ', 'tool_calls': [{'id': 'call_a', 'type': 'function',
             'function': {'name': 'search', 'arguments': '{"query": "alex"}'}}]},
            {'role': 'tool', 'tool_call_id': 'call_a', 'content': 'Alex is a developer'},
        ])
        self.assertEqual(events[-2], {'type': 'assistant_message', 'text': 'Checking.\nAlex develops.'})

    def test_parallel_calls_each_get_their_result(self):
        status, summary = Mock(return_value='7 memories'), Mock(return_value='3 chunks')
        _, requests = self._reply(
            [call_chunk(0, 'status', '{}', id='a'), call_chunk(1, 'file_summary', '{"source": "x.py"}', id='b')],
            [text_chunk('Done.')],
            tools={**tool('status', status), **tool('file_summary', summary)})
        summary.assert_called_once_with(workspace='', source='x.py')
        self.assertEqual(requests[1]['messages'][-2:], [{'role': 'tool', 'tool_call_id': 'a', 'content': '7 memories'},
                                                        {'role': 'tool', 'tool_call_id': 'b', 'content': '3 chunks'}])
        self.assertEqual(requests[1]['messages'][-3]['content'], '')

    def test_malformed_arguments_end_the_reply_with_an_error(self):
        search = Mock()
        events, _ = self._reply([call_chunk(0, 'search', '{"query": ', id='a')], tools=tool('search', search))
        search.assert_not_called()
        self.assertIn('malformed arguments for search', events[-2]['error'])
        self.assertEqual(events[-1], {'type': 'done'})

    def test_stored_tool_exchanges_replay_with_json_arguments(self):
        prior = [{'role': 'user', 'content': 'who?'},
                 {'role': 'tool', 'content': 'Alex', 'tool_name': 'search', 'tool_args': {'query': 'author'}}]
        _, requests = self._reply([text_chunk('Alex.')], prior=prior)
        self.assertEqual(requests[0]['messages'][2:4], [
            {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'hist00001', 'type': 'function',
             'function': {'name': 'search', 'arguments': '{"query": "author"}'}}]},
            {'role': 'tool', 'tool_call_id': 'hist00001', 'content': 'Alex'},
        ])

    def test_budget_reserves_a_tools_free_answer_with_the_last_results(self):
        search = Mock(side_effect=['first source', 'last source evidence'])
        with patch.dict(os.environ, {'IMPRINT_CHAT_RESEARCH_TOKENS': '2050'}), patch.object(chat, '_prompt_tokens', return_value=1):
            events, requests = self._reply(
                [call_chunk(0, 'search', '{"query":"one"}', id='a')],
                [call_chunk(0, 'search', '{"query":"two"}', id='b')],
                [text_chunk('The sources establish the project history.')], tools=tool('search', search))
        self.assertEqual(search.call_count, 2)
        self.assertEqual(len(requests), 3)
        self.assertIsNone(requests[-1]['tools'])
        self.assertIn('last source evidence', str(requests[-1]['messages']))
        self.assertIn('Answer the original user question', requests[-1]['messages'][0]['content'])
        self.assertNotIn('error', [event['type'] for event in events])
        self.assertEqual(events[-2]['text'], 'The sources establish the project history.')
        self.assertEqual(events[-1], {'type': 'done'})

    def test_new_evidence_continues_beyond_twelve_rounds_and_ignores_legacy_round_cap(self):
        status = Mock(return_value='project overview')
        turns = [[call_chunk(0, 'search', json.dumps({'query': f'part {n}'}), id=f'c{n}'),
                  call_chunk(1, 'status', '{}', id=f's{n}')] for n in range(16)]
        with patch.dict(os.environ, {'IMPRINT_CHAT_MAX_TOOL_ITERS': '6'}), patch.object(chat, '_prompt_tokens', return_value=1):
            events, requests = self._reply(*turns, [text_chunk('Complete project overview.')],
                tools={**tool('search', Mock(side_effect=[f'New source evidence number {n}' for n in range(16)])), **tool('status', status)})
        self.assertEqual(status.call_count, 16)
        self.assertEqual(len([event for event in events if event['type'] == 'tool_call']), 32)
        self.assertEqual(len(requests), 17)
        self.assertIsNotNone(requests[-1]['tools'])
        self.assertEqual(events[-2]['text'], 'Complete project overview.')

    def test_identical_read_only_rounds_finish_early_but_writes_do_not(self):
        with patch.dict(os.environ, {'IMPRINT_CHAT_RESEARCH_PATIENCE': '2'}):
            events, requests = self._reply(
                *[[call_chunk(0, 'status', '{}', id=f'c{n}')] for n in range(3)],
                [text_chunk('Here is the overview.')], tools=tool('status', Mock(return_value='same evidence')))
            self.assertEqual(len(requests), 4)
            self.assertTrue(any('no longer adding new evidence' in event.get('text', '') for event in events))
            write = Mock(return_value='stored')
            _, requests = self._reply(
                *[[call_chunk(0, 'store', '{"content":"note"}', id=f'c{n}')] for n in range(4)],
                [text_chunk('Saved.')], tools=tool('store', write))
        self.assertEqual(write.call_count, 4)
        self.assertEqual(len(requests), 5)
        self.assertIsNotNone(requests[-1]['tools'])

    def test_ignored_no_tools_request_never_executes_more_calls(self):
        write = Mock()
        with patch.dict(os.environ, {'IMPRINT_CHAT_RESEARCH_TOKENS': '1025'}), patch.object(chat, '_prompt_tokens', return_value=1):
            events, _ = self._reply([call_chunk(0, 'status', '{}', id='a')],
                [call_chunk(0, 'store', '{"content":"must not save"}', id='b')],
                tools={**tool('status', Mock(return_value='verified project notes')), **tool('store', write)})
        write.assert_not_called()
        self.assertEqual(len([event for event in events if event['type'] == 'tool_call']), 1)
        self.assertNotIn('error', [event['type'] for event in events])
        self.assertIn('verified project notes', events[-2]['text'])
        self.assertIn('incomplete, without synthesis', events[-2]['text'])

    def test_final_provider_failure_preserves_evidence_and_completes(self):
        client, patches = remote_provider([call_chunk(0, 'status', '{}', id='a')])
        client.chat.completions.create.side_effect = [client.streams[0], RuntimeError('provider disconnected')]
        with patches[0], patches[1], patches[2], patch.dict(os.environ, {'IMPRINT_CHAT_RESEARCH_TOKENS': '1025'}), patch.object(chat, '_prompt_tokens', return_value=1), \
             patch.dict(chat.TOOLS, tool('status', Mock(return_value='project: inventory, source: meeting.txt'))):
            events = list(chat.stream_reply([], 'What is this project?'))
        self.assertNotIn('error', [event['type'] for event in events])
        self.assertIn('meeting.txt', events[-2]['text'])
        self.assertEqual(events[-1], {'type': 'done'})

    def test_partial_final_answer_is_preserved_when_its_stream_disconnects(self):
        def interrupted():
            yield text_chunk('The project has a local database. ')
            raise RuntimeError('connection lost')
        with patch.dict(os.environ, {'IMPRINT_CHAT_RESEARCH_TOKENS': '1025'}), patch.object(chat, '_prompt_tokens', return_value=1):
            events, _ = self._reply([call_chunk(0, 'status', '{}', id='a')], interrupted(),
                tools=tool('status', Mock(return_value='Source architecture.md: SQLite storage.')))
        answer = events[-2]['text']
        self.assertTrue(answer.startswith('The project has a local database.'))
        self.assertIn('architecture.md', answer)
        self.assertNotIn('error', [event['type'] for event in events])

    def test_context_exhaustion_after_tools_still_gets_a_tools_free_answer(self):
        from imprint.chat_analysis import SectionAnalysis
        prepare = SectionAnalysis.prepare
        def full_after_first(analysis, messages, offered):
            if offered() and any(message['role'] == 'tool' for message in messages):
                raise ValueError('section reply limit reached')
            return (yield from prepare(analysis, messages, offered))
        with patch.object(SectionAnalysis, 'prepare', full_after_first):
            events, requests = self._reply([call_chunk(0, 'status', '{}', id='a')],
                [text_chunk('The evidence supports this overview.')], tools=tool('status', Mock(return_value='source notes')))
        self.assertEqual(len(requests), 2)
        self.assertIsNone(requests[-1]['tools'])
        self.assertNotIn('error', [event['type'] for event in events])
        self.assertTrue(any('context is full' in event.get('text', '') for event in events))

    def test_final_context_preparation_failure_returns_evidence_without_more_generation(self):
        from imprint.chat_analysis import SectionAnalysis
        prepare = SectionAnalysis.prepare
        def no_more_context(analysis, messages, offered):
            if any(message['role'] == 'tool' for message in messages):
                raise ValueError('section reply limit reached')
            return (yield from prepare(analysis, messages, offered))
        with patch.object(SectionAnalysis, 'prepare', no_more_context):
            events, requests = self._reply([call_chunk(0, 'status', '{}', id='a')],
                tools=tool('status', Mock(return_value='Source project-plan.txt: important evidence.')))
        self.assertEqual(len(requests), 1)
        self.assertIn('project-plan.txt', events[-2]['text'])
        self.assertNotIn('error', [event['type'] for event in events])

    def test_cancellation_at_research_notice_does_not_start_final_generation(self):
        cancel = threading.Event()
        client, patches = remote_provider([call_chunk(0, 'status', '{}', id='a')])
        with patches[0], patches[1], patches[2], patch.dict(os.environ, {'IMPRINT_CHAT_RESEARCH_TOKENS': '1025'}), patch.object(chat, '_prompt_tokens', return_value=1), \
             patch.dict(chat.TOOLS, tool('status', Mock(return_value='source notes'))):
            events = []
            for event in chat.stream_reply([], 'question', cancel_event=cancel):
                events.append(event)
                if event['type'] == 'notice':
                    cancel.set()
        self.assertEqual(client.chat.completions.create.call_count, 1)
        self.assertNotIn('assistant_message', [event['type'] for event in events])

    def test_time_budget_skips_pending_calls_and_reserves_final_answer(self):
        import time
        from imprint.chat_research import ResearchBudget
        clock = [time.monotonic()]
        def factory(**kwargs):
            return ResearchBudget(**kwargs, clock=lambda: clock[0])
        def slow_read(**kwargs):
            clock[0] += 301
            return 'Source architecture.md: local database.'
        write = Mock()
        with patch.object(chat, 'ResearchBudget', side_effect=factory):
            events, requests = self._reply(
                [call_chunk(0, 'status', '{}', id='a'), call_chunk(1, 'store', '{"content":"must not run"}', id='b')],
                [text_chunk('The project uses a local database.')],
                tools={**tool('status', Mock(side_effect=slow_read)), **tool('store', write)})
        write.assert_not_called()
        self.assertEqual(len(requests), 2)
        self.assertIsNone(requests[-1]['tools'])
        self.assertTrue(any('was not executed' in event.get('result', '') for event in events))
        self.assertIn('architecture.md', str(requests[-1]['messages']))
        self.assertTrue(any('time budget is complete' in event.get('text', '') for event in events))

    def test_alternating_read_cycles_stop_even_with_different_queries(self):
        with patch.dict(os.environ, {'IMPRINT_CHAT_RESEARCH_PATIENCE': '3'}):
            events, requests = self._reply(
                *[[call_chunk(0, 'search', json.dumps({'query': f'different {n}'}), id=f'c{n}')] for n in range(5)],
                [text_chunk('These are the two relevant sources.')],
                tools=tool('search', Mock(side_effect=['Evidence source alpha', 'Evidence source beta',
                                                       'Evidence source alpha', 'Evidence source beta', 'Evidence source alpha'])))
        self.assertEqual(len(requests), 6)
        self.assertIsNone(requests[-1]['tools'])
        self.assertTrue(any('no longer adding new evidence' in event.get('text', '') for event in events))

    def test_different_tool_errors_do_not_keep_research_running(self):
        with patch.dict(os.environ, {'IMPRINT_CHAT_RESEARCH_PATIENCE': '2'}):
            events, requests = self._reply(
                [call_chunk(0, 'search', '{}', id='a')],
                [call_chunk(0, 'file_chunks', '{}', id='b')],
                [text_chunk('I could not retrieve enough evidence to answer.')])
        self.assertEqual(len(requests), 3)
        self.assertIsNone(requests[-1]['tools'])
        self.assertNotIn('error', [event['type'] for event in events])


class ApiRecordingTests(unittest.TestCase):
    def test_stopped_reply_keeps_what_was_streamed(self):
        from imprint import api
        replies = iter([{'type': 'token', 'text': 'Half an '}, {'type': 'token', 'text': 'answer'},
                        {'type': 'token', 'text': ' never seen'}])
        with patch.object(api.chat_mod, 'stream_reply', return_value=replies), \
             patch.object(api.chat_store, 'append_message') as append:
            events = api._chat_events('s1', [], 'hi', 'default')
            next(events), next(events)
            events.close()
        append.assert_called_once_with('s1', role='assistant', content='Half an answer')

    def test_completed_reply_is_recorded_once(self):
        from imprint import api
        replies = iter([{'type': 'token', 'text': 'Done'}, {'type': 'assistant_message', 'text': 'Done'},
                        {'type': 'done'}])
        with patch.object(api.chat_mod, 'stream_reply', return_value=replies), \
             patch.object(api.chat_store, 'append_message') as append:
            list(api._chat_events('s1', [], 'hi', 'default'))
        append.assert_called_once_with('s1', role='assistant', content='Done')

    def test_research_completion_records_the_answer_and_keeps_the_tool_evidence(self):
        from imprint import api
        client, patches = remote_provider([call_chunk(0, 'status', '{}', id='a')], [text_chunk('Project summary.')])
        with patches[0], patches[1], patches[2], patch.dict(os.environ, {'IMPRINT_CHAT_RESEARCH_TOKENS': '1025'}), patch.object(chat, '_prompt_tokens', return_value=1), \
             patch.dict(chat.TOOLS, tool('status', Mock(return_value='project evidence'))), \
             patch.object(api.chat_store, 'append_message') as append:
            events = list(api._chat_events('s1', [], 'Tell me about this project', 'default'))
        self.assertFalse(any('"type": "error"' in event for event in events))
        self.assertEqual([call.kwargs['role'] for call in append.call_args_list], ['tool', 'assistant'])
        self.assertEqual(append.call_args_list[-1].kwargs['content'], 'Project summary.')


class ToolBudgetSettingTests(unittest.IsolatedAsyncioTestCase):
    async def test_resource_settings_are_numeric_bounded_and_persistent(self):
        import httpx
        from imprint import config_schema
        from imprint.api import app
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'IMPRINT_DATA_DIR': directory}):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:8420') as client:
                settings = {row['key']: row for row in (await client.get('/api/config')).json()['settings']}
                self.assertNotIn('chat.max_tool_iters', settings)
                for key, default, changed, invalid in (
                    ('chat.research_timeout_sec', 300, 600, ('0', '9', '3601', '2.5', 'true')),
                    ('chat.research_token_budget', 65536, 131072, ('0', '1023', '2000001', '2.5', 'true')),
                    ('chat.research_no_progress_rounds', 4, 6, ('0', '1', '21', '2.5', 'true')),
                ):
                    self.assertEqual((settings[key]['value'], settings[key]['type']), (default, 'int'))
                    for value in invalid:
                        self.assertEqual((await client.put('/api/config/' + key, json={'value': value})).status_code, 400)
                    self.assertEqual((await client.put('/api/config/' + key, json={'value': str(changed)})).status_code, 200)
                    self.assertEqual(config_schema.resolve(key), (changed, 'config'))
                    self.assertEqual((await client.delete('/api/config/' + key)).status_code, 200)
                    self.assertEqual(config_schema.resolve(key), (default, 'default'))
                with patch.dict(os.environ, {'IMPRINT_CHAT_RESEARCH_TOKENS': '32768'}):
                    self.assertEqual(config_schema.resolve('chat.research_token_budget'), (32768, 'env'))


if __name__ == '__main__':
    unittest.main()
