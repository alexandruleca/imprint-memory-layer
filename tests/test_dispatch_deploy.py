"""Deploying the Tasks a Dispatched review passed (ADR 0239)."""

import os
import subprocess
from unittest.mock import patch

from imprint import config_schema, tasks
from imprint.dispatch import deploy, dispatched, git, guards, status, store
from imprint.tasks.actors import SYSTEM

from test_dispatch_loop import LoopCase
from test_dispatch_run import _git


def _out(*args) -> str:
    return subprocess.run(['git', *args], capture_output=True, text=True, check=True).stdout.strip()


class DeployCase(LoopCase):
    """A project on dev whose remote's default branch is main, as this repository's is."""

    def setUp(self):
        super().setUp()
        guards._fetched.clear()
        self.addCleanup(guards._fetched.clear)
        self.bare = self.root / 'remote.git'
        _git(self.root, 'init', '-q', '--bare', '-b', 'main', str(self.bare))
        _git(self.repo, 'config', 'user.name', 'Alex Example')
        _git(self.repo, 'config', 'user.email', 'alex@example.test')
        _git(self.repo, 'remote', 'add', 'origin', str(self.bare))
        _git(self.repo, 'push', '-q', 'origin', 'dev:main')
        _git(self.repo, 'push', '-q', '-u', 'origin', 'dev')
        _git(self.repo, 'remote', 'set-head', 'origin', 'main')
        # Someone else's copy, to move the base while the Task is under review.
        self.other = self.root / 'code' / 'shop-other'
        _git(self.root, 'clone', '-q', '-b', 'dev', str(self.bare), str(self.other))
        status.set_roles('default', {'deploy'})

    def passed(self) -> dict:
        """Worked (a commit on the Task's branch, pushed) and passed by a Dispatched review."""
        with patch.dict(os.environ, {'FAKE_MODE': 'commit'}):
            self.assertEqual(dispatched.run(self.task)['outcome'], 'review')
        self.assertEqual(dispatched.run(self.task, role='review')['outcome'], 'passed')
        return tasks.handoff(self.task)

    def remote(self, ref: str) -> str:
        return _out('--git-dir', str(self.bare), 'rev-parse', ref)

    def move_base(self, name: str, text: str) -> str:
        _git(self.other, 'pull', '-q', '--ff-only')
        (self.other / name).write_text(text)
        _git(self.other, 'add', name)
        _git(self.other, 'commit', '-q', '-m', f'Change {name} elsewhere')
        _git(self.other, 'push', '-q', 'origin', 'dev')
        return _out('-C', str(self.other), 'rev-parse', 'HEAD')

    def notes(self, outcome: str) -> list[str]:
        return [e['data']['text'] for e in tasks.history(self.task)
                if e['kind'] == 'comment' and (e['data'].get('dispatch') or {}).get('outcome') == outcome]


class CandidateTests(DeployCase):
    def test_a_task_a_dispatched_review_passed_waits_and_the_persons_own_done_does_not(self):
        self.passed()
        self.assertEqual([s['id'] for s in deploy.candidates('default')], [self.task])
        other = tasks.create('Done by hand', project='shop', status='ready', acceptance=['ok'])['id']
        tasks.move(other, 'done', actor=tasks.USER)
        self.assertNotIn(other, [s['id'] for s in deploy.candidates('default')])

    def test_nothing_is_deployed_without_the_deploy_role(self):
        self.passed()
        status.set_roles('default', {'review'})
        self.assertIsNone(self.dispatcher.deploy())
        self.assertEqual(self.notes('deployed'), [])


