"""The Board (ADRs 0208-0212): Tasks per workspace, kept as a log of events
whose current state is computed from them, read and changed through the
tasks module's public functions."""
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import secrets
import time

from imprint import config, tasks

GITHUB_TOKEN = 'ghp_' + 'A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8'


class TasksTestCase(unittest.TestCase):
    """A data directory of its own, one device, and two workspaces."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data = Path(directory.name)
        for patcher in (patch.object(config, 'get_data_dir', return_value=self.data),
                        patch.object(tasks.actors, '_device', return_value='device-a'),
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
        with patch.object(secrets, 'choice', side_effect=lambda alphabet: next(drawn)):
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
        with patch.object(time, 'time', return_value=1_800_000_000.0):
            a = tasks.create('a', project='p', workspace='default')
            b = tasks.create('b', project='p', workspace='default')
        with patch.object(time, 'time', return_value=1_700_000_000.0):
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
        with patch.object(time, 'time', return_value=1_800_000_000.0):
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
        with patch.object(time, 'time', return_value=1_800_000_000.0):
            tasks.move(made['id'], 'done', workspace='default')
        with patch.object(time, 'time', return_value=1_800_000_000.0 + 13 * 86400):
            self.assertEqual(len(tasks.board(workspace='default')['columns']['done']), 1)
        with patch.object(time, 'time', return_value=1_800_000_000.0 + 15 * 86400):
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
    return time.time()


def at(seconds):
    return patch.object(time, 'time', return_value=T0 + seconds)


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

    def test_a_worker_that_hands_in_anothers_claim_does_not_mark_it_done(self):
        # A Dispatched worker that moves its Task to Review without taking the Dispatcher's claim over.
        task = self.ready()
        dispatcher = tasks.agent('dispatch:run1', client='claude-code')
        tasks.claim(task, actor=dispatcher, workspace='default')
        review = tasks.move(task, 'review', actor=AGENT, workspace='default')
        self.assertEqual((review['worked_by'], review['submitted_by']), ('dispatch:run1', 'session-1'))
        with self.assertRaisesRegex(PermissionError, 'another session'):
            tasks.move(task, 'done', actor=AGENT, workspace='default')
        self.assertEqual(tasks.move(task, 'done', actor=CODEX, workspace='default')['status'], 'done')

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

    def test_only_standalone_tasks_are_taken_unasked(self):
        feature = self.ready('Checkout', priority='P0')
        child = self.ready('Cart page', feature=feature, priority='P0')
        stale_child = self.ready('Payment page', feature=feature, priority='P0')
        tasks.claim(stale_child, actor=AGENT, pid=dead_pid(), workspace='default')
        self.assertIsNone(self.pick())
        loose = self.ready('Fix the footer', priority='P3')
        self.assertEqual(self.pick(), loose)
        self.assertEqual([t['id'] for t in tasks.frontier(feature, workspace='default')], [child, stale_child])
        tasks.delete(feature, workspace='default')
        self.assertIn(self.pick(), (child, stale_child))

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



class ReviewFindingTests(TasksTestCase):
    """Defects the spec review of phase 1 found."""

    def ready(self, **fields):
        return make(acceptance=['x'], status='ready', **fields)['id']

    def test_releasing_cannot_skip_review_or_the_ready_rule(self):
        task = self.ready()
        tasks.claim(task, actor=AGENT, workspace='default')
        for status in ('done', 'review', 'cancelled', 'in_progress'):
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, 'Backlog or Ready'):
                tasks.release(task, status=status, actor=AGENT, workspace='default')
        bare = make()['id']
        tasks.claim(bare, actor=AGENT, workspace='default')
        with self.assertRaisesRegex(ValueError, 'acceptance criterion'):
            tasks.release(bare, status='ready', actor=AGENT, workspace='default')
        self.assertEqual(tasks.release(bare, actor=AGENT, workspace='default')['status'], 'backlog')

    def test_an_address_with_an_at_sign_reaches_a_name_or_a_client_too(self):
        task = make()['id']
        tasks.comment(task, 'To the name', to=['@grimforge'], workspace='default')
        tasks.comment(task, 'To the client', to=['@codex'], workspace='default')
        got = [c['text'] for c in tasks.inbox('s1', name='grimforge', client='codex', device='studio',
                                              workspace='default')]
        self.assertEqual(got, ['To the name', 'To the client'])

    def test_next_never_offers_a_stale_task_that_was_moved_back_to_backlog(self):
        task = self.ready(project='shop')
        tasks.claim(task, actor=AGENT, pid=dead_pid(), workspace='default')
        tasks.move(task, 'backlog', workspace='default')
        self.assertIsNone(tasks.next_task(project='shop', client='codex', device='bedrock', workspace='default'))

    def test_a_resumed_sessions_name_stays_taken_once_its_server_renews(self):
        tasks.choose_name('conversation:c1', 'grimforge', pid=dead_pid(), workspace='default')
        tasks.renew('conversation:c1')  # The resumed server, alive in this process.
        with self.assertRaisesRegex(ValueError, 'taken'):
            tasks.choose_name('s2', 'grimforge', workspace='default')

    def test_tasks_are_filtered_by_holder(self):
        held = self.ready()
        self.ready()
        tasks.claim(held, actor=AGENT, workspace='default')
        self.assertEqual([t['id'] for t in tasks.listing(holder='Grimforge', workspace='default')], [held])
        self.assertEqual([t['id'] for t in tasks.listing(holder='session-1', workspace='default')], [held])
        self.assertEqual(tasks.listing(holder='quill', workspace='default'), [])

    def test_the_users_inbox_can_be_read_without_marking_it(self):
        task = make()['id']
        tasks.comment(task, 'For you', to=['user'], actor=AGENT, workspace='default')
        self.assertEqual(len(tasks.user_inbox(mark=False, workspace='default')), 1)
        self.assertEqual(len(tasks.user_inbox(mark=False, workspace='default')), 1)



class ProgressTests(TasksTestCase):
    """How far a project or Feature is, and how long is left at the recent pace."""

    def done(self, title, finished_at, **fields):
        task = make(title, acceptance=['x'], **fields)['id']
        with patch.object(time, 'time', return_value=finished_at):
            tasks.move(task, 'done', workspace='default')
        return task

    def test_progress_counts_tasks_by_status_and_leaves_cancelled_and_features_out(self):
        feature = make('Checkout', project='shop')['id']
        self.done('Total line', time.time() - 3600, project='shop', feature=feature)
        make('Coupon field', project='shop', feature=feature, acceptance=['x'], status='ready')
        working = make('Tax rounding', project='shop', feature=feature, acceptance=['x'], status='ready')['id']
        tasks.claim(working, actor=AGENT, workspace='default')
        stuck = make('Payment SDK', project='shop', acceptance=['x'], status='ready')['id']
        tasks.block(stuck, 'Waiting for the vendor', workspace='default')
        dropped = make('Old banner', project='shop', feature=feature)['id']
        tasks.move(dropped, 'cancelled', workspace='default')
        make('Elsewhere', project='blog')
        report = tasks.progress(workspace='default')
        shop = next(p for p in report['projects'] if p['name'] == 'shop')
        self.assertEqual((shop['total'], shop['done'], shop['cancelled'], shop['blocked']), (4, 1, 1, 1))
        self.assertEqual(shop['counts'], {'backlog': 0, 'ready': 2, 'in_progress': 1, 'review': 0, 'done': 1})
        [checkout] = report['features']
        self.assertEqual((checkout['id'], checkout['title'], checkout['project']), (feature, 'Checkout', 'shop'))
        self.assertEqual((checkout['total'], checkout['done'], checkout['cancelled']), (3, 1, 1))
        self.assertEqual([p['name'] for p in tasks.progress(project='blog', workspace='default')['projects']], ['blog'])
        self.assertEqual(tasks.progress(feature=feature, workspace='default')['projects'], [])

    def test_the_estimate_follows_the_pace_of_the_last_14_days(self):
        now = time.time()
        for n in range(4):  # Four finished in the last 14 days: 4/14 a day.
            self.done(f'Finished {n}', now - (n + 1) * 86400, project='shop')
        self.done('Long ago', now - 40 * 86400, project='shop')
        for n in range(6):
            make(f'Open {n}', project='shop')
        with patch.object(time, 'time', return_value=now):
            [shop] = tasks.progress(workspace='default')['projects']
        self.assertEqual((shop['total'], shop['done'], shop['finished_recently']), (11, 5, 4))
        self.assertAlmostEqual(shop['pace'], 4 / 14)
        self.assertAlmostEqual(shop['days_left'], 6 / (4 / 14))

    def test_no_estimate_without_enough_finished_work_and_none_needed_when_all_is_done(self):
        now = time.time()
        self.done('One', now - 86400, project='shop')
        self.done('Two', now - 2 * 86400, project='shop')
        make('Open', project='shop')
        [shop] = tasks.progress(workspace='default')['projects']
        self.assertIsNone(shop['days_left'])
        self.assertEqual(shop['finished_recently'], 2)
        self.done('Three', now - 3 * 86400, project='blog')
        [blog] = [p for p in tasks.progress(workspace='default')['projects'] if p['name'] == 'blog']
        self.assertEqual(blog['days_left'], 0)

    def test_a_features_card_carries_its_progress(self):
        feature = make('Checkout', project='shop')['id']
        self.done('Done one', time.time(), project='shop', feature=feature)
        make('Open one', project='shop', feature=feature)
        self.assertEqual(tasks.get(feature, workspace='default')['progress'], {'done': 1, 'working': 0, 'total': 2})
        reviewing = make('In review', project='shop', feature=feature)
        tasks.move(reviewing['id'], 'review', workspace='default')
        self.assertEqual(tasks.get(feature, workspace='default')['progress'], {'done': 1, 'working': 1, 'total': 3})
        self.assertIsNone(make('Plain', project='shop').get('progress'))


class ProjectNameTests(TasksTestCase):
    """A Task's project is a name, never a folder (ADR 0226)."""

    def test_a_folder_given_as_the_project_becomes_the_name_of_the_project_it_holds(self):
        made = make('Send mail', project='/home/alex/code/gdpr-web-compliance')
        self.assertEqual(made['project'], 'gdpr-web-compliance')
        for folder in ('C:\\Users\\alex\\code\\imprint', '\\\\wsl.localhost\\Ubuntu\\home\\alex\\code\\imprint',
                       '/Users/alex/code/node/imprint'):
            with self.subTest(folder=folder):
                self.assertEqual(tasks.edit(made['id'], project=folder, workspace='default')['project'], 'imprint')

    def test_a_name_with_slashes_stays_and_a_folder_holding_no_project_is_refused(self):
        self.assertEqual(make(project='github.com/owner/repo')['project'], 'github.com/owner/repo')
        with tempfile.TemporaryDirectory() as plain:  # no manifest, and not a code folder in a home
            with self.assertRaisesRegex(ValueError, 'is a folder, not a project'):
                make(project=plain)
        self.assertEqual(len(tasks.listing(workspace='default')), 1)

    def test_filtering_by_a_folder_finds_the_project_s_tasks(self):
        made = make(project='gdpr-web-compliance')
        found = tasks.listing(project='/home/alex/code/gdpr-web-compliance', workspace='default')
        self.assertEqual([t['id'] for t in found], [made['id']])


