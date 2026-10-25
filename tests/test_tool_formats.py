"""Tool-call formats against the official chat templates of each family.

The fixtures are the templates as published (see fixtures/chat_templates/
sources.json). Model outputs are written to each family's documented grammar.
"""
import hashlib
import json
import unittest
from datetime import datetime
from pathlib import Path

import jinja2
import jinja2.ext
from jinja2.sandbox import ImmutableSandboxedEnvironment

from imprint import chat, tool_formats
from imprint.tool_formats import FORMATS

TEMPLATES = Path(__file__).parent / 'fixtures' / 'chat_templates'
SOURCES = json.loads((TEMPLATES / 'sources.json').read_text())


def renderer(name):
    """Render a fixture as local_llm.load's llama_cpp Jinja2ChatFormatter does:
    the same sandboxed environment, globals and enable_thinking=false prefix."""
    environment = ImmutableSandboxedEnvironment(loader=jinja2.BaseLoader(), trim_blocks=True, lstrip_blocks=True,
                                                extensions=[jinja2.ext.loopcontrols])
    environment.filters['tojson'] = lambda value, ensure_ascii=False, indent=None, separators=None, sort_keys=False: \
        json.dumps(value, ensure_ascii=ensure_ascii, indent=indent, separators=separators, sort_keys=sort_keys)
    template = environment.from_string('{% set enable_thinking = false %}\n' + (TEMPLATES / name).read_text())
    source = SOURCES[name]

    def raise_exception(message):
        raise ValueError(message)

    def render(messages, tools=None):
        return template.render(messages=messages, tools=tools, bos_token=source['bos_token'],
                               eos_token=source['eos_token'], add_generation_prompt=True,
                               raise_exception=raise_exception, strftime_now=lambda f: datetime.now().strftime(f))
    return render


def written_call(name, call):
    """The reply the template's model is trained to write for one call: what
    the template renders for an assistant tool call, up to the end of turn."""
    render, eos = renderer(name), SOURCES[name]['eos_token']
    question = [{'role': 'user', 'content': 'what can you tell me about alex?'}]
    tools = chat._tool_schemas(False)
    before = render(question, tools)
    after = render(question + [{'role': 'assistant', 'content': '', 'tool_calls': [
        {'id': 'call00000', 'type': 'function', 'function': call}]}], tools)
    return after[len(before):].split(eos)[0]


class TemplateTests(unittest.TestCase):
    def test_fixtures_are_the_published_templates(self):
        for name, source in SOURCES.items():
            self.assertEqual(hashlib.sha256((TEMPLATES / name).read_bytes()).hexdigest(), source['sha256'], name)

    def test_each_template_is_detected_by_its_own_rendering_of_a_call(self):
        for name, source in SOURCES.items():
            detected = tool_formats.detect(renderer(name), source['eos_token'])
            self.assertEqual(detected and detected.name, source['format'], name)

    def test_a_template_without_tool_calls_has_no_format(self):
        environment = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True)
        template = environment.from_string(
            "{% for m in messages %}<|im_start|>{{ m.role }}\n{{ m.content }}<|im_end|>\n{% endfor %}"
            "{% if add_generation_prompt %}<|im_start|>assistant\n{% endif %}")
        render = lambda messages, tools: template.render(messages=messages, tools=tools, add_generation_prompt=True)
        self.assertIsNone(tool_formats.detect(render, '<|im_end|>'))

    def test_real_tool_calls_render_and_parse_back_to_the_typed_call(self):
        call = {'name': 'file_chunks', 'arguments': {'source': 'imprint/chat.py', 'start': 0, 'end': 2}}
        for name, source in SOURCES.items():
            if source['format']:
                [parsed] = FORMATS[source['format']].parse(written_call(name, call))
                self.assertEqual(chat._typed_args(parsed['name'], parsed['args']), call['arguments'], name)

    def test_a_whole_tool_conversation_renders_through_each_template(self):
        tools = chat._tool_schemas(False)
        messages = [{'role': 'system', 'content': 'You are Imprint.'},
                    {'role': 'user', 'content': 'who is alex?'},
                    {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'hist00002', 'type': 'function',
                     'function': {'name': 'search', 'arguments': {'query': 'alex', 'limit': 10}}}]},
                    {'role': 'tool', 'tool_call_id': 'hist00002', 'content': 'Alex maintains Imprint.'},
                    {'role': 'assistant', 'content': 'Alex maintains Imprint.'},
                    {'role': 'user', 'content': 'what does he prefer?'}]
        for name, source in SOURCES.items():
            if source['format']:
                prompt = renderer(name)(messages, tools)
                self.assertTrue(all(tool in prompt for tool in ('kg_invalidate', 'ingest_url', 'file_chunks')), name)
                self.assertIn('Alex maintains Imprint.', prompt, name)
                self.assertIn('what does he prefer?', prompt, name)


