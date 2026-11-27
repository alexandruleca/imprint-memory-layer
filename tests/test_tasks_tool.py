"""The Board through Imprint's MCP server (ADR 0208): agents read it with the
tasks tool and change it with task_edit."""
import asyncio
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from imprint import config, tasks


class TasksToolTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data = Path(directory.name)
        from imprint import server
        self.server = server
        for patcher in (patch.object(config, 'get_data_dir', return_value=self.data),
                        patch.object(tasks, '_device', return_value='device-a'),
                        patch.dict(os.environ, {'IMPRINT_WORKSPACE': ''}),
                        patch.dict(server._session, {'project': 'imprint'})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def call(self, *calls, client='claude-code'):
        from fastmcp import Client
        from mcp.types import Implementation

        async def run():
            async with Client(self.server.mcp, client_info=Implementation(name=client, version='1')) as session:
                tools = {tool.name: tool for tool in await session.list_tools()}
                results = []
                for name, arguments in calls:
                    result = await session.call_tool(name, arguments, raise_on_error=False)
                    results.append((result.is_error, result.content[0].text))
                return tools, results
        return asyncio.run(run())

    def test_an_agent_creates_a_task_and_reads_it_back_on_the_board(self):
        tools, [(failed, made)] = self.call(('task_edit', {
            'action': 'create', 'title': 'Add retries to the uploader', 'description': 'Uploads fail on 503.',
            'acceptance': ['A 503 is retried three times'], 'status': 'ready', 'priority': 'P1', 'tags': ['Upload']}))
        self.assertFalse(failed, made)
        task_id = tasks.listing(workspace='default')[0]['id']
        self.assertIn(f'Created {task_id}', made)
        self.assertTrue(tools['tasks'].annotations.read_only_hint)
        self.assertFalse(tools['task_edit'].annotations.read_only_hint)

        _, [(_, board), (_, got), (_, listed)] = self.call(
            ('tasks', {}), ('tasks', {'action': 'get', 'task': task_id}), ('tasks', {'action': 'list', 'status': 'ready'}))
        self.assertIn('Ready (1)', board)
        self.assertIn(f'{task_id} P1 Add retries to the uploader', board)
        for text in ('Add retries to the uploader', 'Status: Ready', 'Priority: P1', 'Project: imprint',
                     'Tags: upload', '- A 503 is retried three times', 'Uploads fail on 503.'):
            self.assertIn(text, got)
        self.assertIn(task_id, listed)

    def test_the_task_belongs_to_the_session_project_unless_one_is_named(self):
        self.call(('task_edit', {'action': 'create', 'title': 'Here'}),
                  ('task_edit', {'action': 'create', 'title': 'There', 'project': 'website'}))
        self.assertEqual(sorted(t['project'] for t in tasks.listing(workspace='default')), ['imprint', 'website'])
        with patch.dict(self.server._session, {'project': None}), patch('os.getcwd', return_value='/'):
            _, [(failed, text)] = self.call(('task_edit', {'action': 'create', 'title': 'Nowhere'}))
        self.assertTrue(failed)
        self.assertIn('project', text)

    def test_the_creating_agent_is_recorded_with_its_client(self):
        self.call(('task_edit', {'action': 'create', 'title': 'From Codex'}), client='codex')
        made = tasks.listing(workspace='default')[0]
        self.assertEqual(made['created_by']['kind'], 'agent')
        self.assertEqual(made['created_by']['client'], 'codex')

    def test_mistakes_come_back_as_errors_the_agent_can_act_on(self):
        _, results = self.call(('task_edit', {'action': 'create', 'title': 'No criteria', 'status': 'ready'}),
                               ('tasks', {'action': 'get', 'task': 'T-zzzzz'}),
                               ('tasks', {'action': 'get'}))
        self.assertEqual([failed for failed, _ in results], [True, True, True])
        self.assertIn('acceptance criterion', results[0][1])
        self.assertIn('No task T-zzzzz', results[1][1])
        self.assertIn('task', results[2][1])

    def test_an_empty_board_says_how_to_start(self):
        _, [(_, board)] = self.call(('tasks', {}))
        self.assertIn('No tasks yet', board)
        self.assertFalse((self.data / 'tasks.sqlite3').exists())


    def test_an_agent_shapes_groups_and_filters_tasks(self):
        feature = tasks.create('Checkout redesign', project='imprint', workspace='default')['id']
        blocker = tasks.create('Price API', project='imprint', workspace='default')['id']
        task = tasks.create('Total line', project='imprint', workspace='default')['id']
        _, results = self.call(
            ('task_edit', {'action': 'edit', 'task': task, 'title': 'New total line', 'priority': 'P1',
                           'acceptance': ['Shows tax']}),
            ('task_edit', {'action': 'append', 'task': task, 'text': 'Resolved: round half up.'}),
            ('task_edit', {'action': 'tag', 'task': task, 'add': ['Cart', 'ui']}),
            ('task_edit', {'action': 'assign', 'task': task, 'add': ['codex']}),
            ('task_edit', {'action': 'link', 'task': task, 'feature': feature, 'add': [blocker]}),
            ('task_edit', {'action': 'move', 'task': task, 'status': 'Ready'}),
            ('task_edit', {'action': 'block', 'task': task, 'text': 'Waiting for prices'}),
            ('tasks', {'action': 'get', 'task': task}),
            ('tasks', {'action': 'list', 'feature': feature, 'tag': 'cart', 'priority': 'P1', 'assignee': 'codex',
                       'text': 'round half'}),
            ('tasks', {'action': 'get', 'task': feature}))
        self.assertEqual([failed for failed, _ in results], [False] * 10, results)
        got = results[7][1]
        for text in ('New total line', 'Status: Ready', 'Priority: P1', 'Tags: cart, ui', 'Assignees: codex',
                     f'Feature: {feature} Checkout redesign', f'Blocked by: {blocker} (open)',
                     'Blocked: Waiting for prices', 'Resolved: round half up.'):
            self.assertIn(text, got)
        self.assertIn(task, results[8][1])
        self.assertIn(f'Tasks: {task}', results[9][1])
        _, [(_, unblocked), (_, cancelled)] = self.call(('task_edit', {'action': 'unblock', 'task': task}),
                                                        ('task_edit', {'action': 'cancel', 'task': task}))
        self.assertNotIn('Blocked:', unblocked)
        self.assertIn('Status: Cancelled', cancelled)

    def test_agents_cannot_delete(self):
        tools, _ = self.call()
        actions = tools['task_edit'].inputSchema['properties']['action']['enum']
        self.assertNotIn('delete', actions)


    def as_session(self, conversation, *calls):
        with patch.dict(os.environ, {'CLAUDE_CODE_SESSION_ID': conversation}):
            return self.call(*calls)[1]

    def test_an_agent_claims_checkpoints_and_releases_through_the_tools(self):
        task = tasks.create('Parser', project='imprint', acceptance=['Parses'], status='ready',
                            workspace='default')['id']
        [(failed, claimed), (_, board)] = self.as_session('conv-1', ('task_edit', {'action': 'claim', 'task': task}),
                                                         ('tasks', {}))
        self.assertFalse(failed, claimed)
        self.assertIn(f'Claimed {task}', claimed)
        token = re.search(r'claim token: (\S+)', claimed).group(1)
        self.assertIn('In progress (1)', board)
        self.assertIn('[held by claude-code', board)
        [(failed, refused)] = self.as_session('conv-2', ('task_edit', {'action': 'claim', 'task': task}))
        self.assertTrue(failed)
        self.assertIn('is held by', refused)
        [(_, noted), (_, got)] = self.as_session(
            'conv-1', ('task_edit', {'action': 'checkpoint', 'task': task, 'text': 'Tokenizer done.',
                                     'next_step': 'Write the grammar.', 'branch': f'task/{task[2:]}-parser',
                                     'commit': 'abc1234', 'files': ['parser.py']}),
            ('tasks', {'action': 'get', 'task': task}))
        for text in ('Held by claude-code', 'Last checkpoint', 'Tokenizer done.', 'Next: Write the grammar.',
                     f'task/{task[2:]}-parser @ abc1234'):
            self.assertIn(text, got)
        [(failed, resumed)] = self.as_session('conv-3', ('task_edit', {'action': 'claim', 'task': task, 'token': token}))
        self.assertFalse(failed, resumed)
        [(_, released)] = self.as_session('conv-3', ('task_edit', {'action': 'release', 'task': task}))
        self.assertIn('Status: Ready', released)

    def test_a_lost_claim_is_told_once_on_the_next_reply(self):
        task = tasks.create('Race', project='imprint', acceptance=['x'], status='ready', workspace='default')['id']
        self.as_session('conv-1', ('task_edit', {'action': 'claim', 'task': task}))
        [created] = tasks.history(task, workspace='default')[:1]
        # Stamped with the creation's clock and the highest id: after the creation, before the local claim.
        tasks.import_events([{'id': 'f' * 32, 'task': created['task'], 'kind': 'claimed', 'hlc': created['hlc'],
                              'device': 'device-b', 'actor': 'agent', 'session': 'conversation:far', 'agent': 'quill',
                              'client': 'codex', 'recorded_at': 1.0, 'data': {'token_hash': 'x' * 64}}],
                            workspace='default')
        [(_, first), (_, second)] = self.as_session('conv-1', ('tasks', {}), ('tasks', {}))
        self.assertIn(f'You lost your claim on {task}', first)
        self.assertIn('quill (codex)', first)
        self.assertNotIn('lost your claim', second)

    def test_the_server_renews_its_sessions_leases_with_its_latest_activity(self):
        from imprint import tasks_tool
        task = tasks.create('Lease', project='imprint', acceptance=['x'], status='ready', workspace='default')['id']
        self.as_session('conv-1', ('task_edit', {'action': 'claim', 'task': task}))
        with patch.dict(os.environ, {'CLAUDE_CODE_SESSION_ID': 'conv-1'}):
            self.assertEqual(tasks_tool.renew_now(), 1)
        with patch.dict(os.environ, {'CLAUDE_CODE_SESSION_ID': 'conv-other'}):
            self.assertEqual(tasks_tool.renew_now(), 0)


    def test_agents_name_themselves_and_are_shown_by_name(self):
        task = tasks.create('Named', project='imprint', acceptance=['x'], status='ready', workspace='default')['id']
        [(failed, named), (_, claimed), (_, board)] = self.as_session(
            'conv-1', ('task_edit', {'action': 'name', 'name': 'Grimforge'}),
            ('task_edit', {'action': 'claim', 'task': task}), ('tasks', {}))
        self.assertFalse(failed, named)
        self.assertIn('You are Grimforge', named)
        self.assertNotIn('You had no name', claimed)
        self.assertIn('[held by Grimforge (claude-code)]', board)
        [(failed, taken)] = self.as_session('conv-2', ('task_edit', {'action': 'name', 'name': 'grimforge'}))
        self.assertTrue(failed)
        self.assertIn('Grimforge-2', taken.replace('grimforge-2', 'Grimforge-2'))

    def test_an_agent_that_claims_without_a_name_gets_one_and_is_told(self):
        task = tasks.create('Unnamed', project='imprint', acceptance=['x'], status='ready', workspace='default')['id']
        [(_, claimed)] = self.as_session('conv-9', ('task_edit', {'action': 'claim', 'task': task}))
        fallback = re.search(r'you are (claude-code-\w{4})', claimed).group(1)
        self.assertIn('task_edit(action="name"', claimed)
        self.assertEqual(tasks.get(task, workspace='default')['holder']['name'], fallback)


    def test_an_agent_asks_what_to_take_next_and_reads_a_handoff(self):
        task = tasks.create('Parser', project='imprint', acceptance=['x'], status='ready', priority='P1',
                            workspace='default')['id']
        [(_, offered)] = self.as_session('conv-1', ('tasks', {'action': 'next'}))
        self.assertIn(f'Take {task} next', offered)
        self.assertIn(f'task_edit(action="claim", task="{task}")', offered)
        self.as_session('conv-1', ('task_edit', {'action': 'name', 'name': 'grimforge'}),
                        ('task_edit', {'action': 'claim', 'task': task}),
                        ('task_edit', {'action': 'checkpoint', 'task': task, 'text': 'Tokenizer done.',
                                       'branch': 'task/p', 'commit': 'abc', 'files': ['p.py']}))
        [(_, nothing)] = self.as_session('conv-2', ('tasks', {'action': 'next'}))
        self.assertIn('Nothing on the Board for you to take', nothing)
        [(_, handoff)] = self.as_session('conv-2', ('tasks', {'action': 'handoff', 'task': task}))
        for text in ('Handoff for', 'grimforge (claude-code)', 'Tokenizer done.', 'task/p @ abc', 'p.py',
                     'read_source(source="conversation/conv-1"'):
            self.assertIn(text, handoff)

    def test_a_features_frontier_through_next(self):
        feature = tasks.create('Checkout', project='imprint', workspace='default')['id']
        first = tasks.create('First', project='imprint', acceptance=['x'], status='ready', feature=feature,
                             workspace='default')['id']
        [(_, frontier)] = self.as_session('conv-1', ('tasks', {'action': 'next', 'feature': feature}))
        self.assertIn(f'Frontier of {feature}', frontier)
        self.assertIn(first, frontier)


if __name__ == '__main__':
    unittest.main()
