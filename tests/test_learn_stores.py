"""Learning reads Claude Code, Codex, Cursor, Copilot and Cline stores, local and WSL
(ADR 0034, ADR 0037, ADR 0052, ADR 0056, ADR 0057)."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from imprint import cli_conversations as learner, config, transcripts, vectorstore as vs, workspace_policy as policy, wsl_paths

ANSWER = 'Keep the cache keyed by a content hash, because the path changes when the checkout moves. ' * 2
CLAUDE_SESSION = '7c1e2d3a-9b8f-4e6d-a1b2-c3d4e5f60718'
# Codex ids are time-ordered: sessions started in the same minute share a prefix.
CODEX_FIRST = '01a16711-fc04-77f0-a8b1-8d347fb54da9'
CODEX_SECOND = '01a16711-0b3e-7c2d-9e8f-102938475610'
CURSOR_SESSION = '5df5e6b6-b3fb-41f0-ade6-257fea91e2b9'
MOUNTS = 'C:\\134\t/mnt/c'
# Cline ids are <epoch ms>_<5 base36>: sessions started in the same millisecond share a prefix.
CLINE_FIRST = '1784094124598_rruoq'
CLINE_SECOND = '1784094124598_k2v7d'
COPILOT_SESSION = '3f2a9c4e-8d1b-4e7a-9c2f-1a2b3c4d5e6f'


def claude_session(cwd, question):
    return [
        {'type': 'file-history-snapshot'},
        {'type': 'user', 'cwd': cwd, 'message': {'role': 'user', 'content': question}},
        {'type': 'assistant', 'cwd': cwd, 'message': {'content': [
            {'type': 'thinking', 'thinking': 'private thinking'},
            {'type': 'text', 'text': ANSWER},
            {'type': 'tool_use', 'name': 'Bash', 'input': {'command': 'ls'}}]}},
        {'type': 'user', 'cwd': cwd, 'message': {'content': [{'type': 'tool_result', 'content': 'tool output'}]}},
    ]


def codex_rollout(session, cwd, question, turn_cwd=None):
    def item(**payload):
        return {'type': 'response_item', 'payload': payload}
    return [
        {'type': 'session_meta', 'payload': {'id': session, 'cwd': cwd, 'originator': 'codex_cli_rs'}},
        {'type': 'event_msg', 'payload': {'type': 'task_started'}},
        item(type='message', role='developer', content=[{'type': 'input_text', 'text': 'developer instructions ' * 5}]),
        item(type='message', role='user', content=[{'type': 'input_text',
             'text': f'<environment_context>\n  <cwd>{cwd}</cwd>\n</environment_context>'}]),
        {'type': 'turn_context', 'payload': {'cwd': turn_cwd or cwd, 'approval_policy': 'never'}},
        item(type='message', role='user', content=[{'type': 'input_text', 'text': question}]),
        item(type='reasoning', summary=[{'type': 'summary_text', 'text': 'private reasoning'}]),
        item(type='custom_tool_call', name='exec', input='ls'),
        item(type='message', role='assistant', content=[{'type': 'output_text', 'text': ANSWER}]),
    ]


def cursor_conversation(question):
    # Cursor states no cwd, session id or timestamp field; tool results are not recorded.
    return [
        {'role': 'user', 'message': {'content': [{'type': 'text', 'text':
            '<timestamp>Monday, Jul 27, 2026, 4:44 PM (UTC+3)</timestamp>\n'
            f'<user_query>\n{question}\n</user_query>\n[Image]\n<image_files>\n1. /tmp/attached.png\n</image_files>'}]}},
        {'role': 'assistant', 'message': {'content': [
            {'type': 'text', 'text': ANSWER},
            {'type': 'tool_use', 'name': 'Read', 'input': {'path': '/tool/input/path'}}]}},
        {'type': 'turn_ended', 'status': 'success'},
    ]


def cursor_transcript(store, directory, session, question):
    folder = store / 'projects' / transcripts.folder_name(str(directory)) / 'agent-transcripts' / session
    return write(folder / f'{session}.jsonl', cursor_conversation(question))


def copilot_events(session, cwd, question, *, changed_cwd=None, subagent_cwd=None):
    """A Copilot session's events.jsonl records; cwd None records no working directory."""
    counter = iter(range(1, 100))

    def event(kind, data, **extra):
        return {'id': f'e{next(counter)}', 'parentId': None, 'timestamp': '2026-10-25T08:00:00.000Z',
                'type': kind, 'data': data, **extra}
    start = {'sessionId': session, 'version': 1, 'producer': 'copilot-agent', 'copilotVersion': '1.0.23'}
    if cwd:
        start['context'] = {'cwd': cwd, 'gitRoot': cwd, 'repository': 'me/app', 'branch': 'main'}
    events = [
        event('session.start', start),
        event('user.message', {'content': question, 'source': 'user', 'attachments': [],
                               'transformedContent': f'<current_datetime>now</current_datetime>{question}<reminder>model-facing xml</reminder>'}),
        event('assistant.turn_start', {'turnId': 't1'}),
        event('assistant.message', {'messageId': 'a1', 'content': ANSWER, 'reasoningText': 'private reasoning',
                                    'encryptedContent': 'opaque', 'toolRequests': [
                                        {'toolCallId': 'c1', 'name': 'view', 'arguments': {'path': '/tool/input/path'}}]}),
        event('tool.execution_start', {'toolCallId': 'c1', 'toolName': 'view'}),
        event('tool.execution_complete', {'toolCallId': 'c1', 'result': {'content': 'tool output'}}),
        # A skill's instructions and a subagent's conversation are not the user's.
        event('user.message', {'content': 'Skill instructions for reading PDF files.', 'source': 'skill-pdf'}),
        event('subagent.started', {'toolCallId': 'c2', 'agentName': 'explore'}),
        event('user.message', {'content': 'Subagent prompt text', 'source': 'agent-explore'}, agentId='explore-1'),
        event('assistant.message', {'content': 'Subagent answer text that is long enough to keep.'}, agentId='explore-1'),
        event('assistant.message', {'content': 'Nested answer inside a tool call, long enough to keep.', 'parentToolCallId': 'c2'}),
        event('user.message', {'content': 'Continue with the task until it is complete.', 'isAutopilotContinuation': True}),
        event('assistant.message', {'content': 'After autopilot. ' + ANSWER}),
        event('user.message', {'content': 'And the second question?'}),
        event('assistant.message', {'content': 'Second answer. ' + ANSWER}),
    ]
    if changed_cwd:
        events.insert(6, event('session.context_changed', {'cwd': changed_cwd, 'gitRoot': changed_cwd}))
    if subagent_cwd:
        events.insert(8, event('session.context_changed', {'cwd': subagent_cwd}, agentId='explore-1'))
    return events


