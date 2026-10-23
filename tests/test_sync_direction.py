import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

import test_indexing_retrieval as indexing
from imprint import cli_migrate, config, imprint_graph as kg, queue, sync_ws, vectorstore as vs
from imprint.sync_ws import SyncPolicy, parse_grant, plan_sync

BOTH, SEND, RECEIVE = (SyncPolicy.parse(direction) for direction in ('both', 'send', 'receive'))


class PolicyTests(unittest.TestCase):
    def test_direction_parsing_and_handshake_grant(self):
        self.assertEqual(BOTH.grant(), {'pull': True, 'push': True})
        self.assertEqual(SEND.grant(), {'pull': True, 'push': False})
        self.assertEqual(RECEIVE.grant(), {'pull': False, 'push': True})
        for bad in ('sideways', '', None, ['send'], 'SEND'):
            with self.assertRaises(ValueError):
                SyncPolicy.parse(bad)

    def test_grant_must_be_an_object_and_missing_phases_are_refused(self):
        for body in ('ok', None, '', ['pull']):
            with self.assertRaises(ValueError):
                parse_grant(body)
        self.assertEqual(parse_grant({'pull': True, 'push': False}), {'pull': True, 'push': False})
        self.assertEqual(parse_grant({'pull': True}), {'pull': True, 'push': False})
        self.assertEqual(parse_grant({'pull': 'yes', 'push': 1}), {'pull': False, 'push': False})

    def test_plan_is_the_intersection_with_reasons(self):
        cases = [
            (BOTH, BOTH, True, True, ()),
            (SEND, BOTH, True, False, ('push skipped (the provider is send-only)',)),
            (RECEIVE, BOTH, False, True, ('pull skipped (the provider is receive-only)',)),
            (BOTH, RECEIVE, True, False, ('push skipped (this machine is receive-only)',)),
            (BOTH, SEND, False, True, ('pull skipped (this machine is send-only)',)),
            (SEND, RECEIVE, True, False, ('push skipped (this machine is receive-only)',)),
        ]
        for provider, local, pull, push, skipped in cases:
            plan = plan_sync(provider.grant(), local)
            self.assertEqual((plan.pull, plan.push, plan.skipped), (pull, push, skipped))
        with self.assertRaisesRegex(ValueError, r'^nothing to sync: pull skipped \(this machine is send-only\), '
                                                r'push skipped \(the provider is send-only\)$'):
            plan_sync(SEND.grant(), SEND)
        with self.assertRaisesRegex(ValueError, r'^nothing to sync: pull skipped \(the provider is receive-only\), '
                                                r'push skipped \(this machine is receive-only\)$'):
            plan_sync(RECEIVE.grant(), RECEIVE)

    def test_migrate_copy_reaches_the_cli(self):
        args = queue.build_command_args('migrate', {'from': 'default', 'to': 'team', 'project': 'p', 'copy': True})
        self.assertEqual(args, ['--from', 'default', '--to', 'team', '--project', 'p', '--copy'])
        self.assertNotIn('--copy', queue.build_command_args('migrate', {'from': 'a', 'to': 'b', 'project': 'p'}))


class WorkspaceSyncStorageTests(unittest.TestCase):
    setUp = indexing.StorageTests.setUp
    cleanup = indexing.StorageTests.cleanup
    records = indexing.StorageTests.records

    def prepare(self):
        self.addCleanup(kg._close_all)
        env = patch.dict(os.environ, {'IMPRINT_WORKSPACE': ''})
        env.start()
        self.addCleanup(env.stop)
        vs.store_batch(self.records(['shared one', 'shared two'], project='shared', source='shared/a.md'))
        vs.store_batch(self.records(['private'], project='private', source='private/b.md'))
        kg.add('team', 'uses', 'qdrant', source='shared/a.md')

    def count(self, workspace, project=None):
        client, coll = vs._ensure_collection(workspace)
        flt = cli_migrate._build_filter(project, '', '') if project else None
        return client.count(coll, count_filter=flt, exact=True).count

    def test_export_and_import_use_the_explicit_workspace_not_the_active_one(self):
        self.prepare()
        config.register_workspace('team')
        with tempfile.NamedTemporaryFile('w', suffix='.jsonl', delete=False) as buffer:
            buffer.write('\n'.join(sync_ws.stream_export_lines('default')) + '\n')
        self.addCleanup(os.unlink, buffer.name)
        stats = sync_ws.process_import_buffer(buffer.name, 'team')
        self.assertEqual(stats['memories'], {'inserted': 3, 'skipped': 0})
        self.assertEqual(stats['facts'], {'inserted': 1, 'skipped': 0})
        self.assertEqual(self.count('team'), 3)
        self.assertEqual(kg.count(workspace='team'), 1)
        self.assertEqual(config.get_active_workspace(), 'default')

    def test_migrate_copy_keeps_source_and_repeats_without_duplicates(self):
        self.prepare()
        for _ in range(2):
            copied, scanned, facts = cli_migrate.migrate('default', 'team', project='shared', copy=True)
            self.assertEqual((copied, scanned), (2, 2))
        self.assertEqual(facts, 0)  # the second run finds the fact already present
        self.assertEqual(self.count('default', 'shared'), 2)
        self.assertEqual(self.count('team'), 2)
        self.assertEqual(kg.count(workspace='team'), 1)
        self.assertIn('team', config.get_known_workspaces())
        self.assertEqual(config.get_active_workspace(), 'default')

    def test_dry_run_changes_nothing_and_move_removes_source(self):
        self.prepare()
        cli_migrate.migrate('default', 'curated', project='shared', dry_run=True)
        self.assertNotIn('curated', config.get_known_workspaces())
        self.assertNotIn(config.collection_name('curated'), {c.name for c in self.client.get_collections().collections})
        self.assertFalse(config.graph_db_path('curated').exists())
        moved, _, _ = cli_migrate.migrate('default', 'curated', project='shared')
        self.assertEqual(moved, 2)
        self.assertEqual(self.count('default', 'shared'), 0)
        self.assertEqual(self.count('default', 'private'), 1)
        with self.assertRaisesRegex(ValueError, 'does not exist'):
            cli_migrate.migrate('nowhere', 'curated', project='shared')

    def test_http_sync_endpoints_validate_workspace_and_direction(self):
        from imprint.api import app
        self.prepare()

        async def requests():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:8420') as client:
                for query in ('direction=sideways', 'workspace=missing', 'workspace=Bad_Name', 'relay=bad%20host'):
                    response = await client.get(f'/api/sync/serve?{query}')
                    self.assertEqual(response.status_code, 400, query)
                body = {'room_id': 'room', 'pin': 'PIN'}
                for extra in ({'direction': 'up'}, {'workspace': 7}, {'workspace': 'missing', 'direction': 'send'},
                              {'room_id': 'host/room?x=1'}):
                    response = await client.post('/api/sync/receive', json={**body, **extra})
                    self.assertEqual(response.status_code, 400, extra)
        asyncio.run(requests())


if __name__ == '__main__':
    unittest.main()
