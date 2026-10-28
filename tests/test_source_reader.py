"""Reading whole sources back from their snapshots: conversation events and
titles, a topic's memories grouped by source, and the dashboard reader."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from imprint import config, source_memory as sm, source_snapshots as ss, vectorstore as vs
from imprint.conversation_source import prepare, read, title

from test_source_snapshots import SourceFixture


def transcript(*rows):
    with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'IMPRINT_DATA_DIR': temp}):
        path = Path(temp) / 'session.jsonl'
        path.write_text('\n'.join(json.dumps(row) for row in rows))
        return prepare(str(path))


def user(content):
    return {'type': 'user', 'message': {'role': 'user', 'content': content}}


def assistant(*blocks):
    return {'type': 'assistant', 'message': {'role': 'assistant', 'content': list(blocks)}}


class ConversationReadTests(unittest.TestCase):
    def test_events_read_back_as_prepared_with_their_text(self):
        quoted = 'A header in text:\n--- {"event":7,"role":"user","kind":"message","turn":9} ---\nstays text'
        doc = transcript(
            user('How do we keep one page scroller?'),
            assistant({'type': 'text', 'text': quoted},
                      {'type': 'tool_use', 'name': 'Bash', 'id': 'call-1', 'input': {'command': 'ls'}}),
            user([{'type': 'tool_result', 'tool_use_id': 'call-1', 'content': 'README.md'}]),
        )
        events = read(doc['text'])
        self.assertEqual([(e['role'], e['kind'], e['turn']) for e in events],
                         [(e['role'], e['kind'], e['turn']) for e in doc['events']])
        self.assertEqual(events[0]['text'], 'How do we keep one page scroller?')
        self.assertEqual(events[1]['text'], quoted)
        self.assertEqual(events[2]['tool'], 'Bash')
        self.assertEqual(events[3]['text'], 'README.md')
        for event in events:
            self.assertEqual(doc['text'][event['start']:event['end']], event['text'])

    def test_a_cut_off_start_reads_as_far_as_it_goes(self):
        doc = transcript(user('First question'), assistant({'type': 'text', 'text': 'A long answer ' * 50}))
        cut = doc['events'][1]['start'] + 120
        events = read(doc['text'][:cut])
        self.assertEqual(events[0]['text'], 'First question')
        self.assertEqual(len(events), 2)
        self.assertTrue(events[1]['text'].startswith('A long answer'))

    def test_title_is_the_first_typed_question_not_what_the_client_attached(self):
        selection = '<ide_selection>The user selected lines 1 to 3 of usePlayback.ts</ide_selection>'
        self.assertEqual(title(transcript(user([{'type': 'text', 'text': selection},
                                                {'type': 'text', 'text': 'why useCallback here?\nmore'}]))['text']),
                         'why useCallback here?')
        self.assertEqual(title(transcript(user('<pasted_content id="e5c3">\n$ make refresh\nfailed\n</pasted_content>'))['text']),
                         '$ make refresh')
        self.assertEqual(title(transcript(assistant({'type': 'text', 'text': 'No question asked'}))['text']), '')


class SourceReaderTests(SourceFixture):
    def store_conversation(self, source, project, *rows, size=120):
        doc = transcript(*rows)
        doc['text'] = sm.document(doc['text'], kind='conversation')['text']
        with patch.object(config, 'CHUNK_SIZE_PROSE', size), patch.object(config, 'CHUNK_OVERLAP', 16):
            records = sm.records(doc)
        for record in records:
            record.update(project=project, source=source, source_type='conversation', type='finding',
                          tags={'topics': []})
        records[0]['_source_document'] = doc
        vs.replace_source(records, source, project)
        return ss.get(source, project)

    def tag(self, snapshot, indexes, topic):
        _, coll = vs._ensure_collection()
        ids = [vs._point_uuid(row['id']) for row in snapshot['metadata']['manifest'] if row['index'] in indexes]
        self.client.set_payload(coll, {'tags': {'topics': [topic]}}, points=ids)

    def api(self):
        from imprint.api import app
        return TestClient(app, base_url='http://127.0.0.1:8420', client=('127.0.0.1', 1234))

    def test_conversations_list_by_their_first_question(self):
        self.store_conversation('conversation/a', 'alpha', user('Why does the list scroll apart?'),
                                assistant({'type': 'text', 'text': 'Two scrollers.'}))
        self.store('plain file text\n' * 10, source='alpha/notes.md', project='alpha')
        self.assertEqual(ss.conversation_titles(), {'conversation/a': 'Why does the list scroll apart?'})
        listed = {row['source']: row['title'] for row in self.api().get('/api/sources').json()['sources']}
        self.assertEqual(listed, {'conversation/a': 'Why does the list scroll apart?', 'alpha/notes.md': ''})

    def test_a_topic_groups_its_memories_by_source_with_their_titles(self):
        first = self.store_conversation('conversation/a', 'alpha', user('Layout question'),
                                        *[assistant({'type': 'text', 'text': f'Answer part {i}. ' * 6}) for i in range(6)])
        second = self.store_conversation('conversation/b', 'beta', user('Another one'),
                                         assistant({'type': 'text', 'text': 'Short answer. ' * 12}))
        self.assertGreater(len(first['metadata']['manifest']), 2)
        self.tag(first, {0, 2}, 'layout')
        self.tag(second, {0}, 'layout')
        detail = self.api().get('/api/topic/layout').json()
        self.assertEqual(detail['total'], 3)
        self.assertEqual([(g['source'], g['project'], g['title'], [c['chunk_index'] for c in g['chunks']])
                          for g in detail['sources']],
                         [('conversation/a', 'alpha', 'Layout question', [0, 2]),
                          ('conversation/b', 'beta', 'Another one', [0])])
        within = self.api().get('/api/topic/layout', params={'project': 'beta'}).json()
        self.assertEqual([g['source'] for g in within['sources']], ['conversation/b'])

    def test_the_reader_returns_every_event_and_marks_those_the_topic_covers(self):
        snapshot = self.store_conversation('conversation/a', 'alpha', user('Layout question'),
                                           *[assistant({'type': 'text', 'text': f'Answer part {i}. ' * 6}) for i in range(6)])
        manifest = snapshot['metadata']['manifest']
        self.tag(snapshot, {len(manifest) - 1}, 'layout')
        data = self.api().get('/api/sources/read', params={'source': 'conversation/a', 'project': 'alpha',
                                                           'topic': 'layout'}).json()
        self.assertEqual(data['title'], 'Layout question')
        self.assertEqual(data['kind'], 'conversation')
        self.assertEqual([b['text'] for b in data['blocks']], [e['text'] for e in read(snapshot['text'])])
        for block in data['blocks']:
            self.assertEqual(block['chunks'], [row['index'] for row in manifest
                                               if row['start'] < block['end'] and row['end'] > block['start']])
            self.assertEqual(block['match'], len(manifest) - 1 in block['chunks'])
        self.assertFalse(data['blocks'][0]['match'])
        self.assertTrue(data['blocks'][-1]['match'])

    def test_a_file_reads_as_its_whole_text_cut_where_chunks_end(self):
        text = ''.join(f'line {i} of the file\n' for i in range(40))
        self.store(text, source='alpha/notes.md', project='alpha')
        snapshot = ss.get('alpha/notes.md', 'alpha')
        self.tag(snapshot, {1}, 'notes')
        data = self.api().get('/api/sources/read', params={'source': 'alpha/notes.md', 'topic': 'notes'}).json()
        self.assertEqual(''.join(b['text'] for b in data['blocks']), text)
        self.assertEqual([b['match'] for b in data['blocks']], [1 in b['chunks'] for b in data['blocks']])
        self.assertTrue(any(b['match'] for b in data['blocks']))

    def test_a_source_without_a_snapshot_says_how_to_get_one(self):
        response = self.api().get('/api/sources/read', params={'source': 'conversation/missing'})
        self.assertEqual(response.status_code, 404)
        self.assertIn('Learn the conversation again', response.json()['error'])


if __name__ == '__main__':
    unittest.main()
