"""Placement rules travel between paired devices, and each device moves its
own copies by them (ADR 0134)."""
import asyncio
import json
from pathlib import Path
from unittest.mock import patch

from imprint import config, placement_sync, projects, source_snapshots, sync_delta, sync_ws, vectorstore as vs
from imprint import workspace_policy as policy
from test_projects import ProjectStoreTests


class PlacementSyncTests(ProjectStoreTests):
    def setUp(self):
        super().setUp()
        config.register_workspace('peer')
        for workspace in ('default', 'peer'):
            vs.replace_source(self.records(['scanned form', 'second page']), 'Desktop/a.pdf', 'Desktop',
                              workspace=workspace)
            vs.replace_source(self.records(['unrelated'], source='Desktop/b.pdf'), 'Desktop/b.pdf', 'Desktop',
                              workspace=workspace)

    def send(self, source='default', target='peer'):
        """What one sync sends of the rules, as the delta sync streams it."""
        keys = sync_delta.wanted(sync_delta.manifest(source), sync_delta.manifest(target))
        streams = list(sync_delta.export_chunks(source, {'placements': keys['placements']}))
        buffer = Path(self.data.name) / 'rules.jsonl'
        stats = {}
        for stream in streams:
            buffer.write_text('\n'.join(stream))
            stats = sync_ws.process_import_buffer(str(buffer), target)
        return stats

    def test_a_move_reaches_the_peer_and_moves_its_copies(self):
        projects.move('Desktop', 'branding', ['Desktop/a.pdf'], workspace='default', apply=True)
        stats = self.send()
        self.assertEqual(stats['placements'], {'inserted': 1, 'updated': 0, 'skipped': 0, 'moved': 2})
        self.assertEqual(self.rules('peer'), {('Desktop', 'Desktop/a.pdf'): 'branding'})
        self.assertEqual(self.placed('peer'), self.placed())
        self.assertEqual(self.ids('peer'), self.ids())
        # Nothing newer to send now.
        self.assertEqual(self.send(), {})

    def test_a_source_snapshot_moves_with_its_memories(self):
        from imprint.ingest_core import store_inline_content
        for workspace in ('default', 'peer'):
            store_inline_content('Meeting notes about the branding refresh. ' * 5, 'Desktop/notes.md',
                                 project='Desktop', workspace=workspace)
        self.assertIn(('Desktop', 'Desktop/notes.md'),
                      {(m['project'], m['source']) for m in source_snapshots.list_metadata('peer')})
        projects.move('Desktop', 'branding', ['Desktop/notes.md'], workspace='default', apply=True)
        self.send()
        snapshots = {(m['project'], m['source']) for m in source_snapshots.list_metadata('peer')}
        self.assertIn(('branding', 'Desktop/notes.md'), snapshots)
        self.assertNotIn(('Desktop', 'Desktop/notes.md'), snapshots)
        snapshot = source_snapshots.get('Desktop/notes.md', 'branding', 'peer')
        self.assertTrue(source_snapshots.integrity(snapshot, 'peer')['complete'])

    def test_moving_back_travels_as_a_removed_rule(self):
        projects.move('Desktop', 'branding', ['Desktop/a.pdf'], workspace='default', apply=True)
        self.send()
        projects.move('branding', 'Desktop', ['Desktop/a.pdf'], workspace='default', apply=True)
        manifest = placement_sync.manifest('default')
        self.assertEqual(list(manifest), [json.dumps(['Desktop', 'Desktop/a.pdf'])])
        self.assertEqual(placement_sync.export('default', manifest)[0]['target'], '')
        self.assertEqual(self.rules(), {})
        self.assertEqual(self.send()['placements']['moved'], 2)
        self.assertEqual(self.rules('peer'), {})
        self.assertEqual({project for project, _, _ in self.placed('peer')}, {'Desktop'})

    def test_changes_within_one_clock_tick_still_reach_the_peer(self):
        # Windows' clock can give a move and a quick move back one time.
        with patch('time.time', return_value=1000.0):
            projects.move('Desktop', 'branding', ['Desktop/a.pdf'], workspace='default', apply=True)
            self.send()
            projects.move('branding', 'Desktop', ['Desktop/a.pdf'], workspace='default', apply=True)
            self.assertEqual(self.send()['placements']['updated'], 1)
        self.assertEqual(self.rules('peer'), {})

    def test_the_newer_rule_wins_and_malformed_ones_are_skipped(self):
        projects.move('Desktop', 'branding', ['Desktop/a.pdf'], workspace='peer', apply=True)
        with policy._db() as conn:
            held = conn.execute("SELECT updated_at FROM placements WHERE workspace='peer'").fetchone()[0]
        older = {'origin': 'Desktop', 'source': 'Desktop/a.pdf', 'target': 'archive', 'updated_at': held - 10}
        stats = placement_sync.import_records([older, {'origin': 'Desktop'}, 'rule',
                                               {**older, 'target': ' spaced ', 'updated_at': held + 10},
                                               {**older, 'origin': '', 'source': '', 'updated_at': held + 10}], 'peer')
        self.assertEqual((stats['skipped'], stats['inserted'], stats['updated']), (5, 0, 0))
        stats = placement_sync.import_records([{**older, 'updated_at': held + 10}], 'peer')
        self.assertEqual((stats['updated'], stats['moved']), (1, 2))
        self.assertEqual(self.rules('peer'), {('Desktop', 'Desktop/a.pdf'): 'archive'})
        self.assertIn(('archive', 'Desktop/a.pdf', 'scanned form'), self.placed('peer'))

    def test_a_busy_queue_applies_the_rules_after_its_job(self):
        record = {'origin': 'Desktop', 'source': 'Desktop/a.pdf', 'target': 'branding', 'updated_at': 5.0}
        with patch('imprint.queue_lock.acquire', return_value=None), \
                patch('imprint.queue.submit', return_value={'job_id': 'j', 'merged': False}) as submit:
            stats = placement_sync.import_records([record], 'peer')
        self.assertEqual(stats['moved'], 0)
        submit.assert_called_once_with('apply-placements', {'workspace': 'peer'})
        # The rule already places what arrives; the job moves what was there.
        with patch.dict('os.environ', {'IMPRINT_WORKSPACE': 'peer'}):
            self.assertEqual(placement_sync.main([]), 0)
        self.assertIn(('branding', 'Desktop/a.pdf', 'scanned form'), self.placed('peer'))
        from imprint import queue as job_queue
        self.assertEqual(job_queue._build_argv('apply-placements', {})[1:], ['-m', 'imprint.placement_sync'])

    def test_rules_and_proposals_go_only_to_a_peer_that_offers_them(self):
        sent = []

        async def send(channel, workspace, keys, cancel):
            sent.append(keys)
            yield {'type': 'progress'}

        class Channel:
            async def send(self, frame):
                pass

            async def recv(self, timeout=None):
                return {'kind': 'manifest', 'data': sync_delta.encode({'memories': {}, 'facts': {}, 'sources': {}})}

        local = {'memories': {}, 'facts': {}, 'sources': {}, 'core': {}, 'placements': {'["o", ""]': 1.0},
                 'organize': {'["o", "s"]': 2.0}}

        async def run(extras):
            async for _ in sync_delta.reconcile(Channel(), 'ws', local, provider=True, provider_sends=True,
                                                consumer_sends=False, cancel=asyncio.Event(), result={}, extras=extras):
                pass

        with patch.object(sync_delta, '_send', send):
            asyncio.run(run(frozenset()))
            asyncio.run(run(sync_delta.extras_of({'core': True, 'placements': True, 'organize': True, 'other': True})))
        self.assertEqual([(keys['placements'], keys['organize']) for keys in sent],
                         [([], []), (['["o", ""]'], ['["o", "s"]'])])
        self.assertEqual(sync_delta.extras_offered(), {'core': True, 'placements': True, 'organize': True, 'backlog': True,
                                                       'registry': True, 'recordings': False, 'core_tiers': True, 'deletions': True})
        # An importer checks the stream's datasets.
        self.assertTrue(sync_ws._stream_line_ok({'kind': 'meta', 'vectors': True, 'datasets': {'placements': 1}}, False))
        self.assertFalse(sync_ws._stream_line_ok({'kind': 'meta', 'vectors': True, 'datasets': {'other': 1}}, False))


if __name__ == '__main__':
    import unittest
    unittest.main()
