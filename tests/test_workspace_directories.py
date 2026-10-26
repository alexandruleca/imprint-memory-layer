"""Which workspace a directory belongs to (ADR 0072): project files, their
trust, the session's workspace, asking, use_workspace, hooks and learning."""
import asyncio
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from imprint import config, config_schema, server, workspace_policy as policy

HOOK = Path(__file__).resolve().parents[1] / 'imprint' / 'hook_entry.py'


class Fixture(unittest.TestCase):
    """A data directory, a home directory and workspaces alpha and beta."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.home = self.root / 'home'
        self.home.mkdir()
        env = patch.dict(os.environ, {'IMPRINT_DATA_DIR': str(self.root / 'data'), 'IMPRINT_WORKSPACE': '',
                                      'HOME': str(self.home), 'IMPRINT_UNASSIGNED_WORKSPACE': ''})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop('IMPRINT_UNASSIGNED_WORKSPACE')
        os.environ.pop('IMPRINT_WSL_HOME', None)
        for name in ('alpha', 'beta'):
            config.register_workspace(name)
        session = dict(server._session)
        self.addCleanup(lambda: (server._session.clear(), server._session.update(session)))

    def repo(self, *parts):
        path = self.home.joinpath(*parts)
        (path / '.git').mkdir(parents=True)
        return path

    def propose(self, directory, content):
        (directory / '.imprint').mkdir(parents=True, exist_ok=True)
        (directory / '.imprint' / 'workspace.json').write_text(
            content if isinstance(content, str) else json.dumps(content))


class DirectoryWorkspaceTests(Fixture):
    def test_precedence_env_then_trusted_file_then_most_specific_root_then_default(self):
        repo = self.repo('code', 'app')
        sub = repo / 'src'
        sub.mkdir()
        # Nothing assigns it: commands use the active workspace, sessions workspace.unassigned.
        config.switch_workspace('beta')
        self.assertEqual(policy.resolve(sub)['workspace'], 'beta')
        self.assertEqual(policy.resolve(sub, session=True)['reason'], 'default')
        config_schema.set_value('workspace.unassigned', 'alpha')
        self.assertEqual(policy.resolve(sub, session=True)['workspace'], 'alpha')
        self.assertEqual(policy.resolve(sub)['workspace'], 'beta')
        # The most specific root decides over a broader one.
        policy.add_root(str(self.home / 'code'), 'alpha')
        policy.add_root(str(repo), 'beta', allow_overlap=True)
        self.assertEqual((policy.resolve(sub)['workspace'], policy.resolve(sub)['reason']), ('beta', 'root'))
        # A project file approved on this device outranks roots.
        self.propose(repo, {'workspace': 'alpha'})
        resolution = policy.resolve(sub)
        self.assertEqual((resolution['workspace'], resolution['reason']), ('alpha', 'file'))
        self.assertIn('project file', policy.describe(resolution))
        # IMPRINT_WORKSPACE outranks the directory.
        with patch.dict(os.environ, {'IMPRINT_WORKSPACE': 'beta'}):
            self.assertEqual(policy.resolve(sub)['reason'], 'env')
        # A tool's explicit workspace outranks the session's.
        self.assertEqual(server._validate_ws('beta'), ('beta', None))

    def test_tied_roots_leave_the_directory_unassigned(self):
        repo = self.repo('shared')
        policy.add_root(str(repo), 'alpha')
        policy.add_root(str(repo), 'beta', allow_overlap=True)
        resolution = policy.resolve(repo, session=True)
        self.assertEqual((resolution['reason'], resolution['tied']), ('default', ['alpha', 'beta']))
        self.assertIn('alpha and beta', policy.notice(resolution))

    def test_nearest_file_walk_stops_below_home_and_the_filesystem_root(self):
        outer, inner = self.home / 'clients', self.repo('clients', 'acme', 'web')
        self.propose(outer, {'workspace': 'alpha'})
        policy.add_root(str(outer), 'alpha')
        deep = inner / 'pkg'
        deep.mkdir()
        where = policy.place(policy.canonical(str(deep)))
        # The file above the repository assigns it; the repository is the project.
        self.assertEqual((where.assigned, where.project), ('alpha', inner))
        # The nearer file wins.
        self.propose(inner, {'workspace': 'beta'})
        policy.add_root(str(inner), 'beta', allow_overlap=True)
        self.assertEqual(policy.place(policy.canonical(str(deep))).assigned, 'beta')
        # A project file in the home directory never counts; home is no project.
        self.propose(self.home, {'workspace': 'beta'})
        (self.home / '.git').mkdir()
        plain = self.home / 'notes'
        plain.mkdir()
        where = policy.place(policy.canonical(str(plain)))
        self.assertEqual((where.project, where.file), (None, None))
        self.assertIsNone(policy.place(self.home).project)
        self.assertIsNone(policy.place(Path(self.root.anchor)).project)
        with self.assertRaisesRegex(ValueError, 'home directory'):
            policy.use_workspace('alpha', self.home)
        # Outside home the walk stops below the filesystem root; an ancestor of
        # home is never a project either.
        elsewhere = Path(self.root.anchor, 'imprint-no-such-dir', 'a')
        self.assertEqual(list(policy._candidates(elsewhere)), [elsewhere, elsewhere.parent])
        self.assertEqual(list(policy._candidates(self.root / 'x')), [self.root / 'x'])

    def test_wsl_client_home_is_a_boundary_too(self):
        client_home = self.root / 'wsl-home'
        (client_home / 'proj').mkdir(parents=True)
        (client_home / '.git').mkdir()
        self.assertEqual(policy.place(client_home / 'proj').project, client_home)
        with patch.dict(os.environ, {'IMPRINT_WSL_HOME': str(client_home)}):
            self.assertIsNone(policy.place(client_home / 'proj').project)

    def test_invalid_project_files_are_ignored_with_a_visible_note(self):
        repo = self.repo('app')
        for content in ('{not json', json.dumps(['alpha']), json.dumps({'workspace': 'Not Valid'}),
                        json.dumps({'workspace': 7})):
            with self.subTest(content=content):
                self.propose(repo, content)
                resolution = policy.resolve(repo, session=True)
                self.assertEqual((resolution['reason'], resolution['proposal']), ('default', None))
                self.assertIn('was ignored', resolution['note'])
                self.assertTrue(policy.notice(resolution).startswith('Imprint: '))

    def test_an_untrusted_file_proposes_and_confirming_approves_its_root(self):
        clone = self.repo('clone')
        self.propose(clone, {'workspace': 'beta'})
        resolution = policy.resolve(clone, session=True)
        self.assertEqual((resolution['workspace'], resolution['trusted']), ('default', False))
        self.assertIn("asks for workspace 'beta'", policy.notice(resolution))
        # A workspace this device does not have yet is named as such.
        self.propose(clone, {'workspace': 'client-x'})
        self.assertIn('does not exist on this device', policy.notice(policy.resolve(clone, session=True)))
        result = policy.use_workspace('client-x', clone)
        self.assertEqual((result['created'], result['root_added']), (True, True))
        self.assertEqual(policy.roots('client-x'), [str(clone)])
        resolution = policy.resolve(clone / '.git', session=True)
        self.assertEqual((resolution['workspace'], resolution['reason']), ('client-x', 'file'))
        self.assertEqual(policy.notice(resolution), '')

    def test_a_proposal_against_another_workspaces_root_is_asked_and_needs_sharing(self):
        repo = self.repo('app')
        policy.add_root(str(repo), 'alpha')
        self.assertEqual(policy.notice(policy.resolve(repo, session=True)), '')
        self.propose(repo, {'workspace': 'beta'})
        resolution = policy.resolve(repo, session=True)
        self.assertEqual((resolution['workspace'], resolution['reason']), ('alpha', 'root'))
        self.assertIn("asks for workspace 'beta'", policy.notice(resolution))
        with self.assertRaisesRegex(ValueError, 'alpha'):
            policy.use_workspace('beta', repo)
        self.assertEqual(policy.roots('beta'), [])
        policy.use_workspace('beta', repo, allow_overlap=True)
        self.assertEqual(policy.resolve(repo)['workspace'], 'beta')

    def test_use_workspace_reuses_a_containing_root_and_keeps_existing_workspaces(self):
        repo = self.repo('code', 'app')
        policy.add_root(str(self.home / 'code'), 'alpha')
        result = policy.use_workspace('alpha', repo)
        self.assertEqual((result['created'], result['root_added']), (False, False))
        self.assertEqual(policy.roots('alpha'), [str(self.home / 'code')])
        self.assertEqual(json.loads((repo / '.imprint' / 'workspace.json').read_text()), {'workspace': 'alpha'})
        self.assertEqual(sorted(config.get_known_workspaces()), ['alpha', 'beta', 'default'])
        with self.assertRaisesRegex(ValueError, 'Invalid workspace name'):
            policy.use_workspace('Bad Name', repo)

    def test_cli_use_writes_at_the_git_root_or_the_folder_it_runs_in(self):
        repo = self.repo('app')
        (repo / 'src').mkdir()
        plain = self.home / 'notes'
        plain.mkdir()
        env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1])}

        def cli(cwd, *args):
            return subprocess.run([sys.executable, '-m', 'imprint.cli_workspace', *args], cwd=cwd, env=env,
                                  capture_output=True, text=True)
        result = cli(repo / 'src', 'use', 'gamma')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((repo / '.imprint' / 'workspace.json').is_file())
        self.assertFalse((repo / 'src' / '.imprint').exists())
        self.assertEqual(cli(plain, 'use', 'gamma').returncode, 0)
        self.assertTrue((plain / '.imprint' / 'workspace.json').is_file())
        resolved = json.loads(cli(plain, 'resolve').stdout)
        self.assertEqual((resolved['workspace'], resolved['reason']), ('gamma', 'file'))
        refused = cli(self.home, 'use', 'gamma')
        self.assertEqual(refused.returncode, 1)
        self.assertIn('home directory', refused.stderr)
        self.assertNotIn('Traceback', refused.stderr)

    def test_learning_follows_trusted_project_files(self):
        transcript = self.root / 'session.jsonl'
        repo = self.repo('app')

        def owners(*cwds):
            transcript.write_text('\n'.join(json.dumps({'cwd': str(cwd)}) for cwd in cwds))
            return [ws for ws in ('alpha', 'beta') if policy.transcript_allowed(transcript, ws)]
        self.assertEqual(owners(repo), ['alpha', 'beta'])
        # Shared roots share their conversations (ADR 0034)...
        policy.add_root(str(repo), 'alpha')
        policy.add_root(str(repo), 'beta', allow_overlap=True)
        self.assertEqual(owners(repo), ['alpha', 'beta'])
        # ...until a project file this device approved names one of them.
        self.propose(repo, {'workspace': 'beta'})
        self.assertEqual(owners(repo / 'src'), ['beta'])
        # An unapproved file decides nothing: the roots rule applies.
        other = self.repo('other')
        self.propose(other, {'workspace': 'beta'})
        policy.add_root(str(other), 'alpha')
        self.assertEqual(owners(other), ['alpha'])
        # A conversation touching a project assigned elsewhere stays out.
        self.assertEqual(owners(repo, self.home / 'loose'), ['beta'])

    def test_deleting_a_workspace_resets_the_unassigned_setting(self):
        config_schema.set_value('workspace.unassigned', 'beta')
        self.assertIn('beta', [choice.value for choice in config_schema.SETTINGS_BY_KEY['workspace.unassigned'].options()])
        policy.remove_workspace('alpha')
        self.assertEqual(config_schema.resolve('workspace.unassigned'), ('beta', 'config'))
        policy.remove_workspace('beta')
        self.assertEqual(config_schema.resolve('workspace.unassigned'), ('', 'default'))


class SessionTests(Fixture):
    """The MCP process's session: pinned workspace, one notice, use_workspace."""

    def call(self, *calls):
        from fastmcp import Client

        async def run():
            async with Client(server.mcp) as client:
                return [await client.call_tool(name, args) for name, args in calls]
        return asyncio.run(run())

    def test_project_session_is_asked_once_and_switches_without_restart(self):
        repo = self.repo('app')
        with patch.dict(os.environ):
            server.start_session(str(repo))
            self.assertEqual(os.environ['IMPRINT_WORKSPACE'], 'default')
            first, second, used, after = self.call(('device_context', {}), ('device_context', {}),
                                                   ('use_workspace', {'name': 'gamma'}), ('device_context', {}))
            self.assertTrue(first.content[0].text.startswith('Imprint: No workspace is assigned'))
            self.assertTrue(first.structured_content['result'].startswith('Imprint: '))
            self.assertNotIn('Imprint:', second.content[0].text)
            self.assertIn("now uses workspace 'gamma' (created)", used.content[0].text)
            self.assertIn("user's choice", used.content[0].text)
            self.assertEqual(os.environ['IMPRINT_WORKSPACE'], 'gamma')
            self.assertEqual(config.get_active_workspace(), 'gamma')
            self.assertEqual(json.loads(after.content[0].text)['session']['workspace'], 'gamma')
            self.assertEqual(policy.roots('gamma'), [str(repo)])
        # The global selection did not move.
        self.assertEqual(config._read_workspace_config()['active'], 'default')

    def test_an_assigned_project_is_not_asked(self):
        repo = self.repo('app')
        policy.use_workspace('beta', repo)
        with patch.dict(os.environ):
            server.start_session(str(repo / '.git'))
            self.assertEqual(os.environ['IMPRINT_WORKSPACE'], 'beta')
            (result,) = self.call(('device_context', {}))
        self.assertNotIn('Imprint:', result.content[0].text)

    def test_a_first_use_workspace_call_consumes_the_question(self):
        repo = self.repo('app')
        with patch.dict(os.environ):
            server.start_session(str(repo))
            used, later = self.call(('use_workspace', {'name': 'alpha'}), ('device_context', {}))
        self.assertNotIn('No workspace is assigned', used.content[0].text + later.content[0].text)

    def test_sessions_without_a_project_use_the_unassigned_workspace_and_never_switch(self):
        config_schema.set_value('workspace.unassigned', 'alpha')
        plain = self.home / 'Downloads'
        plain.mkdir()
        # A desktop app gives no usable folder: none at all, a filesystem root,
        # the home directory, or an app's own folder that is no project.
        for directory in (None, self.root.anchor, str(self.home), str(plain)):
            with self.subTest(directory=directory), patch.dict(os.environ):
                server.start_session(directory)
                self.assertEqual(os.environ['IMPRINT_WORKSPACE'], 'alpha')
                first, used = self.call(('device_context', {}), ('use_workspace', {'name': 'personal'}))
                self.assertNotIn('Imprint:', first.content[0].text)
                text = used.content[0].text
                self.assertIn("Pass workspace='personal' on every Imprint call", text)
                self.assertEqual(os.environ['IMPRINT_WORKSPACE'], 'alpha')
                self.assertIn('personal', config.get_known_workspaces())
        self.assertFalse(any(self.home.rglob('workspace.json')))

    def test_use_workspace_is_a_write_tool_and_the_instructions_explain_both_paths(self):
        tool = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}['use_workspace']
        self.assertFalse(tool.annotations.read_only_hint)
        self.assertTrue(tool.annotations.destructive_hint)
        self.assertEqual(set(tool.parameters['properties']), {'name', 'allow_overlap'})
        self.assertIn('use_workspace', server._MCP_INSTRUCTIONS)
        self.assertIn('Use Imprint workspace <name>', server._MCP_INSTRUCTIONS)

    def test_an_unresolvable_directory_is_reported_not_fatal(self):
        with patch.dict(os.environ), patch.object(policy, 'resolve', side_effect=ValueError('broken policy')):
            server.start_session(str(self.home))
            self.assertEqual(os.environ['IMPRINT_WORKSPACE'], 'default')
            self.assertIn('broken policy', server._session['notice'])
        # A client's unknown IMPRINT_WORKSPACE stays and fails each call visibly, as before.
        with patch.dict(os.environ, {'IMPRINT_WORKSPACE': 'missing'}):
            server.start_session(str(self.home))
            self.assertEqual(os.environ['IMPRINT_WORKSPACE'], 'missing')
            self.assertIn('missing', server._session['notice'])
            with self.assertRaisesRegex(ValueError, 'missing'):
                server.device_context()

    def test_overlap_needs_the_users_confirmation(self):
        repo = self.repo('app')
        policy.add_root(str(repo), 'alpha')
        with patch.dict(os.environ):
            server.start_session(str(repo))
            refused, shared = self.call(('use_workspace', {'name': 'beta'}),
                                        ('use_workspace', {'name': 'beta', 'allow_overlap': True}))
            self.assertIn('Nothing changed', refused.content[0].text)
            self.assertIn("Ask whether 'beta' should share it", refused.content[0].text)
            self.assertIn('allow_overlap=true', refused.content[0].text)
            self.assertIn('shares this folder with alpha', shared.content[0].text)
            self.assertEqual(os.environ['IMPRINT_WORKSPACE'], 'beta')


