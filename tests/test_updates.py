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

REPORT = {
    'current': 'v0.13.1', 'latest': 'v0.14.0', 'name': 'v0.14.0',
    'url': 'https://example.invalid/releases/v0.14.0', 'published_at': '2026-10-24T00:00:00Z',
    'update_available': True, 'method': 'windows-setup',
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
        updates._report, updates._checked_at, updates._process = None, 0.0, None
        self.addCleanup(self._reset)

    def _reset(self):
        if updates._process is not None and updates._process.poll() is None:
            updates._process.kill()
            updates._process.wait()
        updates._report, updates._checked_at, updates._process = None, 0.0, None

    def test_check_reads_the_cli_report_and_caches_it(self):
        with patch.object(updates.subprocess, 'run', return_value=completed(json.dumps(REPORT))) as run:
            self.assertEqual(updates.check(), REPORT)
            self.assertEqual(updates.check(), REPORT)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0][1:], ['update', '--check', '--json'])
            updates.check(refresh=True)
            self.assertEqual(run.call_count, 2)

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
        cli = self._fake_cli("print('[*] Downloading', flush=True)\ntime.sleep(30)")
        with patch.object(updates.job_queue, 'find_imprint_binary', return_value=cli), \
                patch.object(updates.job_queue, 'active_job', return_value=None):
            updates.start()
            deadline = time.time() + 10
            while time.time() < deadline and not updates.status()['log']:
                time.sleep(0.05)
            self.assertEqual(updates.status(), {'running': True, 'log': ['[*] Downloading']})
            self.assertEqual(updates._process.args[1:], ['update', '-y'])
            self.assertNotEqual(os.getpgid(updates._process.pid), os.getpgid(0))
            with self.assertRaisesRegex(updates.UpdateConflict, 'already running'):
                updates.start()

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
                patch.object(updates, 'status', return_value={'running': False, 'log': []}):
            self.assertEqual(self.api.api_update(), {'running': False, 'log': [], 'error': 'offline'})


if __name__ == '__main__':
    unittest.main()
