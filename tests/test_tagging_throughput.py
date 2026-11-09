"""How fast LLM tagging runs: a one-line reply (ADR 0163), the prompt's layout
kept (ADR 0164), several memories decoded as one batch on the local model
(ADR 0165), and no provider asked to think (ADR 0166)."""

import os
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from imprint import local_llm, tagger

SECRET = 'ghp_' + 'A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8'
REPLY = '{"type":"bug","domains":["auth"],"topics":["token-refresh"],"lang":"python","layer":"api","kind":"source"}'


def registry(types_=('decision',), domains=('auth', 'db')):
    return patch('imprint.taxonomy.get_all_values',
                 side_effect=lambda axis, ws=None: list(domains if axis == 'domain' else types_))


class PromptTests(unittest.TestCase):
    def test_the_reply_is_asked_for_on_one_line_and_one_parses(self):
        with registry():
            prompt = tagger._classification_prompt('def refresh(): ...')
        self.assertIn('- Return ONLY the JSON object, no explanation\n'
                      '- Write the JSON on one line: no line breaks, no indentation\n\n', prompt)
        result = tagger._parse_llm_classification(REPLY)
        self.assertEqual((result['type'], result['domains'], result['lang']), ('bug', ['auth'], 'python'))

    def test_the_project_and_file_come_first_and_the_chunk_right_after_the_instructions(self):
        # ADR 0164: with the instructions first the model leaned on the path.
        with registry():
            prompt = tagger._classification_prompt('def refresh(): ...', project_hint='api', rel_path='api/auth.py')
        self.assertTrue(prompt.startswith('Project: api\nFile: api/auth.py\n\nClassify this code/text chunk.'))
        self.assertTrue(prompt.endswith('no indentation\n\ndef refresh(): ...'))

    def test_a_local_batch_classifies_each_memory_or_says_why_not(self):
        sent = {}

        def many(llm, prompts, max_tokens, slots):
            sent.update(prompts=prompts, slots=slots, max_tokens=max_tokens)
            return [REPLY, '<think>unfinished', RuntimeError('decode failed')]

        items = [{'content': f'note {i}', 'rel_path': f'p/n{i}.md', 'project_hint': 'p'} for i in range(2)]
        items.append({'content': f'key {SECRET}', 'rel_path': 'p/key.md', 'project_hint': 'p'})
        with patch.object(tagger, '_load_tagger_model', return_value=(object(), None)), \
                patch('imprint.local_llm.complete_many', side_effect=many), registry(), \
                patch('imprint.taxonomy.record_usage') as recorded:
            results = tagger.derive_llm_classify_many(items, slots=4)
        self.assertEqual(results[0]['type'], 'bug')
        self.assertIsInstance(results[1], tagger.ClassificationFailed)
        self.assertIsInstance(results[2], RuntimeError)
        self.assertEqual((sent['slots'], sent['max_tokens']), (4, 200))
        with registry():
            self.assertEqual(sent['prompts'][1], tagger._classification_prompt('note 1', rel_path='p/n1.md', project_hint='p'))
        self.assertNotIn(SECRET, sent['prompts'][2])
        self.assertIn('[REDACTED:github-token', sent['prompts'][2])
        self.assertTrue(recorded.called)  # the registry learns from batched classifications too
        with patch.object(tagger, '_load_tagger_model', return_value=(None, 'no model')):
            self.assertTrue(all(isinstance(r, tagger.ClassificationFailed) for r in tagger.derive_llm_classify_many(items)))

    def test_a_classification_from_a_batch_is_merged_like_one_asked_alone(self):
        parsed = tagger._parse_llm_classification(REPLY)
        with patch.object(tagger, 'derive_llm_classify', side_effect=AssertionError('asked again')):
            tags = tagger.build_payload_tags('def refresh(): ...', 'api/auth.py', llm=True, zero_shot=False,
                                             classification=parsed)
            failed = tagger.build_payload_tags('We refresh tokens in the auth middleware.', 'notes.md', llm=True,
                                               zero_shot=False, classification=tagger.ClassificationFailed('none'))
        self.assertEqual((tags['_llm_type'], tags['domain'], tags['topics']), ('bug', ['auth'], ['token-refresh']))
        self.assertEqual(failed['_llm_type'], '')  # not marked LLM-tagged
        self.assertTrue(failed['domain'])  # the keyword domains stay


