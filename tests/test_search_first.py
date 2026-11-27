"""Whether agents still search Imprint first once their client got the
short rules block (ADR 0214): the share measured from the Agents page's
records, the decision upkeep makes once per client, and where it shows."""
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from imprint import agent_activity as aa
from imprint import config, search_first, upkeep
from imprint.config_schema import resolve

DAY = 86400.0
SINCE = 1_800_000_000.0  # When codex first got the short block.


class SearchFirstCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.data = Path(temp.name)
        for patcher in (patch.dict(os.environ, {'IMPRINT_DATA_DIR': temp.name, 'IMPRINT_INSTRUCTIONS_FULL_RULES': ''}),
                        patch.object(aa, '_session_id', None)):
            patcher.start()
            self.addCleanup(patcher.stop)
        os.environ.pop('IMPRINT_INSTRUCTIONS_FULL_RULES')
        aa.close()
        self.addCleanup(aa.close)
        self.connected = []

    def state(self, clients):
        (self.data / 'instructions.json').write_text(json.dumps({'clients': clients}))

    def session(self, client, at, tools=('search',)):
        """A session of ``client`` whose calls start at ``at``, in order."""
        conn = aa._connect()
        sid = uuid.uuid4().hex
        conn.execute('INSERT INTO agent_sessions (id, pid, client, started_at, last_seen) VALUES (?, 1, ?, ?, ?)',
                     (sid, client, at - 5, at + len(tools)))
        for offset, tool in enumerate(tools):
            conn.execute('INSERT INTO agent_calls (session_id, tool, started_at, duration_ms, ok) VALUES (?, ?, ?, 1, 1)',
                         (sid, tool, at + offset))
        conn.commit()
        return sid

    def sessions(self, client, start, count, searching):
        """``count`` sessions a day apart from ``start``, the first
        ``searching`` of them searching first."""
        for n in range(count):
            self.session(client, start + n * DAY / 2, ('search', 'get_memory') if n < searching else ('get_memory', 'search'))

    def run_step(self, now):
        return search_first.step(now=now, connect=self.connected.append)

    def entry(self, client='codex'):
        return json.loads((self.data / 'instructions.json').read_text())['clients'][client]


class ShareTests(SearchFirstCase):
    def test_the_share_counts_sessions_that_called_imprint_by_their_first_call(self):
        self.session('codex-mcp-client', SINCE + 10)                                 # Searched first.
        self.session('codex-mcp-client', SINCE + 20, ('get_memory', 'search'))       # Read first.
        aa._connect().execute('INSERT INTO agent_sessions (id, pid, client, started_at, last_seen) '
                              'VALUES (?, 1, ?, ?, ?)', ('silent', 'codex-mcp-client', SINCE + 30, SINCE + 40))
        aa._connect().commit()                                                       # Never called Imprint.
        self.session('codex-mcp-client', SINCE - 10, ('get_memory', 'search'))       # Began before the window.
        self.session('claude-code', SINCE + 50)                                      # Another client.
        self.assertEqual(search_first.tally('codex', SINCE, SINCE + DAY), {'sessions': 2, 'search_first': 1})
        self.assertEqual(search_first.tally('claude-code', SINCE, SINCE + DAY), {'sessions': 1, 'search_first': 1})
        self.assertEqual(search_first.tally('cursor', SINCE, SINCE + DAY), {'sessions': 0, 'search_first': 0})

    def test_without_records_or_a_switch_nothing_is_due_or_counted(self):
        self.assertFalse(search_first.due(now=SINCE))
        self.assertEqual(search_first.tally('codex', 0, SINCE), {'sessions': 0, 'search_first': 0})
        self.assertFalse((self.data / 'agent_activity.sqlite3').exists())  # Counting made no store.
        self.assertEqual(search_first.comparison(now=SINCE), [])


