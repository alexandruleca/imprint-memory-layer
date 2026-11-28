"""Task branches travel through the base branch's remote (ADR 0238)."""

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

from imprint import tasks, workspace_policy
from imprint.dispatch import dispatched, git, guards, loop

from test_dispatch_loop import LoopCase
from test_dispatch_run import _git


def _refs(bare: Path, pattern: str) -> list[str]:
    out = subprocess.run(['git', '--git-dir', str(bare), 'for-each-ref', '--format=%(refname)', pattern],
                         capture_output=True, text=True, check=True).stdout
    return out.split()


class RemoteCase(LoopCase):
    def setUp(self):
        super().setUp()
        guards._fetched.clear()
        self.addCleanup(guards._fetched.clear)
        self.bare = self.root / 'remote.git'
        _git(self.root, 'init', '-q', '--bare', '-b', 'dev', str(self.bare))
        _git(self.repo, 'remote', 'add', 'origin', str(self.bare))
        _git(self.repo, 'push', '-q', '-u', 'origin', 'dev')
        # Another device's copy of the project, cloned before any Task branch exists.
        self.other = self.root / 'code' / 'shop-elsewhere'
        _git(self.root, 'clone', '-q', '-b', 'dev', str(self.bare), str(self.other))

    def worked_here(self) -> dict:
        """A Dispatched worker here commits on the Task's branch and moves the Task to Review."""
        with patch.dict(os.environ, {'FAKE_MODE': 'commit'}):
            run = dispatched.run(self.task)
        self.assertEqual(run['outcome'], 'review', run)
        return run


class PushTests(RemoteCase):
    def test_a_run_that_reaches_review_has_its_branch_pushed_by_imprint(self):
        run = self.worked_here()
        self.assertEqual(_refs(self.bare, 'refs/heads/task/'), [f"refs/heads/{run['branch']}"])
        tip = subprocess.run(['git', '--git-dir', str(self.bare), 'rev-parse', run['branch']],
                             capture_output=True, text=True, check=True).stdout.strip()
        here = subprocess.run(['git', '-C', str(self.repo), 'rev-parse', run['branch']],
                              capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(tip, here)
        self.assertEqual(tip, tasks.handoff(self.task)['commit'])
        self.assertIn('pushed', run['detail'])

    def test_a_base_without_an_upstream_pushes_nothing_and_says_nothing(self):
        _git(self.repo, 'branch', '--unset-upstream', 'dev')
        self.worked_here()
        self.assertEqual(_refs(self.bare, 'refs/heads/task/'), [])
        self.assertFalse([c for c in tasks.user_inbox(mark=False) if 'push' in c['text']])

    def test_a_failed_push_leaves_a_comment_and_the_task_in_review(self):
        _git(self.repo, 'remote', 'set-url', 'origin', str(self.root / 'gone.git'))
        self.worked_here()
        self.assertEqual(tasks.get(self.task)['status'], 'review')
        said = [c['text'] for c in tasks.user_inbox(mark=False) if 'could not push' in c['text']]
        self.assertEqual(len(said), 1, said)
        self.assertIn('origin', said[0])


class FetchTests(RemoteCase):
    def elsewhere(self):
        """The project's folder is now the other device's copy, which lacks the work."""
        handoff = tasks.handoff(self.task)
        self.assertFalse(git.has_commit(str(self.other), handoff['commit']))
        # Each device has its own home; here both share one, so the first copy's worktree goes.
        _git(self.repo, 'worktree', 'remove', '--force', str(self.home / '.imprint' / 'worktrees' / 'shop' / self.task))
        workspace_policy.add_root(str(self.other))
        workspace_policy.map_project('shop', str(self.other))
        return handoff

    def test_a_review_elsewhere_fetches_the_branch_first_and_reviews_its_last_commit(self):
        self.worked_here()
        handoff = self.elsewhere()
        from imprint.dispatch import folders
        folder = folders.resolve('shop', 'default')
        self.assertTrue(guards.takeover(tasks.get(self.task), folder, 'default', doing='review this Task')[0])
        self.assertTrue(git.has_commit(str(self.other), handoff['commit']))

        tracked = subprocess.run(['git', '-C', str(self.other), 'rev-parse', f"refs/remotes/origin/{handoff['branch']}"],
                                 capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(tracked, handoff['commit'])

        review = dispatched.run(self.task, role='review')
        self.assertEqual(review['outcome'], 'passed', review)
        # The reviewer's worktree was made at the work's last commit, fetched from the remote.
        prompt = __import__('json').loads(self.record.read_text())['prompt']
        self.assertIn(f"continuing at {handoff['commit']}", prompt)

    def test_without_an_upstream_elsewhere_the_task_is_left_and_the_comment_says_why(self):
        self.worked_here()
        self.elsewhere()
        _git(self.other, 'branch', '--unset-upstream', 'dev')
        from imprint.dispatch import folders
        folder = folders.resolve('shop', 'default')
        self.assertFalse(guards.takeover(tasks.get(self.task), folder, 'default', doing='review this Task')[0])
        [said] = [e['data']['text'] for e in tasks.history(self.task)
                  if e['kind'] == 'comment' and 'did not review this Task' in e['data'].get('text', '')]
        self.assertIn('no upstream', said)

    def test_a_branch_is_fetched_at_most_once_a_pass(self):
        self.worked_here()
        self.elsewhere()
        _git(self.other, 'remote', 'set-url', 'origin', str(self.root / 'gone.git'))  # every fetch fails
        from imprint.dispatch import folders
        folder = folders.resolve('shop', 'default')
        with patch.object(git, 'fetch_branch', wraps=git.fetch_branch) as fetched:
            for _ in range(3):
                guards.takeover(tasks.get(self.task), folder, 'default', doing='review this Task')
        self.assertEqual(fetched.call_count, 1)
