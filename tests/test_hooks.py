import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ENTRY = Path(__file__).resolve().parents[1] / 'imprint' / 'hook_entry.py'


class HookTests(unittest.TestCase):
    def run_hook(self, action, data_dir, payload):
        return subprocess.run([sys.executable, str(ENTRY), action, str(data_dir)],
                              input=json.dumps(payload), text=True, capture_output=True)

    def test_a_read_before_searching_is_never_refused_and_reminds_once(self):
        # ADR 0142: the reminder replaces the old gate, which refused the read (exit 2).
        with tempfile.TemporaryDirectory(prefix="imprint's hook ") as directory:
            root = Path(directory)
            payload = {'hook_event_name': 'PreToolUse', 'session_id': '../../outside', 'tool_name': 'Read'}
            first = self.run_hook('pre-read', root, payload)
            self.assertEqual(first.returncode, 0, first.stderr)
            output = json.loads(first.stdout)['hookSpecificOutput']
            self.assertEqual(output['hookEventName'], 'PreToolUse')
            self.assertNotIn('permissionDecision', output)
            self.assertIn("Prefer searching it with Imprint's search tool", output['additionalContext'])
            again = self.run_hook('pre-read', root, payload)
            self.assertEqual((again.returncode, again.stdout), (0, ''), 'once per session')
            # A session that searched first is not reminded at all.
            self.assertEqual(self.run_hook('post-search', root, {'session_id': 'other'}).returncode, 0)
            searched = self.run_hook('pre-read', root, {'hook_event_name': 'PreToolUse', 'session_id': 'other'})
            self.assertEqual((searched.returncode, searched.stdout), (0, ''))
            # Session names never become paths outside the sessions folder.
            self.assertTrue(all(path.parent == root / '.sessions' for path in (root / '.sessions').iterdir()))

    def test_cursor_reads_are_allowed_silently_and_reminded_after_the_result(self):
        # Cursor's preToolUse only allows or denies (bad output denies), so it says nothing;
        # its postToolUse hook carries the reminder as additional_context.
        with tempfile.TemporaryDirectory() as directory:
            payload = {'hook_event_name': 'preToolUse', 'conversation_id': 'c1', 'cursor_version': '2.1', 'tool_name': 'Read'}
            before = self.run_hook('pre-read', directory, payload)
            self.assertEqual((before.returncode, before.stdout), (0, ''))
            after = self.run_hook('cursor-post-read', directory, {**payload, 'hook_event_name': 'postToolUse'})
            self.assertEqual(after.returncode, 0, after.stderr)
            self.assertIn("Imprint's search tool", json.loads(after.stdout)['additional_context'])
            self.assertEqual(self.run_hook('cursor-post-read', directory, {**payload, 'hook_event_name': 'postToolUse'}).stdout, '')
            # Cursor's sessions are told apart by conversation_id.
            other = self.run_hook('cursor-post-read', directory, {**payload, 'conversation_id': 'c2'})
            self.assertIn('additional_context', json.loads(other.stdout))

    def test_reminder_and_stop_emit_valid_json(self):
        with tempfile.TemporaryDirectory() as directory:
            for action in ('session-start', 'copilot-session-start', 'stop', 'pre-compact', 'cursor-pre-compact'):
                result = self.run_hook(action, directory, {})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIsInstance(json.loads(result.stdout), dict)

    def test_session_start_context_has_each_clients_shape(self):
        # Claude Code and Codex read hookSpecificOutput; GitHub Copilot reads
        # only a top-level additionalContext (its PascalCase SessionStart).
        payload = {'hook_event_name': 'SessionStart', 'session_id': 's1', 'cwd': '/work', 'source': 'startup'}
        with tempfile.TemporaryDirectory() as directory:
            claude = json.loads(self.run_hook('session-start', directory, payload).stdout)
            copilot = json.loads(self.run_hook('copilot-session-start', directory, payload).stdout)
        self.assertEqual(claude['hookSpecificOutput']['hookEventName'], 'SessionStart')
        self.assertEqual(copilot, {'additionalContext': claude['hookSpecificOutput']['additionalContext']})

    def test_session_start_tells_the_agent_about_the_projects_board(self):
        import os
        from unittest.mock import patch
        from imprint import config, tasks
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data, shop = root / 'data', root / 'shop'
            data.mkdir()
            shop.mkdir()
            (shop / 'package.json').write_text('{"name": "shop"}')
            empty = json.loads(self.run_hook('session-start', data, {'session_id': 's1', 'cwd': str(shop)}).stdout)
            self.assertNotIn('Board', empty['hookSpecificOutput']['additionalContext'])
            with patch.object(config, 'get_data_dir', return_value=data), patch.dict(os.environ, {'IMPRINT_WORKSPACE': ''}):
                ready = tasks.create('Fix the total', project='shop', acceptance=['x'], status='ready')['id']
                tasks.create('Elsewhere', project='blog', acceptance=['x'], status='ready')
                tasks.choose_name('conversation:s1', 'grimforge', pid=os.getpid())
                tasks.comment(ready, '@grimforge the tax rule changed')
            context = json.loads(self.run_hook('session-start', data, {'session_id': 's1', 'cwd': str(shop)}).stdout)
            text = context['hookSpecificOutput']['additionalContext']
            self.assertIn('Board', text)
            self.assertIn(ready, text)
            self.assertNotIn('Elsewhere', text)
            self.assertIn('1 unread comment', text)
            self.assertIn('You are grimforge', text)
            self.assertLess(len(text), 2400)
            unnamed = json.loads(self.run_hook('session-start', data, {'session_id': 's9', 'cwd': str(shop)}).stdout)
            self.assertIn('task_edit(action="name"', unnamed['hookSpecificOutput']['additionalContext'])
            # A Codex session is told of comments to Codex: its transcript lives under ~/.codex.
            with patch.object(config, 'get_data_dir', return_value=data), patch.dict(os.environ, {'IMPRINT_WORKSPACE': ''}):
                tasks.comment(ready, 'Codex: rebase first', to=['codex'])
            codex = json.loads(self.run_hook('session-start', data, {
                'session_id': 'x1', 'cwd': str(shop), 'transcript_path': '/home/me/.codex/sessions/x1.jsonl'}).stdout)
            self.assertIn('1 unread comment', codex['hookSpecificOutput']['additionalContext'])


    def test_copilot_stop_payload_imports_its_transcript(self):
        # Copilot's PascalCase Stop payload is Claude Code's: session_id, cwd,
        # transcript_path, stop_reason and stop_hook_active.
        from unittest.mock import patch
        from imprint import hook_entry
        transcript = '/home/me/.copilot/session-state/3f2a/events.jsonl'
        payload = {'hook_event_name': 'Stop', 'session_id': '3f2a', 'cwd': '/home/me/app', 'timestamp': '2026-10-25T08:00:00Z',
                   'transcript_path': transcript, 'stop_reason': 'end_turn', 'stop_hook_active': False}
        for active, expected in ((False, 1), (True, 0)):
            payload['stop_hook_active'] = active
            with tempfile.TemporaryDirectory() as directory, \
                    patch.object(hook_entry.sys, 'argv', ['hook_entry.py', 'stop', directory]), \
                    patch.object(hook_entry.sys, 'stdin', io.StringIO(json.dumps(payload))), \
                    patch.object(hook_entry.sys, 'stdout', io.StringIO()), \
                    patch.dict(hook_entry.os.environ), \
                    patch.object(hook_entry.subprocess, 'Popen') as consume:
                self.assertEqual(hook_entry.main(), 0)
            self.assertEqual(consume.call_count, expected)
            if expected:
                self.assertEqual(consume.call_args.args[0][1:], ['-m', 'imprint.session_learn', transcript])

    def test_stop_starts_a_detached_learner_and_returns(self):
        # The client's hook timeout must never reach the learner (ADR 0077).
        from unittest.mock import patch
        from imprint import hook_entry
        payload = {'session_id': 's', 'cwd': '/home/me/app', 'transcript_path': '/home/me/t.jsonl'}
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(hook_entry.sys, 'argv', ['hook_entry.py', 'stop', directory]), \
                patch.object(hook_entry.sys, 'stdin', io.StringIO(json.dumps(payload))), \
                patch.object(hook_entry.sys, 'stdout', io.StringIO()) as stdout, \
                patch.dict(hook_entry.os.environ), \
                patch.object(hook_entry.subprocess, 'Popen') as learner:
            self.assertEqual(hook_entry.main(), 0)
        options = learner.call_args.kwargs
        for stream in ('stdin', 'stdout', 'stderr'):
            self.assertEqual(options[stream], subprocess.DEVNULL)
        if sys.platform == 'win32':
            self.assertTrue(options['creationflags'] & subprocess.CREATE_NEW_PROCESS_GROUP)
        else:
            self.assertTrue(options['start_new_session'])
        learner.return_value.wait.assert_not_called()
        self.assertEqual(json.loads(stdout.getvalue()), {})

    # Cursor (ADR 0236): its payloads name no cwd, and it runs Claude Code's
    # hooks beside its own, so Imprint's hook for one event can run twice.

    def cursor_payload(self, event, **over):
        return {'conversation_id': 'c1', 'session_id': 'c1', 'generation_id': 'g1', 'hook_event_name': event,
                'cursor_version': '2026.11.22-e9042b2', 'workspace_roots': [], 'transcript_path': None, **over}

    def test_cursors_first_workspace_root_stands_for_the_sessions_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'shop').mkdir()
            (root / 'shop' / 'package.json').write_text('{"name": "shop"}')
            payload = self.cursor_payload('sessionStart', workspace_roots=[str(root / 'shop'), str(root)])
            context = json.loads(self.run_hook('session-start', root / 'data', payload).stdout)
            unnamed = json.loads(self.run_hook('session-start', root / 'data', self.cursor_payload('sessionStart', session_id='c2',
                                                                                             conversation_id='c2')).stdout)
        text = context['hookSpecificOutput']['additionalContext']
        self.assertIn('workspace', text)
        self.assertNotEqual(text, unnamed['hookSpecificOutput']['additionalContext'])

    def test_one_of_imprints_hooks_answers_each_cursor_event(self):
        with tempfile.TemporaryDirectory() as directory:
            start = self.cursor_payload('sessionStart')
            first, second = (json.loads(self.run_hook('session-start', directory, start).stdout) for _ in range(2))
            self.assertIn('additionalContext', first['hookSpecificOutput'])
            self.assertEqual(second, {})
            # Another session, and a later turn of the same one, are answered.
            other = json.loads(self.run_hook('session-start', directory, {**start, 'session_id': 'c2'}).stdout)
            self.assertIn('hookSpecificOutput', other)
            compact = self.cursor_payload('preCompact', generation_id='g2')
            answers = [json.loads(self.run_hook(action, directory, compact).stdout) for action in ('pre-compact', 'cursor-pre-compact')]
            self.assertEqual(answers, [{'user_message': 'Save durable decisions and findings with Imprint before compaction.'}, {}])
            # Claude Code's own payloads are answered every time.
            claude = {'hook_event_name': 'PreCompact', 'session_id': 'c1'}
            for _ in range(2):
                self.assertIn('systemMessage', json.loads(self.run_hook('pre-compact', directory, claude).stdout))
            self.assertEqual(len(list((Path(directory) / '.sessions' / 'once').iterdir())), 3)

    def test_a_cursor_stop_learns_its_transcript_once(self):
        from unittest.mock import patch
        from imprint import hook_entry
        with tempfile.TemporaryDirectory() as directory:
            transcript = str(Path(directory) / 'agent-transcripts' / 'c1' / 'c1.jsonl')
            payload = self.cursor_payload('stop', workspace_roots=[directory], transcript_path=transcript, status='completed')
            for event, starts in (('stop', 1), ('stop', 0), ('sessionEnd', 1)):
                with patch.object(hook_entry.sys, 'argv', ['hook_entry.py', 'stop', directory]), \
                        patch.object(hook_entry.sys, 'stdin', io.StringIO(json.dumps({**payload, 'hook_event_name': event}))), \
                        patch.object(hook_entry.sys, 'stdout', io.StringIO()) as stdout, \
                        patch.dict(hook_entry.os.environ), \
                        patch.object(hook_entry, 'remember_transcript'), \
                        patch.object(hook_entry.subprocess, 'Popen') as learner:
                    self.assertEqual(hook_entry.main(), 0)
                self.assertEqual(json.loads(stdout.getvalue()), {})
                self.assertEqual(learner.call_count, starts, event)
                if starts:
                    self.assertEqual(learner.call_args.args[0][1:], ['-m', 'imprint.session_learn', transcript])

    def test_a_payload_after_byte_order_marks_is_read(self):
        # Cursor on Windows sends one, or two on some computers (ADR 0236).
        import hashlib
        for marks, session in ((1, 'c1'), (2, 'c2')):
            with tempfile.TemporaryDirectory() as directory:
                payload = self.cursor_payload('postToolUse', session_id=session)
                result = subprocess.run([sys.executable, str(ENTRY), 'post-search', directory],
                                        input=('\ufeff' * marks + json.dumps(payload)).encode('utf-8'), capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue((Path(directory) / '.sessions' / hashlib.sha256(session.encode()).hexdigest()).exists(), marks)
