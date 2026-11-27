"""Dispatched sessions for projects in a WSL distribution (ADR 0222, Dispatcher 6).

These run on any system: what reaches ``wsl.exe`` is recorded instead of run.
The real path, a Windows runtime with a project in a distribution, is
checked by hand (see Dispatcher 6's checkpoint)."""

import os
import subprocess
import sys
from unittest.mock import patch

from imprint import tasks, workspace_policy
from imprint.dispatch import clients, dispatched, folders, runners, store, worktrees, wsl

from test_dispatch_run import DispatchCase

DISTRO = 'Ubuntu-26.04'
FOLDER = {'project': 'shop', 'ok': True, 'path': f'\\\\wsl.localhost\\{DISTRO}\\home\\me\\code\\shop', 'source': 'mapping',
          'distro': DISTRO, 'repository': '/home/me/code/shop', 'subfolder': '', 'branch': 'dev', 'commit': 'a' * 40}


class WslCommandTests(DispatchCase):
    def test_the_client_runs_inside_the_distribution_as_its_own_process_group(self):
        argv = wsl.launch(DISTRO, '/home/me/.imprint/worktrees/shop/T-1', ['claude', '-p'], 'run1')
        self.assertEqual(argv[:9], ['wsl.exe', '-d', DISTRO, '--cd', '/home/me/.imprint/worktrees/shop/T-1', '--exec',
                                    'setsid', '--wait', 'sh'])
        self.assertEqual(argv[-3:], ['/tmp/imprint-dispatch-run1.pid', 'claude', '-p'])

    def test_the_workspace_and_run_cross_into_the_distribution_and_back(self):
        env = wsl.launch_env({'IMPRINT_WORKSPACE': 'default', 'IMPRINT_DISPATCH_RUN': 'r', 'WSLENV': 'USERPROFILE/p'})
        self.assertEqual(env['WSLENV'], 'USERPROFILE/p:IMPRINT_WORKSPACE:IMPRINT_DISPATCH_RUN')

    def test_a_worktree_goes_under_the_distributions_home(self):
        with patch.object(wsl, 'home', return_value='/home/me'):
            self.assertEqual(worktrees.location(FOLDER, 'T-1'), '/home/me/.imprint/worktrees/shop/T-1')

    def test_stopping_signals_the_group_inside_the_distribution(self):
        with patch.object(wsl, 'run', return_value='') as ran:
            self.assertTrue(wsl.kill(DISTRO, 'run1', force=False))
        script = ran.call_args.args[1][-1]
        self.assertIn('/tmp/imprint-dispatch-run1.pid', script)
        self.assertIn('/bin/kill -s TERM -- "-$pgid"', script)  # procps: dash's builtin refuses "--"
        self.assertIn('else kill -TERM "-$pgid"', script)


