"""Skills and personas in the clients' own folders (ADRs 0169, 0171): the
fewest folders that reach every connected client, a manifest of what Imprint
wrote, edits made there taken back, nothing Imprint did not write touched,
and personas as Codex's TOML agents in its folder."""
import json
import os
import tempfile
import time
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from imprint import config, registry, registry_files

from tests.test_registry import persona, skill


class RegistryFilesTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.data = root / 'data'
        self.home = root / 'home'
        for patcher in (patch.object(config, 'get_data_dir', return_value=self.data),
                        patch.object(registry, '_device', return_value='device-a')):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.claude_skills = self.home / '.claude' / 'skills'
        self.claude_agents = self.home / '.claude' / 'agents'
        self.shared = self.home / '.agents' / 'skills'
        self.codex_agents = self.home / '.codex' / 'agents'
        self.cursor_agents = self.home / '.cursor' / 'agents'
        self.claude = {'name': 'claude-code', 'label': 'Claude Code', 'connected': True,
                       'skills': [str(self.claude_skills)], 'personas': [str(self.claude_agents)]}
        self.codex = {'name': 'codex', 'label': 'Codex', 'connected': True, 'skills': [str(self.shared)],
                      'personas': [str(self.codex_agents)], 'persona_format': 'codex'}
        self.cursor = {'name': 'cursor', 'label': 'Cursor', 'connected': True,
                       'skills': [str(self.shared), str(self.home / '.cursor' / 'skills'), str(self.claude_skills)],
                       'personas': [str(self.cursor_agents), str(self.claude_agents)]}
        self.clients = [self.claude, self.codex, self.cursor]

    def apply(self, clients=None):
        return registry_files.apply(self.clients if clients is None else clients)

    def manifest(self, folder):
        return json.loads((folder / registry_files.MANIFEST).read_text())['items']

    def test_the_fewest_folders_reach_every_connected_client(self):
        plan = registry_files.plan
        self.assertEqual(plan(self.clients), {'skill': [str(self.claude_skills), str(self.shared)],
                                              'persona': [str(self.claude_agents), str(self.codex_agents)]})
        # Cursor alone takes the shared folder; with Claude Code it reads Claude's.
        self.assertEqual(plan([self.cursor])['skill'], [str(self.shared)])
        self.assertEqual(plan([self.cursor, self.claude])['skill'], [str(self.claude_skills)])
        # Personas the same way: Cursor reads Claude Code's agents, not Codex's TOML.
        self.assertEqual(plan([self.cursor])['persona'], [str(self.cursor_agents)])
        self.assertEqual(plan([self.cursor, self.claude])['persona'], [str(self.claude_agents)])
        self.assertEqual(plan([self.cursor, self.codex])['persona'], [str(self.codex_agents), str(self.cursor_agents)])
        self.assertEqual(plan([{**self.claude, 'connected': False}, self.codex]),
                         {'skill': [str(self.shared)], 'persona': [str(self.codex_agents)]})
        self.assertEqual(registry_files.readers('skill', self.shared, self.clients), ['Codex', 'Cursor'])
        self.assertEqual(registry_files.readers('persona', self.claude_agents, self.clients), ['Claude Code', 'Cursor'])
        # A CLI that gave one folder as a string; a format this runtime does not know reads none.
        self.assertEqual(plan([{**self.claude, 'personas': str(self.claude_agents)}])['persona'],
                         [str(self.claude_agents)])
        self.assertEqual(plan([{**self.codex, 'persona_format': 'yaml'}])['persona'], [])

    def test_items_are_written_byte_for_byte_with_a_manifest(self):
        item = registry.create('skill', skill(**{'scripts/run.sh': 'echo hi\r\n'}))
        role = registry.create('persona', persona())
        report = self.apply()
        for folder in (self.claude_skills, self.shared):
            self.assertEqual((folder / 'deploy' / 'scripts' / 'run.sh').read_bytes(), b'echo hi\r\n')
            self.assertEqual(self.manifest(folder), {'deploy': {'id': item['id'], 'hash': item['hash']}})
        self.assertEqual((self.claude_agents / 'reviewer.md').read_text(), persona()['PERSONA.md'])
        self.assertEqual(self.manifest(self.claude_agents), {'reviewer': {'id': role['id'], 'hash': role['hash']}})
        self.assertTrue((self.codex_agents / 'reviewer.toml').is_file())
        self.assertEqual(len(report['written']), 4)
        self.assertEqual(report['problems'], [])
        self.assertEqual(registry_files.last_report()['written'], report['written'])
        again = self.apply()
        self.assertEqual((again['written'], again['removed'], again['taken']), ([], [], []))

    def test_what_imprint_did_not_write_is_left_alone_and_can_be_adopted(self):
        theirs = self.claude_skills / 'deploy'
        theirs.mkdir(parents=True)
        (theirs / 'SKILL.md').write_text(skill(body='Their own')['SKILL.md'])
        lint = self.shared / 'lint'
        lint.mkdir(parents=True)
        (lint / 'SKILL.md').write_text(skill(name='lint', description='Lint it')['SKILL.md'])
        registry.create('skill', skill())
        report = self.apply()
        self.assertIn('Their own', (theirs / 'SKILL.md').read_text())
        self.assertEqual([p['path'] for p in report['problems']], [str(theirs)])
        self.assertFalse((self.claude_skills / registry_files.MANIFEST).exists())
        self.assertIn('deploy', self.manifest(self.shared))
        found = {(f['name'], f['folder']) for f in registry_files.found(self.clients)}
        self.assertEqual(found, {('deploy', str(self.claude_skills)), ('lint', str(self.shared))})
        adopted = registry_files.adopt(str(lint), 'skill', self.clients)
        self.assertEqual((adopted['name'], adopted['origin']), ('lint', 'adopted'))
        self.assertEqual(self.manifest(self.shared)['lint']['id'], adopted['id'])
        self.assertTrue((self.claude_skills / 'lint' / 'SKILL.md').is_file())
        with self.assertRaises(KeyError):
            registry_files.adopt(str(self.home / 'elsewhere'), 'skill', self.clients)

    def test_a_folder_already_holding_the_same_item_is_managed_from_then_on(self):
        item = registry.create('skill', skill())
        same = self.claude_skills / 'deploy'
        same.mkdir(parents=True)
        # Byte for byte: Windows' text mode would write CRLF, a different item.
        (same / 'SKILL.md').write_text(skill()['SKILL.md'], newline='')
        report = self.apply()
        self.assertNotIn(str(same), report['written'])
        self.assertEqual(self.manifest(self.claude_skills)['deploy']['id'], item['id'])
        self.assertEqual(registry_files.found(self.clients), [])

    def test_an_edit_made_in_a_client_reaches_the_registry_and_the_other_folders(self):
        item = registry.create('skill', skill())
        self.apply()
        edited = self.claude_skills / 'deploy' / 'SKILL.md'
        edited.write_text(skill(body='Edited in Claude Code')['SKILL.md'])
        (edited.parent / 'notes.md').write_text('more')
        future = time.time() + 60
        os.utime(edited, (future, future))
        report = self.apply()
        self.assertEqual(report['taken'], [str(edited.parent)])
        self.assertIn('Edited in Claude Code', registry.get(item['id'])['files']['SKILL.md'])
        self.assertEqual((self.shared / 'deploy' / 'notes.md').read_text(), 'more')
        self.assertEqual(registry.history(item['id'])[0]['hash'], item['hash'])

    def test_an_edit_that_is_not_valid_yet_is_left_in_place(self):
        item = registry.create('skill', skill())
        self.apply()
        edited = self.claude_skills / 'deploy' / 'SKILL.md'
        edited.write_text('---\nname: deploy\n---\nhalf done')
        report = self.apply()
        self.assertEqual(edited.read_text(), '---\nname: deploy\n---\nhalf done')
        self.assertIn('description', report['problems'][0]['problem'])
        self.assertEqual(registry.get(item['id'])['hash'], item['hash'])
        edited.write_text(skill(body='Done now')['SKILL.md'])
        self.apply()
        self.assertIn('Done now', registry.get(item['id'])['files']['SKILL.md'])

    def test_a_newer_change_elsewhere_wins_over_an_older_edit_here_which_stays_in_history(self):
        item = registry.create('skill', skill())
        self.apply()
        edited = self.claude_skills / 'deploy' / 'SKILL.md'
        edited.write_text(skill(body='Old edit')['SKILL.md'])
        past = item['updated_at'] - 3600
        os.utime(edited, (past, past))
        newer = {**registry.export([item['id']])[0], 'files': skill(body='From the laptop'),
                 'updated_at': time.time() + 10}
        registry.import_records([newer])
        report = self.apply()
        self.assertEqual(report['kept'], [str(edited.parent)])
        self.assertIn('From the laptop', edited.read_text())
        self.assertIn('Old edit', registry.history(item['id'])[0]['files']['SKILL.md'])

    def test_a_renamed_skill_moves_to_its_new_folder(self):
        item = registry.create('skill', skill())
        self.apply()
        edited = self.claude_skills / 'deploy' / 'SKILL.md'
        edited.write_text(skill(name='ship')['SKILL.md'])
        self.apply()
        self.assertEqual(registry.get(item['id'])['name'], 'ship')
        for folder in (self.claude_skills, self.shared):
            self.assertFalse((folder / 'deploy').exists())
            self.assertTrue((folder / 'ship' / 'SKILL.md').is_file())
            self.assertEqual(list(self.manifest(folder)), ['ship'])

    def test_removed_items_and_folders_no_client_reads_are_cleaned(self):
        kept = registry.create('skill', skill())
        gone = registry.create('skill', skill(name='old', description='Old one'))
        self.apply()
        (self.claude_skills / 'deploy').joinpath('SKILL.md').unlink()  # Removed in the client: written again.
        registry.update(gone['id'], state='retired')
        report = self.apply()
        self.assertTrue((self.claude_skills / 'deploy' / 'SKILL.md').is_file())
        self.assertFalse((self.claude_skills / 'old').exists())
        self.assertIn(str(self.shared / 'old'), report['removed'])
        # Codex and Cursor disconnected: the shared folder is no one's now.
        mine = self.shared / 'mine'
        mine.mkdir()
        (mine / 'SKILL.md').write_text('the user\'s own')
        self.apply([self.claude])
        self.assertFalse((self.shared / 'deploy').exists())
        self.assertFalse((self.shared / registry_files.MANIFEST).exists())
        self.assertEqual((mine / 'SKILL.md').read_text(), 'the user\'s own')
        self.assertEqual(registry_files._known(), [('skill', str(self.claude_skills), 'markdown')])
        registry.update(kept['id'], state='disabled')
        self.apply([])
        self.assertFalse((self.claude_skills / 'deploy').exists())
        self.assertEqual(registry_files._known(), [])

    def test_nothing_is_written_or_removed_when_the_clients_cannot_be_listed(self):
        registry.create('skill', skill())
        self.apply()
        with patch.object(registry_files, '_clients', side_effect=RuntimeError('imprint clients failed')):
            with self.assertRaises(RuntimeError):
                registry_files.apply()
            self.assertIsNone(registry_files.apply_quietly())
        self.assertTrue((self.claude_skills / 'deploy' / 'SKILL.md').is_file())

    def test_a_folder_another_installation_manages_is_left_to_it(self):
        self.claude_skills.mkdir(parents=True)
        (self.claude_skills / registry_files.MANIFEST).write_text(json.dumps(
            {'imprint': 1, 'kind': 'skill', 'runtime': '/elsewhere/data', 'items': {}}))
        registry.create('skill', skill())
        report = self.apply()
        self.assertFalse((self.claude_skills / 'deploy').exists())
        self.assertIn('another Imprint installation', report['problems'][0]['problem'])
        self.assertTrue((self.shared / 'deploy').exists())

    def test_a_manifest_cannot_point_outside_its_folder(self):
        registry.create('skill', skill())
        self.apply()
        victim = self.home / 'victim'
        victim.mkdir()
        (victim / 'keep.txt').write_text('x')
        doc = json.loads((self.claude_skills / registry_files.MANIFEST).read_text())
        doc['items']['../../victim'] = {'id': '0' * 32, 'hash': 'x'}
        (self.claude_skills / registry_files.MANIFEST).write_text(json.dumps(doc))
        self.apply()
        self.assertTrue((victim / 'keep.txt').exists())
        self.assertEqual(list(self.manifest(self.claude_skills)), ['deploy'])

    def codex_text(self, name='reviewer'):
        return (self.codex_agents / f'{name}.toml').read_text(encoding='utf-8')

    def test_a_persona_is_a_toml_agent_in_codex_and_markdown_in_claude_code_and_cursor(self):
        role = registry.create('persona', persona())
        report = self.apply()
        self.assertEqual(tomllib.loads(self.codex_text()),
                         {'name': 'reviewer', 'description': 'Reviews diffs for bugs',
                          'developer_instructions': 'You review code.\n'})
        self.assertEqual([f.get('format') for f in report['folders'] if f['kind'] == 'persona'], [None, 'codex'])
        # The manifest records the TOML as written, so a second pass changes nothing.
        written = registry.content_hash('persona', {'PERSONA.md': self.codex_text()})
        self.assertEqual(self.manifest(self.codex_agents), {'reviewer': {'id': role['id'], 'hash': written}})
        again = self.apply()
        self.assertEqual((again['written'], again['taken'], again['problems']), ([], [], []))
        self.assertIn(('persona', str(self.codex_agents), 'codex'), registry_files._known())
        # Cursor alone: its own folder, as Markdown; the others are cleaned.
        self.apply([self.cursor])
        self.assertEqual((self.cursor_agents / 'reviewer.md').read_text(), persona()['PERSONA.md'])
        self.assertFalse((self.codex_agents / 'reviewer.toml').exists())
        self.assertFalse((self.claude_agents / 'reviewer.md').exists())

    def test_an_edit_made_in_codex_reaches_the_persona_and_keeps_what_codex_does_not_hold(self):
        role = registry.create('persona', persona())
        self.apply()
        path = self.codex_agents / 'reviewer.toml'
        path.write_text('name = "reviewer"\ndescription = "Reviews diffs for bugs"\nmodel = "gpt-5-codex"\n'
                        'developer_instructions = """\nYou review code. Be strict.\n"""\n', encoding='utf-8')
        future = time.time() + 60
        os.utime(path, (future, future))
        report = self.apply()
        self.assertEqual(report['taken'], [str(path)])
        text = registry.get(role['id'])['files']['PERSONA.md']
        meta, body = registry.front_matter(text)
        self.assertEqual((meta['tools'], meta['codex'], body.strip()),
                         ('Read, Grep', {'model': 'gpt-5-codex'}, 'You review code. Be strict.'))
        self.assertEqual((self.claude_agents / 'reviewer.md').read_text(encoding='utf-8'), text)
        # Written back in Imprint's layout, the Codex setting kept, and then left alone.
        self.assertEqual(tomllib.loads(self.codex_text())['model'], 'gpt-5-codex')
        self.assertEqual(self.apply()['written'], [])
        # Only the instructions changed: the front matter stays as it was written.
        path.write_text(self.codex_text().replace('Be strict.', 'Be kind.'), encoding='utf-8')
        future += 60
        os.utime(path, (future, future))
        self.apply()
        changed = registry.get(role['id'])['files']['PERSONA.md']
        self.assertIn('Be kind.', changed)
        self.assertEqual(changed.split('---')[1], text.split('---')[1])

    def test_a_codex_edit_imprint_cannot_keep_is_left_in_place(self):
        role = registry.create('persona', persona())
        self.apply()
        path = self.codex_agents / 'reviewer.toml'
        broken = 'name = "reviewer"\ndescription = "x"\ndeveloper_instructions = "y"\n[features]\nweb = true\n'
        path.write_text(broken, encoding='utf-8')
        report = self.apply()
        self.assertEqual(path.read_text(encoding='utf-8'), broken)
        self.assertIn('features', report['problems'][0]['problem'])
        self.assertEqual(registry.get(role['id'])['hash'], role['hash'])

    def test_a_codex_agent_found_in_its_folder_can_be_adopted_and_stays_as_written(self):
        self.codex_agents.mkdir(parents=True)
        mine = self.codex_agents / 'triage.toml'
        text = ("name = 'triage'\ndescription = 'Sorts incoming issues'\nmodel_reasoning_effort = 'high'\n"
                "developer_instructions = '''\nLabel each issue.\n'''\n")
        mine.write_text(text, encoding='utf-8')
        (self.codex_agents / 'pr_explorer.toml').write_text(
            'name = "pr_explorer"\ndescription = "x"\ndeveloper_instructions = "y"\n', encoding='utf-8')
        found = {f['name']: f for f in registry_files.found(self.clients) if f['kind'] == 'persona'}
        self.assertEqual((found['triage']['description'], found['triage']['problem']), ('Sorts incoming issues', ''))
        self.assertIn('lower-case', found['pr_explorer']['problem'])
        adopted = registry_files.adopt(str(mine), 'persona', self.clients)
        meta, body = registry.front_matter(adopted['files']['PERSONA.md'])
        self.assertEqual((meta['codex'], body.strip()), ({'model_reasoning_effort': 'high'}, 'Label each issue.'))
        self.assertEqual(mine.read_text(encoding='utf-8'), text)  # Left as the user wrote it, and managed.
        self.assertEqual(self.manifest(self.codex_agents)['triage']['id'], adopted['id'])
        self.assertTrue((self.claude_agents / 'triage.md').is_file())
        self.assertEqual([p for p in self.apply()['problems'] if 'triage' in p['path']], [])

    def test_the_dashboard_makes_edits_and_adopts_through_the_api(self):
        from fastapi.testclient import TestClient
        from imprint.api import app
        client = TestClient(app, base_url='http://127.0.0.1:8420', client=('127.0.0.1', 1234))
        with patch.object(registry_files, '_clients', return_value=self.clients):
            made = client.post('/api/skills', json={'kind': 'skill', 'name': 'deploy', 'description': 'Deploy it',
                                                    'instructions': 'Run make deploy.'})
            self.assertEqual(made.status_code, 200, made.text)
            item = made.json()
            self.assertEqual(item['report']['problems'], [])
            self.assertTrue((self.claude_skills / 'deploy' / 'SKILL.md').is_file())
            refused = client.post('/api/skills', json={'kind': 'skill', 'name': 'Bad Name', 'description': 'x',
                                                       'instructions': 'y'})
            self.assertEqual(refused.status_code, 400)
            self.assertIn('lower-case', refused.json()['error'])
            listed = client.get('/api/skills').json()
            self.assertEqual([i['name'] for i in listed['items']], ['deploy'])
            self.assertEqual(listed['main'], {'skill': 'SKILL.md', 'persona': 'PERSONA.md'})
            changed = client.patch(f"/api/skills/{item['id']}", json={'files': skill(body='Changed')})
            self.assertEqual(changed.status_code, 200, changed.text)
            self.assertIn('Changed', (self.shared / 'deploy' / 'SKILL.md').read_text())
            detail = client.get(f"/api/skills/{item['id']}").json()
            self.assertEqual(detail['history'][0]['hash'], item['hash'])
            self.assertNotIn('files', detail['history'][0])
            restored = client.patch(f"/api/skills/{item['id']}", json={'restore': item['hash']})
            self.assertEqual(restored.json()['hash'], item['hash'])
            retired = client.patch(f"/api/skills/{item['id']}", json={'state': 'retired'})
            self.assertEqual(retired.json()['state'], 'retired')
            self.assertFalse((self.claude_skills / 'deploy').exists())
            self.assertEqual(client.patch(f"/api/skills/{'0' * 32}", json={'state': 'active'}).status_code, 404)
            self.assertEqual(client.get(f"/api/skills/{'0' * 32}").status_code, 404)
            theirs = self.shared / 'lint'
            theirs.mkdir(parents=True)
            (theirs / 'SKILL.md').write_text(skill(name='lint', description='Lint it')['SKILL.md'])
            applied = client.post('/api/skills/apply').json()
            self.assertEqual([f['name'] for f in applied['found']], ['lint'])
            adopted = client.post('/api/skills/adopt', json={'path': str(theirs), 'kind': 'skill'})
            self.assertEqual((adopted.status_code, adopted.json()['name']), (200, 'lint'))
            self.assertEqual(client.post('/api/skills/adopt', json={'path': str(theirs), 'kind': 'skill'}).status_code,
                             404)

    def test_the_cli_adds_lists_disables_and_removes(self):
        import contextlib
        import io
        from imprint import cli_skills
        source = self.home / 'drafts' / 'deploy'
        source.mkdir(parents=True)
        (source / 'SKILL.md').write_text(skill()['SKILL.md'], newline='')
        out = io.StringIO()
        with patch.object(registry_files, '_clients', return_value=self.clients), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli_skills.main(['apply', '--if-needed']), 0)  # Nothing yet: not even the store.
            self.assertFalse(registry.exists())
            self.assertEqual(cli_skills.main(['add', str(source / 'SKILL.md')]), 0)  # A SKILL.md names its folder.
            self.assertTrue((self.claude_skills / 'deploy' / 'SKILL.md').is_file())
            self.assertEqual(cli_skills.main(['list']), 0)
            self.assertEqual(cli_skills.main(['disable', 'deploy']), 0)
            self.assertFalse((self.claude_skills / 'deploy').exists())
            self.assertEqual(cli_skills.main(['remove', 'deploy']), 0)
            with self.assertRaises(SystemExit):
                cli_skills.main(['enable', 'deploy'])  # Removed: no longer found by name.
        self.assertIn('Added skill deploy.', out.getvalue())
        self.assertIn('Disabled skill deploy.', out.getvalue())
        self.assertEqual(registry.list_all()[0]['state'], 'retired')

    def test_the_cli_writes_utf8_when_its_output_is_redirected(self):
        # Windows gives redirected output its ANSI code page (cp1252 here),
        # which cannot encode the report's arrow or a skill's own text.
        import io
        from imprint import cli_skills
        registry.create('skill', skill(description='Ship it → production', body='Run „make deploy“.'))
        out = io.TextIOWrapper(io.BytesIO(), encoding='cp1252')
        err = io.TextIOWrapper(io.BytesIO(), encoding='cp1252')
        with patch.object(registry_files, '_clients', return_value=self.clients), \
                patch('sys.stdout', out), patch('sys.stderr', err):
            self.assertEqual(cli_skills.main(['show', 'deploy']), 0)
            self.assertEqual(cli_skills.main(['apply']), 0)  # Its report reads "skills → <folder>".
            out.flush()
        printed = out.buffer.getvalue().decode('utf-8')
        self.assertIn('Ship it → production', printed)
        self.assertIn('Run „make deploy“.', printed)
        self.assertIn(f'skills → {self.claude_skills}', printed)


if __name__ == '__main__':
    unittest.main()
