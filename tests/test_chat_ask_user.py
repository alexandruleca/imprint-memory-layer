"""A reply can ask the user a clarifying question and carry on with the
answer, in Chat and in Echo (ADR 0159)."""
import json
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from starlette.testclient import TestClient

from emulation import slack
from imprint import api, chat, chat_replies, chat_store
from imprint import voice_api as voice
from imprint.chat_research import ResearchBudget
from test_chat_stream import LocalModel, call_chunk, llama_binding, remote_provider, text_chunk, tool
import test_voice  # its harness, not its tests

QUESTION = {"question": "Which project?", "options": ["Imprint", "Billing"]}


class Asker:
    """Delivers the answers given, in order, the way a window would: a
    ``wait`` returns at once, after advancing ``clock`` by ``waited``."""
    def __init__(self, *answers, clock=None, waited=0.):
        self.answers, self.asked, self.clock, self.waited = list(answers), [], clock, waited

    def __call__(self, event):
        self.asked.append(event)
        asker = self

        class Pending:
            def wait(self, timeout):
                asker.timeout = timeout
                if asker.clock is not None:
                    asker.clock[0] += asker.waited
                return asker.answers.pop(0)
        return Pending()


class AskUserLoopTests(unittest.TestCase):
    def reply(self, *turns, ask=None, read_only=False, tools=None, cancel=None):
        client, patches = remote_provider(*turns)
        with patches[0], patches[1], patches[2], patch.dict(chat.TOOLS, tools or {}):
            events = list(chat.stream_reply([], 'Where is the config?', ask=ask, read_only=read_only, cancel_event=cancel))
        return events, [c.kwargs for c in client.chat.completions.create.call_args_list]

    def test_the_reply_pauses_for_a_chosen_option_and_carries_on_with_it(self):
        search = Mock(return_value='Billing config lives in billing/settings.toml')
        asker = Asker('Billing')
        events, requests = self.reply(
            [call_chunk(0, 'ask_user', json.dumps(QUESTION), id='a')],
            [call_chunk(0, 'search', '{"query": "billing config"}', id='b')],
            [text_chunk('It is billing/settings.toml.')],
            ask=asker, tools=tool('search', search))
        self.assertIn('ask_user', [schema['function']['name'] for schema in requests[0]['tools']])
        self.assertIn('Ask only when it matters', requests[0]['messages'][0]['content'])
        question = next(event for event in events if event['type'] == 'question')
        self.assertEqual((question['question'], question['options'], question['allow_other']),
                         ('Which project?', ['Imprint', 'Billing'], True))
        self.assertEqual(asker.asked, [question])
        self.assertEqual(asker.timeout, chat.ANSWER_TIMEOUT)
        kinds = [event['type'] for event in events]
        self.assertLess(kinds.index('question'), kinds.index('answer'))
        self.assertIn({'type': 'answer', 'id': question['id'], 'text': 'Billing'}, events)
        self.assertIn({'type': 'tool_result', 'name': 'ask_user', 'result': 'The user answered: Billing'}, events)
        self.assertEqual(requests[1]['messages'][-1], {'role': 'tool', 'tool_call_id': 'a', 'content': 'The user answered: Billing'})
        search.assert_called_once()
        self.assertEqual(events[-2], {'type': 'assistant_message', 'text': 'It is billing/settings.toml.'})

    def test_a_free_text_answer_reaches_the_model_and_other_calls_in_the_batch_run(self):
        status = Mock(return_value='12 memories')
        asker = Asker('the payments service, last month')
        events, requests = self.reply(
            [call_chunk(0, 'status', '{}', id='a'),
             call_chunk(1, 'ask_user', '{"question": "Which service, and when?"}', id='b')],
            [text_chunk('Done.')], ask=asker, tools=tool('status', status))
        status.assert_called_once()
        self.assertEqual(asker.asked[0]['options'], [])
        self.assertTrue(asker.asked[0]['allow_other'])
        self.assertEqual(requests[1]['messages'][-2:], [
            {'role': 'tool', 'tool_call_id': 'a', 'content': '12 memories'},
            {'role': 'tool', 'tool_call_id': 'b', 'content': 'The user answered: the payments service, last month'}])

    def test_waiting_for_the_answer_does_not_spend_research_time(self):
        clock = [time.monotonic()]
        budgets = []

        def factory(**kwargs):
            budgets.append(ResearchBudget(**kwargs, clock=lambda: clock[0]))
            return budgets[-1]
        # The user takes far longer to answer than the whole research budget.
        asker = Asker('Imprint', clock=clock, waited=3600)
        with patch.object(chat, 'ResearchBudget', side_effect=factory):
            events, requests = self.reply(
                [call_chunk(0, 'ask_user', json.dumps(QUESTION), id='a')],
                [call_chunk(0, 'status', '{}', id='b')],
                [text_chunk('Imprint has 12 memories.')],
                ask=asker, tools=tool('status', Mock(return_value='12 memories')))
        self.assertEqual(len(requests), 3)
        self.assertIsNotNone(requests[2]['tools'])  # research went on, no budget notice
        self.assertNotIn('research_complete', [event.get('code') for event in events])
        self.assertGreater(budgets[0].remaining(), 0)

    def test_nobody_answering_lets_the_model_go_on_with_its_assumption(self):
        events, requests = self.reply(
            [call_chunk(0, 'ask_user', json.dumps(QUESTION), id='a')], [text_chunk('Assuming Imprint: …')],
            ask=Asker(None))
        result = next(event['result'] for event in events if event['type'] == 'tool_result')
        self.assertTrue(result.startswith('No answer came within 30 minutes.'), result)
        self.assertIn('say what you assumed', requests[1]['messages'][-1]['content'])
        self.assertIn({'type': 'answer', 'id': events[[e['type'] for e in events].index('question')]['id'], 'text': None}, events)

    def test_stopping_ends_the_wait_at_once(self):
        reply = chat_replies.Reply('s')
        events = []
        client, patches = remote_provider([call_chunk(0, 'ask_user', json.dumps(QUESTION), id='a')], [text_chunk('never')])

        def run():
            with patches[0], patches[1], patches[2]:
                events.extend(chat.stream_reply([], 'q', ask=reply.ask, cancel_event=reply.cancel))
        thread = threading.Thread(target=run)
        thread.start()
        deadline = time.monotonic() + slack(10)
        while reply.pending() is None and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertIsNotNone(reply.pending())
        started = time.monotonic()
        reply.stop()
        thread.join(slack(5))
        self.assertFalse(thread.is_alive())
        self.assertLess(time.monotonic() - started, slack(2))
        self.assertEqual(client.chat.completions.create.call_count, 1)
        self.assertNotIn('answer', [event['type'] for event in events])
        self.assertNotIn('assistant_message', [event['type'] for event in events])

    def test_voice_may_ask_but_still_may_not_write(self):
        store = Mock()
        events, requests = self.reply(
            [call_chunk(0, 'ask_user', json.dumps(QUESTION), id='a'), call_chunk(1, 'store', '{"content": "x"}', id='b')],
            [text_chunk('Imprint.')], ask=Asker('Imprint'), read_only=True, tools=tool('store', store))
        offered = [schema['function']['name'] for schema in requests[0]['tools']]
        self.assertIn('ask_user', offered)
        self.assertNotIn('store', offered)
        store.assert_not_called()
        self.assertIn({'type': 'tool_result', 'name': 'ask_user', 'result': 'The user answered: Imprint'}, events)
        self.assertIn('read-only', next(e['result'] for e in events if e['type'] == 'tool_result' and e['name'] == 'store'))

    def test_without_anyone_to_answer_ask_user_is_not_offered(self):
        _, requests = self.reply([text_chunk('Hi.')])
        self.assertNotIn('ask_user', [schema['function']['name'] for schema in requests[0]['tools']])
        self.assertNotIn('ask_user', requests[0]['messages'][0]['content'])

    def test_bad_questions_and_options_are_what_the_model_reads(self):
        self.assertIn('missing required question', chat._question_event({}))
        event = chat._question_event({'question': ' Which  one? ', 'options': ['A', 'A', ' B ', 'C', 'D', 'E', 'F'],
                                      'allow_other': False})
        self.assertEqual((event['question'], event['options'], event['allow_other']), ('Which one?', ['A', 'B', 'C', 'D', 'E'], False))
        # One option is no choice: the user answers in their own words.
        self.assertEqual(chat._question_event({'question': 'Q', 'options': ['Only'], 'allow_other': False})['allow_other'], True)
        self.assertEqual(chat._question_event({'question': 'Q', 'options': 'today, this week'})['options'], ['today', 'this week'])


