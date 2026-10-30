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
    def test_a_session_records_its_client_calls_and_end(self):
        sid = aa.start_session('/home/user/code/app', 'default')
        aa.name_client('claude-code', '2.1.0')
        aa.record_call('search', time.time(), 12.34, ok=True, arguments={'query': 'caching'},
                       result_chars=420, workspace='work')
        aa.record_call('store', time.time(), 3, ok=False, error='ValueError: blank', arguments={})

        [session] = aa.list_sessions()
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
        [ended] = aa.list_sessions()
        self.assertFalse(ended['connected'])
        self.assertIsNotNone(ended['ended_at'])

    def test_a_process_that_died_without_ending_counts_as_disconnected(self):
        dead = subprocess.Popen([sys.executable, '-c', 'pass'])
        dead.wait()
        with patch('os.getpid', return_value=dead.pid):
            aa.start_session('/tmp', 'default')
        [session] = aa.list_sessions()
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
        self.assertEqual([s['id'] for s in aa.list_sessions()], [new])
        self.assertEqual(aa.session_calls(old), [])

    def test_clients_group_sessions(self):
        aa.start_session('/a', 'default')
        aa.name_client('cursor', '1')
        aa.record_call('search', time.time(), 1, ok=True)
        aa.end_session()
        aa.start_session('/b', 'default')
        aa.name_client('cursor', '1')
        aa.start_session('/c', 'default')
        groups = {c['client']: c for c in aa.clients(aa.list_sessions())}
        self.assertEqual(set(groups), {'cursor', 'Unknown client'})
        cursor = groups['cursor']
        self.assertEqual((cursor['sessions'], cursor['connected'], cursor['calls']), (2, 1, 1))

    def test_nothing_is_recorded_without_a_session(self):
        aa.record_call('search', time.time(), 1, ok=True)
        aa.name_client('x', '1')
        aa.end_session()
        self.assertEqual(aa.list_sessions(), [])

    def test_reading_before_any_session_creates_nothing(self):
        self.assertEqual(aa.list_sessions(), [])
        self.assertIsNone(aa.get_session('missing'))
        self.assertFalse(aa._db_path().exists())


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
        self.assertEqual([s['id'] for s in data['sessions']], [sid])
        self.assertEqual(data['clients'][0]['client'], 'codex')
        calls = api.get(f'/api/agents/{sid}/calls').json()
        self.assertEqual(calls['session']['client'], 'codex')
        self.assertEqual([c['tool'] for c in calls['calls']], ['search'])
        self.assertEqual(api.get('/api/agents/missing/calls').status_code, 404)


if __name__ == '__main__':
    unittest.main()
