"""The person's messages to an agent session (ADR 0245): kept with the
session on this device, told once on its next Imprint call, by the hook in
Claude Code or the tool reply in every client, and copied to the Task it
holds without telling it twice."""
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from emulation import slack
from imprint import agent_activity as aa
from imprint import agent_messages, config, tasks, tasks_tool
from test_prompt_notices import SCRIPT, SHELLS

CONVERSATION = '7a1b2c3d-aaaa-bbbb-cccc-444455556666'
BOARD = f'conversation:{CONVERSATION}'
GITHUB_TOKEN = 'ghp_' + 'A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8'


class MessagesCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data = Path(directory.name)
        for patcher in (patch.object(config, 'get_data_dir', return_value=self.data),
                        patch.object(tasks.actors, '_device', return_value='device-a'),
                        patch.dict(os.environ, {'IMPRINT_WORKSPACE': '', 'CLAUDE_CODE_SESSION_ID': '',
                                                'IMPRINT_CLIENT_SESSION': ''}),
                        patch.object(aa, '_session_id', None)):
            patcher.start()
            self.addCleanup(patcher.stop)
        aa.close()
        self.addCleanup(aa.close)

    def session(self, board=BOARD, client='claude-code', workspace='default'):
        """A running agent session on this device (this test's process)."""
        sid = aa.start_session('/home/alex/code/shop', workspace)
        aa.name_client(client, '1.0')
        if board:
            aa.set_board(board)
        return sid

    def claude(self):
        return patch.dict(os.environ, {'CLAUDE_CODE_SESSION_ID': CONVERSATION})

    def prepared(self):
        return self.data / 'prompt-notices' / f'{CONVERSATION}.notice'


class SendTests(MessagesCase):
    def test_a_message_is_kept_redacted_with_the_session_and_waits(self):
        self.session()
        sent = agent_messages.send(BOARD, f'Use the new tax table, token {GITHUB_TOKEN}')
        self.assertEqual(sent['state'], 'waiting')
        self.assertNotIn(GITHUB_TOKEN, sent['text'])
        [kept] = agent_messages.messages(BOARD)
        self.assertEqual((kept['id'], kept['state'], kept['via']), (sent['id'], 'waiting', None))
        self.assertNotIn(GITHUB_TOKEN, kept['text'])
        self.assertTrue((self.data / 'prompt-notices' / f'{CONVERSATION}.poke').exists())

    def test_an_ended_or_unknown_session_and_an_empty_message_are_refused(self):
        with self.assertRaisesRegex(LookupError, 'has ended'):
            agent_messages.send('conversation:gone-0000', 'hello')
        self.session()
        with self.assertRaisesRegex(ValueError, 'Write a message'):
            agent_messages.send(BOARD, '   ')
        with self.assertRaisesRegex(ValueError, 'at most'):
            agent_messages.send(BOARD, 'x' * (agent_messages.TEXT_CHARS + 1))
        aa.end_session()
        with self.assertRaisesRegex(LookupError, 'has ended'):
            agent_messages.send(BOARD, 'hello')

    def test_a_session_is_found_by_its_record_id_as_live_lists_it(self):
        sid = self.session(board=None, client='codex')
        self.assertEqual(agent_messages.board_session_of(sid), f'process:{sid}')
        self.assertIsNone(agent_messages.board_session_of('no-such-session'))