class LocalToolFormatTests(unittest.TestCase):
    def reply(self, model):
        with patch.object(chat, '_is_remote_provider', return_value=False), \
             patch.object(chat, '_llama', return_value=object()), \
             patch.object(chat, '_model_missing', return_value=False), \
             patch.object(chat, '_llm', model), \
             patch.object(chat, 'load_model', return_value=(model, None)), \
             patch.dict(os.environ, {'IMPRINT_CHAT_TOOL_FORMAT': 'qwen-xml'}), llama_binding():
            asker = Asker('This week')
            return list(chat.stream_reply([], 'what changed?', ask=asker)), asker

    def test_a_list_of_options_in_qwen_markup_arrives_as_a_list(self):
        model = LocalModel(['<tool_call>\n<function=ask_user>\n<parameter=question>\nWhich period?\n</parameter>\n'
                            '<parameter=options>\n["This week", "Last month"]\n</parameter>\n'
                            '<parameter=allow_other>\nfalse\n</parameter>\n</function>\n</tool_call>'], ['This week: two decisions.'])
        events, asker = self.reply(model)
        self.assertEqual(asker.asked[0]['options'], ['This week', 'Last month'])
        self.assertFalse(asker.asked[0]['allow_other'])
        self.assertIn({'type': 'tool_call', 'name': 'ask_user', 'args': {
            'question': 'Which period?', 'options': ['This week', 'Last month'], 'allow_other': False}}, events)
        self.assertEqual(model.requests[1]['messages'][-1]['content'], 'The user answered: This week')

    def test_options_written_as_plain_lines_are_still_choices(self):
        model = LocalModel(['<tool_call>\n<function=ask_user>\n<parameter=question>\nWhich period?\n</parameter>\n'
                            '<parameter=options>\nThis week\nLast month\n</parameter>\n</function>\n</tool_call>'], ['Ok.'])
        _, asker = self.reply(model)
        self.assertEqual(asker.asked[0]['options'], ['This week', 'Last month'])


