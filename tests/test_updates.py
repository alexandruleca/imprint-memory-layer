import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from imprint import updates
from emulation import slack

REPORT = {
    'current': 'v0.13.1', 'latest': 'v0.14.0', 'name': 'v0.14.0',
    'url': 'https://example.invalid/releases/v0.14.0', 'published_at': '2026-10-24T00:00:00Z',
    'update_available': True, 'method': 'windows-setup', 'channel': 'stable', 'prerelease': False,
}


def completed(stdout='', returncode=0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr='')


class UpdateModuleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        log = Path(self.directory.name) / 'update.log'
        patcher = patch.object(updates, 'log_path', return_value=log)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.installer_log = Path(self.directory.name) / 'update-setup.log'
        installer = patch.object(updates, 'installer_log_path', return_value=self.installer_log)
        installer.start()
        self.addCleanup(installer.stop)
        channel = patch.dict(os.environ, {'IMPRINT_UPDATE_CHANNEL': 'stable'})
        channel.start()
        self.addCleanup(channel.stop)
        updates._report, updates._checked_at, updates._process = None, 0.0, None
        self.addCleanup(self._reset)

    def _reset(self):
        if updates._process is not None and updates._process.poll() is None:
            updates._process.kill()
            updates._process.wait()
        updates._report, updates._checked_at, updates._process = None, 0.0, None
        updates._auto_target, updates._auto_state = '', ''
        updates._auto_failed.clear()

    def test_an_update_installs_by_itself_only_when_due_unattended_and_nothing_would_be_interrupted(self):
        # ADR 0175.
        with patch.dict(os.environ, {'IMPRINT_UPDATE_AUTO': '0'}), patch.object(updates, 'check') as check:
            self.assertEqual(updates.auto_update(), 'automatic updates are off')
            check.assert_not_called()
            self.assertEqual(updates.auto_status(), {'auto': False, 'auto_state': ''})
        with patch.dict(os.environ, {'IMPRINT_UPDATE_AUTO': '1'}), patch.object(updates, 'start') as start:
            with patch.object(updates, 'check', return_value={**REPORT, 'update_available': False}):
                self.assertEqual(updates.auto_update(), 'up to date')
            with patch.object(updates, 'check', return_value={**REPORT, 'method': 'macos-pkg'}):
                self.assertIn('needs the user', updates.auto_update())  # The Installer app needs the user.
            with patch.object(updates, 'check', return_value=REPORT), \
                    patch.object(updates, 'busy', return_value='a recording is running'):
                self.assertEqual(updates.auto_update(), 'v0.14.0 waits: a recording is running')
                self.assertIn('waiting: a recording is running', updates.auto_status()['auto_state'])
            start.assert_not_called()
            with patch.object(updates, 'check', return_value=REPORT), patch.object(updates, 'busy', return_value=''):
                self.assertEqual(updates.auto_update(), 'updating to v0.14.0')
            start.assert_called_once()
            # That update failed: the same release waits a day before it is tried by itself again.
            with patch.object(updates, 'check', return_value=REPORT), patch.object(updates, 'busy', return_value=''), \
                    patch.object(updates, 'running', return_value=False), \
                    patch.object(updates, 'status', return_value={'failed': True}):
                self.assertEqual(updates.auto_update(), 'v0.14.0 failed lately')
            start.assert_called_once()

    def test_busy_names_what_an_update_would_interrupt(self):
        import contextlib
        from imprint import agent_activity, chat_replies
        now = time.time()
        # With no Echo window and no conversation, Echo is not talking.
        self.assertFalse(updates._echo_talking())
        calm = [patch.object(updates, 'running', return_value=False),
                patch.object(updates.job_queue, 'active_job', return_value=None),
                patch.object(updates, '_recording', return_value=False),
                patch.object(chat_replies, 'writing', return_value=False),
                patch.object(updates, '_echo_talking', return_value=False),
                patch.object(agent_activity, 'last_call_at', return_value=now - 3600),
                patch.object(updates, 'user_idle_seconds', return_value=3600)]
        with contextlib.ExitStack() as stack:
            for quiet in calm:
                stack.enter_context(quiet)
            self.assertEqual(updates.busy(), '')
            for target, name, value, reason in [
                    (updates.job_queue, 'active_job', {'id': 'j1'}, 'a job is running'),
                    (updates, '_recording', True, 'a recording is running'),
                    (chat_replies, 'writing', True, 'a chat reply is being written'),
                    (updates, '_echo_talking', True, 'an Echo conversation is open'),
                    (agent_activity, 'last_call_at', now - 60, 'an agent used Imprint in the last 15 minutes'),
                    (updates, 'user_idle_seconds', 30.0, 'the computer is in use')]:
                with patch.object(target, name, return_value=value):
                    self.assertEqual(updates.busy(), reason)
            # Where the system cannot say how long the user has been away, that does not hold updates back.
            with patch.object(updates, 'user_idle_seconds', return_value=None):
                self.assertEqual(updates.busy(), '')

    def test_a_recording_holds_updates_back_only_while_its_audio_grows(self):
        from imprint import config, recordings
        with patch.object(config, 'get_data_dir', return_value=Path(self.directory.name)):
            meta = recordings.start('Call', consent=True)
            self.assertTrue(updates._recording())
            recordings.append(meta['id'], b'opus')
            old = time.time() - 600
            os.utime(recordings.root() / meta['id'] / 'audio.webm', (old, old))
            self.assertFalse(updates._recording(), 'a recording whose window closed does not hold updates forever')

    @unittest.skipUnless(os.name == 'nt', 'the Windows input clock')
    def test_the_computers_idle_time_comes_from_windows(self):
        idle = updates.user_idle_seconds()
        self.assertIsNotNone(idle)
        self.assertGreaterEqual(idle, 0)

    def test_check_reads_the_cli_report_and_caches_it(self):
        with patch.object(updates.subprocess, 'run', return_value=completed(json.dumps(REPORT))) as run:
            self.assertEqual(updates.check(), REPORT)
            self.assertEqual(updates.check(), REPORT)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0][1:], ['update', '--check', '--json'])
            updates.check(refresh=True)
            self.assertEqual(run.call_count, 2)

    def test_opting_in_to_dev_builds_checks_again_on_that_channel(self):
        dev = {**REPORT, 'latest': 'v0.14.1-dev.2', 'channel': 'dev', 'prerelease': True}
        reports = [completed(json.dumps(REPORT)), completed(json.dumps(dev))]
        with patch.object(updates.subprocess, 'run', side_effect=reports) as run:
            self.assertEqual(updates.check(), REPORT)
            with patch.dict(os.environ, {'IMPRINT_UPDATE_CHANNEL': 'dev'}):
                self.assertEqual(updates.check(), dev)
                self.assertEqual(updates.check(), dev)
        # The CLI reads the setting itself: the dashboard passes no channel.
        self.assertEqual([call.args[0][1:] for call in run.call_args_list], [['update', '--check', '--json']] * 2)

    def test_a_failed_check_reports_the_cli_message_and_is_not_cached(self):
        failure = completed('[x] Could not resolve release: GET api.github.com: timeout\n', returncode=1)
        with patch.object(updates.subprocess, 'run', return_value=failure):
            with self.assertRaisesRegex(RuntimeError, 'Could not resolve release'):
                updates.check()
        self.assertIsNone(updates._report)

    def _fake_cli(self, body):
        script = Path(self.directory.name) / 'imprint'
        script.write_text(f'#!{sys.executable}\nimport sys, time\n{body}\n')
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        return str(script)

    @unittest.skipIf(os.name == 'nt', 'uses a POSIX script as the CLI')
    def test_start_runs_the_cli_detached_and_status_tails_its_log(self):
        # It runs on past the wait for its line.
        cli = self._fake_cli(f"print('[*] Downloading', flush=True)\ntime.sleep({slack(30)})")
        with patch.object(updates.job_queue, 'find_imprint_binary', return_value=cli), \
                patch.object(updates.job_queue, 'active_job', return_value=None):
            updates.start()
            deadline = time.time() + slack(10)
            while time.time() < deadline and not updates.status()['log']:
                time.sleep(0.05)
            self.assertEqual(updates.status(), {'running': True, 'failed': False, 'log': ['[*] Downloading']})
            self.assertEqual(updates._process.args[1:], ['update', '-y'])
            self.assertNotEqual(os.getpgid(updates._process.pid), os.getpgid(0))
            with self.assertRaisesRegex(updates.UpdateConflict, 'already running'):
                updates.start()

    @unittest.skipIf(os.name == 'nt', 'uses a POSIX script as the CLI')
    def test_only_an_updater_that_exits_with_an_error_has_failed(self):
        # A successful exit hands over to the installer or the server restart.
        for body, failed in (("print('[+] Installer started')", False), ("sys.exit('[x] checksum mismatch')", True)):
            with patch.object(updates.job_queue, 'find_imprint_binary', return_value=self._fake_cli(body)), \
                    patch.object(updates.job_queue, 'active_job', return_value=None):
                updates.start()
                updates._process.wait(timeout=slack(10))
                self.assertEqual(updates.status()['running'], False)
                self.assertEqual(updates.status()['failed'], failed)

    @unittest.skipIf(os.name == 'nt', 'uses a POSIX script as the CLI')
    def test_the_windows_installer_is_followed_from_its_log_until_it_closes(self):
        self.installer_log.write_text('A previous update\'s installer log.\n')
        with patch.object(updates.job_queue, 'find_imprint_binary', return_value=self._fake_cli("print('[+] Installer started')")), \
                patch.object(updates.job_queue, 'active_job', return_value=None):
            updates.start()
            self.assertFalse(self.installer_log.exists())
            updates._process.wait(timeout=slack(10))
        self.assertNotIn('installer', updates.status())  # Not started yet.
        stamp = '2026-11-04 20:00:00.000   '
        lines = ['Log opened. (Time zone: UTC+02:00)', 'Installation process succeeded.', '-- Run entry --']
        self.installer_log.write_text('\ufeff' + ''.join(stamp + line + '\n' for line in lines), encoding='utf-8')
        report = updates.status()
        # Files are in place, but provisioning and the server restart still run.
        self.assertEqual((report['installer'], report['failed']), ('running', False))
        self.assertEqual(report['log'][0], '[+] Installer started')
        self.assertEqual(report['log'][-1], stamp + '-- Run entry --')
        with self.installer_log.open('a', encoding='utf-8') as log:
            log.write(stamp + 'Log closed.\n')
        self.assertEqual((updates.status()['installer'], updates.status()['failed']), ('succeeded', False))
        self.installer_log.write_text(''.join(stamp + line + '\n' for line in
                                              ('Log opened.', 'DeleteFile: The process cannot access the file.', 'Log closed.')))
        self.assertEqual((updates.status()['installer'], updates.status()['failed']), ('failed', True))

    @unittest.skipIf(os.name == 'nt', 'uses a POSIX script as the CLI')
    def test_an_updater_that_failed_ignores_any_installer_log(self):
        with patch.object(updates.job_queue, 'find_imprint_binary', return_value=self._fake_cli("sys.exit('[x] checksum mismatch')")), \
                patch.object(updates.job_queue, 'active_job', return_value=None):
            updates.start()
            updates._process.wait(timeout=slack(10))
        self.installer_log.write_text('Installation process succeeded.\nLog closed.\n')
        self.assertNotIn('installer', updates.status())
        self.assertTrue(updates.status()['failed'])

    def test_start_waits_for_a_running_job(self):
        with patch.object(updates.job_queue, 'active_job', return_value={'id': 'j1'}), \
                patch.object(updates.subprocess, 'Popen') as popen:
            with self.assertRaisesRegex(updates.UpdateConflict, 'running job'):
                updates.start()
            popen.assert_not_called()


