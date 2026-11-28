"""The Board through Imprint's MCP server (ADR 0208): agents read it with the
tasks tool and change it with task_edit."""
import asyncio
import os
import re
import tempfile
import time
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
                        patch.object(tasks.actors, '_device', return_value='device-a'),
                        patch.dict(os.environ, {'IMPRINT_WORKSPACE': ''}),
                        # As start_session leaves it: the folder's path, whose name the Board uses.
                        patch.dict(server._session, {'directory': '/home/u/code/imprint',
                                                     'project': '/home/u/code/imprint'})):
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
                    results.append((result.is_error, '\n'.join(part.text for part in result.content)))
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
        with patch.dict(self.server._session, {'directory': None, 'project': None}), patch('os.getcwd', return_value='/'):
            _, [(failed, text), (_, unasked)] = self.call(('task_edit', {'action': 'create', 'title': 'Nowhere'}),
                                                          ('tasks', {'action': 'next'}))
        self.assertTrue(failed)
        self.assertIn('project', text)
        self.assertIn('no project folder, so there is nothing to take unasked', unasked)

    def test_a_wsl_session_names_its_project_as_its_hooks_do(self):
        # The bridge runs the server on Windows, in the \\wsl.localhost path of the client's folder.
        with patch('imprint.wsl_paths.sys.platform', 'win32'), patch.dict(os.environ, {'IMPRINT_WSL_DISTRO': 'Ubuntu'}), \
                patch.dict(self.server._session, {'directory': r'\\wsl.localhost\Ubuntu\home\u\code\shop-app'}):
            self.assertEqual(self.server._board_project(), 'shop-app')

    def test_unasked_work_comes_only_from_the_sessions_project(self):
        mine = tasks.create('Mine', project='imprint', acceptance=['x'], status='ready', priority='P3',
                            workspace='default')['id']
        tasks.create('Elsewhere', project='website', acceptance=['x'], status='ready', priority='P0', workspace='default')
        [(_, offered), (_, named)] = self.as_session('conv-1', ('tasks', {'action': 'next'}),
                                                     ('tasks', {'action': 'next', 'project': 'website'}))
        self.assertIn(f'Take {mine} next', offered)
        self.assertIn(f'Take {mine} next', named)
        self.assertIn("Unasked work comes only from this session's project, imprint, not website.", named)

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

    def test_a_claim_tells_the_persons_unread_comments_once(self):
        task = tasks.create('Parser', project='imprint', acceptance=['Parses'], status='ready',
                            workspace='default')['id']
        tasks.comment(task, 'Keep the old grammar working.\nIt ships tomorrow.', actor=tasks.USER, workspace='default')
        [(failed, claimed)] = self.as_session('conv-1', ('task_edit', {'action': 'claim', 'task': task}))
        self.assertFalse(failed, claimed)
        self.assertIn('From the person on this Task, which outranks your plan', claimed)
        self.assertIn('Keep the old grammar working.\nIt ships tomorrow.', claimed)  # whole
        token = re.search(r'claim token: (\S+)', claimed).group(1)
        [(_, again), (_, board)] = self.as_session('conv-1', ('task_edit', {'action': 'claim', 'task': task, 'token': token}),
                                                   ('tasks', {}))
        self.assertNotIn('From the person on this Task', again)  # told once
        self.assertNotIn('New comment on', board)  # and no notice repeats it

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
        [(_, unasked)] = self.as_session('conv-1', ('tasks', {'action': 'next'}))
        self.assertIn('Nothing on the Board for you to take', unasked)
        self.assertIn("A Feature's Tasks are taken only when the user asks", unasked)


    def test_comments_reach_agents_as_a_notice_once_and_through_the_inbox(self):
        task = tasks.create('Notice me', project='imprint', workspace='default')['id']
        tasks.comment(task, 'Codex: the build is red on main', to=['codex'], workspace='default')
        tools, [(_, first), (_, second), (_, inbox)] = self.call(('tasks', {}), ('tasks', {}),
                                                                 ('tasks', {'action': 'inbox'}), client='codex')
        self.assertIn(f'New comment on {task}', first)
        self.assertIn('the build is red on main', first)
        self.assertNotIn('New comment', second)
        self.assertIn('the build is red on main', inbox)
        _, [(_, again)] = self.call(('tasks', {'action': 'inbox'}), client='codex')
        self.assertIn('No unread comments', again)
        _, [(_, other)] = self.call(('tasks', {}), client='claude-code')
        self.assertNotIn('New comment', other)

    def test_an_agent_comments_and_addresses_others(self):
        task = tasks.create('Talk', project='imprint', workspace='default')['id']
        [(failed, said)] = self.as_session('conv-1', ('task_edit', {'action': 'comment', 'task': task,
                                                                    'text': 'Done with my part', 'to': ['user']}))
        self.assertFalse(failed, said)
        self.assertIn('Done with my part', said)
        [comment] = tasks.user_inbox(workspace='default')
        self.assertTrue(comment['by']['name'].startswith('claude-code-'))

    def test_a_stalling_holder_is_told_once(self):
        task = tasks.create('Slow', project='imprint', acceptance=['x'], status='ready', workspace='default')['id']
        self.as_session('conv-1', ('task_edit', {'action': 'claim', 'task': task}))
        later = time.time() + 61 * 60
        with patch.object(time, 'time', return_value=later):
            tasks.renew('conversation:conv-1')
            [(_, first), (_, second)] = self.as_session('conv-1', ('tasks', {'action': 'get', 'task': task}),
                                                        ('tasks', {}))
        self.assertIn(f'{task} has had no progress from you', first)
        self.assertNotIn('no progress from you', second)


    def test_clients_are_known_by_their_client_table_names(self):
        self.assertEqual(tasks.client_id('codex-mcp-client'), 'codex')
        self.assertEqual(tasks.client_id('cursor-vscode'), 'cursor')
        self.assertEqual(tasks.client_id('claude-code'), 'claude-code')
        self.assertEqual(tasks.client_id('claude-ai'), 'claude-desktop')
        task = tasks.create('For Codex', project='imprint', acceptance=['x'], status='ready', assignees=['codex'],
                            workspace='default')['id']
        tasks.comment(task, 'Codex, this one is yours', to=['codex'], workspace='default')
        _, [(_, offered), (_, claimed)] = self.call(('tasks', {'action': 'next'}),
                                                    ('task_edit', {'action': 'claim', 'task': task}),
                                                    client='codex-mcp-client')
        self.assertIn('Codex, this one is yours', offered)
        self.assertIn(f'Take {task} next', offered)
        self.assertRegex(claimed, r'you are codex-\w{4} ')


    def test_progress_says_how_far_each_project_and_feature_is(self):
        feature = tasks.create('Checkout redesign', project='imprint', workspace='default')['id']
        first = tasks.create('Total line', project='imprint', feature=feature, acceptance=['x'], workspace='default')['id']
        tasks.move(first, 'done', workspace='default')
        tasks.create('Coupon field', project='imprint', feature=feature, workspace='default')
        _, [(failed, text)] = self.call(('tasks', {'action': 'progress'}))
        self.assertFalse(failed, text)
        self.assertIn('imprint: 1 of 2 done (50%)', text)
        self.assertIn(f'{feature} Checkout redesign (imprint): 1 of 2 done (50%)', text)
        self.assertIn('not enough finished work to estimate yet', text)

    def test_an_agent_creates_a_feature_and_reads_it_apart_from_the_columns(self):
        """A Feature's status follows its Tasks (ADR 0242)."""
        [(failed, made)] = self.as_session('conv-1', ('task_edit', {
            'action': 'create', 'title': 'Checkout redesign', 'kind': 'feature'}))
        self.assertFalse(failed, made)
        feature = tasks.listing(workspace='default')[0]['id']
        self.assertIn('A Feature: its status follows its Tasks, and it has none yet', made)
        tasks.create('Total line', project='imprint', acceptance=['x'], status='ready', feature=feature,
                     workspace='default')
        [(_, board), (moved_failed, moved)] = self.as_session(
            'conv-1', ('tasks', {}), ('task_edit', {'action': 'move', 'task': feature, 'status': 'Done'}))
        self.assertIn('Features (1)', board)
        self.assertIn(f'{feature} P2 Checkout redesign · imprint (Feature, Ready: 0 of 1 done)', board)
        self.assertIn('Ready (1)', board)
        self.assertTrue(moved_failed)
        self.assertIn('its status follows its Tasks (now Ready)', moved)


class SkillExamplesTests(unittest.TestCase):
    """The built-in imprint-tasks skill teaches calls the tools accept."""

    def test_every_example_call_names_a_real_action_and_real_arguments(self):
        import asyncio
        from imprint import registry, server
        tools = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
        folder = registry.BUILTINS / 'imprint-tasks'
        text = '\n'.join(path.read_text(encoding='utf-8') for path in folder.glob('*.md'))
        calls = re.findall(r'\b(tasks|task_edit)\(([^()`]*)\)', text)
        self.assertGreater(len(calls), 20)
        for name, arguments in calls:
            schema = tools[name].parameters['properties']
            with self.subTest(call=f'{name}({arguments})'):
                for keyword in re.findall(r'(\w+)=', arguments):
                    self.assertIn(keyword, schema)
                action = re.search(r'action="(\w+)"', arguments)
                if action:
                    self.assertIn(action.group(1), schema['action']['enum'])


if __name__ == '__main__':
    unittest.main()
