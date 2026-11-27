"""Guards against runaway dispatched work (ADR 0220, Dispatcher 10)."""

import os
import time
import unittest
from unittest.mock import patch

from imprint import tasks, tasks_tool
from imprint.dispatch import dispatched, guards, loop, status, store

from test_dispatch_loop import LoopCase


def _ended(task, outcome, *, started=None, workspace='default'):
    """A finished run, as the Dispatcher records one."""
    now = started or time.time()
    return store.add({'id': f'{outcome[:2]}{now:.6f}'.replace('.', ''), 'workspace': workspace, 'task': task,
                      'title': 't', 'project': 'shop', 'client': 'claude-code', 'role': 'work',
                      'started_at': now, 'ended_at': now + 1, 'outcome': outcome, 'detail': outcome})


class DailyLimitTests(LoopCase):
    def test_the_eleventh_session_of_a_day_does_not_start_and_the_strip_says_why(self):
        status.switch('default', True)
        for _ in range(10):
            _ended('T-other', 'review')
        self.assertIsNone(self.dispatcher.dispatch())
        shown = status.status('default', projects=False)
        self.assertIn('10 Dispatched sessions started today', shown['held'])
        self.assertFalse(self.record.exists())

    def test_yesterdays_sessions_do_not_count(self):
        for _ in range(10):
            _ended('T-other', 'review', started=time.time() - 2 * 86400)
        self.assertIsNone(guards.held())

    def test_refused_runs_started_nothing_and_do_not_count(self):
        for _ in range(10):
            _ended('T-other', 'refused')
        self.assertIsNone(guards.held())


class FailuresInARowTests(LoopCase):
    def test_three_failures_in_a_row_switch_it_off_with_one_comment_to_the_person(self):
        status.switch('default', True)
        with patch.dict(os.environ, {'FAKE_MODE': 'fail'}):
            for title in ('Second', 'Third'):
                tasks.create(title, project='shop', status='ready', acceptance=['ok'])
            for _ in range(3):
                self.assertIsNotNone(self.dispatcher.dispatch())
                self.settle()

        self.assertFalse(store.switched_on('default'))
        notices = [c for c in tasks.user_inbox() if 'switched itself off' in c['text']]
        self.assertEqual(len(notices), 1)
        self.assertIsNone(self.dispatcher.dispatch())

    def test_a_success_or_a_new_switch_on_starts_the_count_again(self):
        status.switch('default', True)
        now = time.time()
        _ended('T-a', 'failed', started=now + 1)
        _ended('T-b', 'review', started=now + 2)
        _ended('T-c', 'failed', started=now + 3)
        self.assertEqual(guards.failures_in_a_row(), 1)
        with patch.object(store.time, 'time', return_value=now + 10):
            status.switch('default', True)
        self.assertEqual(guards.failures_in_a_row(), 0)

    def test_sessions_the_person_stopped_are_no_failures(self):
        status.switch('default', True)
        now = time.time()
        _ended('T-a', 'failed', started=now + 1)
        _ended('T-b', 'stopped', started=now + 2)
        _ended('T-c', 'failed', started=now + 3)
        self.assertEqual(guards.failures_in_a_row(), 2)


class TaskFailingTwiceTests(LoopCase):
    def test_a_task_that_failed_twice_goes_to_the_person(self):
        with patch.dict(os.environ, {'FAKE_MODE': 'fail'}):
            dispatched.run(self.task)
            self.assertNotIn('user', tasks.get(self.task)['assignees'])
            dispatched.run(self.task)

        state = tasks.get(self.task)
        self.assertIn('user', state['assignees'])
        self.assertTrue(any('leaves it to you' in c['text'] for c in tasks.user_inbox()))
        status.switch('default', True)
        self.assertNotIn(self.task, [s['id'] for s in loop.candidates('default')])

    def test_a_stopped_session_is_no_failure_of_its_task(self):
        with patch.dict(os.environ, {'FAKE_MODE': 'hang'}):
            for _ in range(2):
                run, proc = dispatched.start(self.task)
                session = dispatched.Session(run, proc)
                session.stop('was stopped by the person')
                self.assertEqual(session.step()['outcome'], 'stopped')
        self.assertNotIn('user', tasks.get(self.task)['assignees'])


class OwnTasksTests(LoopCase):
    def dispatched_agent(self):
        """The agent a Dispatched session became after taking its claim over."""
        dispatched.run(self.task)
        record = self.recorded()
        return tasks.agent('conversation:' + record['argv'][record['argv'].index('--session-id') + 1],
                           client='claude-code')

    def test_the_board_knows_dispatched_sessions_by_the_token_takeover(self):
        agent = self.dispatched_agent()
        sessions = tasks.dispatched_sessions()
        self.assertIn(agent['session'], sessions)
        self.assertTrue(any(s.startswith('dispatch:') for s in sessions))

    def test_a_stale_takeover_without_the_token_is_another_agents(self):
        with patch.dict(os.environ, {'FAKE_MODE': 'fail'}):
            dispatched.run(self.task)  # leaves the Task Ready again
        other = tasks.agent('conversation:interactive', client='codex')
        tasks.claim(self.task, actor=other)
        self.assertNotIn('conversation:interactive', tasks.dispatched_sessions())

    def test_a_dispatched_sessions_new_task_starts_in_backlog(self):
        agent = self.dispatched_agent()
        reply = tasks_tool.create(title='Follow-up found', project='shop', status='Ready', acceptance=['ok'],
                                  actor=agent, workspace='default')
        made = tasks.listing(text='Follow-up found')[0]
        self.assertEqual(made['status'], 'backlog')
        self.assertIn('start in Backlog', reply)

    def test_an_interactive_agents_new_task_can_be_ready(self):
        agent = tasks.agent('conversation:interactive', client='claude-code')
        tasks_tool.create(title='Planned', project='shop', status='Ready', acceptance=['ok'], actor=agent,
                          workspace='default')
        self.assertEqual(tasks.listing(text='Planned')[0]['status'], 'ready')

    def test_a_task_a_dispatched_session_moved_to_ready_is_never_dispatched(self):
        agent = self.dispatched_agent()
        promoted = tasks.create('Found work', project='shop', acceptance=['ok'], actor=agent)['id']
        tasks.move(promoted, 'ready', actor=agent)
        status.switch('default', True)

        self.assertNotIn(promoted, [s['id'] for s in loop.candidates('default')])
        tasks.move(promoted, 'backlog')
        tasks.move(promoted, 'ready')  # the person readies it: now it is taken
        self.assertIn(promoted, [s['id'] for s in loop.candidates('default')])

    def test_a_task_a_review_sent_back_to_ready_is_taken_again(self):
        agent = self.dispatched_agent()  # the Task is in Review now
        reviewer = tasks.agent('conversation:reviewer', client='codex')
        tasks.move(self.task, 'ready', actor=reviewer)
        status.switch('default', True)
        self.assertIn(self.task, [s['id'] for s in loop.candidates('default')])
        self.assertIsNotNone(agent)


if __name__ == '__main__':
    unittest.main()