class ParseTests(unittest.TestCase):
    def test_qwen_xml_reads_parameter_text_verbatim(self):
        note = 'First line.\n\n    indented: 5\ntrue'
        text = ('Let me look.\n\n<tool_call>\n<function=file_chunks>\n<parameter=source>\nsrc/a.py\n</parameter>\n'
                '<parameter=start>\n2\n</parameter>\n</function>\n</tool_call>\n<tool_call>\n<function=store>\n'
                f'<parameter=content>\n{note}\n</parameter>\n</function>\n</tool_call>')
        calls = FORMATS['qwen-xml'].parse(text)
        self.assertEqual(calls, [{'name': 'file_chunks', 'args': {'source': 'src/a.py', 'start': '2'}},
                                 {'name': 'store', 'args': {'content': note}}])
        # The chat types them by schema: the string stays verbatim, the index is a number.
        self.assertEqual([chat._typed_args(call['name'], call['args']) for call in calls],
                         [{'source': 'src/a.py', 'start': 2}, {'content': note}])

    def test_hermes_reads_each_json_call_in_its_tags(self):
        text = ('<tool_call>\n{"name": "search", "arguments": {"query": "alex", "limit": 10}}\n</tool_call>\n'
                '<tool_call>\n{"name": "status", "arguments": {}}\n</tool_call>')
        self.assertEqual(FORMATS['hermes'].parse(text), [
            {'name': 'search', 'args': {'query': 'alex', 'limit': 10}}, {'name': 'status', 'args': {}}])

    def test_llama3_reads_a_reply_that_is_one_call(self):
        call = [{'name': 'search', 'args': {'query': 'alex'}}]
        for text in (' {"name": "search", "parameters": {"query": "alex"}}\n',
                     '<|python_tag|>{"name": "search", "parameters": {"query": "alex"}}',
                     # As Llama 3.2 1B wrote it live, echoing the template's tool list.
                     '<|python_tag|>{"type": "function", "name": "search", "parameters": {"query": "alex"}}'):
            self.assertEqual(FORMATS['llama3'].parse(text), call, text)

    def test_mistral_reads_the_call_list_after_its_marker(self):
        for text in ('[TOOL_CALLS] [{"name": "search", "arguments": {"query": "alex"}, "id": "a1b2c3d4e"}]',
                     '[TOOL_CALLS][{"name": "search", "arguments": {"query": "alex"}}]'):
            self.assertEqual(FORMATS['mistral'].parse(text), [{'name': 'search', 'args': {'query': 'alex'}}])

    def test_each_parser_accepts_only_its_own_shape(self):
        outputs = {
            'qwen-xml': '<tool_call>\n<function=search>\n<parameter=query>\nalex\n</parameter>\n</function>\n</tool_call>',
            'hermes': '<tool_call>\n{"name": "search", "arguments": {"query": "alex"}}\n</tool_call>',
            'llama3': '{"name": "search", "parameters": {"query": "alex"}}',
            'mistral': '[TOOL_CALLS][{"name": "search", "arguments": {"query": "alex"}}]',
        }
        for parser in FORMATS.values():
            for family, text in outputs.items():
                parsed = parser.parse(text)
                self.assertEqual(bool(parsed), parser.name == family, f'{parser.name} on {family}')

    def test_near_misses_are_not_calls(self):
        misses = {
            # The flat JSON Qwen3.5 once wrote for Imprint's old text protocol.
            'hermes': ['<tool_call>{"name": "search", "query": "alex", "limit": 10}</tool_call>',
                       '<tool_call>{"name": "search", "arguments": "alex"}</tool_call>',
                       # As Qwen2.5-0.5B wrote it live, copying its GGUF template's {{"name": …}}.
                       '<tool_call>\n{{"name": "kg_query", "arguments": {"subject": "alex"}}\n</tool_call>'],
            'llama3': ['{"name": "search", "arguments": {"query": "alex"}}',
                       '{"type": "tool", "name": "search", "parameters": {"query": "alex"}}',
                       '{"name": "search", "parameters": {"query": "alex"}} and more text',
                       'Here it is: {"name": "search", "parameters": {"query": "alex"}}'],
            'mistral': ['[{"name": "search", "arguments": {"query": "alex"}}]',
                        '[TOOL_CALLS][{"name": "search", "arguments": {"query": "alex"}}] trailing',
                        '[TOOL_CALLS][{"name": "search", "parameters": {"query": "alex"}}]'],
        }
        for family, texts in misses.items():
            for text in texts:
                self.assertEqual(FORMATS[family].parse(text), [], f'{family}: {text}')


class StreamingTests(unittest.TestCase):
    def test_text_streams_up_to_a_marker_and_holds_back_a_partial_one(self):
        fmt = FORMATS['hermes']
        self.assertEqual(tool_formats.shown(fmt, 'Let me check <tool_call>\n{"na'), len('Let me check '))
        self.assertEqual(tool_formats.shown(fmt, 'Let me check <tool_c'), len('Let me check '))
        self.assertEqual(tool_formats.shown(fmt, '1 < 2'), len('1 < 2'))
        mistral = FORMATS['mistral']
        self.assertEqual(tool_formats.shown(mistral, 'Sure. [TOOL_'), len('Sure. '))

    def test_a_reply_that_can_only_be_a_call_at_its_start_streams_otherwise(self):
        fmt = FORMATS['llama3']
        self.assertEqual(tool_formats.shown(fmt, '  '), 0)
        self.assertEqual(tool_formats.shown(fmt, ' {"name": "sea'), 0)
        self.assertEqual(tool_formats.shown(fmt, '<|python'), 0)
        self.assertEqual(tool_formats.shown(fmt, 'Use {x} here'), len('Use {x} here'))



class SettingTests(unittest.TestCase):
    def test_the_setting_offers_auto_each_format_and_none(self):
        from imprint import config_schema
        setting = config_schema.SETTINGS_BY_KEY['chat.tool_format']
        self.assertEqual(setting.default, 'auto')
        self.assertEqual([choice.value for choice in setting.options()], ['auto', *FORMATS, 'none'])
        self.assertFalse(setting.custom)
        with self.assertRaisesRegex(ValueError, 'expected one of'):
            config_schema.set_value('chat.tool_format', 'chatml')


if __name__ == '__main__':
    unittest.main()
