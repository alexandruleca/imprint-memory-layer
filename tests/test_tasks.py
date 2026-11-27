"""The Board (ADRs 0208-0212): Tasks per workspace, kept as a log of events
whose current state is computed from them, read and changed through the
tasks module's public functions."""
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from imprint import config, tasks

GITHUB_TOKEN = 'ghp_' + 'A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8'


class TasksTestCase(unittest.TestCase):
    """A data directory of its own, one device, and two workspaces."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data = Path(directory.name)
        for patcher in (patch.object(config, 'get_data_dir', return_value=self.data),
                        patch.object(tasks, '_device', return_value='device-a'),
                        patch.dict(os.environ, {'IMPRINT_WORKSPACE': ''})):
            patcher.start()
            self.addCleanup(patcher.stop)
        config.register_workspace('work')


class CreateAndReadTests(TasksTestCase):
    def test_a_created_task_is_read_back_in_its_workspace_only(self):
        made = tasks.create('Add retries to the uploader', project='imprint', description='Uploads fail on 503.',
                            workspace='default')
        self.assertRegex(made['id'], r'^T-[0-9a-hjkmnp-tv-z]{5}$')
        self.assertEqual(made['status'], 'backlog')
        self.assertEqual(made['priority'], 'P2')
        got = tasks.get(made['id'], workspace='default')
        self.assertEqual((got['title'], got['project'], got['description']),
                         ('Add retries to the uploader', 'imprint', 'Uploads fail on 503.'))
        self.assertEqual([t['id'] for t in tasks.listing(workspace='default')], [made['id']])
        self.assertEqual(tasks.listing(workspace='work'), [])
        with self.assertRaisesRegex(LookupError, f'No task {made["id"]}'):
            tasks.get(made['id'], workspace='work')

    def test_a_task_needs_a_title_and_a_project(self):
        with self.assertRaisesRegex(ValueError, 'title'):
            tasks.create('  ', project='imprint', workspace='default')
        with self.assertRaisesRegex(ValueError, 'project'):
            tasks.create('Fix it', project='', workspace='default')

    def test_ids_do_not_repeat_and_a_drawn_id_already_used_is_drawn_again(self):
        first = tasks.create('One', project='p', workspace='default')
        taken = first['id'][2:]
        drawn = iter([*taken, *'abcde'])
        with patch.object(tasks.secrets, 'choice', side_effect=lambda alphabet: next(drawn)):
            second = tasks.create('Two', project='p', workspace='default')
        self.assertEqual(second['id'], 'T-abcde')
        ids = {tasks.create(f'n{n}', project='p', workspace='default')['id'] for n in range(50)}
        self.assertEqual(len(ids), 50)

    def test_every_change_is_an_event_with_its_device_session_and_clock(self):
        made = tasks.create('Write docs', project='p', workspace='default',
                            actor=tasks.agent('session-1', name='grimforge', client='claude-code'))
        [event] = tasks.history(made['id'], workspace='default')
        self.assertEqual(event['kind'], 'created')
        self.assertEqual((event['device'], event['session'], event['agent'], event['client']),
                         ('device-a', 'session-1', 'grimforge', 'claude-code'))
        self.assertRegex(event['hlc'], r'^\d{13}\.\d{4}$')
        self.assertEqual(made['created_by'], {'kind': 'agent', 'session': 'session-1', 'name': 'grimforge',
                                              'client': 'claude-code', 'device': 'device-a'})
        by_user = tasks.create('Review the plan', project='p', workspace='default')
        self.assertEqual(by_user['created_by'], {'kind': 'user', 'device': 'device-a'})

    def test_clocks_keep_order_when_the_wall_clock_stands_still_or_goes_back(self):
        with patch.object(tasks.time, 'time', return_value=1_800_000_000.0):
            a = tasks.create('a', project='p', workspace='default')
            b = tasks.create('b', project='p', workspace='default')
        with patch.object(tasks.time, 'time', return_value=1_700_000_000.0):
            c = tasks.create('c', project='p', workspace='default')
        clocks = [tasks.history(t['id'], workspace='default')[0]['hlc'] for t in (a, b, c)]
        self.assertEqual(clocks, sorted(clocks))
        self.assertEqual(len(set(clocks)), 3)

    def test_credentials_are_stored_redacted(self):
        made = tasks.create(f'Rotate {GITHUB_TOKEN}', project='p', description=f'token={GITHUB_TOKEN}',
                            acceptance=[f'{GITHUB_TOKEN} no longer works'], workspace='default')
        stored = (self.data / 'tasks.sqlite3').read_bytes()
        self.assertNotIn(GITHUB_TOKEN.encode(), stored)
        got = tasks.get(made['id'], workspace='default')
        for text in (got['title'], got['description'], got['acceptance'][0]):
            self.assertNotIn(GITHUB_TOKEN, text)
            self.assertIn('[REDACTED:', text)

    def test_each_workspace_keeps_its_tasks_in_a_store_of_its_own(self):
        tasks.create('Here', project='p', workspace='default')
        tasks.create('There', project='p', workspace='work')
        self.assertTrue((self.data / 'tasks.sqlite3').is_file())
        self.assertTrue((self.data / 'tasks_work.sqlite3').is_file())
        with self.assertRaisesRegex(ValueError, 'workspace'):
            tasks.listing(workspace='../escape')
        with self.assertRaisesRegex(ValueError, "Unknown workspace 'nowhere'"):
            tasks.create('Lost', project='p', workspace='nowhere')
        self.assertFalse((self.data / 'tasks_nowhere.sqlite3').exists())

    def test_reading_an_empty_workspace_makes_no_store(self):
        self.assertEqual(tasks.listing(workspace='default'), [])
        self.assertEqual(tasks.board(workspace='default')['columns']['ready'], [])
        self.assertFalse((self.data / 'tasks.sqlite3').exists())

    def test_the_board_lays_tasks_out_by_status(self):
        a = tasks.create('a', project='p', workspace='default')
        b = tasks.create('b', project='q', workspace='default')
        board = tasks.board(workspace='default')
        self.assertEqual(list(board['columns']), list(tasks.STATUSES))
        self.assertEqual([t['id'] for t in board['columns']['backlog']], [a['id'], b['id']])
        self.assertEqual([t['id'] for t in tasks.board(project='q', workspace='default')['columns']['backlog']],
                         [b['id']])

    def test_a_task_is_found_by_its_id_in_any_case_and_with_or_without_the_prefix(self):
        made = tasks.create('Find me', project='p', workspace='default')
        for form in (made['id'], made['id'].upper(), made['id'][2:]):
            self.assertEqual(tasks.get(form, workspace='default')['id'], made['id'])
        with self.assertRaisesRegex(LookupError, 'No task T-zzzzz'):
            tasks.get('T-zzzzz', workspace='default')



AGENT = tasks.agent('session-1', name='grimforge', client='claude-code')


def make(title='Task', **fields):
    fields.setdefault('project', 'p')
    return tasks.create(title, workspace='default', **fields)


class ShapeTests(TasksTestCase):
    def test_edits_keep_the_last_value_and_appended_text_accumulates(self):
        made = make('Draft', description='First version.')
        tasks.edit(made['id'], title='Final', priority='p0', acceptance=['Docs build'], actor=AGENT, workspace='default')
        tasks.append(made['id'], 'Resolved: use retries.', actor=AGENT, workspace='default')
        tasks.append(made['id'], 'Resolved: keep the timeout.', workspace='default')
        got = tasks.get(made['id'], workspace='default')
        self.assertEqual((got['title'], got['priority'], got['acceptance']), ('Final', 'P0', ['Docs build']))
        self.assertEqual(got['description'], 'First version.\n\nResolved: use retries.\n\nResolved: keep the timeout.')
        tasks.edit(made['id'], description='Rewritten.', workspace='default')
        self.assertEqual(tasks.get(made['id'], workspace='default')['description'], 'Rewritten.')
        kinds = [e['kind'] for e in tasks.history(made['id'], workspace='default')]
        self.assertEqual(kinds, ['created', 'edited', 'appended', 'appended', 'edited'])

    def test_a_task_moves_to_ready_only_with_an_acceptance_criterion(self):
        made = make()
        with self.assertRaisesRegex(ValueError, 'at least one acceptance criterion'):
            tasks.move(made['id'], 'ready', workspace='default')
        tasks.edit(made['id'], acceptance=['It works'], workspace='default')
        self.assertEqual(tasks.move(made['id'], 'ready', workspace='default')['status'], 'ready')
        with self.assertRaisesRegex(ValueError, 'acceptance criterion'):
            tasks.edit(made['id'], acceptance=[], workspace='default')

    def test_cancelling_ends_a_task_and_reopening_it_clears_the_end(self):
        made = make()
        with patch.object(tasks.time, 'time', return_value=1_800_000_000.0):
            cancelled = tasks.move(made['id'], 'cancelled', actor=AGENT, workspace='default')
        self.assertEqual((cancelled['status'], cancelled['ended_at']), ('cancelled', 1_800_000_000.0))
        reopened = tasks.move(made['id'], 'backlog', workspace='default')
        self.assertIsNone(reopened['ended_at'])

    def test_the_blocked_mark_has_a_reason_and_keeps_the_status(self):
        made = make(acceptance=['x'], status='ready')
        blocked = tasks.block(made['id'], 'Waiting for the API key', actor=AGENT, workspace='default')
        self.assertEqual(blocked['status'], 'ready')
        self.assertEqual(blocked['blocked']['reason'], 'Waiting for the API key')
        self.assertEqual(blocked['blocked']['by']['name'], 'grimforge')
        with self.assertRaisesRegex(ValueError, 'reason'):
            tasks.block(made['id'], ' ', workspace='default')
        self.assertIsNone(tasks.unblock(made['id'], workspace='default')['blocked'])

    def test_tags_and_assignees_are_added_and_removed_as_sets(self):
        made = make(tags=['UI'], assignees=['codex'])
        tasks.tag(made['id'], add=['Board', 'ui'], remove=['missing'], workspace='default')
        tasks.assign(made['id'], add=['user', 'claude-code@bedrock', '@bedrock'], remove=['codex'], workspace='default')
        got = tasks.get(made['id'], workspace='default')
        self.assertEqual(got['tags'], ['ui', 'board'])
        self.assertEqual(got['assignees'], ['user', 'claude-code@bedrock', '@bedrock'])
        for bad in ('two words', 'a@b@c', '@', ''):
            with self.subTest(assignee=bad), self.assertRaisesRegex(ValueError, 'assignee'):
                tasks.assign(made['id'], add=[bad], workspace='default')


class GroupingTests(TasksTestCase):
    def test_a_task_belongs_to_one_feature_and_features_do_not_nest(self):
        feature = make('Checkout redesign')
        other = make('Search revamp')
        task = make('New total line', feature=feature['id'])
        self.assertEqual(task['feature'], feature['id'])
        moved = tasks.link(task['id'], feature=other['id'], workspace='default')
        self.assertEqual(moved['feature'], other['id'])
        with self.assertRaisesRegex(ValueError, 'Feature'):
            tasks.link(other['id'], feature=feature['id'], workspace='default')  # other has a Task of its own
        with self.assertRaisesRegex(ValueError, 'Feature'):
            make('Nested', feature=task['id'])  # task belongs to a Feature
        with self.assertRaisesRegex(ValueError, 'itself'):
            tasks.link(feature['id'], feature=feature['id'], workspace='default')
        self.assertIsNone(tasks.link(task['id'], feature='', workspace='default')['feature'])

    def test_blockers_are_tasks_without_cycles_and_open_ones_are_counted(self):
        a, b, c = make('a'), make('b'), make('c')
        tasks.link(c['id'], add_blockers=[a['id'], b['id']], workspace='default')
        got = tasks.get(c['id'], workspace='default')
        self.assertEqual(sorted(got['blockers']), sorted([a['id'], b['id']]))
        self.assertEqual(sorted(got['open_blockers']), sorted([a['id'], b['id']]))
        tasks.edit(a['id'], acceptance=['done'], workspace='default')
        tasks.move(a['id'], 'done', workspace='default')
        self.assertEqual(tasks.get(c['id'], workspace='default')['open_blockers'], [b['id']])
        with self.assertRaisesRegex(ValueError, 'cycle'):
            tasks.link(a['id'], add_blockers=[c['id']], workspace='default')
        with self.assertRaisesRegex(ValueError, 'itself'):
            tasks.link(a['id'], add_blockers=[a['id']], workspace='default')
        with self.assertRaisesRegex(LookupError, 'No task T-zzzzz'):
            tasks.link(a['id'], add_blockers=['T-zzzzz'], workspace='default')
        tasks.link(c['id'], remove_blockers=[b['id']], workspace='default')
        self.assertEqual(tasks.get(c['id'], workspace='default')['blockers'], [a['id']])

    def test_filters_combine(self):
        feature = make('Checkout')
        hit = make('Fix tax rounding', project='shop', tags=['cart'], priority='P1', assignees=['codex'],
                   description='Totals are off by a cent.', feature=feature['id'])
        make('Fix tax rounding elsewhere', project='blog', tags=['cart'], priority='P1')
        make('Cart icon', project='shop', tags=['ui'])
        found = tasks.listing(project='shop', tag='cart', priority='p1', assignee='codex', text='OFF BY',
                              feature=feature['id'], status='backlog', workspace='default')
        self.assertEqual([t['id'] for t in found], [hit['id']])
        self.assertEqual(len(tasks.listing(tag='cart', workspace='default')), 2)
        self.assertEqual(len(tasks.listing(text='tax', workspace='default')), 2)

    def test_ended_tasks_leave_the_board_after_14_days_and_stay_readable(self):
        made = make(acceptance=['x'])
        with patch.object(tasks.time, 'time', return_value=1_800_000_000.0):
            tasks.move(made['id'], 'done', workspace='default')
        with patch.object(tasks.time, 'time', return_value=1_800_000_000.0 + 13 * 86400):
            self.assertEqual(len(tasks.board(workspace='default')['columns']['done']), 1)
        with patch.object(tasks.time, 'time', return_value=1_800_000_000.0 + 15 * 86400):
            self.assertEqual(tasks.board(workspace='default')['columns']['done'], [])
            self.assertEqual(len(tasks.listing(archived=True, workspace='default')), 1)
            self.assertEqual(tasks.get(made['id'], workspace='default')['status'], 'done')


class DeleteTests(TasksTestCase):
    def test_only_the_user_deletes_and_a_deletion_keeps_only_a_tombstone(self):
        made = make(f'Secret plan', description='Very private.')
        with self.assertRaisesRegex(PermissionError, 'cancel'):
            tasks.delete(made['id'], actor=AGENT, workspace='default')
        tasks.append(made['id'], 'More private notes.', workspace='default')
        tasks.delete(made['id'], workspace='default')
        with self.assertRaisesRegex(LookupError, 'deleted'):
            tasks.get(made['id'], workspace='default')
        self.assertEqual(tasks.listing(workspace='default'), [])
        self.assertEqual(tasks.listing(archived=True, workspace='default'), [])
        import sqlite3
        conn = sqlite3.connect(self.data / 'tasks.sqlite3')
        rows = conn.execute('SELECT kind, data FROM events ORDER BY hlc').fetchall()
        conn.close()
        self.assertEqual([kind for kind, _ in rows], ['created', 'appended', 'deleted'])
        self.assertTrue(all('private' not in data for _, data in rows))



CODEX = tasks.agent('session-2', name='quill', client='codex')
T0 = 1_800_000_000.0


def time_now():
    import time
    return time.time()


def at(seconds):
    return patch.object(tasks.time, 'time', return_value=T0 + seconds)


def dead_pid():
    import subprocess
    import sys
    child = subprocess.Popen([sys.executable, '-c', 'pass'])
    child.wait()
    return child.pid


class ClaimTests(TasksTestCase):
    def ready(self, **fields):
        return make(acceptance=['It works'], status='ready', **fields)['id']

    def test_claiming_takes_the_task_into_progress_and_returns_a_token(self):
        task = self.ready()
        with at(0):
            claimed = tasks.claim(task, actor=AGENT, workspace='default')
        self.assertRegex(claimed['token'], r'^[A-Za-z0-9_-]{20,}$')
        state = claimed['task']
        self.assertEqual(state['status'], 'in_progress')
        self.assertEqual({k: state['holder'][k] for k in ('session', 'name', 'client', 'device', 'since')},
                         {'session': 'session-1', 'name': 'grimforge', 'client': 'claude-code', 'device': 'device-a',
                          'since': T0})
        self.assertNotIn(claimed['token'], (self.data / 'tasks.sqlite3').read_bytes().decode('latin-1'))
        again = tasks.claim(task, actor=AGENT, workspace='default')
        self.assertIsNone(again['token'])
        self.assertEqual(again['task']['holder']['session'], 'session-1')

    def test_a_live_claim_is_not_taken_and_names_its_holder(self):
        task = self.ready()
        tasks.claim(task, actor=AGENT, workspace='default')
        with self.assertRaisesRegex(PermissionError, r'held by grimforge \(claude-code\)'):
            tasks.claim(task, actor=CODEX, workspace='default')

    def test_the_claim_token_takes_the_claim_back_after_a_restart(self):
        task = self.ready()
        token = tasks.claim(task, actor=AGENT, workspace='default')['token']
        resumed = tasks.agent('session-1b', name='grimforge', client='claude-code')
        with self.assertRaisesRegex(PermissionError, 'held by'):
            tasks.claim(task, actor=resumed, token='wrong-token-wrong-token', workspace='default')
        state = tasks.claim(task, actor=resumed, token=token, workspace='default')['task']
        self.assertEqual(state['holder']['session'], 'session-1b')

    def test_a_holder_whose_process_ended_releases_its_claim_at_once(self):
        task = self.ready()
        tasks.claim(task, actor=AGENT, pid=dead_pid(), workspace='default')
        state = tasks.get(task, workspace='default')
        self.assertTrue(state['stale'])
        self.assertIsNone(state['holder'])
        self.assertEqual(state['previous_holder']['name'], 'grimforge')
        taken = tasks.claim(task, actor=CODEX, workspace='default')['task']
        self.assertEqual(taken['holder']['name'], 'quill')
        self.assertFalse(taken['stale'])

    def test_leases_run_out_unless_renewed(self):
        task = self.ready()
        with at(0):
            tasks.claim(task, actor=AGENT, workspace='default')
        with at(14 * 60):
            self.assertFalse(tasks.get(task, workspace='default')['stale'])
        with at(16 * 60):
            self.assertTrue(tasks.get(task, workspace='default')['stale'])
        with at(14 * 60):
            self.assertEqual(tasks.renew('session-1'), 1)
        with at(28 * 60):
            self.assertFalse(tasks.get(task, workspace='default')['stale'])
        with patch.dict(os.environ, {'IMPRINT_TASKS_LEASE_MINUTES': '30'}), at(16 * 60 + 14 * 60 + 60):
            self.assertFalse(tasks.get(task, workspace='default')['stale'])

    def test_the_board_and_lists_read_claims_by_their_leases_too(self):
        task = self.ready()
        with at(0):
            tasks.claim(task, actor=AGENT, workspace='default')
        with at(20 * 60):
            tasks.renew('session-1')
            [listed] = tasks.listing(workspace='default')
            [on_board] = tasks.board(workspace='default')['columns']['in_progress']
        self.assertEqual((listed['stale'], on_board['stale']), (False, False))
        self.assertEqual(on_board['holder']['name'], 'grimforge')

    def test_a_holder_without_progress_stalls_then_goes_stale(self):
        task = self.ready()
        with at(0):
            tasks.claim(task, actor=AGENT, workspace='default')
        for minutes, stalling, stale in ((59, False, False), (61, True, False), (121, False, True)):
            with at(minutes * 60):
                tasks.renew('session-1')
                state = tasks.get(task, workspace='default')
            self.assertEqual((state['stalling'], state['stale']), (stalling, stale), minutes)
        with at(121 * 60):
            tasks.claim(task, actor=CODEX, workspace='default')
        with at(150 * 60):
            tasks.renew('session-2')
            tasks.checkpoint(task, 'Wrote the parser.', branch='task/abc-parser', commit='1a2b3c4',
                             files=['imprint/parser.py'], actor=CODEX, workspace='default')
        with at(200 * 60):
            tasks.renew('session-2')
            state = tasks.get(task, workspace='default')
        self.assertEqual((state['stalling'], state['stale']), (False, False))
        self.assertEqual(state['last_checkpoint']['text'], 'Wrote the parser.')
        self.assertEqual(state['last_checkpoint']['branch'], 'task/abc-parser')

    def test_activity_reported_with_a_renewal_counts_as_progress(self):
        task = self.ready()
        with at(0):
            tasks.claim(task, actor=AGENT, workspace='default')
        with at(100 * 60):
            tasks.renew('session-1', active_at=T0 + 99 * 60)
            self.assertFalse(tasks.get(task, workspace='default')['stalling'])

    def test_only_the_holder_records_checkpoints(self):
        task = self.ready()
        tasks.claim(task, actor=AGENT, workspace='default')
        with self.assertRaisesRegex(PermissionError, 'claim'):
            tasks.checkpoint(task, 'Not mine', actor=CODEX, workspace='default')
        state = tasks.checkpoint(task, f'Used {GITHUB_TOKEN}', actor=AGENT, workspace='default')
        self.assertNotIn(GITHUB_TOKEN, state['last_checkpoint']['text'])

    def test_releasing_puts_work_in_progress_back_to_ready(self):
        task = self.ready()
        tasks.claim(task, actor=AGENT, workspace='default')
        state = tasks.release(task, actor=AGENT, workspace='default')
        self.assertEqual((state['status'], state['holder']), ('ready', None))
        with self.assertRaisesRegex(PermissionError, 'not hold'):
            tasks.release(task, actor=CODEX, workspace='default')

    def test_the_session_that_did_the_work_does_not_mark_it_done(self):
        task = self.ready()
        tasks.claim(task, actor=AGENT, workspace='default')
        with self.assertRaisesRegex(PermissionError, 'Review'):
            tasks.move(task, 'done', actor=AGENT, workspace='default')
        review = tasks.move(task, 'review', actor=AGENT, workspace='default')
        self.assertEqual((review['status'], review['holder'], review['worked_by']), ('review', None, 'session-1'))
        with self.assertRaisesRegex(PermissionError, 'another session'):
            tasks.move(task, 'done', actor=AGENT, workspace='default')
        self.assertEqual(tasks.move(task, 'done', actor=CODEX, workspace='default')['status'], 'done')
        other = self.ready()
        tasks.claim(other, actor=AGENT, workspace='default')
        tasks.move(other, 'review', actor=AGENT, workspace='default')
        self.assertEqual(tasks.move(other, 'done', workspace='default')['status'], 'done')

    def test_tasks_for_the_user_or_already_ended_are_not_claimed(self):
        mine = self.ready(assignees=['user'])
        with self.assertRaisesRegex(PermissionError, 'assigned to the user'):
            tasks.claim(mine, actor=AGENT, workspace='default')
        ended = self.ready()
        tasks.move(ended, 'cancelled', workspace='default')
        with self.assertRaisesRegex(ValueError, 'Cancelled'):
            tasks.claim(ended, actor=AGENT, workspace='default')

    def test_of_two_claims_made_on_two_devices_the_earlier_wins_and_the_other_is_told(self):
        task = self.ready()
        with at(10):
            tasks.claim(task, actor=AGENT, workspace='default')
        [created] = [e for e in tasks.history(task, workspace='default') if e['kind'] == 'created']
        remote = {'id': 'f' * 32, 'task': created['task'], 'kind': 'claimed', 'hlc': f'{int(T0 * 1000) + 5000:013d}.0000',
                  'device': 'device-b', 'actor': 'agent', 'session': 'session-9', 'agent': 'quill', 'client': 'codex',
                  'recorded_at': T0 + 5, 'data': {'token_hash': 'x' * 64}}
        self.assertEqual(tasks.import_events([remote], workspace='default'), 1)
        self.assertEqual(tasks.import_events([remote], workspace='default'), 0)
        state = tasks.get(task, workspace='default')
        self.assertEqual(state['holder']['name'], 'quill')
        lost = tasks.lost_claims('session-1', workspace='default')
        self.assertEqual([(l['task'], l['to']['name']) for l in lost], [(task, 'quill')])



class NameTests(TasksTestCase):
    def test_an_agent_chooses_a_name_of_letters_digits_and_hyphens(self):
        self.assertEqual(tasks.choose_name('s1', 'Grimforge', client='claude-code', workspace='default'), 'Grimforge')
        self.assertEqual(tasks.name_of('s1', workspace='default'), 'Grimforge')
        for bad in ('x', 'a' * 25, 'two words', '-lead', 'emoji🙂'):
            with self.subTest(name=bad), self.assertRaisesRegex(ValueError, '2 to 24'):
                tasks.choose_name('s1', bad, workspace='default')

    def test_a_name_a_live_session_has_is_refused_with_a_free_one_suggested(self):
        tasks.choose_name('s1', 'grimforge', workspace='default')
        with self.assertRaisesRegex(ValueError, r'(?i)GRIMFORGE is taken.*grimforge-2'):
            tasks.choose_name('s2', 'GRIMFORGE', workspace='default')
        self.assertEqual(tasks.choose_name('s1', 'grimforge', workspace='default'), 'grimforge')  # Its own.

    def test_the_name_of_a_session_that_ended_is_free_again(self):
        tasks.choose_name('s1', 'grimforge', pid=dead_pid(), workspace='default')
        self.assertEqual(tasks.choose_name('s2', 'grimforge', workspace='default'), 'grimforge')

    def test_a_claim_holders_name_from_another_device_is_taken(self):
        task = make(acceptance=['x'], status='ready')['id']
        [created] = tasks.history(task, workspace='default')
        tasks.import_events([{'id': 'f' * 32, 'task': created['task'], 'kind': 'claimed', 'hlc': created['hlc'],
                              'device': 'device-b', 'actor': 'agent', 'session': 'far', 'agent': 'quill',
                              'client': 'codex', 'recorded_at': time_now(), 'data': {'token_hash': 'x' * 64}}],
                            workspace='default')
        with self.assertRaisesRegex(ValueError, '(?i)quill is taken'):
            tasks.choose_name('s1', 'Quill', workspace='default')

    def test_an_agent_without_a_name_gets_a_fallback_once(self):
        name = tasks.ensure_name('s1', client='codex', workspace='default')
        self.assertRegex(name, r'^codex-[0-9a-hjkmnp-tv-z]{4}$')
        self.assertEqual(tasks.ensure_name('s1', client='codex', workspace='default'), name)
        self.assertEqual(tasks.ensure_name('s2', client=None, workspace='default')[:6], 'agent-')
        tasks.choose_name('s1', 'quill', workspace='default')
        self.assertEqual(tasks.ensure_name('s1', client='codex', workspace='default'), 'quill')

    def test_events_keep_the_name_the_session_had_when_it_wrote_them(self):
        task = make(acceptance=['x'], status='ready')['id']
        tasks.choose_name('s1', 'grimforge', workspace='default')
        tasks.claim(task, actor=tasks.agent('s1', name=tasks.name_of('s1', workspace='default')), workspace='default')
        tasks.choose_name('s1', 'ironquill', workspace='default')
        tasks.checkpoint(task, 'Half way.', actor=tasks.agent('s1', name='ironquill'), workspace='default')
        names = [e['agent'] for e in tasks.history(task, workspace='default') if e['actor'] == 'agent']
        self.assertEqual(names, ['grimforge', 'ironquill'])

    def test_taking_a_claim_back_with_its_token_keeps_the_holders_name(self):
        task = make(acceptance=['x'], status='ready')['id']
        tasks.choose_name('s1', 'grimforge', workspace='default')
        token = tasks.claim(task, actor=tasks.agent('s1', name='grimforge'), workspace='default')['token']
        tasks.claim(task, actor=tasks.agent('s1b'), token=token, workspace='default')
        self.assertEqual(tasks.name_of('s1b', workspace='default'), 'grimforge')



class NextTests(TasksTestCase):
    def ready(self, title, **fields):
        fields.setdefault('project', 'shop')
        return make(title, acceptance=['x'], status='ready', **fields)['id']

    def pick(self, **given):
        given.setdefault('project', 'shop')
        given.setdefault('client', 'codex')
        given.setdefault('device', 'bedrock')
        found = tasks.next_task(workspace='default', **given)
        return found['id'] if found else None

    def test_the_next_task_is_ready_unblocked_unclaimed_and_in_the_agents_project(self):
        make('Backlog only', project='shop')
        self.ready('Other project', project='blog')
        blocker = self.ready('Blocker first', priority='P3')
        self.ready('Waits on the blocker', priority='P0', blockers=[blocker])
        marked = self.ready('Marked blocked', priority='P0')
        tasks.block(marked, 'No access', workspace='default')
        self.ready('For the user', priority='P0', assignees=['user'])
        self.ready('For Claude Code', priority='P0', assignees=['claude-code'])
        held = self.ready('Held', priority='P0')
        tasks.claim(held, actor=AGENT, workspace='default')
        want = self.ready('Take me', priority='P1')
        self.assertEqual(self.pick(), want)
        self.assertIsNone(self.pick(project='nowhere'))

    def test_assignees_match_the_agents_client_device_or_both(self):
        for assignee in ('codex', '@bedrock', 'codex@bedrock'):
            with self.subTest(assignee=assignee):
                task = self.ready(f'For {assignee}', project=assignee, assignees=[assignee])
                self.assertEqual(self.pick(project=assignee), task)
                self.assertIsNone(self.pick(project=assignee, client='claude-code', device='studio'))

    def test_priority_then_position_then_age_decide(self):
        old = self.ready('Old P1', priority='P1')
        new = self.ready('New P1', priority='P1')
        self.assertEqual(self.pick(), old)
        tasks.move(new, 'ready', position=1, workspace='default')
        self.assertEqual(self.pick(), new)
        top = self.ready('P0', priority='P0')
        self.assertEqual(self.pick(), top)

    def test_a_stale_task_is_offered_for_takeover(self):
        task = self.ready('Abandoned')
        tasks.claim(task, actor=AGENT, pid=dead_pid(), workspace='default')
        self.assertEqual(self.pick(), task)

    def test_autonomy_limits_what_is_taken_unasked(self):
        ready = self.ready('Ready one')
        stale = self.ready('Stale one', priority='P3')
        tasks.claim(stale, actor=AGENT, pid=dead_pid(), workspace='default')
        self.assertEqual(tasks.autonomy(workspace='default'), 'ready')
        tasks.set_autonomy('stale', workspace='default')
        self.assertEqual(self.pick(), stale)
        tasks.set_autonomy('off', workspace='default')
        self.assertIsNone(self.pick())
        self.assertEqual(tasks.autonomy(workspace='work'), 'ready')
        with self.assertRaisesRegex(ValueError, 'off, stale or ready'):
            tasks.set_autonomy('always', workspace='default')
        del ready

    def test_a_features_frontier_is_its_open_unblocked_unclaimed_tasks_in_order(self):
        feature = make('Checkout', project='shop')['id']
        first = self.ready('First', feature=feature, priority='P1')
        second = self.ready('Second', feature=feature, priority='P2', assignees=['claude-code'])
        self.ready('Third', feature=feature, blockers=[first])
        self.ready('Mine', feature=feature, assignees=['user'])
        self.ready('Elsewhere')
        tasks.set_autonomy('off', workspace='default')
        self.assertEqual([t['id'] for t in tasks.frontier(feature, workspace='default')], [first, second])

    def test_a_handoff_gives_the_next_agent_where_the_work_stands(self):
        task = self.ready('Parser')
        tasks.choose_name('conversation:5c1d2e3f-aaaa-bbbb-cccc-111122223333', 'grimforge', workspace='default')
        holder = tasks.agent('conversation:5c1d2e3f-aaaa-bbbb-cccc-111122223333', name='grimforge',
                             client='claude-code')
        tasks.claim(task, actor=holder, workspace='default')
        tasks.checkpoint(task, 'Tokenizer done.', branch='task/x-parser', commit='aaa111', files=['a.py'],
                         actor=holder, workspace='default')
        tasks.checkpoint(task, 'Grammar half done.', next_step='Finish the grammar.', branch='task/x-parser',
                         commit='bbb222', files=['b.py', 'a.py'], actor=holder, workspace='default')
        tasks.renew('conversation:5c1d2e3f-aaaa-bbbb-cccc-111122223333', pid=dead_pid())
        handoff = tasks.handoff(task, workspace='default')
        self.assertTrue(handoff['task']['stale'])
        self.assertEqual(handoff['previous_holder']['name'], 'grimforge')
        self.assertEqual(handoff['last_checkpoint']['text'], 'Grammar half done.')
        self.assertEqual((handoff['branch'], handoff['commit']), ('task/x-parser', 'bbb222'))
        self.assertEqual(handoff['files'], ['b.py', 'a.py'])
        self.assertEqual([c['text'] for c in handoff['checkpoints']], ['Tokenizer done.', 'Grammar half done.'])
        self.assertEqual(handoff['conversation'], 'conversation/5c1d2e3f')



class CommentTests(TasksTestCase):
    def inbox(self, session, name=None, client=None, device='bedrock', mark=True):
        return [c['text'] for c in tasks.inbox(session, name=name, client=client, device=device, mark=mark,
                                               workspace='default')]

    def test_comments_are_kept_on_the_task_with_who_wrote_them_and_to_whom(self):
        task = make()['id']
        state = tasks.comment(task, f'@quill please rotate {GITHUB_TOKEN}', to=['user'], actor=AGENT,
                              workspace='default')
        [comment] = state['comments']
        self.assertEqual(comment['by']['name'], 'grimforge')
        self.assertEqual(comment['to'], ['user', 'quill'])
        self.assertNotIn(GITHUB_TOKEN, comment['text'])
        with self.assertRaisesRegex(ValueError, 'comment'):
            tasks.comment(task, '  ', actor=AGENT, workspace='default')

    def test_comments_reach_the_sessions_they_are_addressed_to_once(self):
        task = make()['id']
        tasks.comment(task, 'For the Codex agents', to=['codex'], workspace='default')
        tasks.comment(task, 'For anyone on bedrock', to=['@bedrock'], workspace='default')
        tasks.comment(task, 'For Codex on studio', to=['codex@studio'], workspace='default')
        tasks.comment(task, 'Hey @Quill, look', workspace='default')
        self.assertEqual(self.inbox('s2', name='quill', client='codex'),
                         ['For the Codex agents', 'For anyone on bedrock', 'Hey @Quill, look'])
        self.assertEqual(self.inbox('s2', name='quill', client='codex'), [])
        self.assertEqual(self.inbox('s3', client='codex', device='studio'), ['For the Codex agents', 'For Codex on studio'])

    def test_a_comment_to_the_holder_reaches_whoever_holds_the_task_then(self):
        task = make(acceptance=['x'], status='ready')['id']
        tasks.comment(task, 'Whoever takes this: mind the cache', to=['holder'], workspace='default')
        tasks.comment(task, 'No address goes to the holder too', workspace='default')
        self.assertEqual(self.inbox('s1', name='grimforge', mark=False), [])
        tasks.claim(task, actor=tasks.agent('s1', name='grimforge'), pid=dead_pid(), workspace='default')
        tasks.claim(task, actor=tasks.agent('s2', name='quill'), workspace='default')
        self.assertEqual(self.inbox('s2', name='quill'),
                         ['Whoever takes this: mind the cache', 'No address goes to the holder too'])

    def test_an_agent_is_not_told_of_its_own_comments(self):
        task = make()['id']
        tasks.comment(task, 'Note to self and @grimforge', actor=AGENT, workspace='default')
        self.assertEqual(self.inbox('session-1', name='grimforge'), [])

    def test_the_users_inbox_holds_comments_agents_addressed_to_the_user(self):
        task = make()['id']
        tasks.comment(task, 'Which API key should I use?', to=['user'], actor=AGENT, workspace='default')
        tasks.comment(task, 'Not for you', to=['codex'], actor=AGENT, workspace='default')
        [comment] = tasks.user_inbox(workspace='default')
        self.assertEqual((comment['text'], comment['task']), ('Which API key should I use?', task))
        self.assertEqual(tasks.user_inbox(workspace='default'), [])

    def test_names_that_are_clients_or_addresses_are_refused(self):
        for reserved in ('codex', 'Claude-Code', 'user', 'holder'):
            with self.subTest(name=reserved), self.assertRaisesRegex(ValueError, 'reserved'):
                tasks.choose_name('s1', reserved, workspace='default')


if __name__ == '__main__':
    unittest.main()
