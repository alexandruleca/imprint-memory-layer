"""Live (ADR 0233): what each running agent is doing, from session records,
the Board, the Dispatcher and the tails of the clients' own transcripts, by
rule and without any model."""
import io
import json
import os
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from imprint import agent_activity as aa, agents_live, config, live_transcripts as lt, tasks

GITHUB_TOKEN = 'ghp_' + 'A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8'


def iso(at: float) -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(at)) + f'.{int(at % 1 * 1000):03d}Z'


def claude(kind, at, **message):
    record = {'type': kind, 'timestamp': iso(at)}
    if message:
        record['message'] = message
    return record


def tool_use(call, name, input_, at, msg='m1', usage=None, stop='tool_use'):
    return claude('assistant', at, id=msg, model='claude-opus-5-5', stop_reason=stop,
                  usage=usage or {'input_tokens': 3, 'cache_creation_input_tokens': 100, 'cache_read_input_tokens': 1000,
                                  'output_tokens': 50, 'output_tokens_details': {'thinking_tokens': 20}},
                  content=[{'type': 'tool_use', 'id': call, 'name': name, 'input': input_}])


def result(call, at):
    return claude('user', at, role='user', content=[{'type': 'tool_result', 'tool_use_id': call, 'content': 'ok'}])


def prompt(text, at):
    return claude('user', at, role='user', content=text)


def answer(at, msg='m2', usage=None):
    return claude('assistant', at, id=msg, model='claude-opus-5-5', stop_reason='end_turn',
                  usage=usage or {'input_tokens': 2, 'cache_creation_input_tokens': 10, 'cache_read_input_tokens': 1200,
                                  'output_tokens': 30},
                  content=[{'type': 'text', 'text': 'Done.'}])


def codex(kind, at, payload=None, sub=None):
    record = {'type': kind, 'timestamp': iso(at), 'payload': dict(payload or {})}
    if sub:
        record['payload']['type'] = sub
    return record


def token_count(at, total_in, cached, out, last_in, window=475000, used=12.0):
    return codex('event_msg', at, {'info': {
        'total_token_usage': {'input_tokens': total_in, 'cached_input_tokens': cached, 'output_tokens': out,
                              'reasoning_output_tokens': 5, 'total_tokens': total_in + out},
        'last_token_usage': {'input_tokens': last_in}, 'model_context_window': window},
        'rate_limits': {'primary': {'used_percent': used, 'window_minutes': 10080, 'resets_at': 1795846230}}}, 'token_count')


def fold(records, client='claude'):
    state = lt.State(client)
    for record in records:
        state.feed(record)
    return state


class ClassifyTests(unittest.TestCase):
    def test_each_tool_gets_its_label_by_rule(self):
        cases = [
            ('Edit', {'file_path': '/repo/imprint/ui/task-card.tsx'}, 'editing', 'Editing task-card.tsx'),
            ('Write', {'file_path': 'C:\\repo\\docs\\a.md'}, 'editing', 'Editing a.md'),
            ('Bash', {'command': 'cd ui && npm run build'}, 'building', 'Building'),
            ('Bash', {'command': 'npx next build'}, 'building', 'Building'),
            ('Bash', {'command': 'python -m unittest tests.test_tasks'}, 'testing', 'Testing'),
            ('Bash', {'command': 'npx playwright test tests/board.spec.ts'}, 'testing', 'Testing'),
            ('Bash', {'command': 'git status --short'}, 'running', 'Running'),
            ('Read', {'file_path': '/repo/a.py'}, 'reading', 'Reading code'),
            ('Grep', {'pattern': 'def live'}, 'reading', 'Reading code'),
            ('WebFetch', {'url': 'https://example.com'}, 'web', 'Searching the web'),
            ('Task', {'description': 'Find callers'}, 'delegating', 'Delegating'),
            ('mcp__imprint__search', {'query': 'x'}, 'imprint', 'Searching Imprint'),
            ('mcp__imprint__store', {'content': 'x'}, 'imprint', 'Saving to Imprint'),
            ('mcp__imprint__task_edit', {'action': 'claim'}, 'board', 'Updating the Board'),
            ('AskUserQuestion', {}, 'asking', 'Asking you'),
            ('TodoWrite', {}, 'other', 'Planning'),
            ('shell', {'command': ['bash', '-lc', 'go test ./...']}, 'testing', 'Testing'),
        ]
        for tool, payload, kind, label in cases:
            with self.subTest(tool=tool, payload=payload):
                found = lt.classify(tool, payload)
                self.assertEqual((found['kind'], found['label']), (kind, label))

    def test_codex_code_mode_calls_are_read_for_their_command_or_imprint_tool(self):
        self.assertEqual(lt.classify('exec', 'text((await tools.exec_command({cmd:`npm test`})).output)')['kind'], 'testing')
        self.assertEqual(lt.classify('exec', 'await tools.shell({command:["make","all"]})')['kind'], 'building')
        self.assertEqual(lt.classify('exec', 'const r=await tools.mcp__imprint__tasks({action:"next"})')['label'],
                         'Updating the Board')
        self.assertEqual(lt.classify('exec', 'text(await tools.write_stdin({session_id:4}))')['detail'], 'reading a running command')
        patch_text = '*** Begin Patch\n*** Update File: imprint/agents_live.py\n@@\n-a\n+b\n*** End Patch'
        self.assertEqual(lt.classify('apply_patch', patch_text)['label'], 'Editing agents_live.py')

    def test_commands_are_shortened_and_redacted(self):
        found = lt.classify('Bash', {'command': f'curl -H "Authorization: token {GITHUB_TOKEN}" https://api.github.com/user ' + 'x' * 200})
        self.assertNotIn(GITHUB_TOKEN, found['detail'])
        self.assertIn('[REDACTED', found['detail'])
        self.assertLessEqual(len(found['detail']), lt.DETAIL_CHARS)


