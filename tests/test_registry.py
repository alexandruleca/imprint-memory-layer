"""Skills and personas (ADR 0168): stored verbatim and validated, unique by
name, kept with this device's history, and synced to paired devices with
the newest change of each winning."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from imprint import config, registry, registry_files, sync_delta, sync_ws


def skill(name='deploy', description='Deploy the app to staging', body='Run make deploy.', **extra):
    return {'SKILL.md': f'---\nname: {name}\ndescription: {description}\n---\n\n{body}\n', **extra}


def persona(name='reviewer', description='Reviews diffs for bugs'):
    return {'PERSONA.md': f'---\nname: {name}\ndescription: {description}\ntools: Read, Grep\n---\nYou review code.\n'}


class RegistryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data = Path(directory.name)
        for patcher in (patch.object(config, 'get_data_dir', return_value=self.data),
                        patch.object(registry, '_device', return_value='device-a')):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_an_item_keeps_its_files_verbatim_and_reads_its_front_matter(self):
        files = skill(description='Deploy the app\n  to staging', **{'scripts/run.sh': 'echo hi\r\nexit 0\r\n'})
        item = registry.create('skill', files)
        self.assertEqual((item['kind'], item['name'], item['description'], item['state'], item['origin']),
                         ('skill', 'deploy', 'Deploy the app to staging', 'active', 'user'))
        self.assertEqual(item['files'], files)  # CRLF and all
        self.assertEqual(item['paths'], ['SKILL.md', 'scripts/run.sh'])
        self.assertEqual(item['hash'], registry.content_hash('skill', dict(reversed(list(files.items())))))
        self.assertNotIn('files', registry.list_all()[0])
        made = registry.create('persona', registry.compose('persona', 'helper', 'Helps: with "quotes"', 'Be kind.'))
        self.assertEqual((made['name'], made['description']), ('helper', 'Helps: with "quotes"'))

    def test_an_item_that_clients_could_not_read_is_refused(self):
        refused = {
            'no front matter': {'SKILL.md': 'just text'},
            'broken yaml': {'SKILL.md': '---\nname: [x\n---\nbody'},
            'a name clients reject': skill(name='Deploy App'),
            'a name too long': skill(name='a' * 65),
            'no description': {'SKILL.md': '---\nname: deploy\n---\nbody'},
            'no instructions': skill(body=''),
            'no SKILL.md': {'README.md': 'x'},
            'a path outside': skill(**{'../escape.sh': 'x'}),
            'an absolute path': skill(**{'/etc/passwd': 'x'}),
            'a drive path': skill(**{'C:/x.sh': 'x'}),
            'a data stream': skill(**{'notes/a.md:hidden': 'x'}),
            'an ignored folder': skill(**{'__pycache__/x.pyc': 'x'}),
            'bytes': skill(**{'logo.png': b'\x89PNG'}),
            'a file too large': skill(**{'big.txt': 'x' * (registry.MAX_FILE + 1)}),
        }
        for reason, files in refused.items():
            with self.subTest(reason), self.assertRaises(ValueError):
                registry.create('skill', files)
        with self.assertRaises(ValueError):
            registry.create('persona', {**persona(), 'extra.md': 'x'})
        with self.assertRaises(ValueError):
            registry.create('agent', persona())
        self.assertEqual(registry.list_all(), [])

    def test_a_credential_is_refused_rather_than_stored_redacted(self):
        with self.assertRaises(ValueError) as caught:
            registry.create('skill', skill(**{'scripts/env.sh': 'export AWS_KEY=AKIA' + 'IOSFODNN7EXAMPLE\n'}))
        self.assertIn('scripts/env.sh', str(caught.exception))
        self.assertIn('aws-access-key', str(caught.exception))
        self.assertNotIn('IOSFODNN7EXAMPLE', str(caught.exception))

    def test_a_name_is_held_by_one_item_of_each_kind_until_it_is_removed(self):
        first = registry.create('skill', skill())
        with self.assertRaises(ValueError):
            registry.create('skill', skill(body='Another'))
        registry.create('persona', persona(name='deploy'))
        with self.assertRaises(ValueError):
            registry.find('deploy')  # Ambiguous between the kinds.
        self.assertEqual(registry.find('deploy', 'skill')['id'], first['id'])
        registry.update(first['id'], state='retired')
        second = registry.create('skill', skill(body='Again'))
        with self.assertRaises(ValueError):
            registry.update(first['id'], state='active')  # Its name is taken now.
        self.assertEqual([i['id'] for i in registry.active('skill')], [second['id']])

    def test_changes_keep_the_replaced_version_in_history_and_one_can_be_restored(self):
        item = registry.create('skill', skill(body='One'))
        for body in ('Two', 'Three'):
            registry.update(item['id'], files=skill(body=body))
        unchanged = registry.update(item['id'], files=skill(body='Three'))
        self.assertEqual(unchanged['updated_at'], registry.get(item['id'])['updated_at'])
        versions = registry.history(item['id'])
        self.assertEqual([v['files']['SKILL.md'].split('\n\n')[1].strip() for v in versions], ['Two', 'One'])
        restored = registry.restore(item['id'], versions[1]['hash'])
        self.assertIn('One', restored['files']['SKILL.md'])
        self.assertGreater(restored['updated_at'], item['updated_at'])
        for index in range(registry.HISTORY + 3):
            registry.update(item['id'], files=skill(body=f'v{index}'))
        self.assertEqual(len(registry.history(item['id'])), registry.HISTORY)
        with self.assertRaises(KeyError):
            registry.restore(item['id'], 'f' * 64)
        with self.assertRaises(KeyError):
            registry.update('0' * 32, state='disabled')

    def test_an_edit_dated_before_the_last_change_is_still_dated_after_it(self):
        item = registry.create('skill', skill())
        edited = registry.update(item['id'], files=skill(body='Edited'), at=item['updated_at'] - 100)
        self.assertGreater(edited['updated_at'], item['updated_at'])

    def test_sync_keeps_the_newest_change_of_each(self):
        mine = registry.export([registry.create('skill', skill())['id']])[0]
        newer = {**mine, 'files': skill(body='Newer'), 'updated_at': mine['updated_at'] + 10, 'device': 'device-b'}
        older = {**mine, 'files': skill(body='Older'), 'updated_at': mine['updated_at'] - 10}
        theirs = {**mine, 'id': 'f' * 32, 'kind': 'persona', 'files': persona()}
        broken = [{**mine, 'id': '../x'}, {**mine, 'id': 'e' * 32, 'state': 'deleted'},
                  {**mine, 'id': 'd' * 32, 'kind': 'agent'}, {'id': 'c' * 32},
                  {**mine, 'id': 'b' * 32, 'files': skill(**{'k.sh': 'AKIA' + 'IOSFODNN7EXAMPLE'})}]
        self.assertEqual(registry.import_records([older]), {'inserted': 0, 'updated': 0, 'skipped': 1})
        self.assertEqual(registry.import_records([newer, theirs, *broken]), {'inserted': 1, 'updated': 1, 'skipped': 5})
        self.assertIn('Newer', registry.get(mine['id'])['files']['SKILL.md'])
        self.assertEqual(registry.get(mine['id'])['device'], 'device-b')
        self.assertEqual([v['hash'] for v in registry.history(mine['id'])], [mine['hash']])
        self.assertEqual(registry.manifest(), {mine['id']: newer['updated_at'], 'f' * 32: theirs['updated_at']})

    def test_devices_that_made_the_same_name_before_they_synced_agree_on_its_holder(self):
        base = {'kind': 'skill', 'state': 'active', 'origin': 'user', 'device': 'x', 'updated_at': 5.0}
        later = {**base, 'id': 'a' * 32, 'files': skill(body='Later'), 'created_at': 2.0}
        earlier = {**base, 'id': 'b' * 32, 'files': skill(body='Earlier'), 'created_at': 1.0}
        registry.import_records([later, earlier])
        self.assertEqual([i['id'] for i in registry.active('skill')], ['b' * 32])
        shadowed = {i['id']: i['shadowed'] for i in registry.list_all()}
        self.assertEqual(shadowed, {'a' * 32: True, 'b' * 32: False})

    def test_skills_travel_only_to_a_device_that_offers_them(self):
        self.assertIn('registry', sync_delta.extras_of({'registry': True}))
        self.assertNotIn('registry', sync_delta.extras_of({'core': True, 'core_tiers': True}))
        self.assertTrue(sync_delta.extras_offered()['registry'])
        item = registry.create('skill', skill())
        chunks = list(sync_delta.export_chunks('ws', {'registry': [item['id']]}))
        self.assertEqual(len(chunks), 1)
        frames = [json.loads(line) for line in chunks[0]]
        self.assertEqual(frames[0]['datasets'], {'memories': 0, 'facts': 0, 'registry': 1})
        self.assertTrue(all(sync_ws._stream_line_ok(frame, index > 0) for index, frame in enumerate(frames)))
        # Received on another device, it is stored and written to its clients.
        registry.update(item['id'], files=skill(body='Changed here'))
        buffer = self.data / 'stream.jsonl'
        buffer.write_text('\n'.join(chunks[0]))
        with patch.object(registry_files, 'apply_quietly') as applied:
            stats = sync_ws.process_import_buffer(str(buffer), 'ws')
        self.assertEqual(stats['registry'], {'inserted': 0, 'updated': 0, 'skipped': 1})  # The change here is newer.
        applied.assert_not_called()
        registry.update(item['id'], state='retired')
        record = registry.export([item['id']])[0]
        buffer.write_text('\n'.join(sync_delta._stream(registry=[{**record, 'id': '9' * 32, 'state': 'active'}])))
        with patch.object(registry_files, 'apply_quietly') as applied:
            stats = sync_ws.process_import_buffer(str(buffer), 'ws')
        self.assertEqual(stats['registry'], {'inserted': 1, 'updated': 0, 'skipped': 0})
        applied.assert_called_once()

    def test_large_items_travel_in_streams_of_bounded_size(self):
        ids = [registry.create('skill', skill(name=f's{i}', **{'big.txt': 'x' * 200_000}))['id'] for i in range(6)]
        with patch.object(sync_delta, 'REGISTRY_CHUNK_BYTES', 450_000):
            chunks = list(sync_delta.export_chunks('ws', {'registry': ids}))
        self.assertEqual([json.loads(c[0])['datasets']['registry'] for c in chunks], [2, 2, 2])


if __name__ == '__main__':
    unittest.main()
