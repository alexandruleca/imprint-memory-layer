"""The Board travels between paired devices (ADR 0209): each workspace's
events, claims' leases, settings and the user's read marks, as the extra
dataset `tasks` of the delta sync. Two workspaces of one data directory stand
for the two devices, as test_placement_sync does."""
import asyncio
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from imprint import config, sync_delta, sync_ws, tasks

GRIM = tasks.agent('conversation:c1', name='grimforge', client='claude-code')
QUILL = tasks.agent('process:p2', name='quill', client='codex')
GITHUB_TOKEN = 'ghp_' + 'A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8'


class TasksSyncTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data = Path(directory.name)
        for patcher in (patch.object(config, 'get_data_dir', return_value=self.data),
                        patch.object(tasks.actors, '_device', return_value='device-a'),
                        patch.dict(os.environ, {'IMPRINT_WORKSPACE': ''})):
            patcher.start()
            self.addCleanup(patcher.stop)
        config.register_workspace('peer')

    def send(self, source='default', target='peer'):
        """What one delta sync sends of the Board, stored as it arrives."""
        keys = sync_delta.wanted({'tasks': tasks.sync_manifest(source)}, {'tasks': tasks.sync_manifest(target)})
        stats = {}
        for number, stream in enumerate(sync_delta.export_chunks(source, {'tasks': keys['tasks']})):
            buffer = self.data / f'tasks-{number}.jsonl'
            buffer.write_text('\n'.join(stream))
            got = sync_ws.process_import_buffer(str(buffer), target)
            for key, value in got.get('tasks', {}).items():
                stats[key] = stats.get(key, 0) + value
        return stats

    def both(self):
        self.send('default', 'peer')
        self.send('peer', 'default')

    def board(self, workspace):
        return {t['id']: t for t in tasks.listing(archived=True, workspace=workspace)}

    def test_two_devices_that_both_worked_on_the_board_end_with_the_same_one(self):
        here = tasks.create('Fix the total', project='shop', acceptance=['Tax included'], status='ready', workspace='default')['id']
        there = tasks.create('Search box', project='shop', workspace='peer')['id']
        self.both()
        self.assertEqual(set(self.board('default')), {here, there})
        tasks.claim(here, actor=GRIM, workspace='default')
        tasks.comment(here, 'Use the new tax table', to=['grimforge'], workspace='peer')
        tasks.append(here, 'Resolved: round half up.', workspace='peer')
        tasks.tag(there, add=['search'], workspace='default')
        self.both()
        a, b = self.board('default'), self.board('peer')
        self.assertEqual(a, b)
        self.assertEqual(a[here]['holder']['name'], 'grimforge')
        self.assertEqual([c['text'] for c in a[here]['comments']], ['Use the new tax table'])
        self.assertIn('Resolved: round half up.', a[here]['description'])
        self.assertEqual(a[there]['tags'], ['search'])
        self.assertEqual(self.send('default', 'peer'), {})  # Nothing new: nothing sent.

    def test_a_task_deleted_on_one_device_is_deleted_on_the_other(self):
        made = tasks.create('Secret plan', project='shop', description='Very private.', workspace='default')['id']
        self.both()
        tasks.comment(made, 'More private notes.', workspace='peer')  # Written before the deletion arrives there.
        tasks.delete(made, workspace='default')
        self.both()
        for workspace in ('default', 'peer'):
            with self.subTest(workspace=workspace):
                self.assertEqual(self.board(workspace), {})
                with self.assertRaisesRegex(LookupError, 'deleted'):
                    tasks.get(made, workspace=workspace)
                path = config.tasks_db_path(workspace)
                conn = sqlite3.connect(path)
                rows = conn.execute('SELECT data FROM events').fetchall()
                conn.close()
                self.assertTrue(all('private' not in data for (data,) in rows))

    def test_lease_renewals_the_autonomy_setting_and_the_users_read_marks_travel(self):
        made = tasks.create('Lease', project='shop', acceptance=['x'], status='ready', workspace='default')['id']
        tasks.claim(made, actor=QUILL, workspace='default')
        later = time.time() + 20 * 60
        with patch.object(time, 'time', return_value=later):
            tasks.renew('process:p2')
        self.both()
        with patch.object(time, 'time', return_value=later + 60):
            self.assertFalse(tasks.get(made, workspace='peer')['stale'])  # The renewal arrived, not only the claim.
        tasks.set_autonomy('stale', workspace='peer')
        tasks.comment(made, 'Which bucket?', to=['user'], actor=QUILL, workspace='default')
        self.both()
        self.assertEqual(tasks.autonomy(workspace='default'), 'stale')
        [comment] = tasks.user_inbox(workspace='default')  # Read on this device...
        self.both()
        self.assertEqual(tasks.user_inbox(workspace='peer'), [])  # ...and so read on the other.
        tasks.set_autonomy('ready', workspace='default')
        self.both()
        self.assertEqual(tasks.autonomy(workspace='peer'), 'ready')

    def test_agents_read_state_and_names_stay_on_their_device(self):
        made = tasks.create('Talk', project='shop', workspace='default')['id']
        tasks.choose_name('conversation:c1', 'grimforge', workspace='default')
        tasks.comment(made, '@grimforge look', workspace='default')
        tasks.inbox('conversation:c1', name='grimforge', workspace='default')  # Read here.
        self.both()
        self.assertIsNone(tasks.name_of('conversation:c1', workspace='peer'))
        self.assertEqual(len(tasks.inbox('conversation:c1', name='grimforge', mark=False, workspace='peer')), 1)

    def test_text_from_another_device_is_redacted_on_arrival_and_malformed_records_are_skipped(self):
        made = tasks.create('Rotate a key', project='shop', workspace='default')['id']
        self.both()
        [created] = tasks.history(made, workspace='default')
        sneaky = {**created, 'id': 'f' * 32, 'kind': 'comment', 'data': {'text': f'the token is {GITHUB_TOKEN}', 'to': []},
                  'hlc': f"{int(time.time() * 1000):013d}.0000"}
        stored = tasks.sync_import([{'type': 'event', **sneaky}, {'type': 'event', 'id': 'x'}, {'type': 'mystery'},
                                    {'type': 'lease', 'claim': 'c', 'renewed_at': 'soon'}], workspace='peer')
        self.assertEqual(stored, {'inserted': 1, 'updated': 0, 'skipped': 3})
        [comment] = tasks.get(made, workspace='peer')['comments']
        self.assertNotIn(GITHUB_TOKEN, comment['text'])

    def test_the_board_goes_only_to_a_peer_that_offers_it(self):
        sent = []

        async def send(channel, workspace, keys, cancel):
            sent.append(keys)
            yield {'type': 'progress'}

        class Channel:
            async def send(self, frame):
                pass

            async def recv(self, timeout=None):
                return {'kind': 'manifest', 'data': sync_delta.encode({'memories': {}, 'facts': {}, 'sources': {}})}

        local = {'memories': {}, 'facts': {}, 'sources': {}, 'tasks': {'e:' + 'a' * 32: 0.0}}

        async def run(extras):
            async for _ in sync_delta.reconcile(Channel(), 'ws', local, provider=True, provider_sends=True,
                                                consumer_sends=False, cancel=asyncio.Event(), result={}, extras=extras):
                pass

        with patch.object(sync_delta, '_send', send):
            asyncio.run(run(frozenset()))
            asyncio.run(run(sync_delta.extras_of({'tasks': True})))
        self.assertEqual([keys['tasks'] for keys in sent], [[], ['e:' + 'a' * 32]])
        self.assertTrue(sync_delta.extras_offered()['tasks'])
        self.assertTrue(sync_ws._stream_line_ok({'kind': 'meta', 'vectors': True, 'datasets': {'tasks': 1}}, False))
        # A peer's manifest without the dataset is a valid one.
        self.assertEqual(sync_delta.decode(sync_delta.encode({'memories': {}}))['tasks'], {})


    def test_tasks_given_the_same_id_on_two_devices_end_with_the_same_two_ids_everywhere(self):
        import secrets
        for first, second in (('default', 'peer'), ('peer', 'default')):
            with self.subTest(order=first):
                for workspace in ('default', 'peer'):
                    path = config.tasks_db_path(workspace)
                    for suffix in ('', '-wal', '-shm'):
                        Path(str(path) + suffix).unlink(missing_ok=True)
                with patch.object(secrets, 'choice', side_effect=lambda alphabet: 'k'):
                    older = tasks.create('Made first', project='shop', workspace=first)
                    # Each store keeps its own clock in milliseconds: two creates in one millisecond would tie,
                    # and the tie-break (the event id) is random. Two devices are never that close.
                    started = int(time.time() * 1000)
                    while int(time.time() * 1000) == started:
                        time.sleep(0.0005)
                    newer = tasks.create('Made second', project='shop', workspace=second)
                self.assertEqual(older['id'], newer['id'])
                self.send(first, second)
                self.send(second, first)
                self.send(first, second)
                a, b = self.board('default'), self.board('peer')
                self.assertEqual(sorted(a), sorted(b))
                self.assertEqual(len(a), 2)
                self.assertEqual(a[older['id']]['title'], 'Made first')  # The older one keeps its id.
                [renamed] = [t for t in a.values() if t['title'] == 'Made second']
                self.assertNotEqual(renamed['id'], older['id'])
                self.assertEqual(renamed['former_ids'], [older['id']])
                self.assertEqual(b[renamed['id']]['former_ids'], [older['id']])
                self.assertIn(renamed['id'], [t['id'] for t in tasks.listing(text=older['id'], workspace='peer')])
                [event] = [e for e in tasks.history(renamed['id'], workspace=second) if e['kind'] == 'renamed'][:1]
                self.assertEqual(event['actor'], 'system')


if __name__ == '__main__':
    unittest.main()
