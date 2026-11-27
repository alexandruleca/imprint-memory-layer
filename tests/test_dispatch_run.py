"""Dispatching one Task now with Claude Code (ADR 0220, Dispatcher 2).

A fake ``claude`` stands in for the client: it reads the prompt, takes the
claim over with the token through Imprint's Board as the client's MCP
server would, and then behaves as ``FAKE_MODE`` says.
"""

import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from imprint import agent_activity, cli_tasks, registry, tasks, workspace_policy
from imprint.dispatch import dispatched, runners, store, worktrees

REPO = Path(__file__).resolve().parent.parent

FAKE_CLAUDE = textwrap.dedent('''\
    #!{python}
    import json, os, re, sys, time
    sys.path.insert(0, {repo!r})
    from imprint import tasks
    args = sys.argv[1:]
    if '--version' in args:
        print('9.9.9 (Claude Code)'); sys.exit(0)
    if '--help' in args:
        print('--permission-mode <mode> (choices: "acceptEdits", "auto") --session-id <uuid> --output-format'); sys.exit(0)
    session = args[args.index('--session-id') + 1]
    prompt = sys.stdin.read()
    open(os.environ['FAKE_RECORD'], 'w').write(json.dumps({{'argv': args, 'prompt': prompt, 'cwd': os.getcwd(),
        'workspace': os.environ.get('IMPRINT_WORKSPACE'), 'run': os.environ.get('IMPRINT_DISPATCH_RUN')}}))
    mode = os.environ.get('FAKE_MODE', 'review')
    if mode == 'hang':
        time.sleep(600)
    task = re.search(r'- Task: (T-[0-9a-z]+)', prompt).group(1)
    token = re.search(r'- Your claim token: (\\S+)', prompt).group(1)
    me = tasks.agent('conversation:' + session, client='claude-code')
    tasks.claim(task, actor=me, token=token)
    if '- Your role: review' in prompt:
        verdict = os.environ.get('FAKE_REVIEW', 'pass')
        if verdict == 'pass':
            tasks.comment(task, 'Checked both criteria: they hold', actor=me)
            tasks.move(task, 'done', actor=me)
        elif verdict == 'return':
            tasks.comment(task, 'done.txt is empty: write the word done in it', to=['holder'], actor=me)
            tasks.move(task, 'ready', actor=me)
        print(json.dumps({{'type': 'result', 'subtype': 'success', 'result': 'Reviewed: ' + verdict}}))
        sys.exit(0)
    print(json.dumps({{'type': 'assistant', 'message': {{'content': [{{'type': 'text', 'text': 'Working on it'}}]}}}}))
    if mode == 'review':
        open('done.txt', 'w').write('done')
        tasks.checkpoint(task, 'All criteria hold: done.txt exists', actor=me)
        tasks.move(task, 'review', actor=me)
        print(json.dumps({{'type': 'result', 'subtype': 'success', 'result': 'Moved it to Review'}}))
        sys.exit(0)
    print(json.dumps({{'type': 'result', 'subtype': 'error', 'result': 'The test suite would not start'}}))
    sys.exit(3)
''')


def _git(folder, *args):
    subprocess.run(['git', '-C', str(folder), *args], check=True, capture_output=True,
                   env={**os.environ, 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t',
                        'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t'})


class DispatchCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.record = self.root / 'record.json'
        fake = self.root / 'bin' / 'claude'
        fake.parent.mkdir()
        fake.write_text(FAKE_CLAUDE.format(python=sys.executable, repo=str(REPO)))
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        env = patch.dict(os.environ, {'IMPRINT_DATA_DIR': str(self.root / 'data'), 'IMPRINT_WORKSPACE': 'default',
                                      'IMPRINT_DISPATCH_CLAUDE_CODE_COMMAND': str(fake), 'HOME': str(self.home),
                                      'USERPROFILE': str(self.home), 'FAKE_RECORD': str(self.record),
                                      'FAKE_MODE': 'review'})
        env.start()
        self.addCleanup(env.stop)
        agent_activity.close()
        self.addCleanup(agent_activity.close)
        # Imprint is connected to the fake client, and clients are checked afresh in each test.
        (self.home / '.claude.json').write_text(json.dumps({'mcpServers': {'imprint': {'command': 'imprint'}}}))
        from imprint.dispatch import clients
        clients._cache.clear()
        self.addCleanup(clients._cache.clear)
        self.repo = self.root / 'code' / 'shop'
        self.repo.mkdir(parents=True)
        _git(self.repo, 'init', '-q', '-b', 'dev')
        (self.repo / 'README.md').write_text('shop\n')
        _git(self.repo, 'add', 'README.md')
        _git(self.repo, 'commit', '-q', '-m', 'first')
        workspace_policy.map_project('shop', str(self.repo))
        self.task = tasks.create('Add a done file', project='shop', status='ready',
                                 acceptance=['done.txt exists'])['id']

    def recorded(self):
        return json.loads(self.record.read_text())


