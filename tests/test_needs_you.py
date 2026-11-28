"""What waits for the person, one row per reason (ADR 0241)."""

import time
from unittest.mock import patch

from imprint import needs_you, tasks
from imprint.dispatch import dispatched, guards, status, store
from imprint.tasks.actors import SYSTEM

from test_dispatch_api import DispatchApiCase


class NeedsYouCase(DispatchApiCase):
    def setUp(self):
        super().setUp()
        patcher = patch('imprint.agents_live.live', return_value={'agents': []})
        patcher.start()
        self.addCleanup(patcher.stop)

    def rows(self, kind=None):
        found = self.api.get('/api/tasks/needs-you').json()['rows']
        return [r for r in found if kind is None or r['kind'] == kind]

    def dismiss(self, row):
        return self.api.post('/api/tasks/needs-you/dismiss', json={'id': row['id'], 'workspace': row['workspace']}).json()


class ReasonTests(NeedsYouCase):
    def test_nothing_waits_on_a_quiet_board(self):
        status.set_roles('default', {'review'})  # a Task in Review would be reviewed
        self.assertEqual(self.rows(), [])

    def test_a_comment_to_the_person_is_a_row_until_read(self):
        agent = tasks.agent('conversation:worker', name='anvil', client='codex')
        tasks.comment(self.task, 'Which tax table applies?', to=['user'], actor=agent)
        [row] = self.rows('comment')
        self.assertEqual((row['task'], row['workspace']), (self.task, 'default'))
        self.assertIn('anvil (codex) wrote to you: Which tax table applies?', row['reason'])
        self.assertTrue(self.dismiss(row)['dismissed'])
        self.assertEqual(self.rows('comment'), [])
        self.assertEqual(tasks.user_inbox(mark=False), [])  # dismissing read it

    def test_replying_on_the_task_reads_what_was_written_to_the_person(self):
        agent = tasks.agent('conversation:worker', name='anvil', client='codex')
        tasks.comment(self.task, 'Which tax table applies?', to=['user'], actor=agent)
        self.api.post(f'/api/tasks/{self.task}/comment', json={'text': 'The 2026 one.'})
        self.assertEqual(self.rows('comment'), [])

    def test_a_task_assigned_to_the_person_comes_back_after_a_dismissal_once_it_changes(self):
        tasks.assign(self.task, add=['user'], actor=SYSTEM)
        [row] = self.rows('assigned')
        self.dismiss(row)
        self.assertEqual(self.rows('assigned'), [])
        time.sleep(0.01)
        tasks.comment(self.task, 'Dispatched sessions failed twice', actor=SYSTEM)  # the Task changes
        self.assertEqual(len(self.rows('assigned')), 1)

    def test_review_waits_for_the_person_only_when_no_device_reviews(self):
        dispatched.run(self.task)  # worked: now in Review
        self.assertEqual([r['task'] for r in self.rows('review')], [self.task])
        status.set_roles('default', {'review'})
        self.assertEqual(self.rows('review'), [])
        status.set_roles('default', set())
        tasks.announce_dispatching('other', 'bedrock', True, workspace='default', roles=['review', 'deploy'])
        self.assertEqual(self.rows('review'), [])
        tasks.announce_dispatching('other', 'bedrock', True, workspace='default', roles=['work'])
        self.assertEqual(len(self.rows('review')), 1)
        tasks.announce_dispatching('other', 'bedrock', True, workspace='default')  # an earlier release: on
        self.assertEqual(self.rows('review'), [])

    def test_a_task_dispatched_work_fed_waits_for_the_persons_move(self):
        dispatched.run(self.task)
        record = self.recorded()
        agent = tasks.agent('conversation:' + record['argv'][record['argv'].index('--session-id') + 1],
                            client='claude-code')
        made = tasks.create('Found while working', project='shop', acceptance=['ok'], status='ready', actor=agent)['id']
        self.assertIn(made, [r['task'] for r in self.rows('fed')])
        tasks.move(made, 'backlog', actor=tasks.USER)
        self.assertNotIn(made, [r['task'] for r in self.rows('fed')])

    def test_a_stale_task_whose_work_is_elsewhere_is_a_row(self):
        agent = tasks.agent('conversation:gone', name='wanderer', client='codex')
        tasks.claim(self.task, actor=agent)
        commit = '0123456789abcdef0123456789abcdef01234567'
        tasks.checkpoint(self.task, 'Half done', branch='task/x', commit=commit, actor=agent)
        with patch('imprint.tasks.view._View.claim', return_value=(False, True)):  # Stale
            tasks.comment(self.task, f'did not take it over: its last commit {commit[:12]} {guards.MISSING_WORK}',
                          actor=SYSTEM)
            [row] = self.rows('stale')
        self.assertEqual(row['task'], self.task)

    def test_a_dispatcher_that_switched_itself_off_is_a_row(self):
        status.set_roles('default', {'work'})
        now = time.time() + 1
        for number in range(3):
            store.add({'id': f'f{number}', 'workspace': 'default', 'task': self.task, 'title': 'x', 'project': 'shop',
                       'client': 'claude-code', 'role': 'work', 'started_at': now + number,
                       'ended_at': now + number + 0.5, 'outcome': 'failed', 'pid': 4242})
        self.assertTrue(guards.switch_off_after_failures(store.get('f2')))
        [row] = self.rows('dispatcher')
        self.assertIn('switched itself off after 3', row['reason'])
        status.set_roles('default', {'work'})  # the person switches it on again
        self.assertEqual(self.rows('dispatcher'), [])

    def test_an_agent_waiting_for_an_answer_is_a_row(self):
        agent = {'id': 'sess-1', 'name': 'grimforge', 'client': 'claude-code', 'state': 'needs_answer',
                 'question': 'Ship it to main too?', 'since': 1795800000.0, 'workspace': 'default',
                 'task': {'id': self.task, 'title': 'Add a done file'}, 'activity': {}}
        with patch('imprint.agents_live.live', return_value={'agents': [agent, {**agent, 'id': 'b', 'state': 'working'}]}):
            [row] = self.rows('answer')
        self.assertEqual(row['task'], self.task)
        self.assertIn('grimforge asks: Ship it to main too?', row['reason'])

    def test_a_dismissal_is_a_read_mark_paired_devices_receive(self):
        tasks.assign(self.task, add=['user'], actor=SYSTEM)
        [row] = self.rows('assigned')
        self.dismiss(row)
        self.assertEqual(tasks.unseen('user', [needs_you.DISMISSED + row['id']]), [])

    def test_a_row_id_must_be_short_text(self):
        self.assertEqual(self.api.post('/api/tasks/needs-you/dismiss', json={'id': ''}).status_code, 400)
        self.assertEqual(self.api.post('/api/tasks/needs-you/dismiss', json={'id': 'x' * 300}).status_code, 400)
