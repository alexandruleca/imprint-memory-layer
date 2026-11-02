import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from imprint import opencode_sessions, transcripts


def _v2(path, sessions):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE session_v2 (id TEXT PRIMARY KEY, project_id TEXT, parent_id TEXT, slug TEXT, directory TEXT,
                                 title TEXT, version TEXT, time_created INTEGER, time_updated INTEGER);
        CREATE TABLE session_message (id TEXT PRIMARY KEY, session_id TEXT, type TEXT, seq INTEGER,
                                      time_created INTEGER, time_updated INTEGER, data TEXT);
        CREATE TABLE session_part (message_id TEXT, session_id TEXT, ordinal INTEGER, type TEXT, call_id TEXT,
                                   time_created INTEGER, time_updated INTEGER, data TEXT);
    """)
    for session in sessions:
        conn.execute('INSERT INTO session_v2 VALUES (?,?,?,?,?,?,?,?,?)',
                     (session['id'], 'prj', session.get('parent'), 's', session['cwd'], session.get('title', ''),
                      '2', 1, session.get('updated', 1)))
        for seq, (mid, kind, data, parts) in enumerate(session.get('messages', [])):
            conn.execute('INSERT INTO session_message VALUES (?,?,?,?,?,?,?)',
                         (mid, session['id'], kind, seq, 1000 + seq, 1000 + seq, json.dumps(data)))
            for ordinal, part in enumerate(parts):
                conn.execute('INSERT INTO session_part VALUES (?,?,?,?,?,?,?,?)',
                             (mid, session['id'], ordinal, part['type'], None, 1, 1, json.dumps(part)))
    conn.commit()
    conn.close()


def _v1(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE session (id TEXT PRIMARY KEY, project_id TEXT, parent_id TEXT, slug TEXT, directory TEXT,
                              title TEXT, version TEXT, time_created INTEGER, time_updated INTEGER);
        CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT);
        CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER,
                           time_updated INTEGER, data TEXT);
    """)
    conn.execute("INSERT INTO session VALUES ('ses_old1','prj',NULL,'s','/home/u/code/app','Old','1.19',1,5)")
    conn.execute("""INSERT INTO message VALUES ('msg_a','ses_old1',1,1,'{"role":"user"}')""")
    conn.execute("""INSERT INTO message VALUES ('msg_b','ses_old1',2,2,'{"role":"assistant"}')""")
    rows = [('prt_1', 'msg_a', {'type': 'text', 'text': 'Why does the build fail?'}),
            ('prt_2', 'msg_a', {'type': 'text', 'text': '<file contents>', 'synthetic': True}),
            ('prt_3', 'msg_a', {'type': 'text', 'text': 'ignored', 'ignored': True}),
            ('prt_4', 'msg_b', {'type': 'reasoning', 'text': 'Look at the log.'}),
            ('prt_5', 'msg_b', {'type': 'tool', 'callID': 'c1', 'tool': 'bash',
                                'state': {'status': 'completed', 'input': {'command': 'make'}, 'output': 'error 2'}}),
            ('prt_6', 'msg_b', {'type': 'text', 'text': 'A missing header.'})]
    for pid, mid, data in rows:
        conn.execute('INSERT INTO part VALUES (?,?,?,?,?,?)', (pid, mid, 'ses_old1', 1, 1, json.dumps(data)))
    conn.commit()
    conn.close()


class OpenCodeSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Path(self.tmp.name) / 'opencode'
        self.store.mkdir()
        self.exports = Path(self.tmp.name) / 'exports'
        patcher = patch.object(opencode_sessions, 'export_root', return_value=self.exports)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {}, clear=False)
        env.start()
        os.environ.pop('OPENCODE_DB', None)
        self.addCleanup(env.stop)

    def messages(self, path):
        return [transcripts.message(record) for record in transcripts.records(path) if transcripts.message(record)]

    def test_v2_session_is_exported_as_a_transcript(self):
        _v2(self.store / 'opencode.db', [{
            'id': 'ses_abc', 'cwd': '/home/u/code/app', 'title': 'Fix build', 'messages': [
                ('msg_1', 'user', {'text': 'Why does the build fail?', 'time': {'created': 1}}, []),
                ('msg_2', 'synthetic', {'text': 'Injected context', 'time': {'created': 1}}, []),
                ('msg_3', 'assistant', {'agent': 'build', 'content': []}, [
                    {'type': 'reasoning', 'text': 'Check the log.'},
                    {'type': 'tool', 'id': 'call_1', 'name': 'bash',
                     'state': {'status': 'completed', 'input': {'command': 'make'},
                               'content': [{'type': 'text', 'text': 'error 2'}]}},
                    {'type': 'text', 'text': 'A header is missing.'}]),
                ('msg_4', 'idle', {'outcome': 'succeeded'}, []),
            ]}, {'id': 'ses_child', 'parent': 'ses_abc', 'cwd': '/home/u/code/app'}])
        paths = opencode_sessions.export(self.store)
        self.assertEqual([path.name for path in paths], ['ses_abc.jsonl'])
        path = str(paths[0])
        self.assertEqual(transcripts.session(path), 'ses_abc')
        self.assertEqual(list(transcripts.cwds(path)), ['/home/u/code/app'])
        self.assertEqual(self.messages(path), [('user', 'Why does the build fail?'),
                                               ('assistant', 'A header is missing.')])
        records = list(transcripts.records(path))
        roles = [record.get('role') for record in records[1:]]
        self.assertEqual(roles, ['user', 'context', 'assistant'])
        kinds = [block['type'] for block in records[3]['content']]
        self.assertEqual(kinds, ['reasoning', 'tool_use', 'tool_result', 'text'])

    def test_unchanged_session_keeps_its_export_and_deleted_ones_go(self):
        _v2(self.store / 'opencode.db', [{'id': 'ses_one', 'cwd': '/w', 'messages': [
            ('msg_1', 'user', {'text': 'hello'}, [])]}])
        path = opencode_sessions.export(self.store)[0]
        os.utime(path, (1, 1))
        self.assertEqual(opencode_sessions.export(self.store)[0].stat().st_mtime, 1)
        (self.store / 'opencode.db').unlink()
        _v2(self.store / 'opencode.db', [])
        self.assertEqual(opencode_sessions.export(self.store), [])
        self.assertFalse(path.exists())

    def test_v1_database_is_read_from_its_messages_and_parts(self):
        _v1(self.store / 'opencode-beta.db')
        path = str(opencode_sessions.export(self.store)[0])
        self.assertEqual(self.messages(path), [('user', 'Why does the build fail?'), ('assistant', 'A missing header.')])
        records = list(transcripts.records(path))
        self.assertEqual([record.get('role') for record in records[1:]], ['context', 'user', 'assistant'])

    def test_one_session_is_exported_by_id_and_foreign_ids_are_refused(self):
        _v2(self.store / 'opencode.db', [{'id': 'ses_x', 'cwd': '/w', 'messages': [('msg_1', 'user', {'text': 'q'}, [])]},
                                         {'id': 'ses_y', 'cwd': '/w'}])
        self.assertEqual(opencode_sessions.export_session('ses_y', self.store).name, 'ses_y.jsonl')
        self.assertIsNone(opencode_sessions.export_session('ses_missing', self.store))
        self.assertIsNone(opencode_sessions.export_session('../../etc/passwd', self.store))


if __name__ == '__main__':
    unittest.main()