class DispatchRunTests(DispatchCase):
    def test_a_ready_task_is_worked_in_its_own_worktree_and_reaches_review(self):
        run = dispatched.run(self.task)

        self.assertEqual(run['outcome'], 'review', run)
        state = tasks.get(self.task)
        self.assertEqual(state['status'], 'review')
        seen = self.recorded()
        worktree = self.home / '.imprint' / 'worktrees' / 'shop' / self.task
        self.assertEqual(Path(seen['cwd']).resolve(), worktree.resolve())
        self.assertTrue((worktree / 'done.txt').exists())
        self.assertFalse((self.repo / 'done.txt').exists())  # the project's own checkout is untouched
        branch = subprocess.run(['git', '-C', str(worktree), 'branch', '--show-current'], capture_output=True,
                                text=True).stdout.strip()
        self.assertEqual(branch, f'task/{self.task[2:].lower()}-add-a-done-file')
        self.assertEqual(seen['workspace'], 'default')
        self.assertEqual(seen['run'], run['id'])

    def test_the_prompt_carries_the_task_the_token_and_the_skill(self):
        dispatched.run(self.task)
        prompt = self.recorded()['prompt']

        self.assertIn(f'- Task: {self.task}: Add a done file', prompt)
        self.assertIn('- Workspace: default: pass workspace="default"', prompt)
        self.assertRegex(prompt, r'- Your claim token: \S{20,}')
        self.assertIn('from dev at ', prompt)
        self.assertIn('# You are a Dispatched session', prompt)
        self.assertNotIn('name: imprint-dispatch', prompt)  # the skill's front matter stays out

    def test_the_session_takes_the_claim_over_and_the_board_shows_it(self):
        dispatched.run(self.task)
        conversation = self.recorded()['argv'][self.recorded()['argv'].index('--session-id') + 1]
        claims = [e for e in tasks.history(self.task) if e['kind'] == 'claimed']

        self.assertEqual([e['session'] for e in claims][-1], f'conversation:{conversation}')
        self.assertEqual(claims[-1]['data'].get('replaces'), claims[0]['id'])
        self.assertTrue(claims[0]['session'].startswith('dispatch:'))
        self.assertEqual(tasks.get(self.task)['worked_by'], f'conversation:{conversation}')

    def test_the_dispatchers_first_checkpoint_records_the_base(self):
        dispatched.run(self.task)
        first = next(e for e in tasks.history(self.task) if e['kind'] == 'checkpoint')

        self.assertIn('made from dev at', first['data']['text'])
        self.assertTrue(first['data']['branch'].startswith('task/'))

    def test_a_session_that_ends_short_of_review_releases_the_claim_with_the_end_of_its_log(self):
        with patch.dict(os.environ, {'FAKE_MODE': 'fail'}):
            run = dispatched.run(self.task)

        self.assertEqual(run['outcome'], 'failed')
        self.assertEqual(run['exit_code'], 3)
        state = tasks.get(self.task)
        self.assertEqual((state['status'], state['holder']), ('ready', None))
        comment = [e for e in tasks.history(self.task) if e['kind'] == 'comment'][-1]
        self.assertEqual(comment['actor'], 'system')
        self.assertIn('without moving the Task to Review', comment['data']['text'])
        self.assertIn('Its claim is released', comment['data']['text'])
        self.assertIn('The test suite would not start', comment['data']['text'])

    def test_a_session_past_its_time_limit_is_stopped_and_fails(self):
        with patch.dict(os.environ, {'FAKE_MODE': 'hang'}):
            begun = time.monotonic()
            run = dispatched.run(self.task, minutes=0.05)

        self.assertLess(time.monotonic() - begun, 30)
        self.assertEqual(run['outcome'], 'failed')
        self.assertIn('limit', run['detail'])
        self.assertIsNone(tasks.get(self.task)['holder'])  # the Dispatcher's own claim, released

    def test_a_task_held_by_a_live_session_is_refused_and_nothing_starts(self):
        tasks.claim(self.task, actor=tasks.agent('conversation:someone', client='codex'))
        with self.assertRaises(PermissionError):
            dispatched.run(self.task)

        self.assertFalse(self.record.exists())
        self.assertEqual(store.recent(1)[0]['outcome'], 'refused')

    def test_a_done_task_is_not_dispatched(self):
        tasks.move(self.task, 'done')
        with self.assertRaises(ValueError):
            dispatched.run(self.task)
        self.assertFalse(self.record.exists())

    def test_a_missing_client_claims_nothing(self):
        with patch.dict(os.environ, {'IMPRINT_DISPATCH_CLAUDE_CODE_COMMAND': str(self.root / 'nope')}), \
             patch.object(runners.shutil, 'which', return_value=None):
            with self.assertRaises(LookupError):
                dispatched.run(self.task)
        self.assertIsNone(tasks.get(self.task)['holder'])

    def test_a_second_dispatch_continues_on_the_same_branch_and_worktree(self):
        with patch.dict(os.environ, {'FAKE_MODE': 'fail'}):
            first = dispatched.run(self.task)
        second = dispatched.run(self.task)

        self.assertEqual((second['branch'], second['worktree']), (first['branch'], first['worktree']))
        self.assertEqual(second['outcome'], 'review')

    def test_the_run_and_its_log_are_kept(self):
        run = dispatched.run(self.task)

        self.assertEqual(store.get(run['id'])['outcome'], 'review')
        self.assertIn('Moved it to Review', store.log_path(run['id']).read_text())
        self.assertEqual(dispatched.log_tail(store.log_path(run['id'])), 'Moved it to Review')