class ClaudeStateTests(unittest.TestCase):
    def test_a_turn_runs_a_tool_finishes_it_and_waits_for_you(self):
        t = 1_800_000_000.0
        state = fold([prompt('Build it', t), tool_use('c1', 'Bash', {'command': 'npm run build'}, t + 5)])
        status, doing = agents_live.activity(state, {}, t + 20)
        self.assertEqual((status, doing['kind'], doing['since'], doing['long']), ('working', 'building', t + 5, False))
        self.assertTrue(agents_live.activity(state, {}, t + 5 + lt_long())[1]['long'])
        state.feed(result('c1', t + 30))
        self.assertEqual(agents_live.activity(state, {}, t + 31)[1]['label'], 'Thinking')
        state.feed(answer(t + 40))
        status, doing = agents_live.activity(state, {}, t + 60)
        self.assertEqual((status, doing['label'], doing['since']), ('waiting', 'Waiting for you', t + 40))
        status, doing = agents_live.activity(state, {}, t + 40 + agents_live.WAITING_MINUTES * 60 + 1)
        self.assertEqual((status, doing['label']), ('idle', 'Idle'))
        self.assertEqual([a['label'] for a in state.actions(t + 60)], ['Building'])

    def test_tokens_count_each_reply_once_though_its_blocks_repeat_its_usage(self):
        t = 1_800_000_000.0
        usage = {'input_tokens': 3, 'cache_creation_input_tokens': 100, 'cache_read_input_tokens': 1000, 'output_tokens': 50,
                 'output_tokens_details': {'thinking_tokens': 20}}
        state = fold([prompt('Go', t), tool_use('c1', 'Read', {'file_path': 'a'}, t + 1, msg='m1', usage=usage),
                      tool_use('c2', 'Read', {'file_path': 'b'}, t + 1, msg='m1', usage=usage),
                      result('c1', t + 2), result('c2', t + 2), answer(t + 3, msg='m2')])
        tokens = state.tokens()
        self.assertEqual((tokens['new_input'], tokens['cached_input'], tokens['output'], tokens['thinking']),
                         (3 + 100 + 2 + 10, 1000 + 1200, 50 + 30, 20))
        self.assertEqual(tokens['context'], 2 + 10 + 1200)
        self.assertEqual(tokens['cache_share'], round(2200 / (2200 + 115), 3))
        self.assertIsNone(tokens['window'])
        self.assertEqual(state.model, 'claude-opus-5-5')
        self.assertEqual(state.day_tokens(time.strftime('%Y-%m-%d', time.localtime(t)))['output'], 80)

    def test_an_interrupted_turn_waits_and_a_question_counts_as_waiting(self):
        t = 1_800_000_000.0
        state = fold([prompt('Go', t), tool_use('c1', 'Bash', {'command': 'sleep 100'}, t + 1),
                      prompt('[Request interrupted by user for tool use]', t + 5)])
        self.assertEqual(agents_live.activity(state, {}, t + 6)[0], 'waiting')
        asking = fold([prompt('Plan it', t), tool_use('c1', 'AskUserQuestion', {'questions': []}, t + 2)])
        status, doing = agents_live.activity(asking, {}, t + 500)
        self.assertEqual((status, doing['label']), ('waiting', 'Asking you'))


def lt_long():
    return agents_live.LONG_TOOL + 1


