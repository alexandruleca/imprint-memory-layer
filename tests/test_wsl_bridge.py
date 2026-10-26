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

    def test_distribution_listing_decodes_wsl_output_and_skips_utility_distros(self):
        listing = 'Ubuntu\r\nrancher-desktop\r\nDocker-Desktop-Data\r\nDebian\r\nbad/name\r\n\r\n'
        expected = ['Ubuntu', 'Debian']
        self.assertEqual(wsl_paths.parse_distributions(listing.encode('utf-16-le')), expected)
        self.assertEqual(wsl_paths.parse_distributions(b'\xff\xfe' + listing.encode('utf-16-le')), expected)
        # WSL_UTF8=1 switches wsl.exe to UTF-8.
        self.assertEqual(wsl_paths.parse_distributions(listing.encode('utf-8')), expected)
        self.assertEqual(wsl_paths.parse_distributions(b''), [])
        with patch.object(wsl_paths.sys, 'platform', 'linux'), patch.object(wsl_paths.subprocess, 'run') as run:
            self.assertEqual(wsl_paths.distributions(), [])
            run.assert_not_called()

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

    def test_transcript_cwd_is_translated_before_the_ownership_check(self):
        from imprint import config, workspace_policy as policy
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {'IMPRINT_DATA_DIR': str(Path(directory) / 'data'), 'IMPRINT_WORKSPACE': ''}):
            # Stored roots are canonical; temp paths are not on macOS
            # (/var -> /private/var) or Windows (8.3 short names).
            project = Path(directory).resolve() / 'project'
            project.mkdir()
            config.register_workspace('alpha')
            config.register_workspace('beta')
            transcript = Path(directory) / 'session.jsonl'
            transcript.write_text(json.dumps({'cwd': '/home/test/project'}))
            # Fixture maps the WSL path to a local native directory, then
            # exercises the real policy check. The bridge adds no root itself.
            with patch.object(policy, 'native_path', side_effect=lambda p: str(project) if p == '/home/test/project' else p):
                self.assertTrue(policy.transcript_allowed(str(transcript), 'alpha'))
                policy.add_root('/home/test/project', 'beta')
                self.assertFalse(policy.transcript_allowed(str(transcript), 'alpha'))
                self.assertTrue(policy.transcript_allowed(str(transcript), 'beta'))
            self.assertEqual(policy.roots('alpha'), [])

    def test_copilot_session_start_passes_through_the_bridge(self):
        with tempfile.TemporaryDirectory() as data, patch.dict(os.environ), \
                patch.object(wsl_bridge.os, 'chdir'), \
                patch.object(wsl_bridge.sys, 'platform', 'win32'), \
                patch.object(wsl_bridge.sys, 'stdin', io.StringIO('{"hook_event_name":"SessionStart","source":"startup"}')), \
                patch.object(wsl_bridge.sys, 'stdout', io.StringIO()) as output, \
                patch.object(wsl_bridge.sys, 'argv', ['wsl_bridge.py']):
            result = wsl_bridge.main(['--data-dir', data, '--distribution', 'Ubuntu',
                '--cwd', '/home/project', '--home', '/home/test', 'hook', 'copilot-session-start'])
            self.assertEqual(result, 0)
            self.assertIn('additionalContext', json.loads(output.getvalue()))

    def test_stop_checks_ownership_in_the_sessions_workspace(self):
        # The client's cwd, translated, decides the session's workspace (ADR
        # 0072): here a project file this device approved for beta.
        from imprint import config, workspace_policy as policy
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {'IMPRINT_DATA_DIR': str(Path(directory) / 'data'), 'IMPRINT_WORKSPACE': ''}):
            project = Path(directory).resolve() / 'project'
            (project / '.git').mkdir(parents=True)
            config.register_workspace('beta')
            policy.use_workspace('beta', project)
            seen = []
            payload = {'cwd': '/home/test/project', 'transcript_path': '/home/test/t.jsonl'}
            translate = lambda value, *args: str(project) if value == '/home/test/project' else value
            with patch.object(wsl_bridge, 'linux_to_windows', side_effect=translate), \
                    patch.object(policy, 'native_path', side_effect=lambda value: value), \
                    patch.object(wsl_bridge.os, 'chdir'), \
                    patch.object(wsl_bridge.sys, 'platform', 'win32'), \
                    patch.object(wsl_bridge.sys, 'stdin', io.StringIO(json.dumps(payload))), \
                    patch.object(wsl_bridge.sys, 'stdout', io.StringIO()), \
                    patch.object(wsl_bridge.sys, 'stderr', io.StringIO()), \
                    patch.object(wsl_bridge.sys, 'argv', ['wsl_bridge.py']), \
                    patch.object(policy, 'transcript_allowed',
                                 side_effect=lambda path: seen.append(os.environ['IMPRINT_WORKSPACE']) or False):
                self.assertEqual(wsl_bridge.main(['--data-dir', str(Path(directory) / 'data'), '--distribution', 'Ubuntu',
                    '--cwd', '/home/test/project', '--home', '/home/test', 'hook', 'stop']), 0)
                self.assertEqual(os.environ['IMPRINT_WSL_HOME'], '/home/test')
            self.assertEqual(seen, ['beta'])

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
                patch.object(hook_entry.subprocess, 'Popen') as consume:
            result = wsl_bridge.main(['--data-dir', data, '--distribution', 'Ubuntu',
                '--cwd', '/home/project', '--home', '/home/test', 'hook', 'stop'])
            self.assertEqual(result, 0)
            consume.assert_not_called()


if __name__ == '__main__':
    unittest.main()