class CommandLineTests(DispatchCase):
    def test_dispatch_run_prints_the_outcome(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli_tasks.main(['dispatch', 'run', self.task])

        self.assertEqual(code, 0)
        self.assertIn(f'Dispatched {self.task} to claude-code', out.getvalue())
        self.assertIn(f'{self.task} reached Review.', out.getvalue())

    def test_a_failed_run_exits_nonzero(self):
        with patch.dict(os.environ, {'FAKE_MODE': 'fail'}), redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                cli_tasks.main(['dispatch', 'run', self.task, '--json'])
        self.assertEqual(raised.exception.code, 1)


class SkillTests(DispatchCase):
    def test_the_instructions_are_a_built_in_skill(self):
        found = registry.find('imprint-dispatch', 'skill')
        self.assertEqual(found['origin'], 'builtin')
        self.assertIn('# You are a Dispatched session', dispatched.instructions())

    def test_a_fork_replaces_the_built_in_in_the_prompt(self):
        fork = registry.fork('imprint-dispatch')
        text = fork['files']['SKILL.md'] if 'files' in fork else registry.get(fork['id'])['files']['SKILL.md']
        registry.update(fork['id'], files={'SKILL.md': text.replace('# You are a Dispatched session',
                                                                    '# You are a forked Dispatched session')})
        dispatched.run(self.task)

        self.assertIn('# You are a forked Dispatched session', self.recorded()['prompt'])


class RunnerTests(unittest.TestCase):
    CONTEXT = {'session_id': 'sid-1', 'cwd': '/wt', 'git_dir': '/repo/.git', 'last_message': '/data/run.last'}

    def test_claude_code_runs_one_shot_in_auto_mode_with_its_session_id(self):
        self.assertEqual(runners.CLAUDE_CODE.argv('claude', self.CONTEXT),
                         ['claude', '-p', '--permission-mode', 'auto', '--session-id', 'sid-1',
                          '--output-format', 'stream-json', '--verbose'])

    def test_codex_runs_with_approvals_reviewed_in_its_workspace_write_sandbox(self):
        self.assertEqual(runners.CODEX.argv('codex', self.CONTEXT),
                         ['codex', 'exec', '--approve-for-me', '-C', '/wt',
                          '-c', "sandbox_workspace_write.writable_roots=['/repo/.git']", '--json',
                          '--output-last-message', '/data/run.last'])

    def test_no_bypass_flag_ever_passes(self):
        for flag in ('--dangerously-skip-permissions', '--allow-dangerously-skip-permissions',
                     '--dangerously-bypass-approvals-and-sandbox', '--yolo', '--allow-all'):
            with self.subTest(flag=flag), self.assertRaises(ValueError):
                runners.check(['claude', '-p', flag])
        for value in (['--permission-mode', 'bypassPermissions'], ['--permission-mode=bypassPermissions'],
                      ['-s', 'danger-full-access']):
            with self.subTest(value=value), self.assertRaises(ValueError):
                runners.check(['client', *value])
        with self.assertRaises(ValueError):
            runners.check(['copilot', '-p'], env={'COPILOT_ALLOW_ALL': 'true'})

    def test_every_runner_builds_a_command_line_that_passes_the_check(self):
        for runner in runners.RUNNERS.values():
            with self.subTest(runner=runner.client):
                runners.check(runner.argv(runner.binary, self.CONTEXT))


class WorktreeNameTests(unittest.TestCase):
    def test_branch_names_follow_the_task_convention(self):
        self.assertEqual(worktrees.branch_name('T-7f3kq', 'Retry uploads, with backoff!'),
                         'task/7f3kq-retry-uploads-with-backoff')
        self.assertEqual(worktrees.branch_name('T-ab12c', '???'), 'task/ab12c-work')


if __name__ == '__main__':
    unittest.main()