class DecisionTests(SearchFirstCase):
    def test_a_drop_of_more_than_10_points_restores_the_full_block_once(self):
        self.state({'codex': {'short_since': SINCE}})
        self.sessions('codex', SINCE - 13 * DAY, 10, 9)        # 90% before.
        self.assertTrue(search_first.due(now=SINCE + 60))
        self.run_step(SINCE + 60)                                # The window before is kept at once...
        self.assertEqual(self.entry()['before'], {'sessions': 10, 'search_first': 9})
        self.assertFalse(search_first.due(now=SINCE + 13 * DAY))  # ...then nothing until 14 days have passed.
        self.sessions('codex', SINCE + DAY, 10, 7)               # 70% after.
        self.assertTrue(search_first.due(now=SINCE + 14 * DAY))
        lines = self.run_step(SINCE + 15 * DAY)
        decision = self.entry()['decision']
        self.assertEqual((decision['outcome'], decision['before'], decision['after']), ('restored', 90.0, 70.0))
        self.assertEqual(self.connected, ['codex'])
        self.assertIn('codex', resolve('instructions.full_rules')[0].split(','))
        self.assertIn('codex', lines[0])
        self.assertFalse(search_first.due(now=SINCE + 30 * DAY))
        self.run_step(SINCE + 30 * DAY)
        self.assertEqual(self.connected, ['codex'])  # Once.

    def test_a_drop_of_10_points_or_less_keeps_the_short_block(self):
        config_file = self.data / 'config.json'
        config_file.write_text(json.dumps({'instructions': {'full_rules': 'cursor'}}))
        self.state({'codex': {'short_since': SINCE}})
        self.sessions('codex', SINCE - 13 * DAY, 10, 9)
        self.run_step(SINCE + 60)
        self.sessions('codex', SINCE + DAY, 10, 8)
        self.run_step(SINCE + 14 * DAY)
        self.assertEqual(self.entry()['decision']['outcome'], 'kept')
        self.assertEqual((self.connected, resolve('instructions.full_rules')[0]), ([], 'cursor'))

    def test_too_few_sessions_after_waits_up_to_6_weeks_then_keeps_the_short_block(self):
        self.state({'codex': {'short_since': SINCE}, 'cursor': {'short_since': SINCE}})
        self.sessions('codex', SINCE - 13 * DAY, 10, 10)
        self.sessions('cursor-vscode', SINCE - 13 * DAY, 10, 10)
        self.run_step(SINCE + 60)
        self.sessions('codex', SINCE + DAY, 4, 0)
        self.sessions('cursor-vscode', SINCE + DAY, 4, 4)
        self.run_step(SINCE + 14 * DAY)
        self.assertNotIn('decision', self.entry())               # Not enough yet: the window after grows.
        self.assertFalse(search_first.due(now=SINCE + 14 * DAY + 3600))
        self.sessions('codex', SINCE + 20 * DAY, 6, 0)           # Codex reaches 10: 40%, a drop of 60 points.
        self.run_step(SINCE + 24 * DAY)
        self.assertEqual(self.entry()['decision']['outcome'], 'restored')
        self.assertEqual(self.entry()['after']['sessions'], 10)
        self.assertNotIn('decision', self.entry('cursor'))
        for day in range(25, 43):                                # Cursor never does.
            self.run_step(SINCE + day * DAY)
        cursor = self.entry('cursor')['decision']
        self.assertEqual((cursor['outcome'], self.connected), ('too_few', ['codex']))

    def test_too_few_sessions_before_cannot_grow_so_the_short_block_stays_at_14_days(self):
        self.state({'codex': {'short_since': SINCE}})
        self.sessions('codex', SINCE - 13 * DAY, 3, 3)
        self.run_step(SINCE + 60)
        self.sessions('codex', SINCE + DAY, 12, 0)
        self.run_step(SINCE + 14 * DAY)
        self.assertEqual(self.entry()['decision']['outcome'], 'too_few')
        self.assertEqual(self.connected, [])

    def test_the_window_before_survives_the_activity_records_retention(self):
        self.state({'codex': {'short_since': SINCE}})
        self.sessions('codex', SINCE - 13 * DAY, 10, 9)
        self.run_step(SINCE + 60)
        aa._connect().execute('DELETE FROM agent_sessions')    # 30 days later the Agents page forgets them.
        aa._connect().commit()
        self.sessions('codex', SINCE + DAY, 10, 9)
        self.run_step(SINCE + 14 * DAY)
        self.assertEqual(self.entry()['decision']['outcome'], 'kept')

    def test_what_setup_records_beside_the_measurement_is_kept(self):
        (self.data / 'instructions.json').write_text(json.dumps({'clients': {'codex': {'short_since': SINCE}}, 'other': 1}))
        self.run_step(SINCE + 60)
        data = json.loads((self.data / 'instructions.json').read_text())
        self.assertEqual((data['other'], data['clients']['codex']['short_since']), (1, SINCE))


