import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import Implementation

from imprint import agent_activity as aa
from imprint import server


def listed() -> list[dict]:
    """Every session, connected ones first, as the first page lists them."""
    page = aa.page_sessions(500)
    return page['running'] + page['sessions']


class AgentActivityCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {'IMPRINT_DATA_DIR': temp.name})
        env.start()
        self.addCleanup(env.stop)
        aa.close()
        self.addCleanup(aa.close)
        session = patch.object(aa, '_session_id', None)
        session.start()
        self.addCleanup(session.stop)


class RecordingTests(AgentActivityCase):
    def test_the_last_call_says_when_an_agent_last_used_imprint(self):
        # An automatic update waits for agents to go quiet (ADR 0175).
        self.assertIsNone(aa.last_call_at())
        aa.start_session('/home/user/code/app', 'default')
        aa.record_call('search', 1000.0, 5, ok=True)
        aa.record_call('store', 2000.0, 5, ok=True)
        self.assertEqual(aa.last_call_at(), 2000.0)

    def test_a_session_records_its_client_calls_and_end(self):
        sid = aa.start_session('/home/user/code/app', 'default')
        aa.name_client('claude-code', '2.1.0')
        aa.record_call('search', time.time(), 12.34, ok=True, arguments={'query': 'caching'},
                       result_chars=420, workspace='work')
        aa.record_call('store', time.time(), 3, ok=False, error='ValueError: blank', arguments={})

        [session] = listed()
        self.assertEqual((session['id'], session['client'], session['client_version']), (sid, 'claude-code', '2.1.0'))
        self.assertEqual((session['calls'], session['errors'], session['last_tool']), (2, 1, 'store'))
        self.assertEqual(session['workspace'], 'work')
        self.assertTrue(session['connected'])  # This process is alive.

        calls = aa.session_calls(sid)
        self.assertEqual([c['tool'] for c in calls], ['store', 'search'])
        self.assertEqual((calls[1]['ok'], calls[1]['duration_ms'], calls[1]['result_chars']), (1, 12.3, 420))
        self.assertEqual(calls[1]['arguments'], '{"query": "caching"}')
        self.assertEqual(calls[0]['error'], 'ValueError: blank')
        self.assertEqual([c['tool'] for c in aa.session_calls(sid, before=calls[0]['id'])], ['search'])

        aa.end_session()
        [ended] = listed()
        self.assertFalse(ended['connected'])
        self.assertIsNotNone(ended['ended_at'])

    def test_a_process_that_died_without_ending_counts_as_disconnected(self):
        dead = subprocess.Popen([sys.executable, '-c', 'pass'])
        dead.wait()
        with patch('os.getpid', return_value=dead.pid):
            aa.start_session('/tmp', 'default')
        [session] = listed()
        self.assertFalse(session['connected'])
        self.assertEqual(session['ended_at'], session['last_seen'])

    def test_arguments_are_shortened_and_redacted(self):
        secret = 'ghp_' + 'a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8'
        summary = aa.summarize_arguments({'content': 'x' * 1000, 'note': f'token {secret}', 'n': 3})
        self.assertNotIn(secret, summary)
        self.assertIn('[REDACTED:', summary)
        self.assertIn('x' * aa.VALUE_CHARS + '…', summary)
        self.assertNotIn('x' * (aa.VALUE_CHARS + 1), summary)
        self.assertLessEqual(len(summary), aa.ARGUMENTS_CHARS + 1)

    def test_old_sessions_are_pruned_when_a_session_starts(self):
        old = aa.start_session('/old', 'default')
        aa.record_call('search', time.time(), 1, ok=True)
        aa._connect().execute('UPDATE agent_sessions SET last_seen = ? WHERE id = ?',
                              (time.time() - (aa.RETENTION_DAYS + 1) * 86400, old))
        aa._connect().commit()
        new = aa.start_session('/new', 'default')
        self.assertEqual([s['id'] for s in listed()], [new])
        self.assertEqual(aa.session_calls(old), [])

    def test_clients_group_sessions(self):
        aa.start_session('/a', 'default')
        aa.name_client('cursor', '1')
        aa.record_call('search', time.time(), 1, ok=True)
        aa.end_session()
        aa.start_session('/b', 'default')
        aa.name_client('cursor', '1')
        aa.start_session('/c', 'default')
        groups = {c['client']: c for c in aa.client_totals()}
        self.assertEqual(set(groups), {'cursor', 'Unknown client'})
        cursor = groups['cursor']
        self.assertEqual((cursor['sessions'], cursor['connected'], cursor['calls']), (2, 1, 1))

    def test_nothing_is_recorded_without_a_session(self):
        aa.record_call('search', time.time(), 1, ok=True)
        aa.name_client('x', '1')
        aa.end_session()
        self.assertEqual(listed(), [])

    def test_reading_before_any_session_creates_nothing(self):
        self.assertEqual(listed(), [])
        self.assertEqual(aa.client_totals(), [])
        self.assertIsNone(aa.get_session('missing'))
        self.assertFalse(aa._db_path().exists())


