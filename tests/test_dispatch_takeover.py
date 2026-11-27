"""Stale takeovers by the Dispatcher (ADR 0220, ADR 0223, Dispatcher 8)."""

import json
import subprocess
import sys

from imprint import tasks
from imprint.dispatch import status, store

from test_dispatch_loop import LoopCase
from test_dispatch_run import _git


def _gone_pid() -> int:
    done = subprocess.Popen([sys.executable, '-c', 'pass'])
    done.wait()
    return done.pid


class TakeoverCase(LoopCase):
    def stale(self, title='Half done', *, branch=None, commit=None, priority='P3'):
        """A Task an interactive agent claimed and left: its process is gone, so it is Stale at once."""
        task = tasks.create(title, project='shop', status='ready', acceptance=['finished'], priority=priority)['id']
        agent = tasks.agent('conversation:gone', name='wanderer', client='codex')
        tasks.claim(task, actor=agent)
        if branch or commit:
            tasks.checkpoint(task, 'Wrote the first half', branch=branch, commit=commit, next_step='Write the second half',
                             files=['half.txt'], actor=agent)
        tasks.renew('conversation:gone', pid=_gone_pid())  # its process ends: Stale at once
        self.assertTrue(tasks.get(task)['stale'])
        return task


class OrderTests(TakeoverCase):
    def test_a_stale_task_goes_before_any_ready_one_whatever_their_priority(self):
        stale = self.stale(priority='P3')
        status.switch('default', True)

        run = self.dispatcher.dispatch()
        self.assertEqual(run['task'], stale)
        self.settle()
        self.assertEqual(tasks.get(stale)['status'], 'review')


class BranchTests(TakeoverCase):
    def test_the_takeover_continues_on_the_tasks_own_branch_at_its_last_commit(self):
        task_id = tasks.create('placeholder', project='shop')['id']  # learn the next id's shape only
        tasks.move(task_id, 'cancelled')
        _git(self.repo, 'checkout', '-q', '-b', 'task/work-elsewhere')
        (self.repo / 'half.txt').write_text('first half\n')
        _git(self.repo, 'add', 'half.txt')
        _git(self.repo, 'commit', '-q', '-m', 'first half')
        commit = self.base_commit()
        _git(self.repo, 'checkout', '-q', 'dev')
        stale = self.stale()
        branch = f'task/{stale[2:].lower()}-half-done'
        _git(self.repo, 'branch', '-m', 'task/work-elsewhere', branch)
        agent = tasks.agent('conversation:gone2', client='codex')
        tasks.claim(stale, actor=agent)
        tasks.checkpoint(stale, 'Wrote the first half', branch=branch, commit=commit, actor=agent)
        tasks.renew('conversation:gone2', pid=_gone_pid())
        status.switch('default', True)

        run = self.dispatcher.dispatch()
        self.settle()

        self.assertEqual(run['branch'], branch)
        log = subprocess.run(['git', '-C', run['worktree'], 'log', '--format=%s', '-2'], capture_output=True,
                             text=True).stdout.split('\n')
        self.assertIn('first half', log)

    def test_the_prompt_carries_the_handoff(self):
        stale = self.stale(branch='dev', commit=self.base_commit())
        status.switch('default', True)
        self.dispatcher.dispatch()
        self.settle()

        prompt = json.loads(self.record.read_text())['prompt']
        self.assertIn('Earlier work on this Task (its Handoff)', prompt)
        self.assertIn('Last checkpoint: Wrote the first half', prompt)
        self.assertIn('Its next step: Write the second half', prompt)
        self.assertIn('Previous holder: wanderer (codex)', prompt)
        self.assertIn(stale, prompt)

    def base_commit(self):
        return subprocess.run(['git', '-C', str(self.repo), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()


class MissingWorkTests(TakeoverCase):
    def test_a_stale_task_whose_last_commit_is_elsewhere_is_left_with_one_comment(self):
        stale = self.stale(branch='task/x-half', commit='0123456789abcdef0123456789abcdef01234567', priority='P0')
        status.switch('default', True)

        first = self.dispatcher.dispatch()
        self.assertEqual(first['task'], self.task)  # the Ready one, not the Stale one
        self.settle()
        tasks.move(self.task, 'done')  # out of the way for the next pass
        self.assertIsNone(self.dispatcher.dispatch())

        notes = [e for e in tasks.history(stale) if e['kind'] == 'comment' and e['actor'] == 'system']
        self.assertEqual(len(notes), 1)
        self.assertIn("0123456789ab is not in this device's copy of the repository", notes[0]['data']['text'])
        self.assertEqual(store.runs_of(stale), [])

    def test_a_stale_task_with_no_commit_is_taken(self):
        stale = self.stale()
        status.switch('default', True)
        self.assertEqual(self.dispatcher.dispatch()['task'], stale)
        self.settle()


if __name__ == '__main__':
    import unittest
    unittest.main()
