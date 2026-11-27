"""Built-in skills (ADR 0213): shipped with each release, listed with the
origin ``builtin`` and written into the clients like the user's items,
read-only and never synced. A fork, the user's own skill of the same name,
replaces a built-in on every device; a built-in can be disabled, and that
choice syncs."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from imprint import config, registry, registry_files

from tests.test_registry import skill

ROOT = Path(__file__).resolve().parents[1]


class BuiltinSkillTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.data = root / 'data'
        self.home = root / 'home'
        self.builtins = root / 'release' / 'builtin_skills'
        for patcher in (patch.object(config, 'get_data_dir', return_value=self.data),
                        patch.object(registry, '_device', return_value='device-a'),
                        patch.object(registry, 'BUILTINS', self.builtins)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.ship(skill(name='board', description='Use the task board', body='Take a ready task.',
                        **{'reference/states.md': 'ready, claimed, done\n'}))
        self.claude_skills = self.home / '.claude' / 'skills'
        self.shared = self.home / '.agents' / 'skills'
        self.clients = [{'name': 'claude-code', 'label': 'Claude Code', 'connected': True,
                         'skills': [str(self.claude_skills)], 'personas': [str(self.home / '.claude' / 'agents')]},
                        {'name': 'codex', 'label': 'Codex', 'connected': True, 'skills': [str(self.shared)],
                         'personas': [str(self.home / '.codex' / 'agents')], 'persona_format': 'codex'}]

    def ship(self, files):
        """Put a built-in into the release's folder, as a release would."""
        name = registry.front_matter(files['SKILL.md'])[0]['name']
        folder = self.builtins / name
        shutil.rmtree(folder, ignore_errors=True)
        for path, text in files.items():
            (folder / path).parent.mkdir(parents=True, exist_ok=True)
            (folder / path).write_text(text, newline='')
        return folder

    def apply(self):
        return registry_files.apply(self.clients)

    def manifest(self, folder):
        return json.loads((folder / registry_files.MANIFEST).read_text())['items']

    def builtin(self, name='board'):
        return next(i for i in registry.list_all(with_files=True) if i['origin'] == 'builtin' and i['name'] == name)

    def test_a_built_in_is_listed_with_the_origin_builtin_without_making_a_store(self):
        listed = registry.list_all()
        self.assertEqual([(i['kind'], i['name'], i['origin'], i['state'], i['shadowed']) for i in listed],
                         [('skill', 'board', 'builtin', 'active', False)])
        item = listed[0]
        self.assertRegex(item['id'], r'^[0-9a-f]{32}$')
        self.assertEqual(item['description'], 'Use the task board')
        self.assertEqual(item['paths'], ['SKILL.md', 'reference/states.md'])
        self.assertNotIn('files', item)
        self.assertEqual(registry.get(item['id'])['files']['reference/states.md'], 'ready, claimed, done\n')
        self.assertEqual(registry.find('board')['id'], item['id'])
        self.assertEqual([i['id'] for i in registry.active('skill')], [item['id']])
        self.assertFalse(registry.exists())  # Listing a built-in stores nothing.
        self.assertEqual(registry.manifest(), {})

    def test_a_built_in_reaches_the_clients_folders_marked_built_in(self):
        self.assertTrue(registry_files.needed())  # A release's built-ins alone are reason to write.
        report = self.apply()
        board = self.builtin()
        for folder in (self.claude_skills, self.shared):
            self.assertEqual((folder / 'board' / 'reference' / 'states.md').read_text(), 'ready, claimed, done\n')
            self.assertEqual(self.manifest(folder), {'board': {'id': board['id'], 'hash': board['hash'], 'builtin': True}})
        self.assertEqual(report['problems'], [])
        again = self.apply()
        self.assertEqual((again['written'], again['removed'], again['taken']), ([], [], []))
        self.assertTrue(self.manifest(self.shared)['board']['builtin'])
        # A release that changes the built-in rewrites it; one that drops it removes it.
        self.ship(skill(name='board', description='Use the task board', body='Take the oldest ready task.'))
        self.apply()
        self.assertIn('oldest', (self.claude_skills / 'board' / 'SKILL.md').read_text())
        self.assertFalse((self.claude_skills / 'board' / 'reference').exists())
        shutil.rmtree(self.builtins / 'board')
        self.assertEqual(sorted(self.apply()['removed']), sorted(str(f / 'board') for f in (self.claude_skills, self.shared)))

    def test_a_built_in_is_read_only_and_points_to_forking(self):
        board = self.builtin()
        with self.assertRaisesRegex(ValueError, r'built-in.*imprint skills fork board'):
            registry.update(board['id'], files=skill(name='board', body='Mine now.'))
        with self.assertRaisesRegex(ValueError, 'cannot be removed'):
            registry.update(board['id'], state='retired')
        unchanged = registry.update(board['id'], files=board['files'])  # Its own files are no change.
        self.assertEqual((unchanged['hash'], unchanged['updated_at']), (board['hash'], 0.0))
        with self.assertRaises(KeyError):
            registry.restore(board['id'], board['hash'])
        self.assertEqual(registry.history(board['id']), [])

    def test_a_built_in_can_be_disabled_and_that_choice_syncs(self):
        board = self.builtin()
        self.apply()
        off = registry.update(board['id'], state='disabled')
        self.assertEqual((off['state'], off['origin'], off['device']), ('disabled', 'builtin', 'device-a'))
        self.assertEqual(registry.active('skill'), [])
        self.apply()
        self.assertFalse((self.claude_skills / 'board').exists())
        # The choice is a record under the built-in's id; the built-in's files never travel.
        self.assertEqual(registry.manifest(), {board['id']: off['updated_at']})
        [record] = registry.export([board['id']])
        self.assertEqual({k: record[k] for k in ('id', 'kind', 'name', 'state', 'origin', 'files')},
                         {'id': board['id'], 'kind': 'skill', 'name': 'board', 'state': 'disabled',
                          'origin': 'builtin', 'files': {}})
        self.assertNotIn('Take a ready task', json.dumps(record))
        # Another device takes the newest choice, under the same id.
        other = self.data.parent / 'other'
        with patch.object(config, 'get_data_dir', return_value=other):
            self.assertEqual(registry.import_records([record]), {'inserted': 1, 'updated': 0, 'skipped': 0})
            self.assertEqual(self.builtin()['state'], 'disabled')
            again = registry.update(board['id'], state='active')
            newer = registry.export([board['id']])[0]
        self.assertGreater(again['updated_at'], off['updated_at'])
        self.assertEqual(registry.import_records([record]), {'inserted': 0, 'updated': 0, 'skipped': 1})  # Not newer.
        self.assertEqual(registry.import_records([newer]), {'inserted': 0, 'updated': 1, 'skipped': 0})
        self.assertEqual(self.builtin()['state'], 'active')

    def test_the_disable_choice_travels_in_the_registry_stream(self):
        from imprint import sync_delta, sync_ws
        board = self.builtin()
        registry.update(board['id'], state='disabled')
        [chunk] = list(sync_delta.export_chunks('ws', {'registry': list(registry.manifest())}))
        frames = [json.loads(line) for line in chunk]
        self.assertEqual(frames[0]['datasets']['registry'], 1)
        self.assertTrue(all(sync_ws._stream_line_ok(frame, index > 0) for index, frame in enumerate(frames)))
        other = self.data.parent / 'other'
        other.mkdir()
        (other / 'stream.jsonl').write_text('\n'.join(chunk))
        with patch.object(config, 'get_data_dir', return_value=other), \
                patch.object(registry_files, 'apply_quietly') as applied:
            stats = sync_ws.process_import_buffer(str(other / 'stream.jsonl'), 'ws')
            self.assertEqual(self.builtin()['state'], 'disabled')
        self.assertEqual(stats['registry'], {'inserted': 1, 'updated': 0, 'skipped': 0})
        applied.assert_called_once()  # Its clients lose the built-in at once.

    def test_built_ins_never_travel_in_the_registry_dataset(self):
        board = self.builtin()
        mine = registry.create('skill', skill(name='deploy'))
        self.assertEqual(set(registry.manifest()), {mine['id']})
        self.assertEqual([r['id'] for r in registry.export([board['id'], mine['id']])], [mine['id']])
        # A record that claims to be a built-in carries no files into the registry.
        forged = {'id': board['id'], 'kind': 'skill', 'name': 'board', 'state': 'active', 'origin': 'builtin',
                  'device': 'device-b', 'created_at': 1.0, 'updated_at': 2.0, 'hash': '',
                  'files': skill(name='board', body='Do something else.'), 'description': ''}
        registry.import_records([forged])
        self.assertIn('Take a ready task', self.builtin()['files']['SKILL.md'])
        bad = [{**forged, 'id': 'f' * 32}, {**forged, 'name': 'Bad Name'}, {**forged, 'state': 'retired'}]
        self.assertEqual(registry.import_records(bad)['skipped'], 3)

    def test_a_fork_is_the_users_own_skill_and_replaces_the_built_in(self):
        board = self.builtin()
        self.apply()
        fork = registry.fork('board')
        self.assertEqual((fork['kind'], fork['name'], fork['origin'], fork['state']), ('skill', 'board', 'user', 'active'))
        self.assertEqual(fork['files'], board['files'])
        self.assertNotEqual(fork['id'], board['id'])
        self.assertEqual((fork['forked_from'], fork['fork_of'], fork['builtin_changed']), (board['hash'], board['id'], False))
        self.assertEqual([i['id'] for i in registry.active('skill')], [fork['id']])
        listed = self.builtin()
        self.assertEqual((listed['shadowed'], listed['fork']), (True, fork['id']))
        self.assertEqual(registry.find('board')['id'], fork['id'])
        # The fork travels to paired devices, remembering what it was forked from.
        [record] = registry.export([fork['id']])
        self.assertEqual(record['forked_from'], board['hash'])
        # The clients get the fork, which edits now reach.
        registry.update(fork['id'], files=skill(name='board', description='Use the task board', body='Mine now.'))
        self.apply()
        for folder in (self.claude_skills, self.shared):
            self.assertIn('Mine now.', (folder / 'board' / 'SKILL.md').read_text())
            self.assertEqual(self.manifest(folder)['board']['id'], fork['id'])
            self.assertNotIn('builtin', self.manifest(folder)['board'])
        with self.assertRaisesRegex(ValueError, 'already forked'):
            registry.fork('board')
        with self.assertRaises(KeyError):
            registry.fork('nothing-shipped')

    def test_a_skill_of_the_users_that_already_has_the_name_counts_as_its_fork(self):
        board = self.builtin()
        mine = registry.create('skill', skill(name='board', description='My own board', body='My way.'))
        self.assertEqual((mine['fork_of'], mine['forked_from']), (board['id'], board['hash']))
        self.assertEqual([i['id'] for i in registry.active('skill')], [mine['id']])
        # One a paired device sent, made before this release, too.
        record = {**registry.export([mine['id']])[0], 'id': 'a' * 32, 'name': 'board', 'created_at': 1.0}
        del record['forked_from']  # Sent by a device whose Imprint predates built-ins.
        registry.update(mine['id'], state='retired')
        registry.import_records([record])
        self.assertEqual((registry.get('a' * 32)['fork_of'], registry.get('a' * 32)['forked_from']), (board['id'], ''))
        self.assertEqual([i['id'] for i in registry.active('skill')], ['a' * 32])

    def test_reset_removes_the_fork_and_the_built_in_returns(self):
        board = self.builtin()
        fork = registry.fork('board')
        registry.update(fork['id'], files=skill(name='board', description='Use the task board', body='Mine now.'))
        self.apply()
        back = registry.reset('board')
        self.assertEqual((back['id'], back['origin'], back['shadowed'], back['fork']), (board['id'], 'builtin', False, ''))
        self.assertEqual(registry.get(fork['id'])['state'], 'retired')  # Removed on every paired device.
        self.assertEqual([i['id'] for i in registry.active('skill')], [board['id']])
        self.apply()
        self.assertIn('Take a ready task.', (self.claude_skills / 'board' / 'SKILL.md').read_text())
        self.assertTrue(self.manifest(self.claude_skills)['board']['builtin'])
        with self.assertRaisesRegex(ValueError, 'not forked'):
            registry.reset('board')
        with self.assertRaises(KeyError):
            registry.reset('nothing-shipped')
        # Two forks made on two devices before they synced both go.
        registry.import_records([{**registry.export([fork['id']])[0], 'id': id_, 'state': 'active', 'updated_at': 9e9,
                                  'created_at': n} for n, id_ in enumerate(('b' * 32, 'c' * 32))])
        registry.reset('board')
        self.assertEqual([i['id'] for i in registry.active('skill')], [board['id']])

    def test_a_release_that_changes_a_forked_built_in_is_noticed_with_the_difference(self):
        fork = registry.fork('board')
        self.assertEqual(registry.difference(fork['id']), '')
        registry.update(fork['id'], files=skill(name='board', description='Use the task board', body='Mine now.',
                                                **{'reference/states.md': 'ready, claimed, done\n'}))
        self.assertFalse(registry.get(fork['id'])['builtin_changed'])  # The user's own edits are no release change.
        self.ship(skill(name='board', description='Use the task board', body='Take the oldest ready task.',
                        **{'reference/states.md': 'ready, claimed, blocked, done\n', 'reference/new.md': 'New.\n'}))
        changed = registry.get(fork['id'])
        self.assertTrue(changed['builtin_changed'])
        self.assertEqual(changed['forked_from'], fork['forked_from'])
        diff = registry.difference(fork['id'])
        for line in ('--- fork/SKILL.md', '+++ built-in/SKILL.md', '-Mine now.', '+Take the oldest ready task.',
                     '+++ built-in/reference/new.md', '+ready, claimed, blocked, done'):
            self.assertIn(line, diff.splitlines())
        with self.assertRaises(KeyError):
            registry.difference(self.builtin()['id'])  # Only a fork has a difference from its built-in.
        # Nothing is merged: the fork stays as the user wrote it until they reset it.
        self.assertIn('Mine now.', registry.get(fork['id'])['files']['SKILL.md'])

    def test_an_edit_made_in_a_client_to_a_built_in_becomes_its_fork(self):
        board = self.builtin()
        self.apply()
        (self.claude_skills / 'board' / 'SKILL.md').write_text(
            skill(name='board', description='Use the task board', body='Edited in Claude Code.')['SKILL.md'])
        report = self.apply()
        self.assertEqual(report['taken'], [str(self.claude_skills / 'board')])
        self.assertEqual(report['problems'], [])
        fork = registry.find('board')
        self.assertEqual((fork['origin'], fork['fork_of'], fork['forked_from']), ('user', board['id'], board['hash']))
        self.assertIn('Edited in Claude Code.', fork['files']['SKILL.md'])
        self.assertIn('Edited in Claude Code.', (self.shared / 'board' / 'SKILL.md').read_text())
        self.assertEqual(self.manifest(self.claude_skills)['board'], {'id': fork['id'], 'hash': fork['hash']})
        self.assertEqual(self.apply()['written'], [])

    def test_a_store_made_before_built_ins_gains_the_forks_column(self):
        import sqlite3
        self.data.mkdir(parents=True)
        conn = sqlite3.connect(self.data / 'registry.sqlite3')
        conn.execute('CREATE TABLE items (id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL, '
                     'description TEXT NOT NULL, files TEXT NOT NULL, hash TEXT NOT NULL, state TEXT NOT NULL, '
                     'origin TEXT NOT NULL, device TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL)')
        files = skill(name='deploy')
        conn.execute('INSERT INTO items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                     ('d' * 32, 'skill', 'deploy', 'Deploy the app to staging', json.dumps(files),
                      registry.content_hash('skill', files), 'active', 'user', 'device-a', 1.0, 1.0))
        conn.commit()
        conn.close()
        self.assertEqual(registry.get('d' * 32)['forked_from'], '')
        self.assertEqual(registry.fork('board')['fork_of'], self.builtin()['id'])
        self.assertEqual(sorted(i['name'] for i in registry.active('skill')), ['board', 'deploy'])

    def test_the_mcp_tools_list_built_ins_and_skill_edit_points_to_forking(self):
        from imprint import skills_tool
        with patch.object(registry_files, '_clients', return_value=self.clients), \
                patch.dict(os.environ, {'IMPRINT_WSL_DISTRO': ''}):
            self.assertIn('- board: Use the task board', skills_tool.index('claude-ai'))
            self.assertIn('skill board (2 files) [built-in]: Use the task board', skills_tool.listing())
            self.assertTrue(skills_tool.read('board').startswith('skill board (active, built-in), SKILL.md'))
            self.assertFalse(registry.exists())  # Reading built-ins makes no store.
            for change in ({'instructions': 'Mine.'}, {'description': 'Mine'}, {'files': {'notes.md': 'x'}}):
                with self.subTest(change), self.assertRaisesRegex(ValueError, r'read-only.*imprint skills fork board'):
                    skills_tool.edit('board', **change)
            self.assertIn('Disabled', skills_tool.edit('board', state='disabled'))
            self.assertEqual(self.builtin()['state'], 'disabled')
            self.assertNotIn('board', skills_tool.index('claude-ai'))
            skills_tool.edit('board', state='active')
            # A skill made over MCP with a built-in's name is its fork, and edits reach it.
            said = skills_tool.create('skill', 'board', 'My board', 'My way.')
            self.assertIn('It is the fork of the built-in skill board', said)
            listed = skills_tool.listing()
            self.assertIn('skill board [fork of the built-in]: My board', listed)
            self.assertIn('skill board (2 files) [built-in, replaced by your fork]: Use the task board', listed)
            self.assertIn('Changed skill board', skills_tool.edit('board', instructions='My new way.'))
            self.assertIn('My new way.', (self.claude_skills / 'board' / 'SKILL.md').read_text())

    def test_the_dashboard_marks_built_ins_and_forks_resets_and_disables_them(self):
        from fastapi.testclient import TestClient
        from imprint.api import app
        client = TestClient(app, base_url='http://127.0.0.1:8420', client=('127.0.0.1', 1234))
        board = self.builtin()
        with patch.object(registry_files, '_clients', return_value=self.clients):
            [listed] = client.get('/api/skills').json()['items']
            self.assertEqual((listed['origin'], listed['fork'], listed['state']), ('builtin', '', 'active'))
            detail = client.get(f"/api/skills/{board['id']}").json()
            self.assertEqual((detail['files']['reference/states.md'], detail['history'], detail['difference']),
                             ('ready, claimed, done\n', [], ''))
            refused = client.patch(f"/api/skills/{board['id']}", json={'files': skill(name='board', body='Mine.')})
            self.assertEqual(refused.status_code, 400)
            self.assertIn('Fork it', refused.json()['error'])
            self.assertEqual(client.patch(f"/api/skills/{board['id']}", json={'state': 'retired'}).status_code, 400)
            off = client.patch(f"/api/skills/{board['id']}", json={'state': 'disabled'})
            self.assertEqual((off.status_code, off.json()['state']), (200, 'disabled'))
            client.patch(f"/api/skills/{board['id']}", json={'state': 'active'})

            forked = client.post(f"/api/skills/{board['id']}/fork")
            self.assertEqual(forked.status_code, 200, forked.text)
            fork = forked.json()
            self.assertEqual((fork['origin'], fork['fork_of'], fork['report']['problems']), ('user', board['id'], []))
            self.assertEqual(self.manifest(self.claude_skills)['board']['id'], fork['id'])
            self.assertEqual(client.post(f"/api/skills/{board['id']}/fork").status_code, 400)  # Already forked.
            self.assertEqual(client.post(f"/api/skills/{fork['id']}/fork").status_code, 404)  # Not a built-in.
            self.ship(skill(name='board', description='Use the task board', body='Take the oldest ready task.'))
            detail = client.get(f"/api/skills/{fork['id']}").json()
            self.assertTrue(detail['builtin_changed'])
            self.assertIn('+Take the oldest ready task.', detail['difference'].splitlines())

            reset = client.post(f"/api/skills/{fork['id']}/reset")  # From the fork's page, or the built-in's.
            self.assertEqual(reset.status_code, 200, reset.text)
            self.assertEqual((reset.json()['id'], reset.json()['fork']), (board['id'], ''))
            self.assertIn('oldest', (self.claude_skills / 'board' / 'SKILL.md').read_text())
            self.assertEqual(client.post(f"/api/skills/{board['id']}/reset").status_code, 400)  # Not forked.
            self.assertEqual(client.post(f"/api/skills/{'0' * 32}/reset").status_code, 404)

    def test_a_built_in_that_clients_could_not_read_is_left_out(self):
        broken = self.builtins / 'broken'
        broken.mkdir(parents=True)
        (broken / 'SKILL.md').write_text('no front matter')
        misnamed = self.ship(skill(name='misnamed'))
        misnamed.rename(self.builtins / 'other-folder')
        with self.assertLogs('imprint.registry', 'WARNING'):
            self.assertEqual([i['name'] for i in registry.list_all()], ['board'])


