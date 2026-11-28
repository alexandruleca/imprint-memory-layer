"""The dashboard's /api/tasks (ADR 0208): the Board as the user reads and
changes it on the Agents page."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from imprint import config, tasks

AGENT = tasks.agent('conversation:c1', name='grimforge', client='claude-code')


class TasksApiTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data = Path(directory.name)
        for patcher in (patch.object(config, 'get_data_dir', return_value=self.data),
                        patch.object(tasks.actors, '_device', return_value='device-a'),
                        patch.dict(os.environ, {'IMPRINT_WORKSPACE': ''})):
            patcher.start()
            self.addCleanup(patcher.stop)
        from imprint.api import app
        self.api = TestClient(app, base_url='http://127.0.0.1:8420', client=('127.0.0.1', 1234))

    def create(self, title='Fix the total', **fields):
        body = {'title': title, 'project': 'shop', **fields}
        response = self.api.post('/api/tasks', json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['task']

    def test_an_empty_board_has_every_column_and_makes_no_store(self):
        board = self.api.get('/api/tasks/board').json()
        self.assertEqual(list(board['columns']), list(tasks.STATUSES))
        self.assertEqual(board['workspace'], 'default')
        self.assertEqual(board['total'], 0)
        self.assertFalse((self.data / 'tasks.sqlite3').exists())

    def test_the_user_creates_a_task_and_sees_it_with_the_filters_it_offers(self):
        feature = self.create('Checkout')
        made = self.create(tags=['cart'], assignees=['codex'], priority='P1', acceptance=['Tax included'],
                           status='ready', feature=feature['id'])
        self.assertEqual(made['created_by']['kind'], 'user')
        board = self.api.get('/api/tasks/board').json()
        self.assertEqual([t['id'] for t in board['columns']['ready']], [made['id']])
        options = board['options']
        self.assertEqual(options['projects'], ['shop'])
        self.assertEqual(options['tags'], ['cart'])
        self.assertEqual(options['assignees'], ['codex'])
        self.assertEqual(options['features'], [{'id': feature['id'], 'title': 'Checkout'}])
        self.assertEqual(board['autonomy'], 'ready')

    def test_mistakes_are_refused_with_the_reason(self):
        response = self.api.post('/api/tasks', json={'title': 'No project'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('project', response.json()['error'])
        response = self.api.post('/api/tasks', json={'title': 'x', 'project': 'shop', 'status': 'ready'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('acceptance criterion', response.json()['error'])
        self.assertEqual(self.api.get('/api/tasks/T-zzzzz').status_code, 404)
        self.assertEqual(self.api.post('/api/tasks/T-zzzzz/move', json={'status': 'ready'}).status_code, 404)
        self.assertEqual(self.api.post('/api/tasks', json=['not', 'an', 'object']).status_code, 400)

    def test_moving_follows_the_boards_rules(self):
        made = self.create()
        refused = self.api.post(f"/api/tasks/{made['id']}/move", json={'status': 'Ready'})
        self.assertEqual(refused.status_code, 400)
        self.assertIn('acceptance criterion', refused.json()['error'])
        self.api.post(f"/api/tasks/{made['id']}/edit", json={'acceptance': ['Tax included']})
        moved = self.api.post(f"/api/tasks/{made['id']}/move", json={'status': 'ready', 'position': 2.5}).json()['task']
        self.assertEqual((moved['status'], moved['position']), ('ready', 2.5))

    def test_a_tasks_details_carry_its_history_and_handoff(self):
        made = self.create(acceptance=['x'], status='ready')
        tasks.claim(made['id'], actor=AGENT, workspace='default')
        tasks.checkpoint(made['id'], 'Half way.', branch='task/x', actor=AGENT, workspace='default')
        details = self.api.get(f"/api/tasks/{made['id']}").json()
        self.assertEqual(details['task']['holder']['name'], 'grimforge')
        self.assertEqual([e['kind'] for e in details['history']], ['created', 'claimed', 'checkpoint'])
        self.assertEqual(details['handoff']['branch'], 'task/x')
        self.assertEqual(details['related'], {})
        later = self.create('Ship it', blockers=[made['id']])
        details = self.api.get(f"/api/tasks/{made['id']}").json()
        self.assertEqual(details['task']['blocking'], [later['id']])
        self.assertEqual(details['related'][later['id']]['title'], 'Ship it')

    def test_the_user_shapes_comments_and_ends_tasks(self):
        made = self.create()
        feature = self.create('Checkout')
        tid = made['id']
        for path, body in ((f'/api/tasks/{tid}/edit', {'title': 'Fix the tax total', 'priority': 'P0'}),
                           (f'/api/tasks/{tid}/append', {'text': 'Resolved: round half up.'}),
                           (f'/api/tasks/{tid}/tags', {'add': ['cart']}),
                           (f'/api/tasks/{tid}/assignees', {'add': ['codex'], 'remove': []}),
                           (f'/api/tasks/{tid}/link', {'feature': feature['id']}),
                           (f'/api/tasks/{tid}/block', {'reason': 'Waiting for prices'}),
                           (f'/api/tasks/{tid}/comment', {'text': 'Use the new table', 'to': ['codex']})):
            response = self.api.post(path, json=body)
            self.assertEqual(response.status_code, 200, (path, response.text))
        got = self.api.get(f'/api/tasks/{tid}').json()['task']
        self.assertEqual((got['title'], got['priority'], got['tags'], got['assignees'], got['feature']),
                         ('Fix the tax total', 'P0', ['cart'], ['codex'], feature['id']))
        self.assertEqual(got['blocked']['reason'], 'Waiting for prices')
        self.assertEqual(got['comments'][-1]['text'], 'Use the new table')
        self.assertIsNone(self.api.post(f'/api/tasks/{tid}/unblock').json()['task']['blocked'])
        self.assertEqual(self.api.post(f'/api/tasks/{tid}/release').status_code, 400)
        self.assertEqual(self.api.delete(f'/api/tasks/{tid}').status_code, 200)
        gone = self.api.get(f'/api/tasks/{tid}')
        self.assertEqual(gone.status_code, 404)
        self.assertIn('was deleted', gone.json()['error'])

    def test_the_user_ends_an_agents_claim(self):
        made = self.create(acceptance=['x'], status='ready')
        tasks.claim(made['id'], actor=AGENT, workspace='default')
        released = self.api.post(f"/api/tasks/{made['id']}/release").json()['task']
        self.assertEqual((released['status'], released['holder']), ('ready', None))

    def test_the_board_filters_while_its_options_list_everything(self):
        cart = self.create('Cart total', tags=['cart'], acceptance=['x'], status='ready')
        self.create('Search box', tags=['search'])
        tasks.claim(cart['id'], actor=AGENT, workspace='default')
        board = self.api.get('/api/tasks/board', params={'tag': 'cart', 'holder': 'grimforge', 'text': 'total'}).json()
        self.assertEqual(board['total'], 1)
        self.assertEqual(board['columns']['in_progress'][0]['id'], cart['id'])
        self.assertEqual(board['options']['tags'], ['cart', 'search'])
        self.assertEqual(board['options']['holders'], ['grimforge'])

    def test_the_users_inbox_holds_comments_for_the_user_and_tasks_in_review(self):
        made = self.create(acceptance=['x'], status='ready')
        tasks.comment(made['id'], 'Which key should I use?', to=['user'], actor=AGENT, workspace='default')
        tasks.claim(made['id'], actor=AGENT, workspace='default')
        tasks.move(made['id'], 'review', actor=AGENT, workspace='default')
        inbox = self.api.get('/api/tasks/inbox').json()
        self.assertEqual([c['text'] for c in inbox['comments']], ['Which key should I use?'])
        self.assertEqual([t['id'] for t in inbox['review']], [made['id']])
        self.assertGreater(inbox['review'][0]['status_at'], 0)
        self.assertEqual(self.api.get('/api/tasks/inbox').json()['comments'][0]['text'], 'Which key should I use?')
        self.api.post('/api/tasks/inbox/read', json={'ids': [inbox['comments'][0]['id']]})
        self.assertEqual(self.api.get('/api/tasks/inbox').json()['comments'], [])

    def test_comments_from_another_workspace_reach_the_inbox_and_are_read_there(self):
        config.register_workspace('work')
        made = tasks.create('Elsewhere', project='site', workspace='work')
        tasks.comment(made['id'], 'The Dispatcher switched off', to=['user'], actor=AGENT, workspace='work')
        inbox = self.api.get('/api/tasks/inbox').json()
        self.assertEqual(inbox['workspace'], 'default')
        self.assertEqual([(c['text'], c['workspace']) for c in inbox['comments']], [('The Dispatcher switched off', 'work')])
        self.api.post('/api/tasks/inbox/read', json={'ids': [inbox['comments'][0]['id']], 'workspace': 'work'})
        self.assertEqual(self.api.get('/api/tasks/inbox').json()['comments'], [])

    def test_the_workspaces_autonomy_is_set_from_the_dashboard(self):
        self.assertEqual(self.api.put('/api/tasks/autonomy', json={'value': 'stale'}).json(), {'autonomy': 'stale'})
        self.assertEqual(self.api.get('/api/tasks/board').json()['autonomy'], 'stale')
        self.assertEqual(self.api.put('/api/tasks/autonomy', json={'value': 'always'}).status_code, 400)


    def test_progress_of_projects_and_features(self):
        feature = self.create('Checkout')
        self.create('Total line', feature=feature['id'])
        report = self.api.get('/api/tasks/progress').json()
        self.assertEqual([(p['name'], p['total']) for p in report['projects']], [('shop', 1)])
        self.assertEqual([(f['id'], f['total'], f['done']) for f in report['features']], [(feature['id'], 1, 0)])
        only = self.api.get('/api/tasks/progress', params={'feature': feature['id']}).json()
        self.assertEqual((only['projects'], len(only['features'])), ([], 1))


    def test_features_are_created_with_their_kind_and_kept_out_of_the_columns(self):
        """A Feature's status follows its Tasks, so the Board's columns hold only Tasks (ADR 0242)."""
        feature = self.create('Checkout', kind='feature')
        self.assertEqual((feature['kind'], feature['status']), ('feature', 'backlog'))
        step = self.create('Total line', feature=feature['id'], acceptance=['Tax included'], status='ready')
        board = self.api.get('/api/tasks/board').json()
        self.assertEqual([t['id'] for t in board['columns']['ready']], [step['id']])
        self.assertEqual(board['total'], 1)
        self.assertEqual(board['options']['features'], [{'id': feature['id'], 'title': 'Checkout'}])
        moved = self.api.post(f"/api/tasks/{feature['id']}/move", json={'status': 'done'})
        self.assertEqual(moved.status_code, 400)
        self.assertIn('its status follows its Tasks', moved.json()['error'])
        cancelled = self.api.post(f"/api/tasks/{feature['id']}/move", json={'status': 'cancelled'})
        self.assertEqual(cancelled.json()['task']['status'], 'cancelled')
        empty = self.create('Search', kind='feature')
        edited = self.api.post(f"/api/tasks/{empty['id']}/edit", json={'kind': 'task'})
        self.assertEqual(edited.json()['task']['kind'], 'task')


    def test_the_features_tab_reads_each_feature_with_its_tasks(self):
        feature = self.create('Checkout', kind='feature')
        step = self.create('Total line', feature=feature['id'])
        listed = self.api.get('/api/tasks/features').json()
        self.assertEqual(listed['workspace'], 'default')
        [entry] = listed['features']
        self.assertEqual((entry['feature']['id'], [t['id'] for t in entry['tasks']], entry['summary']['total']),
                         (feature['id'], [step['id']], 1))
        self.assertEqual(self.api.get('/api/tasks/features', params={'project': 'blog'}).json()['features'], [])

if __name__ == '__main__':
    unittest.main()