class DeployTests(DeployCase):
    def test_an_unmoved_base_is_fast_forwarded_and_the_task_branch_deleted(self):
        handoff = self.passed()
        run = self.dispatcher.deploy()
        self.assertEqual((run['role'], run['outcome'], run['pid']), ('deploy', 'deployed', None))
        self.assertEqual(self.remote('refs/heads/dev'), handoff['commit'])
        self.assertEqual(self.remote('refs/heads/main'), _out('-C', str(self.repo), 'rev-parse', 'dev'))  # untouched
        self.assertNotIn(handoff['branch'], _out('--git-dir', str(self.bare), 'branch', '--list'))
        [note] = self.notes('deployed')
        self.assertIn(f"Deployed {handoff['commit'][:12]} to origin/dev: a fast-forward", note)
        self.assertEqual(deploy.candidates('default'), [])
        # The person's checkout stays where it was, on its own dev.
        self.assertEqual(_out('-C', str(self.repo), 'rev-parse', '--abbrev-ref', 'HEAD'), 'dev')
        self.assertNotEqual(_out('-C', str(self.repo), 'rev-parse', 'dev'), handoff['commit'])
        # A deploy counts toward neither the daily limit nor what runs.
        self.assertEqual(guards.started_today(), 2)  # the worker and the reviewer only

    def test_a_moved_base_gets_a_merge_commit_with_the_repositorys_identity_and_no_trailer(self):
        handoff = self.passed()
        moved = self.move_base('NOTES.md', 'elsewhere\n')
        self.dispatcher.deploy()
        head = self.remote('refs/heads/dev')
        parents = _out('--git-dir', str(self.bare), 'rev-list', '--parents', '-n', '1', head).split()[1:]
        self.assertEqual(parents, [moved, handoff['commit']])
        message = _out('--git-dir', str(self.bare), 'log', '-1', '--format=%an <%ae>%n%B', head)
        self.assertEqual(message, f"Alex Example <alex@example.test>\nMerge {handoff['branch']} ({self.task})")
        self.assertIn('merged into origin/dev', self.notes('deployed')[0])

    def test_a_branch_already_in_the_base_is_noted_and_nothing_is_pushed(self):
        handoff = self.passed()
        _git(self.repo, 'push', '-q', 'origin', f"{handoff['commit']}:refs/heads/dev")  # merged by hand
        before = self.remote('refs/heads/dev')
        self.dispatcher.deploy()
        self.assertEqual(self.remote('refs/heads/dev'), before)
        self.assertIn('already in origin/dev', self.notes('deployed')[0])

    def test_a_push_the_moved_base_rejects_is_tried_again_and_counts_as_nothing(self):
        handoff = self.passed()
        real_fetch = git.fetch_branch

        def fetch_then_someone_pushes(path, remote, branch):
            real_fetch(path, remote, branch)
            if branch == 'dev' and not fetched:
                fetched.append(self.move_base('RACE.md', 'won\n'))
        fetched = []
        with patch.object(git, 'fetch_branch', side_effect=fetch_then_someone_pushes):
            self.assertIsNone(self.dispatcher.deploy())
        self.assertEqual(self.notes('deployed'), [])
        self.assertEqual([r for r in store.recent(10) if r['role'] == 'deploy'], [])
        run = self.dispatcher.deploy()  # the next pass
        self.assertEqual(run['outcome'], 'deployed')
        parents = _out('--git-dir', str(self.bare), 'rev-list', '--parents', '-n', '1', 'dev').split()[1:]
        self.assertEqual(parents, [fetched[0], handoff['commit']])

    def test_two_deploying_devices_end_with_one_merge_and_one_note(self):
        self.passed()
        self.dispatcher.deploy()
        # The other device's copy, with the first device's note synced: nothing waits.
        from imprint import workspace_policy
        workspace_policy.add_root(str(self.other))
        workspace_policy.map_project('shop', str(self.other))
        self.assertIsNone(self.dispatcher.deploy())
        self.assertEqual(len(self.notes('deployed')), 1)


class RefusalTests(DeployCase):
    def test_the_remotes_default_branch_is_refused_until_the_person_lists_it(self):
        _git(self.repo, 'checkout', '-q', '-b', 'main', '--track', 'origin/main')
        self.task = tasks.create('Work from main', project='shop', status='ready', acceptance=['ok'])['id']
        handoff = self.passed()
        self.assertIsNone(self.dispatcher.deploy())
        said = [e['data']['text'] for e in tasks.history(self.task) if deploy.DEFAULT_REFUSED in e['data'].get('text', '')]
        self.assertEqual(len(said), 1)
        self.assertIsNone(self.dispatcher.deploy())  # said once
        self.assertEqual(len([e for e in tasks.history(self.task) if deploy.DEFAULT_REFUSED in e['data'].get('text', '')]), 1)
        config_schema.set_value('dispatch.deploy_branches', 'main')
        self.assertEqual(self.dispatcher.deploy()['outcome'], 'deployed')
        self.assertEqual(self.remote('refs/heads/main'), handoff['commit'])

    def test_a_base_without_a_remote_is_left_with_one_comment(self):
        self.passed()
        _git(self.repo, 'branch', '--unset-upstream', 'dev')
        self.assertIsNone(self.dispatcher.deploy())
        self.assertIsNone(self.dispatcher.deploy())
        said = [e for e in tasks.history(self.task) if deploy.NO_REMOTE in e['data'].get('text', '')]
        self.assertEqual(len(said), 1)

    def test_a_failure_is_recorded_and_counts_toward_switching_off(self):
        self.passed()
        _git(self.repo, 'remote', 'set-url', 'origin', str(self.root / 'gone.git'))
        run = self.dispatcher.deploy()
        self.assertEqual((run['outcome'], run['role']), ('failed', 'deploy'))
        self.assertIn('could not deploy to origin/dev', run['detail'])
        self.assertEqual(guards.failures_in_a_row(), 1)


class ConflictTests(DeployCase):
    def test_a_conflict_sends_the_task_back_and_a_second_hands_it_to_the_person(self):
        handoff = self.passed()
        self.move_base('done.txt', 'someone else\n')  # the worker wrote done.txt too
        run = self.dispatcher.deploy()
        self.assertEqual(run['outcome'], 'conflict')
        state = tasks.get(self.task)
        self.assertEqual(state['status'], 'ready')
        self.assertNotIn('user', state['assignees'])
        [note] = self.notes('conflict')
        self.assertIn(f"{handoff['branch']} conflicts with origin/dev in done.txt", note)
        self.assertIn('Merge origin/dev into', note)
        self.assertEqual(self.remote('refs/heads/dev'), _out('-C', str(self.other), 'rev-parse', 'HEAD'))  # nothing pushed

        tasks.move(self.task, 'review', actor=tasks.USER)  # as if resolved, the conflict still there
        self.assertEqual(dispatched.run(self.task, role='review')['outcome'], 'passed')
        self.assertEqual(self.dispatcher.deploy()['outcome'], 'conflict')
        self.assertIn('user', tasks.get(self.task)['assignees'])
        self.assertEqual(guards.failures_in_a_row(), 0)  # conflicts are not failures