class CommandLineTests(unittest.TestCase):
    """``imprint skills`` as a process, with the release's own built-ins."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        home = root / 'home'
        home.mkdir()
        # No real client profile is reached: every place the clients table looks is under this home.
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ('CODEX_HOME', 'CLAUDE_CONFIG_DIR', 'COPILOT_HOME', 'OPENCODE_CONFIG_DIR',
                                 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'WSL_DISTRO_NAME', 'IMPRINT_WSL_DISTRO')}
        self.env.update(PYTHONPATH=str(ROOT), IMPRINT_DATA_DIR=str(root / 'data'), HOME=str(home),
                        USERPROFILE=str(home), APPDATA=str(home / 'AppData' / 'Roaming'),
                        LOCALAPPDATA=str(home / 'AppData' / 'Local'))

    def cli(self, *args):
        result = subprocess.run([sys.executable, '-m', 'imprint.cli_skills', *args], env=self.env, cwd=ROOT,
                                capture_output=True, text=True, encoding='utf-8', timeout=120)
        self.assertNotIn('Traceback', result.stderr)
        return result

    def changed(self, result, said):
        """A change is kept whether or not this checkout's CLI can list the
        clients to write it to (``bin/`` may hold an older build)."""
        self.assertIn(said, result.stdout)
        self.assertTrue(result.returncode == 0 or 'not written to the clients' in result.stderr, result.stderr)

    def listed(self):
        result = self.cli('list', '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        return [i for i in json.loads(result.stdout) if i['name'] == 'imprint-tasks']

    def test_the_cli_lists_forks_and_resets_the_shipped_built_in(self):
        [builtin] = self.listed()
        self.assertEqual((builtin['origin'], builtin['state'], builtin['fork']), ('builtin', 'active', ''))
        self.assertNotIn('files', builtin)
        text = self.cli('list').stdout
        self.assertRegex(text, r'skill\s+imprint-tasks \[built-in\]')
        refused = self.cli('remove', 'imprint-tasks')
        self.assertEqual(refused.returncode, 1)
        self.assertIn('cannot be removed', refused.stderr)
        self.assertIn('fork', refused.stderr)

        self.changed(self.cli('fork', 'imprint-tasks'), 'Forked imprint-tasks')
        fork = next(i for i in self.listed() if i['origin'] == 'user')
        self.assertEqual((fork['fork_of'], fork['forked_from']), (builtin['id'], builtin['hash']))
        self.assertEqual(next(i for i in self.listed() if i['origin'] == 'builtin')['fork'], fork['id'])
        text = self.cli('list').stdout
        self.assertRegex(text, r'imprint-tasks \[fork of the built-in\]')
        self.assertRegex(text, r'imprint-tasks \[built-in, replaced by your fork\]')
        again = self.cli('fork', 'imprint-tasks')
        self.assertEqual(again.returncode, 1)
        self.assertIn('already forked', again.stderr)

        self.changed(self.cli('reset', 'imprint-tasks'), 'Reset imprint-tasks')
        self.assertEqual([(i['origin'], i['state']) for i in self.listed()], [('builtin', 'active'), ('user', 'retired')])
        not_forked = self.cli('reset', 'imprint-tasks')
        self.assertEqual(not_forked.returncode, 1)
        self.assertIn('not forked', not_forked.stderr)
        unknown = self.cli('fork', 'no-such-skill')
        self.assertNotEqual(unknown.returncode, 0)
        self.assertIn('No built-in skill named no-such-skill', unknown.stderr)

    def test_the_cli_disables_and_enables_a_built_in(self):
        self.changed(self.cli('disable', 'imprint-tasks'), 'Disabled skill imprint-tasks')
        self.assertEqual(self.listed()[0]['state'], 'disabled')
        self.assertRegex(self.cli('list').stdout, r'imprint-tasks \[built-in, disabled\]')
        self.changed(self.cli('enable', 'imprint-tasks'), 'Enabled skill imprint-tasks')
        self.assertEqual(self.listed()[0]['state'], 'active')


class ShippedBuiltinTests(unittest.TestCase):
    def test_every_shipped_built_in_is_a_valid_skill_named_after_its_folder(self):
        folders = sorted(p for p in registry.BUILTINS.iterdir() if p.is_dir())
        self.assertIn('imprint-tasks', [p.name for p in folders])
        for folder in folders:
            files = {f.relative_to(folder).as_posix(): f.read_text(encoding='utf-8')
                     for f in folder.rglob('*') if f.is_file()}
            with self.subTest(folder.name):
                valid = registry.validate('skill', files)
                self.assertEqual(valid['name'], folder.name)
        tasks = registry.validate('skill', {'SKILL.md': (registry.BUILTINS / 'imprint-tasks' / 'SKILL.md').read_text()})
        self.assertIn('Board of Tasks', tasks['description'])

    def test_the_release_archive_keeps_the_built_ins(self):
        patterns = [line.strip() for line in (ROOT / '.releaseignore').read_text().splitlines()
                    if line.strip() and not line.startswith('#')]
        for pattern in patterns:
            with self.subTest(pattern):
                self.assertFalse(pattern.strip('/') in ('imprint', 'imprint/builtin_skills', 'builtin_skills')
                                 or pattern.startswith('imprint/builtin_skills'))


if __name__ == '__main__':
    unittest.main()