class WslClientTests(DispatchCase):
    def setUp(self):
        super().setUp()
        # The fake client's native path is no Linux path in a distribution.
        env = patch.dict(os.environ, {'IMPRINT_DISPATCH_CLAUDE_CODE_COMMAND': ''})
        env.start()
        self.addCleanup(env.stop)

    def test_a_client_is_found_on_the_login_shells_path_then_in_vscode_server(self):
        with patch.object(wsl, 'which', return_value=None), \
             patch.object(wsl, 'bundled', return_value=['/home/me/.vscode-server/extensions/x/claude']):
            self.assertEqual(clients.binary(runners.CLAUDE_CODE, DISTRO), '/home/me/.vscode-server/extensions/x/claude')
        with patch.object(wsl, 'which', return_value='/usr/local/bin/claude'):
            self.assertEqual(clients.binary(runners.CLAUDE_CODE, DISTRO), '/usr/local/bin/claude')

    def test_a_linux_path_setting_names_the_client_in_the_distribution(self):
        with patch.dict(os.environ, {'IMPRINT_DISPATCH_CLAUDE_CODE_COMMAND': '/opt/claude'}), \
             patch.object(wsl, 'is_file', return_value=True):
            self.assertEqual(clients.binary(runners.CLAUDE_CODE, DISTRO), '/opt/claude')

    def test_imprint_is_connected_when_the_distributions_profile_starts_it(self):
        with patch.object(wsl, 'home', return_value='/home/me'), \
             patch.object(wsl, 'read', return_value='{"mcpServers": {"imprint": {}}}'):
            self.assertTrue(clients.connected(runners.CLAUDE_CODE, DISTRO))
        with patch.object(wsl, 'home', return_value='/home/me'), patch.object(wsl, 'read', return_value=None):
            self.assertFalse(clients.connected(runners.CODEX, DISTRO))

    def test_clients_are_detected_per_distribution(self):
        def fake_run(distro, argv, timeout=30):
            return '9.9.9' if argv[-1] == '--version' else '--permission-mode auto --session-id --output-format'
        with patch.object(wsl, 'which', side_effect=lambda d, n: f'/usr/bin/{n}' if n == 'claude' else None), \
             patch.object(wsl, 'bundled', return_value=[]), patch.object(wsl, 'run', side_effect=fake_run), \
             patch.object(wsl, 'home', return_value='/home/me'), \
             patch.object(wsl, 'read', return_value='{"mcpServers": {"imprint": {}}}'):
            found = {c['client']: c for c in clients.detected(refresh=True, where=DISTRO)}
        self.assertTrue(found['claude-code']['available'])
        self.assertEqual(found['claude-code']['where'], DISTRO)
        self.assertIn('not installed', found['codex']['reason'])

    def test_the_distributions_in_use_are_read_from_mappings_without_starting_any(self):
        with patch.object(workspace_policy, 'mapped_roots', return_value=[FOLDER['path'], 'C:\\code\\app']), \
             patch('imprint.wsl_paths.running_distributions', return_value=[DISTRO]):
            self.assertEqual(folders.distributions(), [DISTRO])
        with patch.object(workspace_policy, 'mapped_roots', return_value=[FOLDER['path']]), \
             patch('imprint.wsl_paths.running_distributions', return_value=[]):
            self.assertEqual(folders.distributions(), [])


class WslStartTests(DispatchCase):
    def test_a_task_in_a_distribution_starts_its_client_there_and_stops_it_there(self):
        worktree = {'path': '/home/me/.imprint/worktrees/shop/T-1', 'cwd': '/home/me/.imprint/worktrees/shop/T-1',
                    'branch': 'task/1-x', 'base': 'dev', 'base_commit': 'a' * 40, 'git_dir': '/home/me/code/shop/.git',
                    'created': True}
        started = {}
        real_popen = subprocess.Popen

        def popen(argv, **options):
            started.update(argv=argv, env=options['env'], cwd=options['cwd'])
            return real_popen([sys.executable, '-c', 'import sys, time; sys.stdin.read(); time.sleep(30)'],
                              **{**options, 'cwd': None})

        with patch.object(folders, 'resolve', return_value=FOLDER), \
             patch.object(worktrees, 'prepare', return_value=worktree), \
             patch.object(clients, 'allowed', return_value=['claude-code']), \
             patch.object(clients, 'binary', return_value='/home/me/bin/claude'), \
             patch.object(wsl, 'linux_path', return_value='/mnt/c/data/dispatch/r.last'), \
             patch.object(dispatched.subprocess, 'Popen', side_effect=popen), \
             patch.object(wsl, 'kill', return_value=True) as killed, \
             patch.object(wsl, 'group_alive', side_effect=[True, False, False]), \
             patch.object(wsl, 'run', return_value='') as ran:
            run, proc = dispatched.start(self.task)
            session = dispatched.Session(run, proc)
            session.stop('was stopped by the person')
            ended = session.step()

        self.assertEqual(started['argv'][:7], ['wsl.exe', '-d', DISTRO, '--cd', worktree['cwd'], '--exec', 'setsid'])
        self.assertIn('/home/me/bin/claude', started['argv'])
        self.assertIn('--session-id', started['argv'])
        self.assertIsNone(started['cwd'])  # a Linux folder is not one Windows can start in
        self.assertIn('IMPRINT_WORKSPACE', started['env']['WSLENV'])
        self.assertEqual(store.get(run['id'])['distro'], DISTRO)
        self.assertEqual(killed.call_args_list[0].args[:2], (DISTRO, run['id']))
        self.assertEqual(ended['outcome'], 'stopped')
        self.assertIn(['rm', '-f', f"/tmp/imprint-dispatch-{run['id']}.pid"], [c.args[1] for c in ran.call_args_list])
        first = next(e for e in tasks.history(self.task) if e['kind'] == 'checkpoint')
        self.assertIn(f'on this device in {DISTRO}', first['data']['text'])


if __name__ == '__main__':
    import unittest
    unittest.main()
