import json
import os
import stat
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from imprint import chat


def _cli(directory: Path, body: str) -> str:
    """A stand-in for `imprint profile add-llm`."""
    script = directory / 'imprint'
    script.write_text('#!/bin/sh\n' + body)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


@unittest.skipIf(os.name == 'nt', 'stand-in CLI is a POSIX shell script')
class ProvisioningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.site = self.tmp / 'site'
        self.site.mkdir()
        self.model = self.tmp / 'model.gguf'
        self.model.write_bytes(b'gguf')
        sys.path.insert(0, str(self.site))
        self.addCleanup(sys.path.remove, str(self.site))
        self.addCleanup(sys.modules.pop, 'llama_cpp', None)
        replacements = {
            'llama_cpp': None, '_llm': None, '_provisioning': None,
            '_is_remote_provider': MagicMock(return_value=False),
            '_resolve_local_model_path': MagicMock(return_value=(self.model, None)),
            '_models_dir': MagicMock(return_value=self.tmp),
        }
        for patcher in [patch.object(chat, name, value) for name, value in replacements.items()] + \
                [patch('imprint._llama_compat.apply')]:
            patcher.start()
            self.addCleanup(patcher.stop)

    def _install_creates_binding(self) -> str:
        package = self.site / 'llama_cpp'
        return _cli(self.tmp, f"printf '\\033[32m[+] Installing llama-cpp-python\\033[0m\\n'\n"
                              f"mkdir -p '{package}' && printf '{BINDING}' > '{package}/__init__.py'\n")

    def test_binding_installed_while_running_is_picked_up(self):
        self.assertIsNone(chat._llama())
        self.assertIn('llama_cpp', chat._llama_import_error)
        (self.site / 'llama_cpp').mkdir()
        (self.site / 'llama_cpp' / '__init__.py').write_text('')
        self.assertEqual(chat._llama().__name__, 'llama_cpp')
        self.assertIsNone(chat._llama_import_error)

    def test_first_local_chat_installs_the_binding_then_answers(self):
        llm = LocalModel(['Hello.'])
        with patch('imprint.queue.find_imprint_binary', return_value=self._install_creates_binding()), \
             patch.object(chat, 'load_model', return_value=(llm, None)), \
             patch.dict(os.environ, {'IMPRINT_CHAT_TOOL_FORMAT': 'none'}):
            events = list(chat.stream_reply([], 'hi'))
        kinds = [event['type'] for event in events]
        self.assertEqual(kinds, ['install_start', 'install_progress', 'install_complete',
                                 'load_start', 'token', 'assistant_message', 'done'])
        self.assertEqual(events[1]['line'], '[+] Installing llama-cpp-python')

    def test_concurrent_chats_follow_one_install(self):
        release, runs = threading.Event(), []

        def install(job):
            runs.append(job)
            job.emit({'type': 'install_progress', 'line': 'working'})
            release.wait(5)
            (self.site / 'llama_cpp').mkdir()
            (self.site / 'llama_cpp' / '__init__.py').write_text('')

        with patch.object(chat, '_install_binding', side_effect=install):
            first, second = chat._provision(), chat._provision()
            self.assertIs(first, second)
            seen = []
            follower = threading.Thread(target=lambda: seen.extend(second.follow()))
            follower.start()
            release.set()
            events = list(first.follow())
            follower.join(5)
        self.assertEqual(len(runs), 1)
        self.assertEqual(events, seen)
        self.assertEqual([e['type'] for e in events], ['install_start', 'install_progress', 'install_complete'])
        self.assertIsNone(first.error)

    def test_failed_install_ends_the_chat_with_its_output(self):
        cli = _cli(self.tmp, "echo 'error: no matching distribution'\nexit 1\n")
        with patch('imprint.queue.find_imprint_binary', return_value=cli):
            events = list(chat.stream_reply([], 'hi'))
        self.assertEqual(events[-2]['type'], 'error')
        self.assertIn('no matching distribution', events[-2]['error'])
        self.assertEqual(events[-1], {'type': 'done'})
        # A later chat retries instead of replaying the failed job.
        failed = chat._provisioning
        with patch.object(chat, '_run_provisioning'):
            self.assertIsNot(chat._provision(), failed)

    def test_status_reports_missing_binding_without_an_error(self):
        status = chat.status()
        self.assertFalse(status['installed'])
        self.assertIsNone(status['error'])


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
BINDING = 'llama_vocab_is_eog = lambda vocab, token: token == -1\\nLlamaGrammar = None\\n'


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


if __name__ == '__main__':
    unittest.main()