class CodexStateTests(unittest.TestCase):
    def test_codex_turns_tokens_window_and_rate_limit(self):
        t = 1_800_000_000.0
        state = fold([
            codex('session_meta', t, {'cwd': '/repo', 'timestamp': iso(t)}),
            codex('event_msg', t + 1, sub='task_started'),
            codex('turn_context', t + 1, {'model': 'gpt-6.2-sol', 'cwd': '/repo'}),
            codex('response_item', t + 2, {'call_id': 'k1', 'name': 'exec', 'input': 'await tools.shell({command:["pytest"]})'},
                  'custom_tool_call'),
            token_count(t + 3, 1000, 800, 40, 900),
        ], client='codex')
        self.assertEqual(agents_live.activity(state, {}, t + 4)[1]['kind'], 'testing')
        state.feed(codex('response_item', t + 5, {'call_id': 'k1', 'output': 'ok'}, 'custom_tool_call_output'))
        state.feed(token_count(t + 6, 1500, 1200, 70, 1400))
        state.feed(codex('event_msg', t + 7, sub='task_complete'))
        self.assertEqual(agents_live.activity(state, {}, t + 8)[0], 'waiting')
        tokens = state.tokens()
        self.assertEqual((tokens['new_input'], tokens['cached_input'], tokens['output']), (300, 1200, 70))
        self.assertEqual((tokens['context'], tokens['window']), (1400, 475000))
        self.assertEqual(tokens['rate_limit']['used_percent'], 12.0)
        self.assertEqual(state.model, 'gpt-6.2-sol')
        self.assertEqual(state.day_tokens(time.strftime('%Y-%m-%d', time.localtime(t))),
                         {'new_input': 300, 'cached_input': 1200, 'output': 70})


class TailTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 't.jsonl'
        lt.forget()
        self.addCleanup(lt.forget)

    def write(self, text, mode='a'):
        with open(self.path, mode, encoding='utf-8') as handle:
            handle.write(text)

    def test_only_new_bytes_are_read_and_a_line_still_being_written_waits(self):
        t = 1_800_000_000.0
        self.write(json.dumps(prompt('Go', t)) + '\n', 'w')
        state = lt.read(str(self.path), 'claude')
        self.assertTrue(state.turn_running)
        line = json.dumps(tool_use('c1', 'Edit', {'file_path': 'x.py'}, t + 1))
        self.write(line[:40])  # the client is still writing it
        with patch.object(lt.State, 'feed', wraps=state.feed) as feed:
            lt.read(str(self.path), 'claude')
            feed.assert_not_called()
            self.write(line[40:] + '\n')
            lt.read(str(self.path), 'claude')
            self.assertEqual(feed.call_count, 1)
        self.assertEqual(list(state.pending.values())[0]['label'], 'Editing x.py')

    def test_a_file_that_shrank_is_read_again_from_its_start(self):
        t = 1_800_000_000.0
        self.write(json.dumps(prompt('Go', t)) + '\n' + json.dumps(answer(t + 1)) + '\n', 'w')
        self.assertFalse(lt.read(str(self.path), 'claude').turn_running)
        self.write(json.dumps(prompt('Again', t + 2)) + '\n', 'w')
        state = lt.read(str(self.path), 'claude')
        self.assertTrue(state.turn_running)
        self.assertEqual(state.tokens()['output'], 0)

    def test_a_large_file_is_first_read_from_near_its_end_and_says_so(self):
        t = 1_800_000_000.0
        filler = json.dumps(prompt('x' * 200, t)) + '\n'
        self.write(filler * 50 + json.dumps(answer(t + 1)) + '\n', 'w')
        with patch.object(lt, 'INITIAL_BYTES', 1000):
            state = lt.read(str(self.path), 'claude')
        self.assertTrue(state.tokens()['partial'])
        self.assertEqual(state.tokens()['output'], 30)