class ThinkingTests(unittest.TestCase):
    """OpenAI-compatible servers are asked not to think; others need nothing."""

    def setUp(self):
        tagger._thinking_switch_refused.clear()
        self.addCleanup(tagger._thinking_switch_refused.clear)
        self.requests = []
        self.refuse = None

        def create(**kwargs):
            self.requests.append(kwargs)
            if self.refuse and self.refuse(kwargs):
                import httpx
                import openai
                request = httpx.Request('POST', 'http://server/v1/chat/completions')
                raise openai.BadRequestError('bad request', response=httpx.Response(400, request=request), body=None)
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=REPLY))])

        client = MagicMock()
        client.chat.completions.create.side_effect = create
        self.openai = patch('openai.OpenAI', return_value=client)
        self.openai.start()
        self.addCleanup(self.openai.stop)

    def call(self, provider, **env):
        keys = {'OPENAI_API_KEY': 'k', 'GOOGLE_API_KEY': 'k', 'IMPRINT_LLM_TAGGER_PROVIDER': provider,
                'IMPRINT_LLM_TAGGER_BASE_URL': '', **env}
        with patch.dict(os.environ, keys):
            return tagger._call_llm_raw_text(f'Classify it. key {SECRET}')

    def test_each_server_gets_its_switch_and_hosted_apis_none(self):
        expected = {
            'vllm': {'chat_template_kwargs': {'enable_thinking': False}},
            'ollama': {'reasoning_effort': 'none'},
            'openai': None,
            'gemini': None,
        }
        for provider, switch in expected.items():
            self.requests.clear()
            self.assertEqual(self.call(provider), REPLY)
            (request,) = self.requests
            self.assertEqual(request.get('extra_body'), switch, provider)
            self.assertEqual(len(request['messages']), 1)
            self.assertNotIn(SECRET, request['messages'][0]['content'])

    def test_the_openai_provider_at_another_address_is_taken_for_a_local_server(self):
        self.call('openai', IMPRINT_LLM_TAGGER_BASE_URL='http://127.0.0.1:8080/v1')
        self.assertEqual(self.requests[0]['extra_body'], {'chat_template_kwargs': {'enable_thinking': False}})

    def test_a_server_that_refuses_the_switch_is_asked_without_it_from_then_on(self):
        self.refuse = lambda kwargs: 'extra_body' in kwargs
        self.assertEqual(self.call('ollama'), REPLY)
        self.assertEqual([('extra_body' in r) for r in self.requests], [True, False])
        self.requests.clear()
        self.assertEqual(self.call('ollama'), REPLY)
        self.assertEqual([('extra_body' in r) for r in self.requests], [False])

    def test_a_request_refused_without_the_switch_too_fails_and_the_switch_stays(self):
        import openai
        self.refuse = lambda kwargs: True
        with self.assertRaises(openai.BadRequestError):
            self.call('vllm')
        self.assertEqual(tagger._thinking_switch_refused, set())

    def test_anthropic_gets_no_thinking_parameter(self):
        sent = []
        client = MagicMock()
        client.messages.create.side_effect = lambda **kw: sent.append(kw) or types.SimpleNamespace(
            content=[types.SimpleNamespace(text=REPLY)])
        with patch('anthropic.Anthropic', return_value=client), \
                patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'k', 'IMPRINT_LLM_TAGGER_PROVIDER': 'anthropic'}):
            self.assertEqual(tagger._call_llm_raw_text('a chunk'), REPLY)
        self.assertNotIn('thinking', sent[0])