def copilot_session(home, session, cwd, question, **kwargs):
    return write(home / 'session-state' / session / 'events.jsonl', copilot_events(session, cwd, question, **kwargs))


def cline_messages(session, question):
    def text(value):
        return {'type': 'text', 'text': value}
    return {'version': 1, 'updated_at': '2026-10-25T08:00:00.000Z', 'agent': 'lead', 'sessionId': session,
            'origin': {'source': 'vscode', 'mode': 'user'}, 'messages': [
        {'id': 'm1', 'role': 'user', 'ts': 1, 'content': [
            text('<mode_notice>The user switched from plan mode to act mode before sending this message.</mode_notice>'
                 f'<user_input mode="act">{question}</user_input>'),
            {'type': 'file', 'path': '/attached/notes.md', 'content': 'attached file body'}]},
        {'id': 'm2', 'role': 'assistant', 'content': [
            {'type': 'thinking', 'thinking': 'private thinking'}, text(ANSWER),
            {'type': 'tool_use', 'id': 't1', 'name': 'read_file', 'input': {'path': '/tool/input/path'}}],
         'modelInfo': {'id': 'model', 'provider': 'cline'}},
        {'id': 'm3', 'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 't1', 'content': 'tool output'}]},
        # Hook output for the model, flagged as Cline flags it and unflagged.
        {'id': 'm4', 'role': 'user', 'content': '<hook_context source="PostToolUse" tool_name="read_file">hook output text</hook_context>',
         'metadata': {'displayRole': 'system'}},
        {'id': 'm5', 'role': 'user', 'content': [text('<hook_context source="TaskStart">unflagged hook output</hook_context>')]},
        {'id': 'm6', 'role': 'assistant', 'content': [text('After the hook. ' + ANSWER)]},
        {'id': 'm7', 'role': 'user', 'content': [text('<user_input mode="act">The user approved switching to act mode. '
                                                      'Continue with the approved plan now.</user_input>')]},
        {'id': 'm8', 'role': 'assistant', 'content': [{'type': 'redacted_thinking', 'data': 'opaque'}, text('Continuing. ' + ANSWER)]},
        {'id': 'm9', 'role': 'user', 'content': [text('<user_command slash="deep-planning">plan the cache rewrite</user_command>')]},
        {'id': 'm10', 'role': 'assistant', 'content': [text('Plan: ' + ANSWER)]},
        {'id': 'm11', 'role': 'assistant', 'content': [text('Provider error: the request was rate limited for a while.')],
         'metadata': {'displayOnly': True, 'displayRole': 'error'}},
    ]}


def cline_session(store, session, cwd, question, workspace_root=None, manifest=True, **metadata):
    """A Cline session folder; the manifest's messages_path points somewhere else, which learning never reads."""
    folder = store / session
    folder.mkdir(parents=True, exist_ok=True)
    messages = folder / f'{session}.messages.json'
    messages.write_text(json.dumps(cline_messages(session, question)), encoding='utf-8')
    if manifest:
        (folder / f'{session}.json').write_text(json.dumps({
            'version': 1, 'session_id': session, 'source': 'vscode', 'pid': 1, 'started_at': '2026-10-25T08:00:00.000Z',
            'status': 'completed', 'interactive': True, 'provider': 'cline', 'model': 'model', 'cwd': cwd,
            'workspace_root': workspace_root or cwd, 'enable_tools': True, 'enable_spawn': True, 'enable_teams': False,
            'metadata': {'title': question, 'sessionHistoryOrigin': {'mode': 'user'}, **metadata},
            'messages_path': f'/elsewhere/{session}.messages.json'}), encoding='utf-8')
    return messages


def write(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(json.dumps(record) for record in records) + '\n', encoding='utf-8')
    return path


class LearnStoresTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        env = patch.dict(os.environ, {'IMPRINT_DATA_DIR': str(self.root / 'data'), 'IMPRINT_WORKSPACE': '',
                                      'IMPRINT_LLM_TAGS': '0', 'IMPRINT_WSL_DISTRO': '', 'IMPRINT_WSL_MOUNTS': ''})
        env.start()
        self.addCleanup(env.stop)
        config.register_workspace('alpha')
        config.register_workspace('beta')
        config.switch_workspace('alpha')
        # Folder names resolve against the live filesystem, once per process.
        transcripts.folder_directory.cache_clear()
        self.addCleanup(transcripts.folder_directory.cache_clear)
        self.batches, self.memories, self.deleted, self.known = [], [], [], {}
        for target, attribute, kwargs in (
                (vs, 'store_batch', {'side_effect': lambda records: self.batches.append(records) or (len(records), 0)}),
                (vs, 'store', {'side_effect': lambda **record: self.memories.append(record)}),
                (vs, 'delete_by_source', {'side_effect': self.deleted.append}),
                (vs, 'get_source_mtimes', {'side_effect': lambda: dict(self.known)}),
                (learner.tagger, 'build_payload_tags', {'side_effect': lambda *args, **kwargs: {}}),
                (learner.chunker, 'chunk_prose', {'side_effect': lambda text: [text]}),
                (learner, 'classify', {'return_value': ('decision', 1.0)})):
            patcher = patch.object(target, attribute, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def learn(self, *stores):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = learner.learn(list(stores))
        return result, output.getvalue()

    def stored(self):
        return [record for batch in self.batches for record in batch]

    def test_learns_claude_code_and_codex_stores_including_auto_memory(self):
        project = self.root / 'app'
        project.mkdir()
        claude, codex = self.root / 'claude', self.root / 'codex'
        folder = claude / 'projects' / '-app'
        write(folder / f'{CLAUDE_SESSION}.jsonl', claude_session(str(project), 'How should the cache be keyed?'))
        write(folder / CLAUDE_SESSION / 'subagents' / 'agent-1.jsonl', claude_session(str(project), 'Subagent prompt text'))
        (folder / 'memory').mkdir()
        (folder / 'memory' / 'MEMORY.md').write_text('- Cache keys are content hashes.', encoding='utf-8')
        write(codex / 'sessions' / '2026' / '10' / '23' / f'rollout-2026-10-23T07-43-14-{CODEX_FIRST}.jsonl',
              codex_rollout(CODEX_FIRST, str(project), 'Where do rollouts get written?'))
        write(codex / 'archived_sessions' / f'rollout-2026-10-23T07-43-50-{CODEX_SECOND}.jsonl',
              codex_rollout(CODEX_SECOND, str(project), 'Can archived sessions be learned?'))
        (codex / 'session_index.jsonl').write_text('{"id": "not a conversation"}\n', encoding='utf-8')

        result, output = self.learn(('claude-code', str(claude)), ('codex', str(codex)))

        self.assertEqual(result, 0, output)
        self.assertEqual({record['source'] for record in self.stored()},
                         {f'conversation/{CLAUDE_SESSION[:8]}', f'conversation/{CODEX_FIRST}', f'conversation/{CODEX_SECOND}'})
        content = '\n'.join(record['content'] for record in self.stored())
        for question in ('How should the cache be keyed?', 'Where do rollouts get written?', 'Can archived sessions be learned?'):
            self.assertIn(f'Q: {question}', content)
        for hidden in ('Subagent prompt', 'private thinking', 'tool output', 'developer instructions',
                       'private reasoning', 'environment_context'):
            self.assertNotIn(hidden, content)
        self.assertEqual([memory['source'] for memory in self.memories], ['memory/MEMORY.md'])

    def test_learns_a_cursor_store_whose_folder_names_one_directory(self):
        project = self.root / 'code' / 'shop'
        project.mkdir(parents=True)
        cursor = self.root / 'cursor'
        transcript = cursor_transcript(cursor, project, CURSOR_SESSION, 'How is the shop deployed?')
        write(transcript.parent / 'subagents' / 'd49d3031-9092-45fa-b952-a80f14e5e83d.jsonl',
              cursor_conversation('Subagent prompt text'))
        # The directory of this conversation no longer exists: no evidence.
        cursor_transcript(cursor, self.root / 'gone', '394a816b-bbb2-4785-913c-6fa7684c619a', 'Is this one learned?')

        result, output = self.learn(('cursor', str(cursor)))

        self.assertEqual(result, 0, output)
        self.assertIn(f'Cursor {cursor}: 2 conversations', output)
        self.assertIn('1 not this workspace', output)
        stored = self.stored()
        self.assertEqual({record['source'] for record in stored}, {f'conversation/{CURSOR_SESSION[:8]}'})
        self.assertEqual({record['project'] for record in stored}, {learner.derive_project(str(project))})
        content = '\n'.join(record['content'] for record in stored)
        self.assertIn('Q: How is the shop deployed?\n\nA: Keep the cache', content)
        for hidden in ('Subagent prompt', 'Is this one learned', 'timestamp', 'Jul 27', 'image_files', 'attached.png',
                       '/tool/input/path'):
            self.assertNotIn(hidden, content)

    def test_a_cursor_folder_name_stands_for_exactly_one_existing_directory(self):
        def resolve(path):
            transcripts.folder_directory.cache_clear()
            return transcripts.folder_directory(transcripts.folder_name(str(path)))

        unique = self.root / 'code' / 'web-app'
        unique.mkdir(parents=True)
        (self.root / 'code' / 'web-app.md').write_text('a file beside it', encoding='utf-8')
        self.assertEqual(resolve(unique), str(unique))
        (self.root / 'code' / '.hidden' / 'site').mkdir(parents=True)
        self.assertEqual(resolve(self.root / 'code' / '.hidden' / 'site'), str(self.root / 'code' / '.hidden' / 'site'))
        # Separators are lost: /code/web/app has the same folder name.
        (self.root / 'code' / 'web' / 'app').mkdir(parents=True)
        self.assertIsNone(resolve(unique))
        workspace = self.root / 'code' / 'team.code-workspace'
        workspace.write_text('{}', encoding='utf-8')
        self.assertIsNone(resolve(workspace))
        self.assertIsNone(resolve(self.root / 'code' / 'missing'))
        self.assertIsNone(transcripts.folder_directory('1785135189657'))

        # A candidate directory that cannot be listed leaves uniqueness unknown.
        (self.root / 'locked').mkdir()
        (self.root / 'other' / 'api').mkdir(parents=True)
        scandir = os.scandir

        def guarded(path):
            if Path(path) == self.root / 'locked':
                raise PermissionError(13, 'Permission denied', str(path))
            return scandir(path)

        with patch.object(transcripts.os, 'scandir', side_effect=guarded):
            self.assertIsNone(resolve(self.root / 'locked' / 'api'))
            self.assertEqual(resolve(self.root / 'other' / 'api'), str(self.root / 'other' / 'api'))

    def test_a_cursor_folder_is_ownership_evidence(self):
        claimed, unclaimed = self.root / 'claimed', self.root / 'unclaimed'
        claimed.mkdir()
        unclaimed.mkdir()
        policy.add_root(str(claimed), 'beta')
        cursor = self.root / 'cursor'
        mine = cursor_transcript(cursor, unclaimed, CURSOR_SESSION, 'Whose conversation is this?')
        theirs = cursor_transcript(cursor, claimed, CURSOR_SESSION, 'Whose conversation is this?')
        nowhere = cursor_transcript(cursor, self.root / 'gone', CURSOR_SESSION, 'Whose conversation is this?')
        self.assertTrue(policy.transcript_allowed(str(mine), 'alpha'))
        self.assertFalse(policy.transcript_allowed(str(theirs), 'alpha'))
        self.assertTrue(policy.transcript_allowed(str(theirs), 'beta'))
        self.assertFalse(policy.transcript_allowed(str(nowhere), 'alpha'))
        self.assertFalse(policy.transcript_allowed(str(nowhere), 'beta'))

    def test_a_wsl_cursor_store_resolves_folder_names_in_the_distribution(self):
        wsl = self.root / 'wsl-namespace'

        def native(value):
            # Everything outside the fixture is the distribution's filesystem,
            # reached the way the bridge reaches \\wsl.localhost\Ubuntu.
            if value.startswith(str(self.root)):
                return value
            windows = wsl_paths.linux_to_windows(value, 'Ubuntu', MOUNTS)
            prefix = '\\\\wsl.localhost\\Ubuntu\\'
            self.assertTrue(windows.startswith(prefix), windows)
            return str(wsl.joinpath(*windows[len(prefix):].split('\\')))

        (wsl / 'home' / 'test' / 'code' / 'arnia' / 'shopware').mkdir(parents=True)
        store = wsl / 'home' / 'test' / '.cursor'
        folder = store / 'projects' / 'home-test-code-arnia-shopware' / 'agent-transcripts' / CURSOR_SESSION
        write(folder / f'{CURSOR_SESSION}.jsonl', cursor_conversation('How is the shop deployed?'))

        # Learn runs a WSL store's group in that distribution's path context.
        with patch.dict(os.environ, {'IMPRINT_WSL_DISTRO': 'Ubuntu'}), \
                patch.object(wsl_paths, 'native_path', side_effect=native), \
                patch.object(transcripts, 'native_path', side_effect=native), \
                patch.object(policy, 'native_path', side_effect=native):
            result, output = self.learn(('cursor', '/home/test/.cursor'))

        self.assertEqual(result, 0, output)
        # Named like a Claude Code or Codex session in /home/test/code/arnia/shopware.
        self.assertEqual({(record['source'], record['project']) for record in self.stored()},
                         {(f'conversation/{CURSOR_SESSION[:8]}', 'arnia-shopware')})

    def test_wsl_store_resolves_paths_like_the_bridge_and_names_projects_like_native_learning(self):
        wsl, drive = self.root / 'wsl-namespace', self.root / 'c-drive'

        def native(value):
            # The bridge's own translator, onto fixture folders standing in for
            # \\wsl.localhost\Ubuntu and C:. This machine's paths stay native.
            if not value.startswith(('/home/test', '/mnt/c')):
                return value
            windows = wsl_paths.linux_to_windows(value, 'Ubuntu', MOUNTS)
            for prefix, local in (('\\\\wsl.localhost\\Ubuntu\\', wsl), ('C:\\', drive)):
                if windows.startswith(prefix):
                    return str(local.joinpath(*windows[len(prefix):].split('\\')))
            raise AssertionError(windows)

        claimed = drive / 'Users' / 'a' / 'claimed'
        claimed.mkdir(parents=True)
        (wsl / 'home' / 'test' / 'code' / 'personal' / 'app').mkdir(parents=True)
        policy.add_root(str(claimed), 'beta')
        claude = wsl / 'home' / 'test' / '.claude' / 'projects' / '-home-test-code-personal-app'
        write(claude / f'{CLAUDE_SESSION}.jsonl', claude_session('/home/test/code/personal/app', 'How should the cache be keyed?'))
        (claude / 'memory').mkdir()
        (claude / 'memory' / 'MEMORY.md').write_text('- Cache keys are content hashes.', encoding='utf-8')
        sessions = wsl / 'home' / 'test' / '.codex' / 'sessions' / '2026' / '10' / '23'
        write(sessions / f'rollout-2026-10-23T07-43-14-{CODEX_FIRST}.jsonl',
              codex_rollout(CODEX_FIRST, '/home/test/code/personal/app', 'Where do rollouts get written?'))
        # A turn in a drive-mounted project another workspace claimed keeps the session there.
        write(sessions / f'rollout-2026-10-23T07-43-50-{CODEX_SECOND}.jsonl',
              codex_rollout(CODEX_SECOND, '/home/test/code/personal/app', 'Is this one learned?',
                            turn_cwd='/mnt/c/Users/a/claimed'))

        with patch.object(wsl_paths, 'native_path', side_effect=native), \
                patch.object(policy, 'native_path', side_effect=native):
            result, output = self.learn(('claude-code', '/home/test/.claude'), ('codex', '/home/test/.codex'))

        self.assertEqual(result, 0, output)
        stored = self.stored()
        self.assertEqual({record['source'] for record in stored},
                         {f'conversation/{CLAUDE_SESSION[:8]}', f'conversation/{CODEX_FIRST}'})
        # The recorded Linux cwd names the project, exactly as when this
        # profile's own runtime learned it; the file path is the reachable one.
        self.assertEqual({record['project'] for record in stored}, {'app'})
        for record in stored:
            self.assertTrue(Path(record['source_path']).is_relative_to(wsl), record['source_path'])
        self.assertEqual([memory['source_path'] for memory in self.memories],
                         [str(claude / 'memory' / 'MEMORY.md')])
        self.assertIn('1 not this workspace', output)
        self.assertEqual(policy.roots('alpha'), [])

    def test_a_conversation_reachable_twice_is_learned_once_and_replaced_when_modified(self):
        project = self.root / 'app'
        project.mkdir()
        first, second = self.root / 'profile-a', self.root / 'profile-b'
        older = write(first / 'projects' / '-app' / f'{CLAUDE_SESSION}.jsonl',
                      claude_session(str(project), 'How should the cache be keyed?'))
        newer = write(second / 'projects' / '-app' / f'{CLAUDE_SESSION}.jsonl',
                      claude_session(str(project), 'How should the cache be keyed now?'))
        os.utime(older, (1_000_000, 1_000_000))
        os.utime(newer, (2_000_000, 2_000_000))
        source = f'conversation/{CLAUDE_SESSION[:8]}'

        self.learn(('claude-code', str(first)), ('claude-code', str(second)))
        self.assertEqual(len(self.batches), 1)
        self.assertIn('cache be keyed now?', self.stored()[0]['content'])
        self.assertEqual(self.deleted, [])

        self.batches.clear()
        self.known = {source: 2_000_000}
        _, output = self.learn(('claude-code', str(first)), ('claude-code', str(second)))
        self.assertEqual(self.batches, [])
        self.assertIn('1 unchanged', output)

        self.known = {source: 1_000_000}
        self.learn(('claude-code', str(first)), ('claude-code', str(second)))
        self.assertEqual(self.deleted, [source])
        self.assertEqual(len(self.batches), 1)

    def test_an_unreadable_store_is_reported_and_the_others_are_learned(self):
        project = self.root / 'app'
        project.mkdir()
        readable, unreadable, missing = self.root / 'claude', self.root / 'codex', self.root / 'absent'
        write(readable / 'projects' / '-app' / f'{CLAUDE_SESSION}.jsonl',
              claude_session(str(project), 'How should the cache be keyed?'))
        unreadable.mkdir()
        listdir = os.listdir

        def guarded(path):
            if Path(path) == unreadable:
                raise PermissionError(13, 'Permission denied', str(path))
            return listdir(path)

        with patch.object(learner.os, 'listdir', side_effect=guarded):
            result, output = self.learn(('claude-code', str(readable)), ('codex', str(unreadable)),
                                        ('codex', str(missing)))
        self.assertEqual(result, 1)
        self.assertEqual(len(self.batches), 1)
        self.assertIn(f'Codex {unreadable}: could not be read', output)
        self.assertIn(f'Not learned: Codex {unreadable}', output)
        self.assertIn(f'Codex {missing}: no profile directory', output)

    def test_codex_session_and_turn_cwds_are_ownership_evidence(self):
        project, claimed = self.root / 'app', self.root / 'claimed'
        project.mkdir()
        claimed.mkdir()
        policy.add_root(str(claimed), 'beta')
        rollout = self.root / 'rollout.jsonl'
        write(rollout, codex_rollout(CODEX_FIRST, str(project), 'Where do rollouts get written?'))
        self.assertTrue(policy.transcript_allowed(str(rollout), 'alpha'))
        write(rollout, codex_rollout(CODEX_FIRST, str(project), 'Where do rollouts get written?', turn_cwd=str(claimed)))
        self.assertFalse(policy.transcript_allowed(str(rollout), 'alpha'))
        self.assertTrue(policy.transcript_allowed(str(rollout), 'beta'))

    def test_each_recorded_cwd_is_resolved_once(self):
        # Resolving a path through \\wsl.localhost is a filesystem round trip;
        # Claude Code repeats the cwd on nearly every record.
        project = self.root / 'app'
        (project / 'web').mkdir(parents=True)
        transcript = write(self.root / f'{CLAUDE_SESSION}.jsonl',
                           claude_session(str(project), 'How should the cache be keyed?') * 50
                           + claude_session(str(project / 'web'), 'And for the web app?'))
        with patch.object(policy, 'canonical', wraps=policy.canonical) as canonical:
            self.assertTrue(policy.transcript_allowed(str(transcript), 'alpha'))
        self.assertEqual(sorted(call.args[0] for call in canonical.call_args_list), [str(project), str(project / 'web')])

    def test_stop_hook_import_of_a_codex_rollout_uses_its_session_id(self):
        project = self.root / 'app'
        project.mkdir()
        rollout = write(self.root / f'rollout-2026-10-23T07-43-14-{CODEX_FIRST}.jsonl',
                        codex_rollout(CODEX_FIRST, str(project), 'Where do rollouts get written?'))
        with patch.object(learner.sys, 'argv', ['cli_conversations', '--transcript', str(rollout)]), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(learner.main(), 0)
        self.assertIn('Stored 1', output.getvalue())
        self.assertEqual([record['source'] for record in self.stored()], [f'conversation/{CODEX_FIRST}'])

    def test_learns_a_cline_store_from_session_manifests(self):
        project = self.root / 'app'
        project.mkdir()
        store = self.root / 'cline' / 'data' / 'sessions'
        first = cline_session(store, CLINE_FIRST, str(project), 'How should the cache be keyed?')
        cline_session(store, CLINE_SECOND, str(project), 'Where are sessions written?')
        # A subagent's and a teammate's messages beside the session's own.
        for name in ('agent_k3x9q', 'agent_k3x9q__task_2'):
            (first.parent / f'{name}.messages.json').write_text(
                json.dumps(cline_messages(name, 'Subagent prompt text')), encoding='utf-8')
        # Imported from another client's store, which owns them.
        cline_session(store, '1784094200000_imp01', str(project), 'Imported finished', importedFrom={'tool': 'claude-code'})
        cline_session(store, '1784094200001_imp02', str(project), 'Imported in progress',
                      sessionHistoryOrigin={'mode': 'import', 'trigger': 'codex'})
        # Without a manifest a session records no working directory.
        cline_session(store, '1784094300000_nomf0', str(project), 'Is this one learned?', manifest=False)

        result, output = self.learn(('cline', str(store)))

        self.assertEqual(result, 0, output)
        self.assertIn(f'Cline {store}: 3 conversations', output)
        self.assertIn('1 not this workspace', output)
        stored = self.stored()
        self.assertEqual({record['source'] for record in stored},
                         {f'conversation/{CLINE_FIRST}', f'conversation/{CLINE_SECOND}'})
        content = '\n'.join(record['content'] for record in stored)
        self.assertIn('Q: How should the cache be keyed?\n\nA: Keep the cache', content)
        self.assertIn('After the hook.', content)
        self.assertIn('Continuing.', content)
        self.assertIn('Q: /deep-planning plan the cache rewrite\n\nA: Plan:', content)
        for hidden in ('Subagent prompt', 'Imported', 'Is this one learned', 'mode_notice', 'switched from plan',
                       'user_input', 'attached file body', 'private thinking', 'opaque', 'tool output',
                       '/tool/input/path', 'hook output', 'approved switching', 'Provider error'):
            self.assertNotIn(hidden, content)

    def test_cline_user_text_is_what_the_user_typed(self):
        def typed(text):
            return transcripts.message({'role': 'user', 'content': [{'type': 'text', 'text': text}]})[1]

        self.assertEqual(typed('<user_input mode="plan">Explain the cache</user_input>\n<file_content path="a">x</file_content>'),
                         'Explain the cache')
        # Sessions written before the wrappers hold the typed text as it is.
        self.assertEqual(typed('can you make the filter icon smaller?'), 'can you make the filter icon smaller?')
        self.assertEqual(typed('<user_command slash="newtask"></user_command>'), '/newtask')
        self.assertEqual(typed('<user_input mode="act">[TASK RESUMPTION] This task was interrupted.</user_input>'), '')
        self.assertIsNone(transcripts.message({'role': 'user', 'content': 'compacted summary', 'metadata': {'displayRole': 'Status'}}))

    def test_cline_session_manifests_are_ownership_evidence(self):
        unclaimed, claimed = self.root / 'unclaimed', self.root / 'claimed'
        unclaimed.mkdir()
        claimed.mkdir()
        policy.add_root(str(claimed), 'beta')
        store = self.root / 'sessions'
        mine = cline_session(store, CLINE_FIRST, str(unclaimed), 'Whose conversation is this?')
        # The workspace root counts as well as the cwd.
        theirs = cline_session(store, CLINE_SECOND, str(unclaimed / 'sub'), 'Whose conversation is this?',
                               workspace_root=str(claimed))
        nowhere = cline_session(store, '1784094300000_nomf0', str(unclaimed), 'Whose?', manifest=False)
        self.assertEqual(list(transcripts.cwds(theirs)), [str(unclaimed / 'sub'), str(claimed)])
        self.assertTrue(policy.transcript_allowed(str(mine), 'alpha'))
        self.assertFalse(policy.transcript_allowed(str(theirs), 'alpha'))
        self.assertTrue(policy.transcript_allowed(str(theirs), 'beta'))
        self.assertFalse(policy.transcript_allowed(str(nowhere), 'alpha'))
        self.assertFalse(policy.transcript_allowed(str(nowhere), 'beta'))
        # A subagent's messages are not the session's: they record nothing.
        subagent = mine.parent / 'agent_k3x9q.messages.json'
        subagent.write_text(json.dumps(cline_messages('agent_k3x9q', 'Subagent')), encoding='utf-8')
        self.assertEqual(list(transcripts.cwds(subagent)), [])

    def test_cline_projects_come_from_the_recorded_cwd(self):
        windows = cline_session(self.root / 'windows-sessions', CLINE_FIRST,
                                'C:\\Users\\u\\code\\personal\\shop', 'Which project?')
        self.assertEqual(learner.identify(str(windows)), (f'conversation/{CLINE_FIRST}', 'shop'))
        # A session without a folder open runs in Cline's chat workspace: no project.
        store = self.root / 'sessions'
        chat = self.root / 'home' / 'u' / '.cline' / 'data' / 'workspaces' / 'chat'
        chat.mkdir(parents=True)
        session = cline_session(store, CLINE_SECOND, str(chat), 'What is a good name for a cat?')
        self.assertEqual(learner.identify(str(session)), (f'conversation/{CLINE_SECOND}', ''))
        self.assertEqual(learner.derive_project('/home/u/.cline/data/workspaces/chat'), '')
        result, output = self.learn(('cline', str(store)))
        self.assertEqual(result, 0, output)
        self.assertEqual({(record['source'], record['project']) for record in self.stored()},
                         {(f'conversation/{CLINE_SECOND}', '')})

    def test_a_wsl_cline_store_resolves_like_the_bridge(self):
        wsl = self.root / 'wsl-namespace'

        def native(value):
            if value.startswith(str(self.root)):
                return value
            windows = wsl_paths.linux_to_windows(value, 'Ubuntu', MOUNTS)
            prefix = '\\\\wsl.localhost\\Ubuntu\\'
            self.assertTrue(windows.startswith(prefix), windows)
            return str(wsl.joinpath(*windows[len(prefix):].split('\\')))

        (wsl / 'home' / 'test' / 'code' / 'personal' / 'app').mkdir(parents=True)
        cline_session(wsl / 'home' / 'test' / '.cline' / 'data' / 'sessions', CLINE_FIRST,
                      '/home/test/code/personal/app', 'How should the cache be keyed?')

        with patch.object(wsl_paths, 'native_path', side_effect=native), \
                patch.object(policy, 'native_path', side_effect=native):
            result, output = self.learn(('cline', '/home/test/.cline/data/sessions'))

        self.assertEqual(result, 0, output)
        self.assertEqual({(record['source'], record['project']) for record in self.stored()},
                         {(f'conversation/{CLINE_FIRST}', 'app')})

    def test_learns_a_copilot_home_from_session_events(self):
        project = self.root / 'app'
        project.mkdir()
        home = self.root / 'copilot'
        copilot_session(home, COPILOT_SESSION, str(project), 'How should the cache be keyed?')
        # A caller-chosen id, and a session that records no working directory.
        copilot_session(home, 'release-notes', str(project), 'Where do the release notes go?')
        copilot_session(home, 'no-context', None, 'Is this one learned?')
        (home / 'session-state' / COPILOT_SESSION / 'workspace.yaml').write_text('cwd: /elsewhere\n', encoding='utf-8')

        result, output = self.learn(('copilot', str(home)))

        self.assertEqual(result, 0, output)
        self.assertIn(f'GitHub Copilot {home}: 3 conversations', output)
        self.assertIn('1 not this workspace', output)
        stored = self.stored()
        self.assertEqual({record['source'] for record in stored},
                         {f'conversation/{COPILOT_SESSION}', 'conversation/release-notes'})
        content = '\n'.join(record['content'] for record in stored)
        self.assertIn('Q: How should the cache be keyed?\n\nA: Keep the cache', content)
        self.assertIn('After autopilot.', content)
        self.assertIn('Q: And the second question?\n\nA: Second answer.', content)
        for hidden in ('Skill instructions', 'Subagent', 'Nested answer', 'Continue with the task', 'private reasoning',
                       'opaque', 'tool output', '/tool/input/path', 'model-facing', 'current_datetime', 'Is this one learned'):
            self.assertNotIn(hidden, content)

    def test_copilot_context_events_are_ownership_evidence(self):
        project, claimed = self.root / 'app', self.root / 'claimed'
        project.mkdir()
        claimed.mkdir()
        policy.add_root(str(claimed), 'beta')
        home = self.root / 'copilot'
        mine = copilot_session(home, 'mine', str(project), 'Whose conversation is this?')
        moved = copilot_session(home, 'moved', str(project), 'Whose conversation is this?', changed_cwd=str(claimed))
        # A subagent working in another workspace's project keeps the session there too.
        delegated = copilot_session(home, 'delegated', str(project), 'Whose?', subagent_cwd=str(claimed))
        nowhere = copilot_session(home, 'nowhere', None, 'Whose?')
        self.assertEqual(list(transcripts.cwds(moved)), [str(project), str(claimed)])
        self.assertTrue(policy.transcript_allowed(str(mine), 'alpha'))
        for theirs in (moved, delegated):
            self.assertFalse(policy.transcript_allowed(str(theirs), 'alpha'))
            self.assertTrue(policy.transcript_allowed(str(theirs), 'beta'))
        self.assertFalse(policy.transcript_allowed(str(nowhere), 'alpha'))
        self.assertFalse(policy.transcript_allowed(str(nowhere), 'beta'))

    def test_copilot_sessions_are_named_from_their_recorded_cwd(self):
        home = self.root / 'copilot'
        windows = copilot_session(home, COPILOT_SESSION, 'C:\\Users\\u\\code\\personal\\shop', 'Which project?')
        self.assertEqual(learner.identify(str(windows)), (f'conversation/{COPILOT_SESSION}', 'shop'))
        linux = copilot_session(home, 'linux', '/home/u/code/personal/imprint-memory-layer', 'Which project?')
        self.assertEqual(learner.identify(str(linux)), ('conversation/linux', 'imprint-memory-layer'))

    def test_a_wsl_copilot_home_resolves_like_the_bridge(self):
        wsl = self.root / 'wsl-namespace'

        def native(value):
            if value.startswith(str(self.root)):
                return value
            windows = wsl_paths.linux_to_windows(value, 'Ubuntu', MOUNTS)
            prefix = '\\\\wsl.localhost\\Ubuntu\\'
            self.assertTrue(windows.startswith(prefix), windows)
            return str(wsl.joinpath(*windows[len(prefix):].split('\\')))

        (wsl / 'home' / 'test' / 'code' / 'personal' / 'app').mkdir(parents=True)
        copilot_session(wsl / 'home' / 'test' / '.copilot', COPILOT_SESSION, '/home/test/code/personal/app',
                        'How should the cache be keyed?')

        with patch.object(wsl_paths, 'native_path', side_effect=native), \
                patch.object(policy, 'native_path', side_effect=native):
            result, output = self.learn(('copilot', '/home/test/.copilot'))

        self.assertEqual(result, 0, output)
        self.assertEqual({(record['source'], record['project']) for record in self.stored()},
                         {(f'conversation/{COPILOT_SESSION}', 'app')})

    def test_stop_hook_import_of_a_copilot_session_uses_its_session_id(self):
        project = self.root / 'app'
        project.mkdir()
        events = copilot_session(self.root / 'copilot', COPILOT_SESSION, str(project), 'How should the cache be keyed?')
        with patch.object(learner.sys, 'argv', ['cli_conversations', '--transcript', str(events)]), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(learner.main(), 0)
        self.assertIn('Stored 2', output.getvalue())
        self.assertEqual({record['source'] for record in self.stored()}, {f'conversation/{COPILOT_SESSION}'})

    def test_project_names_follow_claude_folder_naming_of_the_recorded_cwd(self):
        self.assertEqual(learner.derive_project('/home/u/code/personal/imprint-memory-layer'), 'imprint-memory-layer')
        self.assertEqual(learner.derive_project('/home/u/code/brightspaces/node/office-client'),
                         'brightspaces-node-office-client')
        self.assertEqual(learner.derive_project('/home/u/code/personal'), 'personal')
        self.assertEqual(learner.derive_project('/home/u'), '')
        # macOS and Windows homes name projects as a Linux home does.
        self.assertEqual(learner.derive_project('/Users/u/code/personal/imprint-memory-layer'), 'imprint-memory-layer')
        self.assertEqual(learner.derive_project('C:\\Users\\u\\code\\personal\\imprint'), 'imprint')
        self.assertEqual(learner.derive_project('/Volumes/data/code/imprint'), '')


if __name__ == '__main__':
    unittest.main()
