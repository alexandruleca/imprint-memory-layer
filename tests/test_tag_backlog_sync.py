"""Memories that wait for LLM tagging travel to a paired device that tags them,
and the tags come back (ADR 0138)."""
import asyncio
import os
from pathlib import Path
from unittest.mock import patch

from imprint import config, sync_delta, sync_pairs, sync_ws, tag_backlog, vectorstore as vs
from test_projects import ProjectStoreTests


def pair(workspace='default', **over):
    return {'id': 'pair-1', 'peer': {'fingerprint': 'f' * 64, 'name': 'bedrock'}, 'workspace': workspace,
            'remote_workspace': 'default', 'state': 'active', 'enabled': True, **over}


class Channel:
    """The other device's side of a paired exchange: answers in order."""
    def __init__(self, *replies):
        self.replies, self.sent = list(replies), []

    async def send(self, message):
        self.sent.append(message)

    async def recv(self, timeout=None):
        return self.replies.pop(0)


class TagBacklogSyncTests(ProjectStoreTests):
    """'default' is the laptop that leaves tagging to its peer; 'peer' is the
    desktop that tags."""

    def setUp(self):
        super().setUp()
        config.register_workspace('peer')

    def store(self, workspace, texts, pending, source='notes/a.md'):
        records = [{**record, **({'tag_pending': True} if pending else {})}
                   for record in self.records(texts, project='notes', source=source)]
        vs.replace_source(records, source, 'notes', workspace=workspace)

    def payloads(self, workspace):
        client, coll = vs._ensure_collection(workspace)
        rows, _ = client.scroll(coll, limit=1000, with_payload=True)
        return {row.payload['content']: row.payload for row in rows}

    def send(self, source, target, datasets=None):
        """What one delta sync sends from ``source`` and ``target`` stores."""
        keys = sync_delta.wanted(sync_delta.manifest(source), sync_delta.manifest(target))
        if datasets is not None:
            keys = {dataset: keys[dataset] if dataset in datasets else [] for dataset in keys}
        buffer = Path(self.data.name) / 'stream.jsonl'
        stats = {}
        for stream in sync_delta.export_chunks(source, keys):
            buffer.write_text('\n'.join(stream))
            for dataset, values in sync_ws.process_import_buffer(str(buffer), target).items():
                held = stats.setdefault(dataset, {})
                for key, value in values.items():
                    held[key] = held.get(key, 0) + value
        return stats

    def tag(self, workspace):
        """The desktop's background job tags everything that waits there."""
        client, coll = vs._ensure_collection(workspace)
        rows, _ = client.scroll(coll, scroll_filter=tag_backlog._pending_filter(), limit=1000, with_payload=['_mid'])
        vs.set_classifications(client, coll, [(row.id, {'topics': ['meetings']}, 'decision') for row in rows])
        return len(rows)

    def test_new_memories_wait_on_the_peer_that_tags_and_their_tags_come_back(self):
        self.store('default', ['first note', 'second note'], pending=True)
        self.assertEqual(tag_backlog.pending_count('default'), 2)
        self.send('default', 'peer')
        self.assertEqual({text: pl.get('tag_pending') for text, pl in self.payloads('peer').items()},
                         {'first note': True, 'second note': True})
        self.assertEqual(self.tag('peer'), 2)
        self.send('peer', 'default')
        laptop = self.payloads('default')
        self.assertEqual(tag_backlog.pending_count('default'), 0)
        self.assertEqual({(pl['type'], pl['llm_tagged'], pl['tags']['topics'][0]) for pl in laptop.values()},
                         {('decision', True, 'meetings')})
        # Both now agree: nothing more to send either way.
        self.assertEqual(self.send('default', 'peer'), {})
        self.assertEqual(self.send('peer', 'default'), {})

    def test_memories_both_devices_already_hold_start_waiting_on_the_peer(self):
        # Synced before this change: the peer holds them without the marker.
        self.store('default', ['old note', 'older note'], pending=True)
        self.store('peer', ['old note', 'older note'], pending=False)
        self.assertEqual(sync_delta.wanted(sync_delta.manifest('default'), sync_delta.manifest('peer'))['memories'], [])
        stats = self.send('default', 'peer')
        self.assertEqual(stats['backlog'], {'waiting': 2, 'skipped': 0})
        self.assertEqual(stats['memories']['inserted'] + stats['memories']['updated'], 0, 'no memory is sent again')
        self.assertEqual(tag_backlog.pending_count('peer'), 2)
        self.assertEqual(self.send('default', 'peer'), {})
        self.tag('peer')
        self.send('peer', 'default')
        self.assertEqual(tag_backlog.pending_count('default'), 0)

    def test_a_memory_classified_here_or_unknown_here_does_not_start_waiting(self):
        self.store('peer', ['tagged here', 'waits'], pending=True)
        client, coll = vs._ensure_collection('peer')
        rows, _ = client.scroll(coll, limit=10, with_payload=['_mid', 'content'])
        mids = {row.payload['content']: row.payload['_mid'] for row in rows}
        vs.set_classifications(client, coll, [(row.id, {'topics': ['x']}, 'finding')
                                              for row in rows if row.payload['content'] == 'tagged here'])
        client.set_payload(coll, payload={'tag_pending': False}, points=[row.id for row in rows])
        stats = tag_backlog.adopt([{'id': mids['tagged here']}, {'id': mids['waits']}, {'id': 'unknown'},
                                   {'id': 7}, 'junk'], 'peer')
        self.assertEqual(stats, {'waiting': 1, 'skipped': 4})
        self.assertEqual({text: bool(pl.get('tag_pending')) for text, pl in self.payloads('peer').items()},
                         {'tagged here': False, 'waits': True})

    def test_the_backlog_goes_only_to_a_device_that_offers_it(self):
        self.assertIn('backlog', sync_delta.EXTRAS)
        self.assertTrue(sync_delta.extras_offered()['backlog'])
        older = {'digest': 'a' * 64, 'core': True, 'placements': True, 'organize': True}
        self.assertNotIn('backlog', sync_delta.extras_of(older))
        # An older device's manifest has no backlog; it decodes to an empty one.
        decoded = sync_delta.decode(sync_delta.encode({'memories': {'m': 0.0}}))
        self.assertEqual(decoded['backlog'], {})

    def test_new_memories_wait_only_while_a_paired_device_tags(self):
        with patch.dict(os.environ, {'IMPRINT_LLM_TAGS': '0', 'IMPRINT_PEER_TAGS': '1'}):
            self.assertFalse(tag_backlog.marking('default'), 'no paired device')
            sync_pairs.save(pair(peer_tags=False))
            self.assertFalse(tag_backlog.marking('default'), 'the paired device does not tag')
            sync_pairs.update('pair-1', peer_tags=True)
            self.assertTrue(tag_backlog.marking('default'))
            self.assertFalse(tag_backlog.marking('peer'), 'another workspace is not paired')
            sync_pairs.update('pair-1', enabled=False)
            self.assertFalse(tag_backlog.marking('default'), 'a paused pairing')
            sync_pairs.update('pair-1', enabled=True)
        with patch.dict(os.environ, {'IMPRINT_LLM_TAGS': '0', 'IMPRINT_PEER_TAGS': '0'}):
            self.assertFalse(tag_backlog.marking('default'), 'tagger.peer off')
        with patch.dict(os.environ, {'IMPRINT_LLM_TAGS': '1', 'IMPRINT_PEER_TAGS': '0'}):
            self.assertTrue(tag_backlog.marking('default'), 'this device tags')

    def test_a_sync_says_whether_this_device_tags_and_remembers_whether_the_peer_does(self):
        record = pair()
        sync_pairs.save(record)
        remote = {'pair_id': 'pair-1', 'state': 'active', 'enabled': True, 'workspace': 'default'}
        with patch.dict(os.environ, {'IMPRINT_LLM_TAGS': '0'}):
            channel = Channel(remote, {'digest': 'a' * 64, 'tagging': True})
            asyncio.run(sync_pairs.exchange(channel, record, record['peer'], provider=True))
        self.assertIs(channel.sent[1]['tagging'], False)
        self.assertIs(sync_pairs.get('pair-1')['peer_tags'], True)
        # An older device says nothing: it is not known to tag.
        channel = Channel(remote, {'digest': 'a' * 64})
        asyncio.run(sync_pairs.exchange(channel, record, record['peer'], provider=True))
        self.assertIs(sync_pairs.get('pair-1')['peer_tags'], False)
