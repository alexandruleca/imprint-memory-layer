"""Claude Code's prompt hook (ADR 0212): the session's Imprint server
prepares what it would announce in a file for its conversation, and a few
lines of shell print it once on the next prompt, without starting Python."""
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
from imprint import config, tasks, tasks_tool

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
        return self.data / 'prompt-notices' / f'{CONVERSATION}.json'

    def hook(self, payload=None, shell=('sh',)):
        """The hook as the WSL bridge runs it (sh with an argument), or with
        ``shell=('bash', '-c')`` as Claude Code sources it in Git Bash."""
        payload = {'session_id': CONVERSATION, 'hook_event_name': 'UserPromptSubmit', 'prompt': 'hi'} if payload is None else payload
        if shell[0] not in SHELLS:
            self.skipTest(f'needs {shell[0]}')
        if len(shell) == 1:
            command = [SHELLS[shell[0]], str(SCRIPT), str(self.data)]
        else:  # Forward slashes, as setup registers it.
            command = [SHELLS[shell[0]], *shell[1:], f"IMPRINT_PROMPT_DATA='{self.data.as_posix()}' . '{SCRIPT.as_posix()}'"]
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
        old, fresh = folder / 'ended-conversation.json', folder / 'other-conversation.json'
        for path in (old, fresh):
            path.write_text('{}')
        os.utime(old, (time.time() - 3 * 86400,) * 2)
        with patch.dict(tasks_tool._swept, {'at': 0.0}):
            tasks_tool.refresh_prompt_notices(client='claude-code')
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ['other-conversation.json'])

    @unittest.skipIf(os.name == 'nt', 'Git Bash timing is measured by hand (ADR 0212)')
    def test_the_hook_adds_well_under_100_ms(self):
        tasks.comment(self.task, '@grimforge quick', workspace='default')
        times = []
        for _ in range(5):
            tasks_tool.refresh_prompt_notices(client='claude-code')
            started = time.perf_counter()
            self.hook()
            times.append(time.perf_counter() - started)
            tasks.comment(self.task, '@grimforge again', workspace='default')
        self.assertLess(sorted(times)[2], slack(0.1))


if __name__ == '__main__':
    unittest.main()
