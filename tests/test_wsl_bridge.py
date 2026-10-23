import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from imprint import wsl_paths, wsl_bridge


class WSLPathTests(unittest.TestCase):
    def test_cli_only_translates_filesystem_operands(self):
        args = ['purge', '/absolute-source-key', '--source']
        self.assertEqual(wsl_bridge.cli_arguments(args, 'Ubuntu'), args)
        args = ['config', 'set', 'tagger.llm.model', '/a-model-name']
        self.assertEqual(wsl_bridge.cli_arguments(args, 'Ubuntu'), args)
        mapped = wsl_bridge.cli_arguments(['workspace', 'map', '/project-label', '/home/project'], 'Ubuntu')
        self.assertEqual(mapped[2], '/project-label')
        self.assertEqual(mapped[3], r'\\wsl.localhost\Ubuntu\home\project')

    def test_namespace_and_mount_paths_do_not_assume_automount_root(self):
        convert = wsl_paths.linux_to_windows
        self.assertEqual(convert('/home/test/project/../file.py', 'Ubuntu'),
                         r'\\wsl.localhost\Ubuntu\home\test\file.py')
        self.assertEqual(convert('/mnt/c/Users/test/a.txt', 'Ubuntu'),
                         r'\\wsl.localhost\Ubuntu\mnt\c\Users\test\a.txt')
        self.assertEqual(convert('https://example.com/a', 'Ubuntu'), 'https://example.com/a')
        self.assertEqual(convert('project/file.py', 'Ubuntu'), 'project/file.py')
        for distro in ('../Ubuntu', 'Ubuntu\\root', 'bad|name', ''):
            with self.assertRaises(ValueError):
                convert('/home/test', distro)

    def test_translation_only_applies_in_windows_bridge_context(self):
        with patch.dict(os.environ, {'IMPRINT_WSL_DISTRO': 'Ubuntu'}), patch.object(wsl_paths.sys, 'platform', 'win32'):
            native = wsl_paths.native_path('/home/test/project/file.py')
            self.assertEqual(wsl_paths.client_path(native), '/home/test/project/file.py')
            self.assertEqual(wsl_paths.client_path(r'C:\Users\test\file.py'), r'C:\Users\test\file.py')
        with patch.object(wsl_paths.sys, 'platform', 'linux'):
            self.assertEqual(wsl_paths.native_path('/home/test'), '/home/test')

    def test_drive_mounts_use_real_windows_paths_and_round_trip_custom_mount_roots(self):
        mounts = 'C:\\134\t/windows\\040files/c\nD:\\134\t/mnt/d'
        self.assertEqual(wsl_paths.linux_to_windows('/windows files/c/Users/a/project', 'Ubuntu', mounts),
                         r'C:\Users\a\project')
        self.assertEqual(wsl_paths.linux_to_windows('/mnt/d/project', 'Ubuntu', mounts), r'D:\project')
        self.assertEqual(wsl_paths.linux_to_windows('/mnt/d-other/project', 'Ubuntu', mounts),
                         r'\\wsl.localhost\Ubuntu\mnt\d-other\project')
        with patch.dict(os.environ, {'IMPRINT_WSL_DISTRO': 'Ubuntu', 'IMPRINT_WSL_MOUNTS': mounts}), \
                patch.object(wsl_paths.sys, 'platform', 'win32'):
            self.assertEqual(wsl_paths.client_path(r'C:\Users\a\project'), '/windows files/c/Users/a/project')

    def test_hook_maps_metadata_but_preserves_message_content(self):
        from imprint import hook_entry
        payload = {'cwd': '/home/test/project', 'transcript_path': '/home/test/transcript.jsonl',
                   'content': '/home/test is mentioned as plain text'}
        def capture():
            actual = json.load(wsl_bridge.sys.stdin)
            self.assertEqual(actual['cwd'], r'\\wsl.localhost\Ubuntu\home\test\project')
            self.assertEqual(actual['transcript_path'], r'\\wsl.localhost\Ubuntu\home\test\transcript.jsonl')
            self.assertEqual(actual['content'], payload['content'])
            return 0
        with tempfile.TemporaryDirectory() as data, \
                patch.dict(os.environ), patch.object(wsl_bridge.os, 'chdir') as chdir, \
                patch.object(wsl_bridge.sys, 'platform', 'win32'), \
                patch.object(wsl_bridge.sys, 'stdin', io.StringIO(json.dumps(payload))), \
                patch.object(wsl_bridge.sys, 'argv', ['wsl_bridge.py']), \
                patch.object(hook_entry, 'main', side_effect=capture):
            self.assertEqual(wsl_bridge.main(['--data-dir', data, '--distribution', 'Ubuntu',
                '--cwd', '/home/test/project', '--home', '/home/test', 'hook', 'post-search']), 0)
            chdir.assert_called_once_with(r'\\wsl.localhost\Ubuntu\home\test\project')

    def test_transcript_cwd_still_requires_approved_root(self):
        from imprint import workspace_policy as policy
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transcript = root / 'session.jsonl'
            transcript.write_text(json.dumps({'cwd': '/home/test/project'}))
            # Fixture maps the Windows UNC to a local native directory, then
            # exercises the real policy check. The bridge adds no root itself.
            with patch.object(policy, 'native_path', side_effect=lambda p: str(root) if p == '/home/test/project' else p):
                with patch.object(policy, 'roots', return_value=[]):
                    self.assertFalse(policy.transcript_allowed(str(transcript)))

                with patch.object(policy, 'roots', return_value=[str(root)]):
                    self.assertTrue(policy.transcript_allowed(str(transcript)))
                transcript.write_text(json.dumps({'cwd': '/outside/root'}))
                with patch.object(policy, 'roots', return_value=[str(root)]):
                    self.assertFalse(policy.transcript_allowed(str(transcript)))

    def test_unapproved_stop_does_not_launch_any_transcript_consumer(self):
        from imprint import hook_entry, workspace_policy
        with tempfile.TemporaryDirectory() as data, patch.dict(os.environ), \
                patch.object(wsl_bridge.os, 'chdir'), \
                patch.object(wsl_bridge.sys, 'platform', 'win32'), \
                patch.object(wsl_bridge.sys, 'stdin', io.StringIO('{"transcript_path":"/private/session.jsonl"}')), \
                patch.object(wsl_bridge.sys, 'stdout', io.StringIO()), \
                patch.object(wsl_bridge.sys, 'stderr', io.StringIO()), \
                patch.object(wsl_bridge.sys, 'argv', ['wsl_bridge.py']), \
                patch.object(workspace_policy, 'transcript_allowed', return_value=False), \
                patch.object(hook_entry.subprocess, 'run') as consume:
            result = wsl_bridge.main(['--data-dir', data, '--distribution', 'Ubuntu',
                '--cwd', '/home/project', '--home', '/home/test', 'hook', 'stop'])
            self.assertEqual(result, 0)
            consume.assert_not_called()


if __name__ == '__main__':
    unittest.main()