def finished(sid: str, last_seen: float, client: str | None = 'claude-code', directory: str = '/code/app',
             workspace: str = 'default') -> None:
    """A session that ended at ``last_seen``, written as a finished process leaves it."""
    conn = aa._connect()
    conn.execute(
        'INSERT INTO agent_sessions (id, pid, client, directory, workspace, started_at, last_seen, ended_at)'
        ' VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (sid, 999999, client, directory, workspace, last_seen - 60, last_seen, last_seen))
    conn.commit()


class PagingTests(AgentActivityCase):
    def test_pages_cover_the_history_once_newest_first_with_running_sessions_on_top(self):
        now = time.time()
        for i in range(7):
            finished(f'f{i}', now - 100 * (i + 1))
        live = aa.start_session('/live', 'default')  # This process: connected.

        first = aa.page_sessions(3)
        self.assertEqual([s['id'] for s in first['running']], [live])
        self.assertEqual([s['id'] for s in first['sessions']], ['f0', 'f1', 'f2'])
        self.assertEqual(first['total'], 7)
        seen, cursor = [s['id'] for s in first['sessions']], first['next']
        while cursor:
            page = aa.page_sessions(3, cursor)
            self.assertEqual(page['running'], [])
            seen += [s['id'] for s in page['sessions']]
            cursor = page['next']
        self.assertEqual(seen, [f'f{i}' for i in range(7)])

    def test_a_new_session_does_not_shift_the_next_page(self):
        now = time.time()
        for i in range(4):
            finished(f'f{i}', now - 100 * (i + 1))
        first = aa.page_sessions(2)
        finished('newer', now)  # Arrives while the user reads the first page.
        second = aa.page_sessions(2, first['next'])
        self.assertEqual([s['id'] for s in second['sessions']], ['f2', 'f3'])
        self.assertIsNone(second['next'])
        self.assertEqual(aa.page_sessions(2)['total'], 5)

    def test_sessions_last_seen_together_page_by_id(self):
        at = time.time() - 50
        for sid in ('a', 'b', 'c'):
            finished(sid, at)
        first = aa.page_sessions(2)
        second = aa.page_sessions(2, first['next'])
        self.assertEqual([s['id'] for s in first['sessions'] + second['sessions']], ['c', 'b', 'a'])

    def test_filters_by_client_and_by_folder_workspace_or_client_text(self):
        now = time.time()
        finished('a', now - 1, client='cursor', directory='/code/site')
        finished('b', now - 2, client=None, directory='/code/100%_done')
        finished('c', now - 3, client='claude-code', directory='/code/app', workspace='research')
        self.assertEqual([s['id'] for s in aa.page_sessions(client='Unknown client')['sessions']], ['b'])
        self.assertEqual([s['id'] for s in aa.page_sessions(query='SITE')['sessions']], ['a'])
        self.assertEqual([s['id'] for s in aa.page_sessions(query='100%_')['sessions']], ['b'])
        self.assertEqual([s['id'] for s in aa.page_sessions(query='%')['sessions']], ['b'])
        self.assertEqual([s['id'] for s in aa.page_sessions(query='research')['sessions']], ['c'])
        self.assertEqual(aa.page_sessions(client='cursor', query='app')['total'], 0)

    def test_a_malformed_cursor_is_refused(self):
        finished('a', time.time())
        for cursor in ('nope', '12|', 'x|a'):
            with self.assertRaises(ValueError):
                aa.page_sessions(2, cursor)

    def test_client_totals_count_every_session_not_one_page(self):
        now = time.time()
        for i in range(5):
            finished(f'f{i}', now - i)
        aa._connect().execute("INSERT INTO agent_calls (session_id, tool, started_at, duration_ms, ok) VALUES ('f4', 'search', 0, 1, 0)")
        aa._connect().commit()
        aa.page_sessions(2)
        [totals] = aa.client_totals()
        self.assertEqual((totals['client'], totals['sessions'], totals['calls'], totals['errors'], totals['connected']),
                         ('claude-code', 5, 1, 1, 0))


class MiddlewareTests(AgentActivityCase, unittest.IsolatedAsyncioTestCase):
    async def test_the_handshake_names_the_client_and_each_call_is_recorded(self):
        app = FastMCP('t')

        @app.tool
        def echo(text: str) -> str:
            return text * 2

        @app.tool
        def broken() -> str:
            raise ValueError('nope')

        app.add_middleware(server._ActivityLog())
        sid = aa.start_session('/work', 'default')
        with patch.dict(os.environ, {'IMPRINT_WORKSPACE': 'chosen'}):
            async with Client(app, client_info=Implementation(name='test-agent', version='9.9')) as client:
                await client.call_tool('echo', {'text': 'hi'})
                with self.assertRaises(ToolError):
                    await client.call_tool('broken', {})

        session = aa.get_session(sid)
        self.assertEqual((session['client'], session['client_version'], session['workspace']), ('test-agent', '9.9', 'chosen'))
        broken, echo = aa.session_calls(sid)
        self.assertEqual((echo['tool'], echo['ok'], echo['arguments'], echo['result_chars']), ('echo', 1, '{"text": "hi"}', 4))
        self.assertEqual((broken['tool'], broken['ok']), ('broken', 0))
        self.assertIn('nope', broken['error'])


class ApiTests(AgentActivityCase):
    def api(self):
        from imprint.api import app
        return TestClient(app, base_url='http://127.0.0.1:8420', client=('127.0.0.1', 1234))

    def test_sessions_and_their_calls(self):
        sid = aa.start_session('/work', 'default')
        aa.name_client('codex', '0.5')
        aa.record_call('search', time.time(), 2, ok=True, arguments={'query': 'q'})
        api = self.api()
        data = api.get('/api/agents').json()
        self.assertEqual(([s['id'] for s in data['running']], data['sessions']), ([sid], []))
        self.assertEqual(data['clients'][0]['client'], 'codex')
        calls = api.get(f'/api/agents/{sid}/calls').json()
        self.assertEqual(calls['session']['client'], 'codex')
        self.assertEqual([c['tool'] for c in calls['calls']], ['search'])
        self.assertEqual(api.get('/api/agents/missing/calls').status_code, 404)

    def test_the_session_list_pages_with_a_cursor(self):
        now = time.time()
        for i in range(3):
            finished(f'f{i}', now - 10 * (i + 1))
        live = aa.start_session('/live', 'default')
        api = self.api()
        first = api.get('/api/agents', params={'limit': 2}).json()
        self.assertEqual([s['id'] for s in first['running']], [live])
        self.assertEqual([s['id'] for s in first['sessions']], ['f0', 'f1'])
        self.assertEqual((first['total'], sum(c['sessions'] for c in first['clients'])), (3, 4))
        second = api.get('/api/agents', params={'limit': 2, 'before': first['next']}).json()
        self.assertEqual(([s['id'] for s in second['sessions']], second['running'], second['next']), (['f2'], [], None))
        self.assertEqual(api.get('/api/agents', params={'q': 'live'}).json()['total'], 0)
        self.assertEqual(api.get('/api/agents', params={'before': 'bad'}).status_code, 400)


if __name__ == '__main__':
    unittest.main()