class HookTests(Fixture):
    def hook(self, action, payload):
        env = {**os.environ}
        return subprocess.run([sys.executable, str(HOOK), action, os.environ['IMPRINT_DATA_DIR']],
                              input=json.dumps(payload), text=True, capture_output=True, env=env)

    def test_session_start_asks_in_an_unassigned_project_and_names_an_assigned_one(self):
        from imprint import hook_entry
        repo = self.repo('app')
        result = self.hook('session-start', {'cwd': str(repo), 'source': 'startup'})
        self.assertEqual(result.returncode, 0, result.stderr)
        context = json.loads(result.stdout)['hookSpecificOutput']['additionalContext']
        self.assertIn('Imprint memory is available', context)
        self.assertIn('call use_workspace', context)
        policy.use_workspace('beta', repo)
        result = self.hook('copilot-session-start', {'cwd': str(repo)})
        context = json.loads(result.stdout)['additionalContext']
        self.assertIn("workspace is 'beta' (project file", context)
        self.assertNotIn('use_workspace', context)
        # Without a cwd (Cursor's payload) nothing is claimed about the directory.
        context = json.loads(self.hook('session-start', {'workspace_roots': [str(repo)]}).stdout)
        self.assertEqual(context['hookSpecificOutput']['additionalContext'], hook_entry.REMINDER)

    def test_stop_imports_in_the_sessions_workspace(self):
        from imprint import hook_entry
        repo = self.repo('app')
        policy.use_workspace('beta', repo)
        payload = {'cwd': str(repo), 'transcript_path': str(self.root / 't.jsonl'), 'session_id': 's'}
        seen = []
        with patch.dict(os.environ), patch.object(sys, 'argv', ['hook_entry.py', 'stop', os.environ['IMPRINT_DATA_DIR']]), \
                patch.object(sys, 'stdin', io.StringIO(json.dumps(payload))), \
                patch.object(hook_entry.subprocess, 'run', side_effect=lambda *a, **k: seen.append(os.environ['IMPRINT_WORKSPACE'])), \
                patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(hook_entry.main(), 0)
        self.assertEqual(seen, ['beta'] * 3)


if __name__ == '__main__':
    unittest.main()
