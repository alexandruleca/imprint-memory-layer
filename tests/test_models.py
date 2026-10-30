import tempfile
import json
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

from imprint import config, embeddings, local_llm, tagger


class EncodingTests(unittest.TestCase):
    def test_snapshot_mismatch_fails_before_storage_or_recovery(self):
        from imprint.cli_sync import import_snapshot
        with tempfile.TemporaryDirectory() as directory, patch('imprint.cli_sync.vectorstore._ensure_collection') as storage:
            path = Path(directory)
            (path / 'manifest.json').write_text(json.dumps({'embedding_identity': 'another-profile'}))
            with self.assertRaisesRegex(ValueError, 'profile mismatch'):
                import_snapshot(path)
            storage.assert_not_called()

    def test_pooling_follows_the_model_family_or_the_gguf(self):
        import llama_cpp
        cases = [('auto', 'qwen3', llama_cpp.LLAMA_POOLING_TYPE_LAST), ('auto', 'bge-m3', llama_cpp.LLAMA_POOLING_TYPE_CLS),
                 ('auto', 'custom', llama_cpp.LLAMA_POOLING_TYPE_UNSPECIFIED), ('mean', 'qwen3', llama_cpp.LLAMA_POOLING_TYPE_MEAN)]
        for pooling, family, expected in cases:
            with self.subTest(pooling=pooling, family=family), patch.object(config, 'POOLING', pooling), \
                    patch.object(config, 'MODEL_FAMILY', family):
                self.assertEqual(embeddings._pooling(), expected)

    def test_query_instruction_never_applies_to_documents(self):
        with patch.object(config, 'QUERY_INSTRUCTION', 'Find evidence'), \
             patch.object(embeddings, '_embed_raw', return_value=np.ones((1, 3))) as run:
            embeddings.embed_query('How is auth configured?')
            self.assertEqual(run.call_args.args[0], ['Instruct: Find evidence\nQuery: How is auth configured?'])
            embeddings.embed_document('auth implementation')
            self.assertEqual(run.call_args.args[0], ['auth implementation'])

    class Model:
        """A llama.cpp embedding model whose tokens are characters and whose
        tokenizer appends one end-of-text token."""
        def __init__(self, vectors):
            self.vectors, self.inputs = vectors, []

        def tokenize(self, text, add_bos=True):
            return list(text) + ([0] if add_bos else [])

        def detokenize(self, tokens):
            return bytes(tokens)

        def embed(self, texts, normalize, truncate):
            self.inputs = texts
            return self.vectors

    def embedded(self, model, texts, dim):
        with patch.object(embeddings, '_load', return_value=model), patch.object(embeddings, '_specials', 1), \
                patch.object(config, 'EMBEDDING_DIM', dim), patch.object(config, 'MODEL_FAMILY', 'qwen3'):
            return embeddings._embed_raw(texts)

    def test_matryoshka_projection_and_normalization(self):
        result = self.embedded(self.Model([list(range(1, 65))]), ['text'], 32)
        self.assertEqual(result.shape, (1, 32))
        self.assertAlmostEqual(float(np.linalg.norm(result)), 1, places=6)
        np.testing.assert_allclose(result[0], np.arange(1, 33) / np.linalg.norm(np.arange(1, 33)), rtol=1e-6)

    def test_a_long_input_loses_words_from_its_end_but_keeps_the_end_of_text_token(self):
        model = self.Model([[1.0, 0.0, 0.0]])
        with patch.object(config, 'MAX_SEQ_LENGTH', 10):
            self.embedded(model, ['abcdefghijklmnopqrstuvwxyz'], 3)
            self.assertEqual(model.inputs, ['abcdefghi'])
            self.assertEqual(len(model.tokenize(model.inputs[0].encode())), 10)
            self.embedded(model, ['short'], 3)
            self.assertEqual(model.inputs, ['short'])

    def test_bad_dimensions_nonfinite_and_zero_vectors_fail(self):
        for vectors in ([[1.0, 1.0]], [[np.nan] * 3], [[0.0] * 3], [[1.0] * 3, [1.0] * 3]):
            with self.subTest(vectors=vectors), self.assertRaises(ValueError):
                self.embedded(self.Model(vectors), ['text'], 3)

    def test_embedding_identity_changes_with_encoding_semantics(self):
        original = config.embedding_identity()
        for key, value in [('QUERY_INSTRUCTION', 'another task'), ('MAX_SEQ_LENGTH', 128), ('MODEL_REVISION', 'another revision')]:
            with patch.object(config, key, value):
                self.assertNotEqual(config.embedding_identity(), original)