class DeliveryTests(MessagesCase):
    def test_the_next_imprint_reply_tells_it_once_in_any_workspace(self):
        self.session()
        sent = agent_messages.send(BOARD, 'Stop and summarize where you are.')
        with self.claude():
            told = tasks_tool.notices(client='claude-code')
            self.assertIn("From the user, sent from Imprint's dashboard:", told)
            self.assertIn('Stop and summarize where you are.', told)
            self.assertEqual(tasks_tool.notices(client='claude-code'), '')
        [kept] = agent_messages.messages(BOARD)
        self.assertEqual((kept['id'], kept['state'], kept['via']), (sent['id'], 'delivered', 'reply'))
        self.assertIsNotNone(kept['delivered_at'])

    def test_a_codex_session_hears_it_on_its_reply(self):
        sid = self.session(board=None, client='codex')
        agent_messages.send(f'process:{sid}', 'Check the CSV import too.')
        self.assertIn('Check the CSV import too.', tasks_tool.notices(client='codex'))
        self.assertEqual(agent_messages.messages(f'process:{sid}')[0]['via'], 'reply')

    def test_a_hook_after_a_tool_call_tells_it_without_a_board_and_the_reply_does_not_repeat_it(self):
        self.session()
        agent_messages.send(BOARD, 'Use the staging database.')
        self.assertFalse(config.tasks_db_path('default').exists())
        with self.claude():
            tasks_tool.refresh_prompt_notices(client='claude-code')
            self.assertTrue(self.prepared().exists())
            if 'sh' in SHELLS:
                payload = {'session_id': CONVERSATION, 'hook_event_name': 'PostToolUse'}
                done = subprocess.run([SHELLS['sh'], str(SCRIPT), str(self.data), 'PostToolUse'],
                                      input=json.dumps(payload), capture_output=True, text=True, timeout=slack(30))
                self.assertIn('Use the staging database.', json.loads(done.stdout)['hookSpecificOutput']['additionalContext'])
            else:  # What the script does: take the file, and say which hook did.
                self.prepared().unlink()
                (self.data / 'prompt-notices' / f'{CONVERSATION}.notice-by').write_text('PostToolUse', encoding='utf-8')
            self.assertEqual(tasks_tool.notices(client='claude-code'), '')
        self.assertEqual(agent_messages.messages(BOARD)[0]['via'], 'tool')

    def test_a_message_the_inbox_held_still_waits_for_the_notice_path(self):
        self.session()
        sent = agent_messages.send(BOARD, 'Pause after this step.')
        agent_messages.mark(sent['id'], 'held', 'Held for approval')
        self.assertEqual(agent_messages.messages(BOARD)[0]['state'], 'held')
        with self.claude():
            self.assertIn('Pause after this step.', tasks_tool.notices(client='claude-code'))
        self.assertEqual(agent_messages.messages(BOARD)[0]['state'], 'delivered')
        with self.assertRaises(ValueError):
            agent_messages.mark(sent['id'], 'delivered')
        agent_messages.mark(sent['id'], 'refused')  # A delivered message stays delivered.
        self.assertEqual(agent_messages.messages(BOARD)[0]['state'], 'delivered')

    def test_a_copy_lands_on_the_task_it_holds_and_it_hears_the_message_once(self):
        self.session()
        made = tasks.create('Fix the total', project='shop', acceptance=['x'], status='ready', workspace='default')
        tasks.claim(made['id'], actor=tasks.agent(BOARD, name='grimforge', client='claude-code'), workspace='default')
        sent = agent_messages.send(BOARD, 'The tax table changed.')
        self.assertEqual(sent['task'], made['id'])
        [comment] = tasks.get(made['id'], workspace='default')['comments']
        self.assertEqual((comment['text'], comment['by']['kind'], comment['to']), ('The tax table changed.', 'user', ['holder']))
        with self.claude():
            told = tasks_tool.notices(client='claude-code')
            self.assertEqual(told.count('The tax table changed.'), 1)
            self.assertNotIn('New comment on', told)
            self.assertEqual(tasks_tool.notices(client='claude-code'), '')


class ApiTests(MessagesCase):
    def setUp(self):
        super().setUp()
        from fastapi.testclient import TestClient
        from imprint.api import app
        self.api = TestClient(app, base_url='http://127.0.0.1:8420', client=('127.0.0.1', 1234))

    def test_the_dashboard_sends_and_lists_a_sessions_messages(self):
        sid = self.session()
        sent = self.api.post(f'/api/agents/{sid}/messages', json={'text': 'Run the tests first.'})
        self.assertEqual(sent.status_code, 200, sent.text)
        self.assertEqual(sent.json()['message']['state'], 'waiting')
        listed = self.api.get(f'/api/agents/{sid}/messages').json()['messages']
        self.assertEqual([m['text'] for m in listed], ['Run the tests first.'])
        self.assertEqual(self.api.post(f'/api/agents/{sid}/messages', json={'text': ''}).status_code, 400)
        aa.end_session()
        ended = self.api.post(f'/api/agents/{sid}/messages', json={'text': 'Still there?'})
        self.assertEqual(ended.status_code, 409)
        self.assertIn('has ended', ended.json()['error'])
        self.assertEqual(len(self.api.get(f'/api/agents/{sid}/messages').json()['messages']), 1)


if __name__ == '__main__':
    unittest.main()
