"""A running agent's whole conversation (ADR 0244): read from its client's
transcript on this device, redacted, without its thinking; the end first,
earlier parts on request, and only new complete lines while it is open."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from imprint import agent_activity as aa
from imprint import agent_conversation as conversation
from imprint import agent_messages, agents_live
from test_agents_live import GITHUB_TOKEN, LiveCase, claude, codex, iso, prompt, tool_use

T = 1_800_000_000.0


def thinking_and_reply(text, at, call=None):
    content = [{'type': 'thinking', 'thinking': 'Secret plan', 'signature': 'x'}, {'type': 'text', 'text': text}]
    if call:
        content.append({'type': 'tool_use', 'id': call, 'name': 'Bash', 'input': {'command': 'pytest -q'}})
    return claude('assistant', at, id='m1', model='claude-opus-5-5', stop_reason='tool_use' if call else 'end_turn',
                  usage={'input_tokens': 1, 'output_tokens': 1}, content=content)


def tool_result(call, text, at, error=False):
    return claude('user', at, role='user', content=[{'type': 'tool_result', 'tool_use_id': call,
                                                     'content': [{'type': 'text', 'text': text}], 'is_error': error}])


def peer(text, at):
    record = claude('user', at, role='user', content=text)
    record['origin'] = {'kind': 'peer'}
    return record


class ReadTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'c.jsonl'

    def write(self, records, mode='w', end='\n'):
        with open(self.path, mode, encoding='utf-8') as handle:
            handle.write(''.join(json.dumps(r) + '\n' for r in records[:-1]) + json.dumps(records[-1]) + end)

    def test_a_claude_conversation_shows_prompts_replies_and_tool_rows_without_thinking(self):
        self.write([prompt(f'Fix the total; the key is {GITHUB_TOKEN}', T),
                    thinking_and_reply('Running the tests first.', T + 1, call='c1'),
                    tool_result('c1', '3 passed', T + 2),
                    peer('From the user, sent from Imprint\'s dashboard:\n- Use the new tax table', T + 3),
                    thinking_and_reply('Done. Should I push it?', T + 4)])
        part = conversation.read(str(self.path), 'claude')
        kinds = [(e['kind'], e.get('text') or e.get('label')) for e in part['entries']]
        self.assertEqual([k for k, _ in kinds], ['prompt', 'reply', 'tool', 'result', 'context', 'reply'])
        self.assertNotIn(GITHUB_TOKEN, part['entries'][0]['text'])
        self.assertNotIn('Secret plan', json.dumps(part['entries']))
        tool = part['entries'][2]
        self.assertEqual((tool['call'], tool['label'], tool['detail']), ('c1', 'Testing', 'pytest -q'))
        self.assertEqual((part['entries'][3]['call'], part['entries'][3]['text']), ('c1', '3 passed'))
        self.assertEqual(part['entries'][4]['origin'], 'peer')
        self.assertEqual((part['start'], part['next'], part['more_before']), (0, self.path.stat().st_size, False))
        self.assertEqual(len({e['id'] for e in part['entries']}), len(part['entries']))

    def test_a_codex_conversation_keeps_its_context_apart(self):
        self.write([
            codex('response_item', T, {'role': 'user', 'content': [{'type': 'input_text', 'text': '<environment_context>cwd</environment_context>'}]}, 'message'),
            codex('response_item', T + 1, {'role': 'user', 'content': [{'type': 'input_text', 'text': 'Add CSV export'}]}, 'message'),
            codex('response_item', T + 2, {'summary': [], 'encrypted_content': 'x'}, 'reasoning'),
            codex('response_item', T + 3, {'call_id': 'k1', 'name': 'shell', 'arguments': json.dumps({'command': ['pytest']})}, 'function_call'),
            codex('response_item', T + 4, {'call_id': 'k1', 'output': '2 passed'}, 'function_call_output'),
            codex('response_item', T + 5, {'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'Added it.'}]}, 'message'),
        ])
        entries = conversation.read(str(self.path), 'codex')['entries']
        self.assertEqual([e['kind'] for e in entries], ['context', 'prompt', 'tool', 'result', 'reply'])
        self.assertEqual((entries[2]['label'], entries[3]['text'], entries[4]['text']), ('Testing', '2 passed', 'Added it.'))

    def test_only_new_complete_lines_are_read_and_a_line_being_written_waits(self):
        self.write([prompt('Go', T)])
        first = conversation.read(str(self.path), 'claude')
        line = json.dumps(thinking_and_reply('Half way there', T + 1))
        with open(self.path, 'a', encoding='utf-8') as handle:
            handle.write(line[:30])
        waiting = conversation.read(str(self.path), 'claude', after=first['next'])
        self.assertEqual((waiting['entries'], waiting['next']), ([], first['next']))
        with open(self.path, 'a', encoding='utf-8') as handle:
            handle.write(line[30:] + '\n')
        later = conversation.read(str(self.path), 'claude', after=waiting['next'])
        self.assertEqual([e['text'] for e in later['entries']], ['Half way there'])
        self.assertEqual(later['next'], self.path.stat().st_size)

    def test_a_long_conversation_opens_at_its_end_and_earlier_parts_follow(self):
        self.write([prompt(f'Message {n}', T + n) for n in range(40)])
        with patch.object(conversation, 'TAIL_BYTES', 600), patch.object(conversation, 'READ_BYTES', 600):
            end = conversation.read(str(self.path), 'claude')
            self.assertTrue(end['more_before'])
            self.assertEqual(end['entries'][-1]['text'], 'Message 39')
            earlier = conversation.read(str(self.path), 'claude', before=end['start'])
        self.assertEqual(earlier['next'], end['start'])
        numbers = [int(e['text'].split()[1]) for e in earlier['entries'] + end['entries']]
        self.assertEqual(numbers, list(range(numbers[0], 40)))  # Contiguous, each line once.

    def test_a_delivered_message_is_seen_once_the_transcript_shows_it(self):
        messages = [{'id': 'a', 'text': 'Use the "new" tax table', 'state': 'delivered', 'seen_at': None},
                    {'id': 'b', 'text': 'Not sent yet', 'state': 'waiting', 'seen_at': None}]
        raw = json.dumps(tool_result('c1', 'From the user:\n- Use the "new" tax table\n- Not sent yet', T)).encode()
        self.assertEqual(conversation.seen(raw, messages), ['a'])


class ApiTests(LiveCase):
    def test_the_panel_reads_a_running_sessions_conversation_with_its_messages_seen(self):
        from fastapi.testclient import TestClient
        from imprint.api import app
        api = TestClient(app, base_url='http://127.0.0.1:8420', client=('127.0.0.1', 1234))
        now = time.time()
        sid = self.session('claude-code', '/home/alex/code/shop', board='conversation:c0nv2', name='grimforge')
        aa._session_id = sid  # This process is that session's server.
        sent = agent_messages.send('conversation:c0nv2', 'Use the new tax table')
        agent_messages._settle([f"msg:{sent['id']}"], 'conversation:c0nv2', 'reply', now)
        path = self.home / '.claude' / 'projects' / '-home-alex-code-shop' / 'c0nv2.jsonl'
        self.transcript(path, [prompt('Fix it', now - 30), tool_result('c1', 'From the user:\n- Use the new tax table', now - 5)])
        with patch.object(agents_live, '_runs', return_value=[]):
            got = api.get(f'/api/agents/{sid}/conversation').json()
        self.assertEqual((got['client'], got['transcript']), ('claude', True))
        self.assertEqual([e['kind'] for e in got['entries']], ['prompt', 'result'])
        [message] = got['messages']
        self.assertEqual((message['state'], message['via']), ('delivered', 'reply'))
        self.assertIsNotNone(message['seen_at'])
        with patch.object(agents_live, '_runs', return_value=[]):
            again = api.get(f"/api/agents/{sid}/conversation", params={'after': got['next']}).json()
        self.assertEqual(again['entries'], [])
        self.assertEqual(api.get('/api/agents/nobody/conversation').json()['transcript'], False)


if __name__ == '__main__':
    unittest.main()
