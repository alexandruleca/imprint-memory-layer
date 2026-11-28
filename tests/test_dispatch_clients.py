"""Which clients the Dispatcher can start, and Codex as one (ADR 0222, Dispatcher 5)."""

import json
import os
import stat
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

from imprint import tasks
from imprint.dispatch import clients, dispatched, loop, runners, status, store

from test_dispatch_run import DispatchCase, REPO, executable

FAKE_CODEX = textwrap.dedent('''\
    #!{python}
    import json, os, re, sys, uuid
    sys.path.insert(0, {repo!r})
    args = sys.argv[1:]
    if '--version' in args:
        print('codex-cli {version}'); sys.exit(0)
    if '--help' in args:
        print({help!r}); sys.exit(0)
    prompt = sys.stdin.read()
    open(os.environ['FAKE_RECORD'], 'w').write(json.dumps({{'argv': args, 'prompt': prompt, 'cwd': os.getcwd()}}))
    from imprint import tasks
    task = re.search(r'- Task: (T-[0-9a-z]+)', prompt).group(1)
    token = re.search(r'- Your claim token: (\\S+)', prompt).group(1)
    me = tasks.agent('process:' + uuid.uuid4().hex, client='codex')
    tasks.claim(task, actor=me, token=token)
    last = args[args.index('--output-last-message') + 1]
    if '- Your role: review' in prompt:
        verdict = os.environ.get('FAKE_REVIEW', 'pass')
        if verdict == 'pass':
            tasks.comment(task, 'Codex checked it: fine', actor=me)
            tasks.move(task, 'done', actor=me)
        else:
            tasks.comment(task, 'Codex found a problem', to=['holder'], actor=me)
            tasks.move(task, 'ready', actor=me)
        open(last, 'w').write('Reviewed: ' + verdict)
        sys.exit(0)
    print(json.dumps({{'type': 'item.completed', 'item': {{'type': 'agent_message', 'text': 'working'}}}}))
    if os.environ.get('FAKE_MODE', 'review') == 'review':
        tasks.checkpoint(task, 'Done and verified', actor=me)
        tasks.move(task, 'review', actor=me)
        open(last, 'w').write('Moved it to Review')
        sys.exit(0)
    open(last, 'w').write('The sandbox refused git commit')
    sys.exit(1)
''')
CODEX_HELP = '--sandbox <SANDBOX_MODE> [possible values: read-only, workspace-write] --approve-for-me --json --output-last-message'


class ClientsCase(DispatchCase):
    def setUp(self):
        super().setUp()
        self.codex = self.root / 'bin' / 'codex'
        self.write_codex(CODEX_HELP)
        (self.home / '.codex').mkdir()
        (self.home / '.codex' / 'config.toml').write_text('[mcp_servers.imprint]\ncommand = "imprint"\n')
        env = patch.dict(os.environ, {'IMPRINT_DISPATCH_CODEX_COMMAND': str(self.codex_command)})
        env.start()
        self.addCleanup(env.stop)

    def write_codex(self, help_text, version='0.171.0'):
        self.codex_command = executable(self.codex, FAKE_CODEX.format(python=sys.executable, repo=str(REPO),
                                                                      help=help_text, version=version))
        clients._cache.clear()


class DetectionTests(ClientsCase):
    def test_both_clients_are_found_with_their_versions(self):
        found = {c['client']: c for c in clients.detected()}
        self.assertTrue(found['claude-code']['available'], found['claude-code'])
        self.assertTrue(found['codex']['available'], found['codex'])
        self.assertEqual(found['codex']['version'], 'codex-cli 0.171.0')
        self.assertEqual(clients.allowed(), ['claude-code', 'codex'])
        self.assertEqual(clients.default(), 'claude-code')

    def test_a_version_without_the_safety_flags_is_refused_and_says_so(self):
        self.write_codex('--sandbox <SANDBOX_MODE> --json')
        codex = next(c for c in clients.detected() if c['client'] == 'codex')
        self.assertFalse(codex['available'])
        self.assertIn('--approve-for-me', codex['reason'])
        self.assertNotIn('codex', clients.allowed())

    def test_a_client_imprint_is_not_connected_to_is_unavailable(self):
        (self.home / '.codex' / 'config.toml').write_text('[mcp_servers.other]\ncommand = "x"\n')
        codex = next(c for c in clients.detected(refresh=True) if c['client'] == 'codex')
        self.assertFalse(codex['available'])
        self.assertIn('imprint connect codex', codex['reason'])

    def test_a_missing_client_says_it_is_not_installed(self):
        with patch.dict(os.environ, {'IMPRINT_DISPATCH_CODEX_COMMAND': ''}), \
             patch.object(runners.shutil, 'which', return_value=None), \
             patch.object(runners.Runner, 'bundled', return_value=[]):
            codex = next(c for c in clients.detected(refresh=True) if c['client'] == 'codex')
        self.assertIn('not installed', codex['reason'])

    def test_a_bundled_vscode_binary_is_found_when_none_is_on_path(self):
        bundle = self.home / '.vscode-server' / 'extensions' / f'openai.chatgpt-26.1-{runners._vscode_suffix()}' / 'bin'
        target = bundle / runners.CODEX.bundles[0][1].split('/', 1)[1]
        target.parent.mkdir(parents=True)
        target.write_text('#!/bin/sh\n')
        target.chmod(0o755)
        with patch.dict(os.environ, {'IMPRINT_DISPATCH_CODEX_COMMAND': ''}), \
             patch.object(runners.shutil, 'which', return_value=None):
            self.assertEqual(runners.find(runners.CODEX), str(target))

    def test_clients_without_a_safe_unattended_mode_show_but_never_start(self):
        real_which = clients.shutil.which
        with patch.object(clients.shutil, 'which', side_effect=lambda n: '/usr/bin/opencode' if n == 'opencode' else real_which(n)):
            found = {c['client']: c for c in clients.detected(refresh=True)}
        self.assertFalse(found['opencode']['available'])
        self.assertIn('no safe unattended mode', found['opencode']['reason'])
        self.assertNotIn('cline', found)  # not installed: not listed
        self.assertNotIn('opencode', clients.allowed())

    def test_unticked_clients_are_not_allowed_and_the_default_follows(self):
        with patch.dict(os.environ, {'IMPRINT_DISPATCH_EXCLUDED_CLIENTS': 'claude-code'}):
            self.assertEqual(clients.allowed(), ['codex'])
            self.assertEqual(clients.default(), 'codex')
            listed = {c['client']: c for c in clients.listing()}
            self.assertFalse(listed['claude-code']['allowed'])
            self.assertTrue(listed['codex']['default'])
        with patch.dict(os.environ, {'IMPRINT_DISPATCH_DEFAULT_CLIENT': 'codex'}):
            self.assertEqual(clients.default(), 'codex')


