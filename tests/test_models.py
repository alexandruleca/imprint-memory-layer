import tempfile
import json
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

from imprint import config, embeddings, local_llm


class EncodingTests(unittest.TestCase):
    def test_snapshot_mismatch_fails_before_storage_or_recovery(self):
        from imprint.cli_sync import import_snapshot
        with tempfile.TemporaryDirectory() as directory, patch('imprint.cli_sync.vectorstore._ensure_collection') as storage:
            path = Path(directory)
            (path / 'manifest.json').write_text(json.dumps({'embedding_identity': 'another-profile'}))
            with self.assertRaisesRegex(ValueError, 'profile mismatch'):
                import_snapshot(path)
            storage.assert_not_called()

    def test_last_token_pooling_handles_both_padding_sides(self):
        values = np.arange(24).reshape(2, 4, 3)
        mask = np.array([[1, 1, 0, 0], [0, 0, 1, 1]])
        with patch.object(config, 'POOLING', 'auto'), patch.object(config, 'MODEL_FAMILY', 'qwen3'):
            np.testing.assert_array_equal(embeddings._pool(values, mask), values[[0, 1], [1, 3]])

    def test_query_instruction_never_applies_to_documents(self):
        with patch.object(config, 'QUERY_INSTRUCTION', 'Find evidence'), \
             patch.object(embeddings, '_embed_raw', return_value=np.ones((1, 3))) as run:
            embeddings.embed_query('How is auth configured?')
            self.assertEqual(run.call_args.args[0], ['Instruct: Find evidence\nQuery: How is auth configured?'])
            embeddings.embed_document('auth implementation')
            self.assertEqual(run.call_args.args[0], ['auth implementation'])

    def session(self, values, output_names=('last_hidden_state', 'sentence_embedding')):
        session = MagicMock()
        session.get_inputs.return_value = [types.SimpleNamespace(name=n) for n in ('input_ids', 'attention_mask', 'position_ids')]
        session.get_outputs.return_value = [types.SimpleNamespace(name=n) for n in output_names]
        session.run.return_value = [values]
        return session

    def test_named_pooled_output_projection_and_normalization(self):
        session = self.session(np.arange(1, 65).reshape(1, 64).astype(np.float32))
        tokenizer = MagicMock()
        tokenizer.encode_batch.return_value = [types.SimpleNamespace(ids=[1, 2], attention_mask=[1, 1])]
        with patch.object(embeddings, '_tokenizer', tokenizer), patch.object(config, 'EMBEDDING_DIM', 32), \
             patch.object(config, 'MODEL_FAMILY', 'qwen3'), patch.object(config, 'MODEL_OUTPUT', 'auto'):
            result = embeddings._run_session(session, ['text'])
        self.assertEqual(result.shape, (1, 32))
        self.assertAlmostEqual(float(np.linalg.norm(result)), 1, places=6)
        self.assertEqual(session.run.call_args.args[0], ['sentence_embedding'])
        np.testing.assert_array_equal(session.run.call_args.args[1]['position_ids'], [[0, 1]])

    def test_bad_dimensions_nonfinite_and_zero_vectors_fail(self):
        tokenizer = MagicMock()
        tokenizer.encode_batch.return_value = [types.SimpleNamespace(ids=[1], attention_mask=[1])]
        for values in (np.ones((1, 2)), np.full((1, 3), np.nan), np.zeros((1, 3))):
            with self.subTest(values=values), patch.object(embeddings, '_tokenizer', tokenizer), \
                 patch.object(config, 'EMBEDDING_DIM', 3), patch.object(config, 'MODEL_OUTPUT', 'auto'):
                with self.assertRaises(ValueError):
                    embeddings._run_session(self.session(values), ['text'])

    def test_qwen_decoder_export_receives_empty_past_cache(self):
        session = self.session(np.ones((1, 32)))
        session.get_inputs.return_value.append(types.SimpleNamespace(
            name='past_key_values.0.key', type='tensor(float)', shape=['batch_size', 8, 'past_sequence_length', 128]))
        tokenizer = MagicMock()
        tokenizer.encode_batch.return_value = [types.SimpleNamespace(ids=[1], attention_mask=[1])]
        with patch.object(embeddings, '_tokenizer', tokenizer), patch.object(config, 'EMBEDDING_DIM', 32), \
             patch.object(config, 'MODEL_FAMILY', 'qwen3'), patch.object(config, 'MODEL_OUTPUT', 'auto'):
            embeddings._run_session(session, ['text'])
        cache = session.run.call_args.args[1]['past_key_values.0.key']
        self.assertEqual(cache.shape, (1, 8, 0, 128))
        self.assertEqual(cache.dtype, np.float32)

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
             patch('imprint._llama_compat.apply'):
            first = local_llm.load(Path(directory) / 'model.gguf', 8192, 0)
            second = local_llm.load(Path(directory) / 'model.gguf', 8192, 0)
            self.assertIs(first, second)
            llama.Llama.assert_called_once()
            llm.set_cache.assert_called_once_with(None)
            self.assertIn('set enable_thinking = false', formatter_module.Jinja2ChatFormatter.call_args.kwargs['template'])

    def test_context_budget_discards_history_and_rejects_oversized_current_input(self):
        llm = MagicMock()
        llm.n_ctx.return_value = 100
        llm._imprint_formatter.side_effect = lambda messages: types.SimpleNamespace(prompt=''.join(m['content'] for m in messages))
        llm.tokenize.side_effect = lambda text, **kw: list(text)
        current = [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'new'}]
        messages = [current[0], {'role': 'user', 'content': 'x' * 100}, {'role': 'assistant', 'content': 'old'}, current[-1]]
        self.assertEqual(local_llm.fit_messages(llm, messages, 20), current)
        with self.assertRaisesRegex(ValueError, 'context exceeded'):
            local_llm.fit_messages(llm, [{'role': 'user', 'content': 'x' * 100}], 20)

    def test_structured_response_uses_schema_grammar(self):
        llm = MagicMock()
        llm.create_chat_completion.return_value = {'choices': [{'message': {'content': '{"type":"finding"}'}}]}
        schema = {'type': 'object'}
        with patch.object(local_llm, 'fit_messages', side_effect=lambda llm, messages, limit: messages):
            self.assertIn('finding', local_llm.complete_json(llm, 'classify', schema))
        self.assertEqual(llm.create_chat_completion.call_args.kwargs['response_format'], {'type': 'json_object', 'schema': schema})

    def test_context_pruning_preserves_request_during_tool_loop(self):
        llm = MagicMock()
        llm.n_ctx.return_value = 100
        llm._imprint_formatter.side_effect = lambda messages: types.SimpleNamespace(prompt=''.join(m['content'] for m in messages))
        llm.tokenize.side_effect = lambda text, **kw: list(text)
        messages = [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'current request'},
                    {'role': 'assistant', 'content': 'old tool call'}, {'role': 'user', 'content': '[TOOL RESULT]' + 'x' * 100},
                    {'role': 'assistant', 'content': 'new call'}, {'role': 'user', 'content': '[TOOL RESULT]7'}]
        result = local_llm.fit_messages(llm, messages, 20)
        self.assertEqual(result, messages[:2] + messages[-2:])

    def test_chat_search_uses_workspace_wake_state_and_retrieval_filters(self):
        from imprint import chat, server
        with patch.object(server, '_validate_ws', return_value=('example', None)), \
             patch.object(server, '_woken_workspaces', set()), patch.object(server, 'search', return_value='evidence') as search:
            chat._tool_search('query', source='project/source.py', offset=10, mode='semantic', unknown='ignored')
            self.assertIn('example', server._woken_workspaces)
            self.assertEqual(search.call_args.kwargs['offset'], 10)
            self.assertNotIn('unknown', search.call_args.kwargs)

    def test_chat_generation_receives_current_device_and_historical_path_guidance(self):
        from imprint import chat, device
        origin = {'id': 'bbbbbbbb', 'name': 'device-B', 'os': 'linux'}
        with patch.object(chat, '_is_remote_provider', return_value=True), \
             patch.object(chat, '_get_chat_provider', return_value='ollama'), \
             patch.object(device, 'current', return_value=origin), \
             patch.object(chat.os, 'getcwd', return_value='/checkout-B'), \
             patch.object(chat, '_stream_remote', return_value=iter(['Shared knowledge.'])) as generate:
            events = list(chat.stream_reply([], 'Recall our old conversation'))
        prompt = generate.call_args.args[0][0]['content']
        self.assertIn('device-B', prompt)
        self.assertIn('/checkout-B', prompt)
        self.assertIn('Original paths in historical text', prompt)
        self.assertEqual(events[-1], {'type': 'done'})