class LiveTests(unittest.TestCase):
    """live() over a data folder, a home with transcripts and the Board."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.data, self.home = root / 'data', root / 'home'
        self.data.mkdir()
        self.home.mkdir()
        for patcher in (patch.dict(os.environ, {'IMPRINT_DATA_DIR': str(self.data), 'IMPRINT_WORKSPACE': ''}),
                        patch.object(agents_live.Path, 'home', return_value=self.home),
                        patch.object(tasks.actors, '_device', return_value='device-a'),
                        patch.object(aa, '_session_id', None)):
            patcher.start()
            self.addCleanup(patcher.stop)
        aa.close()
        self.addCleanup(aa.close)
        lt.forget()
        self.addCleanup(lt.forget)
        for cache in (agents_live._found, agents_live._looked, agents_live._firsts, agents_live._projects):
            cache.clear()

    def session(self, client, directory, board=None, name=None, started=None):
        aa._session_id = None
        sid = aa.start_session(directory, 'default')
        aa.name_client(client, '1.0')
        if board:
            aa.set_board(board, name)
        if started is not None:
            aa._write('UPDATE agent_sessions SET started_at = ? WHERE id = ?', (started, sid))
        aa._session_id = None
        return sid

    def transcript(self, path: Path, records):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(json.dumps(r) + '\n' for r in records), encoding='utf-8')
        return path

    def test_a_claude_code_session_shows_its_task_activity_and_tokens(self):
        now = time.time()
        folder = '/home/alex/code/shop'
        self.session('claude-code', folder, board='conversation:c0nv1', name='grimforge')
        made = tasks.create('Fix the total', project='shop', acceptance=['x'], status='ready', workspace='default')
        tasks.claim(made['id'], actor=tasks.agent('conversation:c0nv1', name='grimforge', client='claude-code'),
                    workspace='default')
        tasks.checkpoint(made['id'], 'Half way.', next_step='Run the tests',
                         actor=tasks.agent('conversation:c0nv1', name='grimforge', client='claude-code'), workspace='default')
        self.transcript(self.home / '.claude' / 'projects' / '-home-alex-code-shop' / 'c0nv1.jsonl',
                        [prompt('Fix it', now - 30), tool_use('c1', 'Bash', {'command': 'pytest -q'}, now - 10)])
        with patch.object(agents_live, '_runs', return_value=[]):
            report = agents_live.live(now)
        [agent] = report['agents']
        self.assertEqual((agent['name'], agent['client'], agent['project'], agent['model']),
                         ('grimforge', 'claude-code', 'shop', 'claude-opus-5-5'))
        self.assertEqual((agent['state'], agent['activity']['label']), ('working', 'Testing'))
        self.assertEqual((agent['task']['id'], agent['task']['role'], agent['task']['next']), (made['id'], 'working', 'Run the tests'))
        self.assertEqual(agent['tokens']['context'], 1103)
        self.assertEqual(report['counts'], {'waiting': 0, 'working': 1, 'idle': 0, 'others': 0})
        self.assertEqual(report['today'][0]['client'], 'claude-code')

    def test_a_dispatched_review_shows_as_reviewing(self):
        now = time.time()
        self.session('claude-code', '/home/alex/code/shop', board='conversation:r3v')
        made = tasks.create('Fix the total', project='shop', acceptance=['x'], status='ready', workspace='default')
        tasks.claim(made['id'], actor=tasks.agent('conversation:r3v', client='claude-code'), workspace='default')
        with patch.object(agents_live, '_runs', return_value=[{'id': 'run1', 'role': 'review', 'conversation': 'r3v', 'session': None}]):
            [agent] = agents_live.live(now)['agents']
        self.assertEqual((agent['task']['role'], agent['dispatched']), ('reviewing', {'run': 'run1', 'role': 'review'}))

    def test_sessions_without_a_conversation_id_are_matched_one_to_one_by_start_time(self):
        now = time.time()
        folder = '/home/alex/code/shop'
        early = self.session('claude-code', folder, started=now - 600)
        late = self.session('claude-code', folder, started=now - 300)
        projects = self.home / '.claude' / 'projects' / '-home-alex-code-shop'
        self.transcript(projects / 'aaa.jsonl', [prompt('First', now - 590), answer(now - 580)])
        self.transcript(projects / 'bbb.jsonl', [prompt('Second', now - 280), tool_use('c1', 'Edit', {'file_path': 'a.py'}, now - 5)])
        self.transcript(projects / 'old.jsonl', [prompt('Long ago', now - 9000)])
        with patch.object(agents_live, '_runs', return_value=[]):
            agents = {a['id']: a for a in agents_live.live(now)['agents']}
        self.assertEqual(agents[early]['state'], 'waiting')
        self.assertEqual(agents[late]['activity']['label'], 'Editing a.py')

    def test_a_codex_session_uses_the_path_its_hook_recorded(self):
        now = time.time()
        folder = '/home/alex/code/shop'
        self.session('codex-mcp-client', folder, started=now - 100)
        rollout = self.transcript(self.data.parent / 'elsewhere' / 'rollout.jsonl', [
            codex('session_meta', now - 95, {'cwd': folder, 'timestamp': iso(now - 95)}),
            codex('event_msg', now - 90, sub='task_started'), token_count(now - 50, 500, 400, 20, 450)])
        aa.record_transcript('019a-codex', 'codex', folder, str(rollout))
        aa._write('UPDATE agent_transcripts SET recorded_at = ?', (now - 98,))
        with patch.object(agents_live, '_runs', return_value=[]):
            [agent] = agents_live.live(now)['agents']
        self.assertEqual((agent['client'], agent['state'], agent['tokens']['window']), ('codex', 'working', 475000))

    def test_a_session_without_a_transcript_shows_its_recent_imprint_call_then_idles(self):
        now = time.time()
        sid = self.session('cursor-vscode', '/home/alex/code/shop')
        aa._write('INSERT INTO agent_calls (session_id, tool, started_at, duration_ms, ok) VALUES (?, ?, ?, ?, ?)',
                  (sid, 'search', now - 10, 5, 1))
        with patch.object(agents_live, '_runs', return_value=[]):
            [agent] = agents_live.live(now)['agents']
            self.assertEqual((agent['state'], agent['activity']['label'], agent['transcript']), ('working', 'Searching Imprint', False))
            [agent] = agents_live.live(now + 600)['agents']
        self.assertEqual(agent['state'], 'idle')

    def test_paired_devices_holders_are_listed_apart(self):
        now = time.time()
        made = tasks.create('Elsewhere', project='shop', acceptance=['x'], status='ready', workspace='default')
        with patch.object(tasks.actors, '_device', return_value='device-b'):
            tasks.claim(made['id'], actor=tasks.agent('conversation:far', name='quill', client='codex'), workspace='default')
        with patch.object(agents_live, '_runs', return_value=[]):
            report = agents_live.live(now)
        self.assertEqual(report['agents'], [])
        self.assertEqual([(o['name'], o['device'], o['task']) for o in report['others']], [('quill', 'device-b', made['id'])])
        self.assertEqual(report['counts']['others'], 1)

    def test_live_calls_no_model(self):
        with patch('imprint.tagger.build_payload_tags', side_effect=AssertionError('model')), \
                patch('imprint.embeddings.embed_document', side_effect=AssertionError('model')), \
                patch.object(agents_live, '_runs', return_value=[]):
            self.session('claude-code', '/home/alex/code/shop', board='conversation:z')
            agents_live.live()


class WslFolderTests(unittest.TestCase):
    def test_a_wsl_session_served_by_windows_is_found_in_its_distributions_home(self):
        directory = '\\\\wsl.localhost\\Ubuntu-26.04\\home\\alex\\code\\shop'
        home, folder = agents_live._home(directory)
        self.assertEqual((home, folder), ('\\\\wsl.localhost\\Ubuntu-26.04\\home\\alex', '/home/alex/code/shop'))
        self.assertEqual(agents_live._join(home, '.claude', 'projects', '-home-alex-code-shop', 'c1.jsonl'),
                         '\\\\wsl.localhost\\Ubuntu-26.04\\home\\alex\\.claude\\projects\\-home-alex-code-shop\\c1.jsonl')
        agents_live._projects.clear()
        self.assertEqual(agents_live._project(directory), 'shop')
        self.assertEqual(agents_live._home('\\\\wsl.localhost\\Ubuntu\\opt\\work'), (None, None))


class HookTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        env = patch.dict(os.environ, {'IMPRINT_DATA_DIR': directory.name})
        env.start()
        self.addCleanup(env.stop)
        aa.close()
        self.addCleanup(aa.close)

    def test_session_start_records_the_transcript_and_bad_payloads_are_ignored(self):
        from imprint import hook_entry
        hook_entry.remember_transcript({'session_id': 'c1', 'cwd': '/home/alex/code/shop',
                                        'transcript_path': '/home/alex/.claude/projects/-home-alex-code-shop/c1.jsonl'})
        hook_entry.remember_transcript({'session_id': 7, 'transcript_path': None})
        hook_entry.remember_transcript({'session_id': 'c2', 'transcript_path': '/home/alex/.codex/sessions/r.jsonl'})
        found = {t['conversation']: t for t in aa.transcripts(['c1', 'c2'])}
        self.assertEqual(found['c1']['client'], 'claude-code')
        self.assertEqual(found['c1']['cwd'], '/home/alex/code/shop')
        self.assertEqual(found['c2']['client'], 'codex')
        first = found['c1']['recorded_at']
        hook_entry.remember_transcript({'session_id': 'c1', 'transcript_path': '/moved/c1.jsonl'})
        again = aa.transcripts(['c1'])[0]
        self.assertEqual((again['path'], again['recorded_at']), ('/moved/c1.jsonl', first))


if __name__ == '__main__':
    unittest.main()