class LinkTests(TasksTestCase):
    def test_a_task_lists_the_tasks_waiting_on_it(self):
        first = make('Folder')
        second = make('Run', blockers=[first['id']])
        third = make('Stop', blockers=[first['id'], second['id']])
        self.assertEqual(tasks.get(first['id'], workspace='default')['blocking'], sorted([second['id'], third['id']]))
        self.assertEqual(tasks.get(third['id'], workspace='default')['blocking'], [])

    def test_related_reads_every_linked_task_once_and_never_the_task_itself(self):
        feature = make('Dispatcher', project='imprint')
        first = make('Folder', feature=feature['id'])
        second = make('Run', feature=feature['id'], blockers=[first['id']], priority='P1')
        later = make('Docs', blockers=[second['id']])
        tasks.block(first['id'], 'Waiting for the key', workspace='default')
        found = tasks.related(second['id'], workspace='default')
        self.assertEqual(set(found), {feature['id'], first['id'], later['id']})
        self.assertEqual(found[feature['id']], {'title': 'Dispatcher', 'status': 'backlog', 'priority': 'P2',
                                                'project': 'imprint', 'stale': False, 'blocked': False})
        self.assertTrue(found[first['id']]['blocked'])
        self.assertEqual(set(tasks.related(feature['id'], workspace='default')), {first['id'], second['id']})
        self.assertEqual(tasks.related(make('Alone')['id'], workspace='default'), {})



