"""Where a project lives on this device, for the Dispatcher (ADR 0220)."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from imprint import agent_activity, tasks, workspace_policy
from imprint.dispatch import folders, git


def _git(folder, *args):
    subprocess.run(['git', '-C', str(folder), *args], check=True, capture_output=True,
                   env={**os.environ, 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t',
                        'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t'})


def _repository(folder, branch='dev'):
    folder.mkdir(parents=True, exist_ok=True)
    _git(folder, 'init', '-q', '-b', branch)
    (folder / 'README.md').write_text('x\n')
    _git(folder, 'add', 'README.md')
    _git(folder, 'commit', '-q', '-m', 'first')
    return folder


class FolderCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        data = self.root / 'data'
        env = patch.dict(os.environ, {'IMPRINT_DATA_DIR': str(data), 'IMPRINT_WORKSPACE': 'default'})
        env.start()
        self.addCleanup(env.stop)
        agent_activity.close()
        self.addCleanup(agent_activity.close)
        session = patch.object(agent_activity, '_session_id', None)
        session.start()
        self.addCleanup(session.stop)


class ResolveTests(FolderCase):
    def test_a_mapped_project_resolves_to_its_folder_and_checked_out_branch(self):
        repo = _repository(self.root / 'elsewhere' / 'shop', branch='dev')
        workspace_policy.map_project('shop', str(repo))

        found = folders.resolve('shop')

        self.assertTrue(found['ok'], found)
        self.assertEqual(Path(found['path']), repo.resolve())
        self.assertEqual((found['source'], found['branch'], found['subfolder'], found['distro']),
                         ('mapping', 'dev', '', None))
        self.assertEqual(Path(found['repository']), repo.resolve())
        self.assertRegex(found['commit'], r'^[0-9a-f]{40}$')

    def test_a_mapped_folder_inside_a_repository_names_its_place_in_it(self):
        repo = _repository(self.root / 'mono')
        package = repo / 'packages' / 'api'
        package.mkdir(parents=True)
        workspace_policy.map_project('api', str(package))

        found = folders.resolve('api')

        self.assertTrue(found['ok'], found)
        self.assertEqual(found['subfolder'], 'packages/api')
        self.assertEqual(Path(found['repository']), repo.resolve())

    def test_a_detached_head_has_no_branch_but_its_commit(self):
        repo = _repository(self.root / 'detached')
        commit = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
        _git(repo, 'checkout', '-q', '--detach')
        workspace_policy.map_project('detached', str(repo))

        found = folders.resolve('detached')

        self.assertEqual((found['branch'], found['commit']), (None, commit))

    def test_without_a_mapping_the_most_recent_session_in_the_project_gives_the_folder(self):
        older = _repository(self.root / 'home' / 'user' / 'code' / 'old-copy' / 'shop')
        newer = _repository(self.root / 'home' / 'user' / 'code' / 'shop')
        named = {str(older): 'shop', str(newer): 'shop'}
        agent_activity.start_session(str(older), 'default')
        agent_activity.start_session(str(self.root), 'default')  # another project
        agent_activity.start_session(str(newer), 'default')
        with patch.object(folders, 'derive_project', side_effect=lambda cwd: named.get(cwd, 'other')):
            found = folders.resolve('shop')

        self.assertTrue(found['ok'], found)
        self.assertEqual((found['path'], found['source']), (str(newer), 'session'))

    def test_a_session_folder_that_is_gone_is_passed_over(self):
        kept = _repository(self.root / 'kept')
        agent_activity.start_session(str(kept), 'default')
        agent_activity.start_session(str(self.root / 'deleted'), 'default')
        with patch.object(folders, 'derive_project', return_value='shop'):
            found = folders.resolve('shop')

        self.assertEqual(found['path'], str(kept))

    def test_a_project_with_no_folder_is_unavailable_and_says_how_to_map_one(self):
        found = folders.resolve('nowhere')

        self.assertFalse(found['ok'])
        self.assertIsNone(found['path'])
        self.assertIn('imprint workspace map nowhere <dir>', found['reason'])

    def test_a_folder_outside_any_git_repository_is_unavailable_with_the_reason(self):
        plain = self.root / 'plain'
        plain.mkdir()
        workspace_policy.map_project('plain', str(plain))
        ceiling = patch.dict(os.environ, {'GIT_CEILING_DIRECTORIES': str(self.root)})
        with ceiling:
            found = folders.resolve('plain')

        self.assertFalse(found['ok'])
        self.assertEqual((Path(found['path']), found['source']), (plain.resolve(), 'mapping'))
        self.assertIn('not in a git repository', found['reason'])


class OpenProjectsTests(FolderCase):
    def test_projects_of_open_tasks_are_listed_with_their_folders(self):
        repo = _repository(self.root / 'shop')
        workspace_policy.map_project('shop', str(repo))
        tasks.create('Fix totals', project='shop', status='ready', acceptance=['totals add up'])
        tasks.create('Someday', project='attic')  # Backlog: not open to the Dispatcher
        tasks.create('Review me', project='nowhere', status='ready', acceptance=['ok'])

        listed = {entry['project']: entry for entry in folders.open_projects()}

        self.assertEqual(set(listed), {'shop', 'nowhere'})
        self.assertTrue(listed['shop']['ok'])
        self.assertFalse(listed['nowhere']['ok'])


class GitWhereTheFolderLivesTests(unittest.TestCase):
    def test_a_wsl_folder_on_a_windows_runtime_runs_git_inside_its_distribution(self):
        with patch.object(sys, 'platform', 'win32'), \
             patch.object(git.subprocess, 'CREATE_NO_WINDOW', 0x08000000, create=True):
            argv, options = git.command('\\\\wsl.localhost\\Ubuntu-26.04\\home\\me\\code\\shop', 'status')

        self.assertEqual(argv, ['wsl.exe', '-d', 'Ubuntu-26.04', '--exec', 'git', '-C', '/home/me/code/shop', 'status'])
        self.assertEqual(options['env']['WSL_UTF8'], '1')
        self.assertEqual(options['creationflags'], 0x08000000)

    def test_a_native_folder_runs_git_on_it(self):
        argv, _ = git.command('/home/me/code/shop', 'status')
        self.assertEqual(argv, ['git', '-C', '/home/me/code/shop', 'status'])

    def test_git_failures_raise_with_the_last_line_of_its_error(self):
        with tempfile.TemporaryDirectory() as plain, \
             patch.dict(os.environ, {'GIT_CEILING_DIRECTORIES': os.path.dirname(plain)}):
            with self.assertRaises(git.GitError) as raised:
                git.run(plain, 'rev-parse', 'HEAD')
        self.assertIn('not a git repository', str(raised.exception))


if __name__ == '__main__':
    unittest.main()