class LocalHelperTests(unittest.TestCase):
    def test_queued_reindex_retains_force_and_batch_size(self):
        from imprint.queue import build_command_args
        for command in ('ingest', 'refresh'):
            self.assertEqual(build_command_args(command, {'dir': '/path with spaces', 'force': True, 'batch_size': 1}),
                             ['/path with spaces', '--force', '--batch-size', '1'])
            args = ['/path with spaces', '--force', '--batch-size=1']
            self.assertEqual(build_command_args(command, {'args': args}), args)

    def test_model_is_shared_and_template_explicitly_disables_thinking(self):
        llm = MagicMock()
        llm.metadata = {'tokenizer.chat_template': '{{ enable_thinking }}'}
        llm.token_bos.return_value = 1
        llm.token_eos.return_value = 2
        llm.detokenize.return_value = b'<special>'
        llama = types.ModuleType('llama_cpp')
        llama.Llama = MagicMock(return_value=llm)
        formatter_module = types.ModuleType('llama_cpp.llama_chat_format')
        formatter_module.Jinja2ChatFormatter = MagicMock()
        with tempfile.TemporaryDirectory() as directory, patch.dict(local_llm._models, {}, clear=True), \
             patch.dict('sys.modules', {'llama_cpp': llama, 'llama_cpp.llama_chat_format': formatter_module}), \
             patch('imprint._llama_compat.apply'), patch('imprint.llama_runtime._quiet_logs'):
            first = local_llm.load(Path(directory) / 'model.gguf', 8192, 0)
            second = local_llm.load(Path(directory) / 'model.gguf', 8192, 0)
            self.assertIs(first, second)
            llama.Llama.assert_called_once()
            # Quiet without pointing standard output at the null device.
            self.assertEqual((llama.Llama.call_args.kwargs['verbose'], llama.Llama.call_args.kwargs['n_gpu_layers']), (True, 0))
            llm.set_cache.assert_called_once_with(None)
            self.assertIn('set enable_thinking = false', formatter_module.Jinja2ChatFormatter.call_args.kwargs['template'])

    def test_context_budget_discards_history_and_rejects_oversized_current_input(self):
        llm = MagicMock()
        llm.n_ctx.return_value = 100
        llm._imprint_formatter.side_effect = lambda messages, tools=None: types.SimpleNamespace(prompt=''.join(m['content'] for m in messages))
        llm.tokenize.side_effect = lambda text, **kw: list(text)
        current = [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'new'}]
        messages = [current[0], {'role': 'user', 'content': 'x' * 100}, {'role': 'assistant', 'content': 'old'}, current[-1]]
        self.assertEqual(local_llm.fit_messages(llm, messages, 20), current)
        with self.assertRaisesRegex(ValueError, 'context exceeded'):
            local_llm.fit_messages(llm, [{'role': 'user', 'content': 'x' * 100}], 20)

    def test_generation_decodes_special_tokens_and_split_characters_until_end_of_generation(self):
        from test_chat_stream import LocalModel, llama_binding
        llm = LocalModel(['[TOOL_CALLS]', b'\xe2\x86', b'\x92 ok'], ['a', 'b', 'c'])
        question = [{'role': 'user', 'content': 'x'}]
        with llama_binding():
            self.assertEqual(''.join(local_llm.generate(llm, question, 50, 0.3)), '[TOOL_CALLS]→ ok')
            self.assertEqual(''.join(local_llm.generate(llm, question, 2, 0.3)), 'ab')
        self.assertEqual((llm.requests[0]['temp'], llm.requests[0]['grammar']), (0.3, None))

    def test_a_schema_constrains_the_reply_and_none_leaves_it_free(self):
        from test_chat_stream import LocalModel, llama_binding
        llm = LocalModel(['{"type":', '"finding"}'], ['{"type":', '"finding"}'])
        schema = {'type': 'object'}
        with llama_binding():
            self.assertIn('finding', local_llm.complete(llm, 'summarize', schema=schema))
            self.assertEqual(llm.requests[-1]['grammar'], ('grammar', schema))
            self.assertIn('finding', local_llm.complete(llm, 'classify'))
            self.assertIsNone(llm.requests[-1]['grammar'])

    def test_a_deadline_stops_waiting_and_generating(self):
        from test_chat_stream import LocalModel, llama_binding
        closed = []

        class Model(LocalModel):
            def generate(self, tokens, **sampling):
                try:
                    yield from super().generate(tokens, **sampling)
                finally:
                    closed.append(True)
        llm = Model(['a', 'b', 'c'], ['a', 'b', 'c'])
        clock = iter([0.0, 1.0, 2.0, 3.0])
        with llama_binding():
            with patch.object(local_llm.time, 'monotonic', return_value=5.0):
                with self.assertRaises(TimeoutError):
                    local_llm.complete(llm, 'late', deadline=4.0)
                self.assertEqual(llm.requests, [])
            with patch.object(local_llm.time, 'monotonic', side_effect=lambda: next(clock)):
                with self.assertRaises(TimeoutError):
                    local_llm.complete(llm, 'slow', deadline=2.5)
            self.assertEqual(closed, [True])
            self.assertEqual(local_llm.complete(llm, 'unbounded'), 'abc')

    def test_the_prompt_names_only_the_most_used_labels(self):
        from imprint import taxonomy
        domains = [f'domain-{i}' for i in range(2000)]  # most used first, as the registry returns them
        sent = []
        reply = '{"type": "decision", "domains": ["auth"], "topics": ["token-refresh"]}'
        with patch.object(taxonomy, 'get_all_values', side_effect=lambda axis, ws=None: domains if axis == 'domain' else ['decision']), \
             patch.object(taxonomy, 'record_usage'), \
             patch.object(tagger, '_call_llm_raw_text', side_effect=lambda text: sent.append(text) or reply):
            tagger.derive_llm_classify('We decided to refresh tokens in the auth middleware.')
        self.assertIn('domain-0, ', sent[0])
        self.assertIn('domain-79\n', sent[0])
        self.assertNotIn('domain-80', sent[0])
        self.assertLess(len(sent[0]), 6000)

    def test_a_reply_without_a_classification_is_not_stored_as_one(self):
        for reply in ('', '<think>The chunk is about tokens.</think>'):
            with patch.object(tagger, '_call_llm_raw_text', return_value=reply), \
                 self.assertRaises(tagger.ClassificationFailed):
                tagger.derive_llm_classify('We decided to refresh tokens in the auth middleware.')
            with patch.object(tagger, '_call_llm_raw_text', return_value=reply):
                tags = tagger.build_payload_tags('We decided to refresh tokens in the auth middleware.', llm=True, zero_shot=False)
            self.assertFalse(tags.get('_llm_type'))  # so the memory is not marked LLM-tagged
            self.assertTrue(tags['domain'])  # the keyword domains stay

    def test_local_classification_is_unconstrained_and_validated(self):
        from test_chat_stream import LocalModel, llama_binding
        llm = LocalModel(['Here it is:\n```json\n{"type": "Decision", "domains": ["auth"], "topics": ["Token Refresh"]}\n```'])
        with patch.object(tagger, '_get_llm_provider', return_value='local'), \
             patch.object(tagger, '_load_tagger_model', return_value=(llm, None)), llama_binding():
            result = tagger.derive_llm_classify('We decided to refresh tokens in the auth middleware.')
        self.assertIsNone(llm.requests[-1]['grammar'])
        self.assertEqual((result['type'], result['domains']), ('decision', ['auth']))

    def test_context_pruning_preserves_request_during_tool_loop(self):
        llm = MagicMock()
        llm.n_ctx.return_value = 100
        llm._imprint_formatter.side_effect = lambda messages, tools: types.SimpleNamespace(
            prompt=''.join(m['content'] or '' for m in messages) + tools)
        llm.tokenize.side_effect = lambda text, **kw: list(text)
        call = lambda name: {'role': 'assistant', 'content': '', 'tool_calls': [{'id': name, 'type': 'function',
                             'function': {'name': 'search', 'arguments': {'query': name}}}]}
        messages = [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'current request'},
                    call('old'), {'role': 'tool', 'tool_call_id': 'old', 'content': 'x' * 40},
                    {'role': 'tool', 'tool_call_id': 'old', 'content': 'y' * 40},
                    call('new'), {'role': 'tool', 'tool_call_id': 'new', 'content': '7'}]
        result = local_llm.fit_messages(llm, messages, 20, tools='schemas')
        self.assertEqual(result, messages[:2] + messages[-2:])
        self.assertEqual(llm._imprint_formatter.call_args.kwargs['tools'], 'schemas')
        with self.assertRaisesRegex(ValueError, 'context exceeded'):
            local_llm.fit_messages(llm, messages[:2] + [call('only'), {'role': 'tool', 'tool_call_id': 'only', 'content': 'z' * 100}], 20, tools='')

    def test_chat_search_uses_workspace_wake_state_and_retrieval_filters(self):
        from imprint import chat, server
        with patch.object(server, '_validate_ws', return_value=('example', None)), \
             patch.object(server, '_woken_workspaces', set()), patch.object(server, 'search', return_value='evidence') as search:
            result = chat._run_tool('search', {'query': 'query', 'source': 'project/source.py', 'offset': 10,
                                               'mode': 'semantic', 'workspace': 'other'}, 'example', False)
            self.assertEqual(result, 'evidence')
            self.assertIn('example', server._woken_workspaces)
            self.assertEqual(search.call_args.kwargs['offset'], 10)
            self.assertEqual(search.call_args.kwargs['workspace'], 'example')
            search.reset_mock()
            result = chat._run_tool('search', {'query': 'query', 'unknown': 'x'}, 'example', False)
            self.assertEqual(result, '[tool error] bad args for search: unknown unknown')
            search.assert_not_called()

    def test_chat_generation_receives_current_device_and_historical_path_guidance(self):
        from imprint import chat, device
        origin = {'id': 'bbbbbbbb', 'name': 'device-B', 'os': 'linux'}
        from test_chat_stream import remote_provider, text_chunk
        client, provider = remote_provider([text_chunk('Shared knowledge.')])
        with provider[0], provider[1], provider[2], \
             patch.object(device, 'current', return_value=origin), \
             patch.object(chat.os, 'getcwd', return_value='/checkout-B'):
            events = list(chat.stream_reply([], 'Recall our old conversation'))
        prompt = client.chat.completions.create.call_args.kwargs['messages'][0]['content']
        self.assertIn('device-B', prompt)
        self.assertIn('/checkout-B', prompt)
        self.assertIn('Original paths in historical text', prompt)
        self.assertEqual(events[-1], {'type': 'done'})