class UpdateEndpointTests(unittest.TestCase):
    def setUp(self):
        from imprint import api
        self.api = api

    def body(self, response):
        return response.status_code, json.loads(response.body)

    def test_unsupported_installations_cannot_start(self):
        report = {**REPORT, 'method': '', 'unsupported': 'running in a container: pull the new image'}
        with patch.object(updates, 'check', return_value=report), patch.object(updates, 'start') as start:
            self.assertEqual(self.body(self.api.api_update_start()), (400, {'error': report['unsupported']}))
            start.assert_not_called()

    def test_an_up_to_date_installation_cannot_start(self):
        with patch.object(updates, 'check', return_value={**REPORT, 'update_available': False}):
            self.assertEqual(self.body(self.api.api_update_start()), (400, {'error': 'Already on v0.13.1'}))

    def test_a_conflict_is_409_and_a_start_names_the_target(self):
        with patch.object(updates, 'check', return_value=REPORT), \
                patch.object(updates, 'start', side_effect=updates.UpdateConflict('An update is already running')):
            self.assertEqual(self.body(self.api.api_update_start()), (409, {'error': 'An update is already running'}))
        with patch.object(updates, 'check', return_value=REPORT), patch.object(updates, 'start'):
            self.assertEqual(self.api.api_update_start(), {'started': True, 'target': 'v0.14.0'})

    def test_status_is_reported_even_when_the_check_fails(self):
        with patch.object(updates, 'check', side_effect=RuntimeError('offline')), \
                patch.object(updates, 'status', return_value={'running': False, 'failed': False, 'log': []}), \
                patch.object(updates, 'auto_status', return_value={'auto': True, 'auto_state': ''}):
            self.assertEqual(self.api.api_update(), {'running': False, 'failed': False, 'log': [], 'auto': True,
                                                     'auto_state': '', 'error': 'offline'})

    def test_the_update_report_says_whether_updates_install_by_themselves(self):
        with patch.object(updates, 'check', return_value=REPORT), \
                patch.object(updates, 'status', return_value={'running': False, 'failed': False, 'log': []}), \
                patch.object(updates, 'auto_status', return_value={'auto': True, 'auto_state': 'Installing v0.14.0.'}):
            report = self.api.api_update()
        self.assertEqual((report['latest'], report['auto'], report['auto_state']), ('v0.14.0', True, 'Installing v0.14.0.'))


if __name__ == '__main__':
    unittest.main()