class Model:
    """A loaded model whose tokens are characters and whose template wraps
    the message in its role."""

    def __init__(self):
        self._imprint_formatter = lambda messages, tools=None: types.SimpleNamespace(
            prompt=''.join(f"<{m['role']}>{m['content']}</{m['role']}>" for m in messages))

    def tokenize(self, text, add_bos=False, special=True):
        return list(text.decode())


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.groups, self.alone = [], []

        def decode(llm, held, prompts, max_tokens):
            self.groups.append([''.join(p) for p in prompts])
            return [f"batch:{''.join(p)}" for p in prompts]

        def complete(llm, text, max_tokens):
            self.alone.append(text)
            if text == 'boom':
                raise ValueError('context exceeded')
            return f'alone:{text}'

        for target in (patch.object(local_llm, '_decode_group', side_effect=decode),
                       patch.object(local_llm, '_batch_context', return_value=object()),
                       patch.object(local_llm, 'complete', side_effect=complete)):
            target.start()
            self.addCleanup(target.stop)

    def test_inputs_are_decoded_slots_at_a_time_in_order(self):
        results = local_llm.complete_many(Model(), list('abcde'), 20, slots=2)
        # The last group would hold one memory: it is completed alone.
        self.assertEqual(results, ['batch:<user>a</user>', 'batch:<user>b</user>', 'batch:<user>c</user>',
                                   'batch:<user>d</user>', 'alone:e'])
        self.assertEqual(self.groups, [['<user>a</user>', '<user>b</user>'], ['<user>c</user>', '<user>d</user>']])

    def test_one_slot_completes_each_input_on_its_own(self):
        self.assertEqual(local_llm.complete_many(Model(), ['a', 'b'], 20, slots=1), ['alone:a', 'alone:b'])
        self.assertEqual(self.groups, [])

    def test_a_prompt_too_long_for_a_batch_sequence_is_completed_on_its_own_in_place(self):
        long = 'x' * local_llm.BATCH_SEQUENCE_TOKENS
        results = local_llm.complete_many(Model(), ['a', long, 'b'], 20, slots=4)
        self.assertEqual(results, ['batch:<user>a</user>', f'alone:{long}', 'batch:<user>b</user>'])

    def test_an_error_of_a_request_completed_alone_is_returned_in_its_place(self):
        results = local_llm.complete_many(Model(), ['fine', 'boom'], 20, slots=1)
        self.assertEqual(results[0], 'alone:fine')
        self.assertIsInstance(results[1], ValueError)

    def test_a_batch_the_gpu_cannot_hold_falls_back_and_is_not_tried_again(self):
        llm = Model()
        with patch.object(local_llm, '_batch_context', side_effect=RuntimeError('out of device memory')) as make, \
                patch('sys.stderr'):
            self.assertEqual(local_llm.complete_many(llm, ['a', 'b', 'c', 'd'], 20, slots=2),
                             ['alone:a', 'alone:b', 'alone:c', 'alone:d'])
            self.assertEqual(make.call_count, 1)
            self.assertEqual(local_llm.complete_many(llm, ['e', 'f'], 20, slots=2), ['alone:e', 'alone:f'])
            self.assertEqual(make.call_count, 1)


@unittest.skipUnless(os.environ.get('IMPRINT_TEST_TAGGER_GGUF'), 'set IMPRINT_TEST_TAGGER_GGUF to a Qwen3.5 GGUF')
class RealModelBatchTests(unittest.TestCase):
    """The batch decoder against llama.cpp itself: a hybrid model's sequences,
    logits per sequence, and replies in order."""

    def test_a_batch_answers_each_input_like_one_alone(self):
        path = Path(os.environ['IMPRINT_TEST_TAGGER_GGUF'])
        llm = local_llm.load(path, 4096, -1)
        try:
            fruits = ['apple', 'pear', 'plum', 'mango', 'lemon']
            prompts = [f'Reply with only the fruit named here, in capital letters: {f}.' for f in fruits]
            alone = [local_llm.complete(llm, p, 16) for p in prompts]
            batched = local_llm.complete_many(llm, prompts, 16, slots=4)
            self.assertFalse(getattr(llm, '_imprint_unbatched', False))
            self.assertEqual(llm._imprint_batch.slots, 4)
            for fruit, one, many in zip(fruits, alone, batched):
                self.assertIn(fruit.upper(), one.upper())
                self.assertIn(fruit.upper(), many.upper(), batched)
        finally:
            local_llm.release(path)
            llm.close()


if __name__ == '__main__':
    unittest.main()
