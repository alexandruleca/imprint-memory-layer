import os
import stat
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from imprint import chat


def _cli(directory: Path, body: str) -> str:
    """A stand-in for `imprint profile add-llm`."""
    script = directory / 'imprint'
    script.write_text('#!/bin/sh\n' + body)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


@unittest.skipIf(os.name == 'nt', 'stand-in CLI is a POSIX shell script')
class ProvisioningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.site = self.tmp / 'site'
        self.site.mkdir()
        self.model = self.tmp / 'model.gguf'
        self.model.write_bytes(b'gguf')
        sys.path.insert(0, str(self.site))
        self.addCleanup(sys.path.remove, str(self.site))
        self.addCleanup(sys.modules.pop, 'llama_cpp', None)
        replacements = {
            'llama_cpp': None, '_llm': None, '_provisioning': None,
            '_is_remote_provider': MagicMock(return_value=False),
            '_resolve_local_model_path': MagicMock(return_value=(self.model, None)),
            '_models_dir': MagicMock(return_value=self.tmp),
        }
        for patcher in [patch.object(chat, name, value) for name, value in replacements.items()] + \
                [patch('imprint._llama_compat.apply')]:
            patcher.start()
            self.addCleanup(patcher.stop)

    def _install_creates_binding(self) -> str:
        package = self.site / 'llama_cpp'
        return _cli(self.tmp, f"printf '\\033[32m[+] Installing llama-cpp-python\\033[0m\\n'\n"
                              f"mkdir -p '{package}' && : > '{package}/__init__.py'\n")

    def test_binding_installed_while_running_is_picked_up(self):
        self.assertIsNone(chat._llama())
        self.assertIn('llama_cpp', chat._llama_import_error)
        (self.site / 'llama_cpp').mkdir()
        (self.site / 'llama_cpp' / '__init__.py').write_text('')
        self.assertEqual(chat._llama().__name__, 'llama_cpp')
        self.assertIsNone(chat._llama_import_error)

    def test_first_local_chat_installs_the_binding_then_answers(self):
        llm = object()
        with patch('imprint.queue.find_imprint_binary', return_value=self._install_creates_binding()), \
             patch.object(chat, 'load_model', return_value=(llm, None)), \
             patch.object(chat, '_stream_local', return_value=iter(['Hello.'])):
            events = list(chat.stream_reply([], 'hi'))
        kinds = [event['type'] for event in events]
        self.assertEqual(kinds, ['install_start', 'install_progress', 'install_complete',
                                 'load_start', 'token', 'assistant_message', 'done'])
        self.assertEqual(events[1]['line'], '[+] Installing llama-cpp-python')

    def test_concurrent_chats_follow_one_install(self):
        release, runs = threading.Event(), []

        def install(job):
            runs.append(job)
            job.emit({'type': 'install_progress', 'line': 'working'})
            release.wait(5)
            (self.site / 'llama_cpp').mkdir()
            (self.site / 'llama_cpp' / '__init__.py').write_text('')

        with patch.object(chat, '_install_binding', side_effect=install):
            first, second = chat._provision(), chat._provision()
            self.assertIs(first, second)
            seen = []
            follower = threading.Thread(target=lambda: seen.extend(second.follow()))
            follower.start()
            release.set()
            events = list(first.follow())
            follower.join(5)
        self.assertEqual(len(runs), 1)
        self.assertEqual(events, seen)
        self.assertEqual([e['type'] for e in events], ['install_start', 'install_progress', 'install_complete'])
        self.assertIsNone(first.error)

    def test_failed_install_ends_the_chat_with_its_output(self):
        cli = _cli(self.tmp, "echo 'error: no matching distribution'\nexit 1\n")
        with patch('imprint.queue.find_imprint_binary', return_value=cli):
            events = list(chat.stream_reply([], 'hi'))
        self.assertEqual(events[-2]['type'], 'error')
        self.assertIn('no matching distribution', events[-2]['error'])
        self.assertEqual(events[-1], {'type': 'done'})
        # A later chat retries instead of replaying the failed job.
        failed = chat._provisioning
        with patch.object(chat, '_run_provisioning'):
            self.assertIsNot(chat._provision(), failed)

    def test_status_reports_missing_binding_without_an_error(self):
        status = chat.status()
        self.assertFalse(status['installed'])
        self.assertIsNone(status['error'])


class StreamingTests(unittest.TestCase):
    def _reply(self, *iterations):
        with patch.object(chat, '_is_remote_provider', return_value=True), \
             patch.object(chat, '_get_chat_provider', return_value='ollama'), \
             patch.object(chat, '_stream_remote', side_effect=[iter(parts) for parts in iterations]), \
             patch.dict(chat.TOOLS, {'status': lambda workspace='': 'all good'}):
            return list(chat.stream_reply([], 'hi'))

    def test_tool_call_markup_is_never_streamed(self):
        events = self._reply(['Let me check <tool', '_call>{"name": "status", "args": {}}</tool_call>'],
                             ['All ', 'good.'])
        tokens = [e['text'] for e in events if e['type'] == 'token']
        self.assertEqual(tokens, ['Let me check ', 'All ', 'good.'])
        self.assertIn({'type': 'tool_result', 'name': 'status', 'result': 'all good'}, events)
        self.assertEqual(events[-2], {'type': 'assistant_message', 'text': 'Let me check\nAll good.'})

    def test_held_back_text_that_is_not_a_tag_still_streams(self):
        events = self._reply(['1 <', ' 2'])
        self.assertEqual([e['text'] for e in events if e['type'] == 'token'], ['1 ', '< 2'])

    def test_stopped_reply_keeps_what_was_streamed(self):
        from imprint import api
        replies = iter([{'type': 'token', 'text': 'Half an '}, {'type': 'token', 'text': 'answer'},
                        {'type': 'token', 'text': ' never seen'}])
        with patch.object(api.chat_mod, 'stream_reply', return_value=replies), \
             patch.object(api.chat_store, 'append_message') as append:
            events = api._chat_events('s1', [], 'hi', 'default')
            next(events), next(events)
            events.close()
        append.assert_called_once_with('s1', role='assistant', content='Half an answer')

    def test_completed_reply_is_recorded_once(self):
        from imprint import api
        replies = iter([{'type': 'token', 'text': 'Done'}, {'type': 'assistant_message', 'text': 'Done'},
                        {'type': 'done'}])
        with patch.object(api.chat_mod, 'stream_reply', return_value=replies), \
             patch.object(api.chat_store, 'append_message') as append:
            list(api._chat_events('s1', [], 'hi', 'default'))
        append.assert_called_once_with('s1', role='assistant', content='Done')


if __name__ == '__main__':
    unittest.main()