class FeatureStatusTests(TasksTestCase):
    """A Feature groups Tasks, and its status follows theirs (ADR 0242)."""

    def feature(self, title='Checkout', **fields):
        return make(title, kind='feature', **fields)['id']

    def task(self, feature, title='Step', status='backlog'):
        return make(title, feature=feature, acceptance=['x'], status=status)['id']

    def status(self, task):
        return tasks.get(task, workspace='default')['status']

    def test_a_feature_made_as_one_is_known_before_its_tasks_and_waits_in_backlog(self):
        made = make('Checkout', kind='feature', acceptance=['Every Task is Done'])
        self.assertEqual((made['kind'], made['status'], made['tasks']), ('feature', 'backlog', []))
        self.assertEqual(made['progress'], {'done': 0, 'working': 0, 'total': 0})
        self.assertEqual(make('Plain')['kind'], 'task')
        with self.assertRaisesRegex(ValueError, "status follows its Tasks"):
            make('Ready Feature', kind='feature', acceptance=['x'], status='ready')
        with self.assertRaisesRegex(ValueError, 'kind'):
            make('Odd', kind='epic')

    def test_a_features_status_follows_its_tasks_and_a_new_task_reopens_it(self):
        feature = self.feature()
        first = self.task(feature, 'First')
        self.assertEqual(self.status(feature), 'backlog')
        tasks.move(first, 'ready', workspace='default')
        self.assertEqual(self.status(feature), 'ready')
        second = self.task(feature, 'Second', status='ready')
        tasks.claim(first, actor=AGENT, workspace='default')
        self.assertEqual(self.status(feature), 'in_progress')
        tasks.move(first, 'review', workspace='default', actor=AGENT)
        self.assertEqual(self.status(feature), 'in_progress')
        with at(100):
            tasks.move(first, 'done', workspace='default')
        self.assertEqual(self.status(feature), 'in_progress')
        with at(200):
            tasks.move(second, 'cancelled', workspace='default')
        got = tasks.get(feature, workspace='default')
        self.assertEqual((got['status'], got['ended_at']), ('done', T0 + 100))
        self.task(feature, 'Follow-up')
        reopened = tasks.get(feature, workspace='default')
        self.assertEqual((reopened['status'], reopened['ended_at']), ('in_progress', None))

    def test_a_task_with_tasks_linked_to_it_is_a_feature_and_its_stored_status_no_longer_counts(self):
        earlier = make('Made the old way', acceptance=['x'], status='ready')['id']
        self.assertEqual(tasks.get(earlier, workspace='default')['kind'], 'task')
        self.task(earlier, 'Its first Task')
        got = tasks.get(earlier, workspace='default')
        self.assertEqual((got['kind'], got['status']), ('feature', 'backlog'))

    def test_moving_a_feature_is_refused_but_cancelling_and_reopening_it_are_not(self):
        feature = self.feature()
        step = self.task(feature, status='ready')
        for status in ('backlog', 'ready', 'in_progress', 'review', 'done'):
            with self.assertRaisesRegex(ValueError, r'is a Feature: its status follows its Tasks \(now Ready\)'):
                tasks.move(feature, status, workspace='default')
        tasks.move(feature, 'cancelled', workspace='default')
        tasks.claim(step, actor=AGENT, workspace='default')
        self.assertEqual(self.status(feature), 'cancelled')  # Kept by hand, whatever its Tasks do.
        tasks.move(feature, 'backlog', workspace='default')  # Reopened: its Tasks decide again.
        self.assertEqual(self.status(feature), 'in_progress')

    def test_a_status_written_for_a_feature_elsewhere_is_ignored(self):
        """An older release moves Features by hand: its move syncs as an event, and the status stays computed."""
        feature = self.feature()
        self.task(feature)
        with tasks.store._writing('default') as conn:
            uid = tasks.store._uid(conn, feature)
            tasks.store._append(conn, uid, 'moved', {'status': 'done'}, tasks.actors._actor(None))
            tasks.store._project(conn, uid)
        self.assertEqual(self.status(feature), 'backlog')

    def test_agents_neither_claim_nor_take_a_feature_even_before_its_tasks_exist(self):
        # T-e5zyz: a Ready Task that becomes a Feature before any Task is linked to it.
        planned = make('Checkout', project='shop', acceptance=['x'], status='ready')['id']
        self.assertEqual(tasks.next_task(project='shop', workspace='default')['id'], planned)
        tasks.edit(planned, kind='feature', workspace='default')
        self.assertIsNone(tasks.next_task(project='shop', workspace='default'))
        self.assertEqual(tasks.takeable(workspace='default'), [])
        with self.assertRaisesRegex(ValueError, 'is a Feature: agents claim its Tasks'):
            tasks.claim(planned, actor=AGENT, workspace='default')
        step = make('Step', project='shop', feature=planned, acceptance=['x'], status='ready')['id']
        self.assertEqual(self.status(planned), 'ready')
        self.assertIsNone(tasks.next_task(project='shop', workspace='default'))
        self.assertEqual([t['id'] for t in tasks.frontier(planned, workspace='default')], [step])

    def test_a_task_held_by_a_claim_does_not_become_a_feature(self):
        held = make('Being worked on', acceptance=['x'], status='ready')['id']
        tasks.claim(held, actor=AGENT, workspace='default')
        other = make('Split off')['id']
        with self.assertRaisesRegex(ValueError, 'held by grimforge, so it cannot become a Feature'):
            tasks.link(other, feature=held, workspace='default')
        with self.assertRaisesRegex(ValueError, 'held by grimforge, so it cannot become a Feature'):
            make('Split off too', feature=held)
        with self.assertRaisesRegex(ValueError, 'held by grimforge, so it cannot become a Feature'):
            tasks.edit(held, kind='feature', workspace='default')
        tasks.release(held, workspace='default', actor=AGENT)
        self.assertEqual(tasks.link(other, feature=held, workspace='default')['feature'], held)

    def test_a_feature_becomes_a_task_again_only_without_tasks_and_features_do_not_nest(self):
        feature = self.feature()
        step = self.task(feature)
        with self.assertRaisesRegex(ValueError, 'has Tasks of its own'):
            tasks.edit(feature, kind='task', workspace='default')
        with self.assertRaisesRegex(ValueError, 'Features do not nest'):
            make('Sub-feature', kind='feature', feature=feature)
        with self.assertRaisesRegex(ValueError, 'Features do not nest'):
            tasks.link(self.feature('Search'), feature=feature, workspace='default')
        with self.assertRaisesRegex(ValueError, 'Features do not nest'):
            tasks.edit(step, kind='feature', workspace='default')
        empty = self.feature('Nothing yet')
        self.assertEqual(tasks.edit(empty, kind='task', workspace='default')['kind'], 'task')

    def test_the_board_lays_out_tasks_by_status_and_features_beside_them(self):
        feature = self.feature()
        step = self.task(feature, status='ready')
        empty = self.feature('Nothing yet')
        board = tasks.board(workspace='default')
        self.assertEqual([t['id'] for t in board['columns']['ready']], [step])
        self.assertEqual(board['columns']['backlog'], [])
        self.assertEqual(sorted(f['id'] for f in board['features']), sorted([feature, empty]))

    def test_progress_counts_a_feature_without_tasks_as_a_feature(self):
        empty = self.feature('Nothing yet', project='shop')
        make('Plain', project='shop')
        report = tasks.progress(workspace='default')
        [shop] = report['projects']
        self.assertEqual(shop['total'], 1)
        self.assertEqual([(f['id'], f['total']) for f in report['features']], [(empty, 0)])


    def test_each_feature_lists_its_tasks_in_working_order_with_its_progress(self):
        feature = self.feature(project='shop')
        last = make('Ship it', project='shop', feature=feature, priority='P0')['id']
        first = make('Lay the ground', project='shop', feature=feature, priority='P3')['id']
        middle = make('Build on it', project='shop', feature=feature, priority='P1', blockers=[first])['id']
        tasks.link(last, add_blockers=[middle], workspace='default')
        loose = make('Unordered', project='shop', feature=feature, priority='P2')['id']
        self.feature('Elsewhere', project='blog')
        [entry] = tasks.features(project='shop', workspace='default')
        self.assertEqual(entry['feature']['id'], feature)
        self.assertEqual([t['id'] for t in entry['tasks']], [loose, first, middle, last])
        self.assertEqual((entry['summary']['total'], entry['summary']['done']), (4, 0))
        self.assertEqual(len(tasks.features(workspace='default')), 2)

    def test_a_feature_that_ended_over_14_days_ago_is_listed_only_when_asked(self):
        feature = self.feature()
        step = self.task(feature, status='ready')
        with at(0):
            tasks.move(step, 'done', workspace='default')
        with at(15 * 86400):
            self.assertEqual(tasks.features(workspace='default'), [])
            [entry] = tasks.features(archived=True, workspace='default')
            self.assertEqual((entry['feature']['status'], entry['summary']['done']), ('done', 1))

if __name__ == '__main__':
    unittest.main()
