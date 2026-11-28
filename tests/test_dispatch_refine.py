"""Dispatched refinement of Backlog Tasks (ADR 0230, Refine Backlog 2)."""

import io
import json
import os
import subprocess
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from imprint import tasks
from imprint.dispatch import dispatched, guards, loop, store

from test_dispatch_loop import LoopCase


class RefineCase(LoopCase):
    def setUp(self):
        super().setUp()
        tasks.move(self.task, 'backlog', actor=tasks.USER)
        tasks.edit(self.task, acceptance=[], actor=tasks.USER)  # a Backlog Task without criteria
        store.switch_refining('default', True)

    def branches(self):
        return subprocess.run(['git', '-C', str(self.repo), 'branch', '--list', 'task/*'], capture_output=True,
                              text=True).stdout.strip()


class ChoiceTests(RefineCase):
    def test_backlog_tasks_without_criteria_are_refined_and_others_are_not(self):
        with_criteria = tasks.create('Clear one', project='shop', acceptance=['ok'], actor=tasks.USER)['id']
        persons = tasks.create('Mine', project='shop', actor=tasks.USER)['id']
        tasks.assign(persons, add=['user'], actor=tasks.USER)
        feature = tasks.create('A Feature', project='shop', actor=tasks.USER)['id']
        tasks.create('Part of it', project='shop', feature=feature, acceptance=['ok'], actor=tasks.USER)

        chosen = [s['id'] for s in loop.refinements('default')]
        self.assertEqual(chosen, [self.task])
        self.assertNotIn(with_criteria, chosen)
        self.assertEqual(loop.refinements('default')[0]['role'], 'refine')

    def test_it_refines_only_when_no_review_or_work_started(self):
        ready = tasks.create('Ready work', project='shop', status='ready', acceptance=['ok'])['id']
        store.switch('default', True)
        run = self.dispatcher.dispatch()
        self.assertEqual((run['task'], run['role']), (ready, 'work'))
        self.settle()
        tasks.move(ready, 'done', actor=tasks.USER)  # nothing to review or work any more

        run = self.dispatcher.dispatch()
        self.assertEqual((run['task'], run['role']), (self.task, 'refine'))
        self.settle()

    def test_its_own_switch_refines_while_the_dispatcher_is_off(self):
        self.assertFalse(store.switched_on('default'))
        run = self.dispatcher.dispatch()
        self.assertEqual(run['role'], 'refine')
        self.settle()

    def test_nothing_is_refined_while_refinement_is_off(self):
        store.switch_refining('default', False)
        self.assertIsNone(self.dispatcher.dispatch())


class RunTests(RefineCase):
    def test_a_refined_task_stays_in_backlog_and_its_copy_goes(self):
        run = dispatched.run(self.task, role='refine')

        self.assertEqual((run['outcome'], run['detail']), ('refined', 'refined the Task, which stays in Backlog'))
        state = tasks.get(self.task)
        self.assertEqual((state['status'], state['holder']), ('backlog', None))
        self.assertEqual(state['acceptance'], ['It works when run twice'])
        seen = json.loads(self.record.read_text())
        self.assertIn('- Your role: refine', seen['prompt'])
        self.assertIn('a throwaway copy of dev', seen['prompt'])
        self.assertTrue(seen['cwd'].endswith(f'{self.task}.refine'))
        self.assertFalse(Path(seen['cwd']).exists())  # removed when it ended
        self.assertEqual(self.branches(), '')  # no branch made
        self.assertFalse(guards.fed_by_dispatched(self.task, 'default', tasks.dispatched_sessions('default')))

    def test_a_task_is_refined_once_until_the_person_changes_it(self):
        with patch.dict(os.environ, {'FAKE_REFINE': 'nothing'}):
            run = dispatched.run(self.task, role='refine')
        self.assertEqual((run['outcome'], run['detail']), ('failed', 'ended without refining the Task'))
        self.assertEqual(tasks.get(self.task)['status'], 'backlog')
        self.assertEqual(loop.refinements('default'), [])  # tried once already
        tasks.comment(self.task, 'It is about the export button', actor=tasks.USER)
        self.assertEqual([s['id'] for s in loop.refinements('default')], [self.task])

    def test_a_refinement_that_moves_its_task_on_has_failed_and_the_task_goes_back(self):
        with patch.dict(os.environ, {'FAKE_REFINE': 'ready'}):
            run = dispatched.run(self.task, role='refine')
        self.assertEqual(run['outcome'], 'failed')
        self.assertIn('which a refinement never does', run['detail'])
        self.assertEqual(tasks.get(self.task)['status'], 'backlog')

    def test_a_refined_task_the_person_readies_is_worked(self):
        dispatched.run(self.task, role='refine')
        tasks.move(self.task, 'ready', actor=tasks.USER)
        store.switch('default', True)
        self.assertIn(self.task, [s['id'] for s in loop.candidates('default')])

    def test_refinement_refuses_a_task_not_in_backlog(self):
        tasks.edit(self.task, acceptance=['ok'], actor=tasks.USER)
        tasks.move(self.task, 'ready', actor=tasks.USER)
        with self.assertRaises(ValueError):
            dispatched.run(self.task, role='refine')

    def test_refinement_shares_the_daily_limit(self):
        for n in range(10):
            store.add({'id': f'today{n}', 'workspace': 'default', 'task': self.task, 'title': 't', 'project': 'shop',
                       'client': 'claude-code', 'role': 'work', 'started_at': __import__('time').time(),
                       'ended_at': __import__('time').time(), 'outcome': 'review'})
        with redirect_stderr(io.StringIO()):
            self.assertIsNone(self.dispatcher.dispatch())


if __name__ == '__main__':
    unittest.main()