def events_of(lines):
    return [json.loads(line[6:]) for line in lines if line.startswith('data: ')]


class ChatAnswerApiTests(unittest.TestCase):
    """The question survives the window and is answered through the API."""
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for patcher in (patch.dict(os.environ, {'IMPRINT_DATA_DIR': directory.name}),
                        patch.dict(chat_store._conns, clear=True),
                        patch.dict(chat_replies._replies, clear=True)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(chat_store._close_all)
        self.client = TestClient(api.app, base_url='http://127.0.0.1:8420', client=('127.0.0.1', 1234))
        self.sid = chat_store.create_session()
        question = {**QUESTION, 'allow_other': False}
        self.provider, patches = remote_provider(
            [text_chunk('Let me check. '), call_chunk(0, 'ask_user', json.dumps(question), id='a')],
            [text_chunk('In Billing it is port 8101.')])
        for patcher in (*patches, patch.dict(chat.TOOLS, tool('search', Mock(return_value='')))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.stop_replies)

    def stop_replies(self):
        """Stop what still runs and let it finish before the store closes."""
        for reply in list(chat_replies._replies.values()):
            reply.stop()
            while not reply.wait(len(reply.events), slack(5))[1]:
                pass

    def ask_and_leave(self):
        """Ask, read until the question, then close the window."""
        lines = []
        with self.client.stream('POST', '/api/chat', json={'session_id': self.sid, 'message': 'Which port?'}) as response:
            for line in response.iter_lines():
                lines.append(line)
                if line.startswith('data: ') and json.loads(line[6:])['type'] == 'question':
                    break
        return events_of(lines)[-1]

    def wait_until(self, check):
        deadline = time.monotonic() + slack(10)
        while not check() and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertTrue(check())

    def answer(self, body):
        return self.client.post(f'/api/chat/sessions/{self.sid}/answer', json=body)

    def test_a_window_that_comes_back_sees_the_question_and_answers_it(self):
        question = self.ask_and_leave()
        self.assertEqual(question['type'], 'question')
        session = self.client.get(f'/api/chat/sessions/{self.sid}').json()
        self.assertTrue(session['replying'])
        self.assertEqual(session['question'], question)
        with self.client.stream('GET', f'/api/chat/sessions/{self.sid}/reply') as response:
            lines = response.iter_lines()
            replayed = []
            for line in lines:
                if line.startswith('data: '):
                    replayed.append(json.loads(line[6:]))
                    if replayed[-1]['type'] == 'question':
                        break
            self.assertEqual(replayed[-1], question)
            self.assertEqual(self.answer({'id': question['id'], 'answer': 'Billing'}).json(), {'ok': True})
            rest = events_of(list(lines))
        self.assertIn({'type': 'answer', 'id': question['id'], 'text': 'Billing'}, rest)
        self.assertEqual(rest[-1], {'type': 'done'})
        self.wait_until(lambda: chat_replies.current(self.sid) is None)
        # The question and its answer are kept with the chat, for later turns and Chat history.
        messages = chat_store.get_messages(self.sid)
        self.assertEqual([m['role'] for m in messages], ['user', 'tool', 'assistant'])
        self.assertEqual((messages[1]['tool_name'], messages[1]['content']), ('ask_user', 'The user answered: Billing'))
        self.assertEqual(messages[1]['tool_args']['question'], 'Which project?')
        self.assertEqual(messages[2]['content'], 'Let me check.\nIn Billing it is port 8101.')
        self.assertIsNone(self.client.get(f'/api/chat/sessions/{self.sid}').json()['question'])

    def test_answers_are_checked(self):
        self.assertEqual(self.answer({'id': 'x', 'answer': 'Billing'}).status_code, 404)  # no reply yet
        question = self.ask_and_leave()
        for body, status in (({'id': 'other', 'answer': 'Billing'}, 409),
                             ({'id': question['id'], 'answer': '  '}, 400),
                             ({'id': question['id']}, 400),
                             ({'id': question['id'], 'answer': 'x' * (chat_replies.MAX_ANSWER + 1)}, 400),
                             ({'id': question['id'], 'answer': 'Payments'}, 400)):  # not an option, and no other allowed
            response = self.answer(body)
            self.assertEqual(response.status_code, status, (body, response.text))
            self.assertIn('error', response.json())
        self.assertEqual(self.client.post(f'/api/chat/sessions/{self.sid}/answer', content=b'[1]').status_code, 400)
        self.assertEqual(self.answer({'id': question['id'], 'answer': ' Imprint '}).status_code, 200)
        # Answered already: refused while the reply goes on, and once it has ended.
        self.assertIn(self.answer({'id': question['id'], 'answer': 'Billing'}).status_code, (404, 409))

    def test_stop_while_a_question_waits_ends_the_reply(self):
        self.ask_and_leave()
        self.assertEqual(self.client.post(f'/api/chat/sessions/{self.sid}/stop').json(), {'ok': True})
        self.wait_until(lambda: chat_replies.current(self.sid) is None)
        self.assertEqual(self.provider.chat.completions.create.call_count, 1)
        self.assertEqual([(m['role'], m['content']) for m in chat_store.get_messages(self.sid)],
                         [('user', 'Which port?'), ('assistant', 'Let me check.')])


class VoiceAnswerTests(unittest.TestCase):
    """Echo asks aloud and on screen; what the user says next is the answer."""
    harness = test_voice.VoiceRunsToCompletionTests
    token, setUp, ORIGIN = harness.token, harness.setUp, harness.ORIGIN
    connect, open, until, snapshot = harness.connect, harness.open, harness.until, harness.snapshot
    wait_for, fixtures, speech = harness.wait_for, harness.fixtures, harness.speech
    QUESTION = {"type": "question", "id": "q1", "question": "Which project?", "options": ["Imprint", "Billing"],
                "allow_other": True}

    def test_the_next_thing_said_is_the_answer_not_a_new_question(self):
        transcripts, spoken = {}, []
        flags, _ = self.fixtures({"Which port?": ["Let me check. ", self.QUESTION, "Billing is on 8101."]}, transcripts, spoken)
        token, socket = self.connect()
        with socket as ws:
            self.open(ws, token)
            ws.send_json({"type": "turn", "id": 1, "text": "Which port?"})
            asked = self.until(ws, lambda event: event["type"] == "question")[-1]
            self.assertEqual(asked, {"type": "question", "id": 1, "question_id": "q1", "question": "Which project?",
                                     "options": ["Imprint", "Billing"], "allow_other": True})
            self.wait_for(lambda: "Which project? You can say Imprint or Billing, or something else." in spoken)
            ws.send_json({"type": "turn", "id": 2, "audio": self.speech("Billing, please.", transcripts)})
            events = self.until(ws, lambda event: event["type"] == "done")
        kinds = [(event["type"], event.get("id")) for event in events]
        self.assertIn(("answering", 2), kinds)
        self.assertNotIn(("queued", 2), kinds)
        self.assertNotIn(("user", 2), kinds)
        self.assertIn({"type": "answered", "id": 1, "question_id": "q1", "text": "Billing, please."}, events)
        self.assertEqual(flags["answers"], ["Billing, please."])
        turns = self.snapshot()["turns"]
        self.assertEqual([turn["id"] for turn in turns], [1])
        self.assertEqual(turns[0]["question"], {"id": "q1", "question": "Which project?", "options": ["Imprint", "Billing"],
                                                "allow_other": True, "answer": "Billing, please."})

    def test_a_tapped_option_or_typed_text_answers_too(self):
        self.fixtures({"Which port?": [self.QUESTION, "Imprint uses 8420."], "And auth?": [self.QUESTION, "Auth too."]})
        token, socket = self.connect()
        with socket as ws:
            self.open(ws, token)
            ws.send_json({"type": "turn", "id": 1, "text": "Which port?"})
            self.until(ws, lambda event: event["type"] == "question")
            ws.send_json({"type": "answer", "question_id": "q1", "text": "Imprint"})
            self.assertIn({"type": "answered", "id": 1, "question_id": "q1", "text": "Imprint"},
                          self.until(ws, lambda event: event["type"] == "done"))
            ws.send_json({"type": "turn", "id": 2, "text": "And auth?"})
            self.until(ws, lambda event: event["type"] == "question")
            ws.send_json({"type": "turn", "id": 3, "text": "Billing"})
            events = self.until(ws, lambda event: event["type"] == "done")
        self.assertIn({"type": "answering", "id": 3, "of": 2}, events)
        self.assertIn({"type": "answered", "id": 2, "question_id": "q1", "text": "Billing"}, events)

    def test_stop_reply_ends_the_wait(self):
        self.fixtures({"Which port?": [self.QUESTION, "never"]})
        token, socket = self.connect()
        with socket as ws:
            self.open(ws, token)
            ws.send_json({"type": "turn", "id": 1, "text": "Which port?"})
            self.until(ws, lambda event: event["type"] == "question")
            question = voice.sessions.conversation.question
            self.assertFalse(question.closed)
            ws.send_json({"type": "cancel"})
            self.until(ws, lambda event: event["type"] == "cancelled")
            # The reply's thread no longer waits: what it waits on is closed, unanswered.
            self.assertTrue(question.closed)
            self.assertIsNone(question.wait(0))

    def test_the_wait_for_an_answer_matches_chat(self):
        self.assertEqual(voice.ANSWER_WAIT, chat.ANSWER_TIMEOUT)


if __name__ == '__main__':
    unittest.main()
