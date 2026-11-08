"""Paged topics and the workspace-migration preview (ADR 0150): the topic list
is searched, sorted and cut on the server, a topic's sources come a page at a
time, and a copy or move between workspaces is counted before it runs."""
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import test_indexing_retrieval as indexing
from imprint import cli_migrate, config, imprint_graph as kg, vectorstore as vs


class CurationTests(unittest.TestCase):
    setUp = indexing.StorageTests.setUp
    cleanup = indexing.StorageTests.cleanup
    records = indexing.StorageTests.records

    def prepare(self):
        self.addCleanup(kg._close_all)
        env = patch.dict(os.environ, {'IMPRINT_WORKSPACE': ''})
        env.start()
        self.addCleanup(env.stop)
        # Topic sizes 5, 4, 3, 2, 1 by name: alpha … echo; "beta" is also on
        # two of alpha's memories so a union counts them once.
        layout = {'alpha': 5, 'Bravo': 4, 'charlie': 3, 'delta': 2, 'echo': 1}
        for topic, size in layout.items():
            vs.store_batch(self.records([f'{topic} memory {i}' for i in range(size)],
                                        project='shared' if topic in ('alpha', 'Bravo') else 'private',
                                        source=f'{topic}/notes.md'))
        client, coll = vs._ensure_collection()
        rows, _ = client.scroll(coll, with_payload=['content'], limit=100)
        for row in rows:
            topic = row.payload['content'].split()[0]
            topics = [topic] + (['Bravo'] if topic == 'alpha' and row.payload['content'][-1] in '01' else [])
            client.set_payload(coll, {'tags': {'topics': topics}}, points=[row.id])

    def api(self):
        from imprint.api import app
        return TestClient(app, base_url='http://127.0.0.1:8420', client=('127.0.0.1', 1234))

    def test_topics_page_search_and_sort_on_the_server(self):
        self.prepare()
        first = self.api().get('/api/topics', params={'limit': 2}).json()
        self.assertEqual([(t['name'], t['count']) for t in first['topics']], [('Bravo', 6), ('alpha', 5)])
        self.assertEqual((first['matched'], first['topic_count'], first['offset'], first['limit']), (5, 5, 0, 2))
        self.assertEqual(first['topics'][0]['topProjects'], ['shared'])
        second = self.api().get('/api/topics', params={'limit': 2, 'offset': 2}).json()
        self.assertEqual([t['name'] for t in second['topics']], ['charlie', 'delta'])
        by_name = self.api().get('/api/topics', params={'sort': 'name'}).json()
        self.assertEqual([t['name'] for t in by_name['topics']], ['alpha', 'Bravo', 'charlie', 'delta', 'echo'])
        found = self.api().get('/api/topics', params={'q': 'HA'}).json()
        self.assertEqual(([t['name'] for t in found['topics']], found['matched'], found['topic_count']),
                         (['alpha', 'charlie'], 2, 5))
        self.assertEqual(self.api().get('/api/topics', params={'sort': 'size'}).status_code, 422)
        self.assertEqual(self.api().get('/api/topics', params={'limit': 0}).status_code, 422)

    def test_a_topic_pages_its_sources_and_searches_them(self):
        self.prepare()
        whole = self.api().get('/api/topic/Bravo').json()
        self.assertEqual(([g['source'] for g in whole['sources']], whole['total'], whole['source_count']),
                         (['Bravo/notes.md', 'alpha/notes.md'], 6, 2))
        self.assertNotIn('limit', whole)
        page = self.api().get('/api/topic/Bravo', params={'limit': 1, 'offset': 1}).json()
        self.assertEqual([g['source'] for g in page['sources']], ['alpha/notes.md'])
        self.assertEqual(sorted(c['content'] for c in page['sources'][0]['chunks']), ['alpha memory 0', 'alpha memory 1'])
        self.assertEqual((page['total'], page['source_count'], page['offset'], page['limit']), (6, 2, 1, 1))
        found = self.api().get('/api/topic/Bravo', params={'limit': 10, 'q': 'ALPHA/'}).json()
        self.assertEqual(([g['source'] for g in found['sources']], found['source_count'], found['total']),
                         (['alpha/notes.md'], 1, 6))

    def test_preview_counts_a_union_and_what_the_target_already_holds(self):
        self.prepare()
        config.register_workspace('team')
        result = self.api().post('/api/migrate/preview', json={'from': 'default', 'to': 'team', 'mode': 'topic',
                                                              'values': ['alpha', 'Bravo']}).json()
        self.assertEqual([(i['value'], i['memories'], i['sources']) for i in result['items']],
                         [('alpha', 5, 1), ('Bravo', 6, 2)])
        self.assertEqual((result['total'], result['existing'], result['sources'], result['target_exists']), (9, 0, 2, True))
        cli_migrate.migrate('default', 'team', topic='alpha', copy=True)
        again = cli_migrate.preview('default', 'team', 'topic', ['alpha', 'Bravo'])
        self.assertEqual((again['total'], again['existing']), (9, 5))
        # A preview changes nothing, even for a target that does not exist yet.
        fresh = cli_migrate.preview('default', 'archive', 'project', ['private'])
        self.assertEqual((fresh['total'], fresh['existing'], fresh['target_exists']), (6, 0, False))
        self.assertNotIn('archive', config.get_known_workspaces())

    def test_preview_refuses_what_migrate_would(self):
        self.prepare()
        for body in ({'from': 'default', 'to': 'default', 'mode': 'topic', 'values': ['alpha']},
                     {'from': 'default', 'to': 'team', 'mode': 'domain', 'values': ['alpha']},
                     {'from': 'default', 'to': 'team', 'mode': 'topic', 'values': []},
                     {'from': 'default', 'to': 'team', 'mode': 'topic', 'values': 'alpha'},
                     {'from': 'nowhere', 'to': 'team', 'mode': 'topic', 'values': ['alpha']},
                     {'to': 'team', 'mode': 'topic', 'values': ['alpha']}):
            response = self.api().post('/api/migrate/preview', json=body)
            self.assertEqual(response.status_code, 400, body)
            self.assertIn('error', response.json())


if __name__ == '__main__':
    unittest.main()
