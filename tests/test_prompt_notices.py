"""Claude Code's notice hooks (ADRs 0212, 0240): the session's Imprint server
prepares what it would announce in a file for its conversation, and a few
lines of shell print it once, after the next tool call or on the next
prompt, without starting Python."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from emulation import slack
from imprint import config, notices, tasks, tasks_tool

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / 'imprint' / 'prompt_notices.sh'
CONVERSATION = '5c1d2e3f-aaaa-bbbb-cccc-111122223333'


def _shells() -> dict:
    """sh and bash as Claude Code runs hooks: the system's, or Git Bash's on
    Windows (never WSL's bash.exe, which would read Windows paths wrongly)."""
    if os.name != 'nt':
        return {name: found for name in ('sh', 'bash') if (found := shutil.which(name))}
    git = shutil.which('git')
    roots = [Path(git).resolve().parent.parent] if git else []
    roots.append(Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Git')
    for root in roots:
        if (root / 'bin' / 'bash.exe').exists():
            return {'sh': str(root / 'bin' / 'sh.exe'), 'bash': str(root / 'bin' / 'bash.exe')}
    return {}


SHELLS = _shells()


@unittest.skipUnless('sh' in SHELLS, 'needs sh, or Git Bash on Windows')
class PromptNoticeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data = Path(directory.name)
        for patcher in (patch.object(config, 'get_data_dir', return_value=self.data),
                        patch.object(tasks.actors, '_device', return_value='device-a'),
                        patch.dict(os.environ, {'IMPRINT_WORKSPACE': '', 'CLAUDE_CODE_SESSION_ID': CONVERSATION})):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.task = tasks.create('Fix the total', project='shop', workspace='default')['id']
        tasks_tool.choose_name('grimforge', actor=tasks_tool.actor('claude-code'))

    def prepared(self):
        return self.data / 'prompt-notices' / f'{CONVERSATION}.notice'

    def hook(self, payload=None, shell=('sh',), event=None):
        """The hook as the WSL bridge runs it (sh with arguments), or with
        ``shell=('bash', '-c')`` as Claude Code sources it in Git Bash.
        Without ``event`` it is called as releases before ADR 0240 did."""
        payload = {'session_id': CONVERSATION, 'hook_event_name': event or 'UserPromptSubmit', 'prompt': 'hi'} \
            if payload is None else payload
        if shell[0] not in SHELLS:
            self.skipTest(f'needs {shell[0]}')
        if len(shell) == 1:
            command = [SHELLS[shell[0]], str(SCRIPT), str(self.data), *([event] if event else [])]
        else:  # Forward slashes, as setup registers it.
            named = f'IMPRINT_NOTICE_EVENT={event} ' if event else ''
            command = [SHELLS[shell[0]], *shell[1:],
                       f"IMPRINT_PROMPT_DATA='{self.data.as_posix()}' {named}. '{SCRIPT.as_posix()}'"]
        return subprocess.run(command, input=json.dumps(payload) if isinstance(payload, dict) else payload,
                              capture_output=True, text=True, timeout=slack(30))

    def test_a_comment_reaches_the_next_prompt_once_and_is_then_told(self):
        tasks.comment(self.task, '@grimforge the tax rule changed', workspace='default')
        tasks_tool.refresh_prompt_notices(client='claude-code')
        first = self.hook()
        self.assertEqual(first.returncode, 0, first.stderr)
        context = json.loads(first.stdout)['hookSpecificOutput']
        self.assertEqual(context['hookEventName'], 'UserPromptSubmit')
        self.assertIn(f'New comment on {self.task}', context['additionalContext'])
        self.assertIn('the tax rule changed', context['additionalContext'])
        self.assertEqual(self.hook().stdout, '')  # Once.
        tasks_tool.refresh_prompt_notices(client='claude-code')  # The server sees it was taken...
        self.assertFalse(self.prepared().exists())
        self.assertEqual(tasks_tool.notices(client='claude-code'), '')  # ...and a tool reply does not repeat it.

    def test_what_a_tool_reply_told_leaves_no_file_behind(self):
        tasks.comment(self.task, '@grimforge look here', workspace='default')
        tasks_tool.refresh_prompt_notices(client='claude-code')
        self.assertTrue(self.prepared().exists())
        self.assertIn('look here', tasks_tool.notices(client='claude-code'))
        self.assertFalse(self.prepared().exists())
        self.assertEqual(self.hook().stdout, '')

    def test_nothing_new_means_no_file_and_a_quiet_hook(self):
        tasks_tool.refresh_prompt_notices(client='claude-code')
        self.assertFalse(self.prepared().exists())
        for shell in (('sh',), ('sh', '-c'), ('bash', '-c')):
            for payload in (None, {'hook_event_name': 'UserPromptSubmit'}, {'session_id': '../../etc/passwd'}, 'not json'):
                with self.subTest(shell=shell, payload=payload):
                    done = self.hook(payload, shell)
                    self.assertEqual((done.returncode, done.stdout), (0, ''))

    def test_sourced_in_bash_or_sh_it_prints_once_and_a_long_prompt_costs_nothing(self):
        for shell in (('sh', '-c'), ('bash', '-c')):
            with self.subTest(shell=shell):
                tasks.comment(self.task, f'@grimforge via {shell[0]}', workspace='default')
                tasks_tool.refresh_prompt_notices(client='claude-code')
                long = {'session_id': CONVERSATION, 'hook_event_name': 'UserPromptSubmit', 'prompt': 'x' * 300_000}
                self.assertIn(f'via {shell[0]}', self.hook(long, shell).stdout)
                self.assertEqual(self.hook(long, shell).stdout, '')

    def test_a_session_without_a_conversation_id_prepares_nothing(self):
        tasks.comment(self.task, 'for the holder', workspace='default')
        with patch.dict(os.environ, {'CLAUDE_CODE_SESSION_ID': ''}):
            tasks_tool.refresh_prompt_notices(client='claude-code')
        self.assertFalse((self.data / 'prompt-notices').exists() and any((self.data / 'prompt-notices').iterdir()))

    def test_files_that_ended_conversations_left_are_swept(self):
        folder = self.data / 'prompt-notices'
        folder.mkdir()
        # A release before ADR 0240 wrote <conversation>.json; those go the same way.
        old, older, fresh = (folder / 'ended-conversation.json', folder / 'ended-conversation.notice',
                             folder / 'other-conversation.notice')
        for path in (old, older, fresh):
            path.write_text('{}')
        for path in (old, older):
            os.utime(path, (time.time() - 3 * 86400,) * 2)
        with patch.dict(notices._swept, {'at': 0.0}):
            tasks_tool.refresh_prompt_notices(client='claude-code')
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ['other-conversation.notice'])

    def test_after_a_tool_call_the_hook_answers_as_a_post_tool_use_hook(self):
        for shell in (('sh',), ('sh', '-c'), ('bash', '-c')):
            with self.subTest(shell=shell):
                tasks.comment(self.task, f'@grimforge mid-turn via {shell}', workspace='default')
                tasks_tool.refresh_prompt_notices(client='claude-code')
                done = self.hook(shell=shell, event='PostToolUse')
                context = json.loads(done.stdout)['hookSpecificOutput']
                self.assertEqual(context['hookEventName'], 'PostToolUse')
                self.assertIn('mid-turn', context['additionalContext'])
                self.assertEqual(self.hook(shell=shell, event='PostToolUse').stdout, '')
                self.assertEqual(self.hook(shell=shell, event='UserPromptSubmit').stdout, '')  # once, by either hook

    def test_an_unknown_event_prints_nothing(self):
        tasks.comment(self.task, '@grimforge hello', workspace='default')
        tasks_tool.refresh_prompt_notices(client='claude-code')
        self.assertEqual(self.hook(event='Stop').stdout, '')
        self.assertTrue(self.prepared().exists())

    def test_the_server_records_which_hook_told_it_and_when(self):
        told = []
        notices.register_provider('probe', lambda me, ws: notices.Section('Probe:', ['probe:1'], ['one']))
        notices.register_settler('probe', lambda keys, session, via, at, workspace=None: told.append((keys, via, at)) or keys)
        self.addCleanup(notices._providers.pop, 'probe', None)
        self.addCleanup(notices._settlers.pop, 'probe', None)
        tasks_tool.refresh_prompt_notices(client='claude-code')
        self.assertIn('one', self.hook(event='PostToolUse').stdout)
        notices._providers.pop('probe')
        tasks_tool.refresh_prompt_notices(client='claude-code')  # the server sees the hook took it
        [(keys, via, at)] = told
        self.assertEqual((keys, via), (['probe:1'], 'tool'))
        self.assertLess(abs(at - time.time()), 30)

    def test_a_comment_for_the_holder_pokes_its_conversation(self):
        tasks.claim(self.task, actor=tasks_tool.actor('claude-code'), workspace='default')
        tasks_tool.refresh_prompt_notices(client='claude-code')
        self.assertFalse(tasks_tool.poked())
        time.sleep(0.05)
        tasks.comment(self.task, 'Stop and check the tax table first', actor=tasks.USER, workspace='default')
        self.assertTrue((self.data / 'prompt-notices' / f'{CONVERSATION}.poke').exists())
        self.assertTrue(tasks_tool.poked())
        tasks_tool.refresh_prompt_notices(client='claude-code')
        self.assertFalse(tasks_tool.poked())
        self.assertIn('Stop and check the tax table', self.hook(event='PostToolUse').stdout)

    @unittest.skipIf(os.name == 'nt', 'Git Bash timing is measured by hand (ADR 0212)')
    def test_the_hook_adds_well_under_100_ms(self):
        for event in ('UserPromptSubmit', 'PostToolUse'):
            with self.subTest(event=event):
                tasks.comment(self.task, '@grimforge quick', workspace='default')
                times = []
                for _ in range(5):
                    tasks_tool.refresh_prompt_notices(client='claude-code')
                    started = time.perf_counter()
                    self.hook(event=event)
                    times.append(time.perf_counter() - started)
                    tasks.comment(self.task, '@grimforge again', workspace='default')
                self.assertLess(sorted(times)[2], slack(0.1))


class ComposeTests(unittest.TestCase):
    def test_sections_fit_under_the_cap_or_say_where_the_rest_is(self):
        big = notices.Section('Messages:', ['msg:1'], ['x' * 5000], more='Messages: more in your inbox.')
        small = notices.Section('From the Imprint Board:', ['notified:1'], ['New comment'])
        keys, text = notices.compose([small, big, big], cap=8000)
        self.assertEqual(keys, ['notified:1', 'msg:1'])  # the third did not fit, so its key waits
        self.assertIn('Messages: more in your inbox.', text)
        self.assertLessEqual(len(text), 8000)

    def test_keys_whose_prefix_has_no_handler_are_kept(self):
        seen = []
        notices.register_settler('known', lambda keys, session, via, at, workspace=None: seen.extend(keys) or keys)
        self.addCleanup(notices._settlers.pop, 'known', None)
        told = notices.settle(['known:1', 'stranger:2', 'no-prefix'], 'conversation:x', 'inbox')
        self.assertEqual((told, seen), (['known:1'], ['known:1']))
        with self.assertRaises(ValueError):
            notices.settle(['known:1'], 'conversation:x', 'carrier-pigeon')


if __name__ == '__main__':
    unittest.main()