class WhereItShowsTests(SearchFirstCase):
    def test_upkeep_runs_it_once_for_the_device_without_the_vector_store(self):
        self.state({'codex': {'short_since': SINCE}})
        with patch.object(search_first.time, 'time', return_value=SINCE + 60):
            upkeep.reset()
            self.assertIn((config.get_active_workspace(), 'search-first'), upkeep.due(fresh=True))
            self.assertEqual([step for _, step in upkeep.due(fresh=True)].count('search-first'), 1)
            from imprint import vectorstore
            with patch.object(vectorstore, '_get_client', side_effect=AssertionError('no store needed')):
                self.assertEqual(upkeep.run(), 0)
        self.assertIn('before', self.entry())

    def test_the_comparison_doctor_and_the_dashboard_show(self):
        self.state({'codex': {'short_since': SINCE}, 'cursor': {'short_since': SINCE}})
        self.sessions('codex', SINCE - 13 * DAY, 10, 9)
        self.sessions('codex', SINCE + DAY, 10, 7)
        self.sessions('cursor-vscode', SINCE - 13 * DAY, 2, 2)
        self.run_step(SINCE + 15 * DAY)
        rows = {row['client']: row for row in search_first.comparison(now=SINCE + 15 * DAY)}
        self.assertEqual((rows['codex']['before']['share'], rows['codex']['after']['share']), (90.0, 70.0))
        self.assertEqual(rows['codex']['decision']['outcome'], 'restored')
        self.assertEqual(rows['cursor']['decision']['outcome'], 'too_few')
        from imprint import doctor
        with patch.object(search_first.time, 'time', return_value=SINCE + 15 * DAY):
            checks = {c['name']: c for c in doctor.report(Path(__file__).resolve().parents[1])['checks']}
        self.assertIn('90% of 10 sessions before', checks['search-first:codex']['detail'])
        self.assertIn('full block', checks['search-first:codex']['detail'])
        self.assertEqual(checks['search-first:codex']['status'], 'ok')
        from fastapi.testclient import TestClient
        from imprint.api import app
        api = TestClient(app, base_url='http://127.0.0.1:8420', client=('127.0.0.1', 1234))
        with patch.object(search_first.time, 'time', return_value=SINCE + 15 * DAY):
            body = api.get('/api/agents/search-first').json()
        self.assertEqual({row['client'] for row in body['clients']}, {'codex', 'cursor'})

    def test_a_client_still_measuring_shows_when_it_will_be_compared(self):
        self.state({'codex': {'short_since': SINCE}})
        self.sessions('codex', SINCE - 13 * DAY, 4, 4)
        self.sessions('codex', SINCE + DAY, 2, 1)
        [row] = search_first.comparison(now=SINCE + 3 * DAY)
        self.assertIsNone(row['decision'])
        self.assertEqual((row['before']['sessions'], row['after']['sessions'], row['decide_at']), (4, 2, SINCE + 14 * DAY))
        self.assertIn('compared on', search_first.describe(row))


if __name__ == '__main__':
    unittest.main()
