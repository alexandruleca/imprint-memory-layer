"""Dispatched reviews (ADR 0221, Dispatcher 7)."""

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

from imprint import tasks
from imprint.dispatch import dispatched, loop, status, store

from test_dispatch_clients import ClientsCase
from test_dispatch_loop import LoopCase


def _settle(dispatcher, case):
    case.assertTrue(__import__('test_dispatch_loop')._until(lambda: (dispatcher.watch(), not dispatcher.sessions)[1]))


class ReviewOrderTests(LoopCase):
    def test_a_task_in_review_is_reviewed_before_any_other_work(self):
        dispatched.run(self.task)  # worked: now in Review
        ready = tasks.create('Next', project='shop', status='ready', acceptance=['ok'], priority='P0')['id']
        status.switch('default', True)

        run = self.dispatcher.dispatch()
        self.assertEqual((run['task'], run['role']), (self.task, 'review'))
        self.settle()
        self.assertEqual(tasks.get(self.task)['status'], 'done')
        self.assertEqual(tasks.get(ready)['status'], 'ready')

    def test_with_one_client_a_fresh_session_of_it_reviews(self):
        dispatched.run(self.task)
        status.switch('default', True)
        [review] = loop.reviews('default')
        self.assertEqual(review['client'], 'claude-code')

    def test_tasks_in_review_that_are_the_persons_or_held_are_left(self):
        dispatched.run(self.task)
        tasks.assign(self.task, add=['user'])
        self.assertEqual(loop.reviews('default'), [])


class ReviewOutcomeTests(LoopCase):
    def test_a_passing_review_moves_it_to_done_and_names_the_branch_for_the_person(self):
        worked = dispatched.run(self.task)
        review = dispatched.run(self.task, role='review')

        self.assertEqual(review['outcome'], 'passed')
        self.assertEqual(tasks.get(self.task)['status'], 'done')
        prompt = json.loads(self.record.read_text())['prompt']
        self.assertIn('- Your role: review', prompt)
        self.assertRegex(prompt, r'- Review against: [0-9a-f]{40}')  # the base the worker's branch was made from
        notice = [c for c in tasks.user_inbox() if 'ready to merge' in c['text']]
        self.assertEqual(len(notice), 1)
        self.assertIn(worked['branch'], notice[0]['text'])

    def test_a_failing_review_sends_it_back_and_the_next_worker_continues_on_the_branch(self):
        worked = dispatched.run(self.task)
        with patch.dict(os.environ, {'FAKE_REVIEW': 'return'}):
            review = dispatched.run(self.task, role='review')

        self.assertEqual(review['outcome'], 'returned')
        state = tasks.get(self.task)
        self.assertEqual((state['status'], state['holder']), ('ready', None))
        again = dispatched.run(self.task)
        self.assertEqual(again['branch'], worked['branch'])
        self.assertIn('done.txt is empty', json.loads(self.record.read_text())['prompt'] + ''.join(
            e['data'].get('text', '') for e in tasks.history(self.task) if e['kind'] == 'comment'))

    def test_two_failed_reviews_hand_the_task_to_the_person(self):
        with patch.dict(os.environ, {'FAKE_REVIEW': 'return'}):
            for _ in range(2):
                dispatched.run(self.task)
                dispatched.run(self.task, role='review')
        state = tasks.get(self.task)
        self.assertIn('user', state['assignees'])
        self.assertTrue(any('sent this Task back 2 times' in c['text'] for c in tasks.user_inbox()))

    def test_a_reviewer_that_decides_nothing_fails_and_leaves_it_in_review(self):
        dispatched.run(self.task)
        with patch.dict(os.environ, {'FAKE_REVIEW': 'nothing'}):
            review = dispatched.run(self.task, role='review')
        self.assertEqual(review['outcome'], 'failed')
        state = tasks.get(self.task)
        self.assertEqual((state['status'], state['holder']), ('review', None))

    def test_a_review_is_refused_for_a_task_not_in_review(self):
        with self.assertRaises(ValueError):
            dispatched.run(self.task, role='review')


class CrossClientTests(ClientsCase):
    def test_codex_reviews_what_claude_code_did(self):
        dispatched.run(self.task, client='claude-code')
        status.switch('default', True)
        [review] = loop.reviews('default')
        self.assertEqual(review['client'], 'codex')
        run = dispatched.run(self.task, role='review')
        self.assertEqual((run['client'], run['outcome']), ('codex', 'passed'))

    def test_claude_code_reviews_what_codex_did(self):
        dispatched.run(self.task, client='codex')
        [review] = loop.reviews('default')
        self.assertEqual(review['client'], 'claude-code')


class CleanupTests(LoopCase):
    def test_a_done_tasks_worktree_goes_and_its_branch_stays(self):
        worked = dispatched.run(self.task)
        dispatched.run(self.task, role='review')
        self.assertTrue(Path(worked['worktree']).exists())

        self.assertEqual(loop.cleanup(), 1)
        self.assertFalse(Path(worked['worktree']).exists())
        branches = subprocess.run(['git', '-C', str(self.repo), 'branch', '--list', worked['branch']],
                                  capture_output=True, text=True).stdout
        self.assertIn(worked['branch'], branches)
        self.assertEqual(loop.cleanup(), 0)  # once

    def test_an_open_tasks_worktree_stays(self):
        worked = dispatched.run(self.task)  # in Review, not ended
        self.assertEqual(loop.cleanup(), 0)
        self.assertTrue(Path(worked['worktree']).exists())

    def test_a_worktree_in_use_is_tried_again_later(self):
        worked = dispatched.run(self.task)
        tasks.move(self.task, 'cancelled')
        with patch.object(loop.worktrees, 'remove', side_effect=RuntimeError('in use')):
            self.assertEqual(loop.cleanup(), 0)
        self.assertEqual(loop.cleanup(), 1)
        self.assertFalse(Path(worked['worktree']).exists())


if __name__ == '__main__':
    import unittest
    unittest.main()