class RoutingTests(ClientsCase):
    def test_a_task_goes_to_the_client_it_is_assigned_to_else_the_default(self):
        both = ['claude-code', 'codex']
        self.assertEqual(clients.for_task({'assignees': []}, both, 'here'), 'claude-code')
        self.assertEqual(clients.for_task({'assignees': ['codex']}, both, 'here'), 'codex')
        self.assertEqual(clients.for_task({'assignees': ['codex@here']}, both, 'here'), 'codex')
        self.assertEqual(clients.for_task({'assignees': ['@here']}, both, 'here'), 'claude-code')
        self.assertIsNone(clients.for_task({'assignees': ['codex']}, ['claude-code'], 'here'))
        self.assertIsNone(clients.for_task({'assignees': ['cursor']}, both, 'here'))

    def test_the_loop_picks_each_tasks_client(self):
        codex_task = tasks.create('For Codex', project='shop', status='ready', acceptance=['ok'], priority='P1',
                                  assignees=['codex'])['id']
        status.switch('default', True)
        picked = loop.candidates('default')
        self.assertEqual([(s['id'], s['client']) for s in picked], [(codex_task, 'codex'), (self.task, 'claude-code')])

    def test_a_task_for_a_client_not_allowed_here_waits(self):
        tasks.assign(self.task, add=['codex'])
        with patch.dict(os.environ, {'IMPRINT_DISPATCH_EXCLUDED_CLIENTS': 'codex'}):
            self.assertEqual(loop.candidates('default'), [])


class CodexRunTests(ClientsCase):
    def test_codex_takes_the_claim_over_and_reaches_review(self):
        run = dispatched.run(self.task, client='codex')

        self.assertEqual(run['outcome'], 'review', run)
        seen = json.loads(self.record.read_text())
        self.assertEqual(seen['argv'][:3], ['exec', '--approve-for-me', '-C'])
        # The fake records os.getcwd(), which resolves symlinks (macOS's /var is /private/var): the same folder.
        self.assertEqual(os.path.realpath(seen['argv'][3]), os.path.realpath(seen['cwd']))
        self.assertNotIn('--sandbox', seen['argv'])  # Codex refuses it beside --approve-for-me
        self.assertIn('escalated permissions', seen['prompt'])  # how to commit from its sandbox
        git_dir = seen['argv'][seen['argv'].index('-c') + 1]
        self.assertEqual(git_dir, f"sandbox_workspace_write.writable_roots=['{(self.repo / '.git').resolve()}']")
        claims = [e for e in tasks.history(self.task) if e['kind'] == 'claimed']
        self.assertTrue(claims[-1]['session'].startswith('process:'))
        self.assertIn(claims[-1]['session'], tasks.dispatched_sessions())

    def test_a_failed_codex_session_comments_its_last_message(self):
        with patch.dict(os.environ, {'FAKE_MODE': 'fail'}):
            run = dispatched.run(self.task, client='codex')
        self.assertEqual(run['outcome'], 'failed')
        comment = [e for e in tasks.history(self.task) if e['kind'] == 'comment'][-1]
        self.assertIn('The sandbox refused git commit', comment['data']['text'])

    def test_dispatching_without_a_client_uses_the_tasks_assignment(self):
        tasks.assign(self.task, add=['codex'])
        run = dispatched.run(self.task)
        self.assertEqual(run['client'], 'codex')


class StripClientsTests(ClientsCase):
    def test_the_status_lists_the_clients_with_their_state(self):
        shown = status.status('default', projects=False, refresh=True)
        listed = {c['client']: c for c in shown['clients']}
        self.assertTrue(listed['claude-code']['default'])
        self.assertTrue(listed['codex']['allowed'])
        self.assertEqual(shown['next'][0]['client'], 'claude-code')


if __name__ == '__main__':
    import unittest
    unittest.main()
