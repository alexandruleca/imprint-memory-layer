"""Skills and personas through Imprint's MCP server (ADR 0170): listed in the
handshake for clients that do not load them from their own folders, read,
created and changed with the skills and skill_edit tools."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from imprint import config, registry, registry_files, skills_tool

from tests.test_registry import persona, skill


class SkillsToolTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.data = root / 'data'
        self.data.mkdir()
        self.agents = root / 'home' / '.claude' / 'agents'
        self.skills = root / 'home' / '.claude' / 'skills'
        self.clients = [{'name': 'claude-code', 'label': 'Claude Code', 'connected': True,
                         'skills': [str(self.skills)], 'personas': [str(self.agents)]}]
        for patcher in (patch.object(config, 'get_data_dir', return_value=self.data),
                        patch.object(registry, '_device', return_value='device-a'),
                        patch.object(registry_files, '_clients', side_effect=lambda: self.clients),
                        patch.dict(os.environ, {'IMPRINT_WSL_DISTRO': ''})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_nothing_is_listed_and_no_store_is_made_without_items(self):
        self.assertEqual(skills_tool.index(), '')
        self.assertIn('No skills or personas yet', skills_tool.listing())
        with self.assertRaisesRegex(ValueError, 'No skill or persona named deploy'):
            skills_tool.read('deploy')
        self.assertFalse(registry.exists())

    def test_the_handshake_lists_active_items_for_clients_without_their_folders(self):
        registry.create('skill', skill())
        registry.create('persona', persona())
        off = registry.create('skill', skill(name='old', description='Old one'))
        registry.update(off['id'], state='disabled')
        block = skills_tool.index('claude-ai')
        self.assertIn('Skills:\n- deploy: Deploy the app to staging\nPersonas:\n- reviewer: Reviews diffs for bugs',
                      block)
        self.assertNotIn('old', block)
        # Claude Code reads both folders on this computer, as the last write reported.
        registry_files.apply()
        self.assertEqual(skills_tool.index('claude-code'), '')
        self.assertIn('deploy', skills_tool.index('claude-ai'))
        # Served from Windows to a WSL session, it reads none of them.
        with patch.dict(os.environ, {'IMPRINT_WSL_DISTRO': 'Ubuntu'}):
            self.assertIn('reviewer', skills_tool.index('claude-code'))
        # A client that reads only skills folders gets the personas.
        self.clients = [{**self.clients[0], 'personas': []}]
        registry_files.apply()
        block = skills_tool.index('claude-code')
        self.assertNotIn('deploy', block)
        self.assertIn('Personas:\n- reviewer', block)

    def test_the_list_stays_within_its_budget(self):
        for n in range(80):
            registry.create('skill', skill(name=f'skill-{n:02d}', description='x' * 400))
        block = skills_tool.index()
        self.assertLessEqual(len(block), skills_tool.INDEX_BUDGET * skills_tool.CHARS_PER_TOKEN + 40)
        self.assertIn('- skill-00: ' + 'x' * 240, block)
        self.assertNotIn('x' * 251, block)
        self.assertRegex(block, r'- and \d+ more: skills\(action="list"\)\n$')

    def test_list_and_get_read_items_and_page_long_files(self):
        registry.create('skill', skill(**{'scripts/run.sh': 'echo hi\n'}))
        registry.create('persona', persona(name='deploy'))
        listed = skills_tool.listing()
        self.assertIn('skill deploy (2 files): Deploy the app to staging', listed)
        self.assertIn('persona deploy: Reviews diffs for bugs', listed)
        self.assertEqual(skills_tool.listing('persona').count('\n'), 2)
        with self.assertRaisesRegex(ValueError, 'both a skill and a persona'):
            skills_tool.read('deploy')
        text = skills_tool.read('deploy', 'skill')
        self.assertIn('Its other files: scripts/run.sh', text)
        self.assertTrue(text.endswith(skill()['SKILL.md']))
        self.assertTrue(skills_tool.read('deploy', 'skill', 'scripts/run.sh').endswith('echo hi\n'))
        with self.assertRaisesRegex(ValueError, 'no file nope.md'):
            skills_tool.read('deploy', 'skill', 'nope.md')
        long = registry.create('skill', skill(name='long', body='y' * 30_000))
        first = skills_tool.read('long')
        self.assertIn('next_start=20000', first)
        rest = skills_tool.read('long', start=20_000)
        self.assertNotIn('next_start', rest)
        self.assertEqual(first.split('\n\n', 1)[1] + rest.split('\n\n', 1)[1], long['files']['SKILL.md'])

    def test_create_and_edit_write_into_the_clients_at_once(self):
        said = skills_tool.create('skill', 'deploy', 'Deploy the app', 'Run make deploy.',
                                  {'scripts/run.sh': 'make deploy\n'})
        self.assertIn('Kept skill deploy', said)
        self.assertIn(str(self.skills / 'deploy'), said)
        self.assertEqual((self.skills / 'deploy' / 'scripts' / 'run.sh').read_text(), 'make deploy\n')
        role = skills_tool.create('persona', files=persona())
        self.assertIn(str(self.agents / 'reviewer.md'), role)
        with self.assertRaisesRegex(ValueError, 'already exists'):
            skills_tool.create('skill', 'deploy', 'Again', 'Again.')
        with self.assertRaisesRegex(ValueError, 'needs name, description and instructions'):
            skills_tool.create('skill', 'lint')
        with self.assertRaisesRegex(ValueError, 'credential'):
            skills_tool.create('skill', 'leak', 'Leaks', 'Use key AKIAABCDEFGHIJKLMNOP to deploy.')

        said = skills_tool.edit('reviewer', description='Reviews diffs for bugs and style', instructions='Be kind.')
        self.assertIn('Changed persona reviewer', said)
        text = (self.agents / 'reviewer.md').read_text()
        meta, body = registry.front_matter(text)
        self.assertEqual((meta['description'], meta['tools'], body.strip()),
                         ('Reviews diffs for bugs and style', 'Read, Grep', 'Be kind.'))
        skills_tool.edit('deploy', files={'scripts/run.sh': None, 'notes.md': 'Staging only.'})
        self.assertEqual(sorted(p.name for p in (self.skills / 'deploy').rglob('*') if p.is_file()),
                         ['SKILL.md', 'notes.md'])
        self.assertIn('Disabled', skills_tool.edit('deploy', state='disabled'))
        self.assertFalse((self.skills / 'deploy').exists())
        skills_tool.edit('deploy', state='active')
        self.assertTrue((self.skills / 'deploy' / 'SKILL.md').is_file())
        self.assertIn('nothing changed', skills_tool.edit('deploy', instructions='Run make deploy.'))
        with self.assertRaisesRegex(ValueError, 'cannot be removed'):
            skills_tool.edit('deploy', files={'SKILL.md': None})
        with self.assertRaisesRegex(ValueError, 'active or disabled'):
            skills_tool.edit('deploy', state='retired')
        with self.assertRaisesRegex(ValueError, 'Nothing to change'):
            skills_tool.edit('deploy')
        history = registry.history(registry.find('reviewer')['id'])
        self.assertEqual(len(history), 1)

    def test_a_change_is_kept_when_the_clients_cannot_be_listed(self):
        with patch.object(registry_files, '_clients', side_effect=RuntimeError('imprint clients failed')):
            said = skills_tool.create('skill', 'deploy', 'Deploy the app', 'Run make deploy.')
        self.assertIn('Not written to the clients yet (imprint clients failed)', said)
        self.assertEqual(registry.find('deploy')['state'], 'active')


class ServerTests(unittest.TestCase):
    """The tools and the handshake through the MCP server."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data = Path(directory.name)
        self.clients = [{'name': 'claude-code', 'label': 'Claude Code', 'connected': True,
                         'skills': [str(self.data / 'skills')], 'personas': [str(self.data / 'agents')]}]
        for patcher in (patch.object(config, 'get_data_dir', return_value=self.data),
                        patch.object(registry, '_device', return_value='device-a'),
                        patch.object(registry_files, '_clients', return_value=self.clients),
                        patch.dict(os.environ, {'IMPRINT_WSL_DISTRO': ''})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_the_handshake_fits_the_client_and_the_tools_read_and_write(self):
        from fastmcp import Client
        from mcp.types import Implementation
        from imprint import server
        registry.create('skill', skill())
        registry_files.apply()

        async def session(name, mode='legacy'):
            async with Client(server.mcp, client_info=Implementation(name=name, version='1'), mode=mode) as client:
                tools = {tool.name: tool for tool in await client.list_tools()}
                listed = await client.call_tool('skills', {})
                made = await client.call_tool('skill_edit', {'action': 'create', 'name': f'from-{name}',
                                                             'description': 'Made over MCP',
                                                             'instructions': 'Do it.'})
                got = await client.call_tool('skills', {'action': 'get', 'name': f'from-{name}'})
                return client.initialize_result.instructions, tools, listed, made, got

        async def discovered(name):
            async with Client(server.mcp, client_info=Implementation(name=name, version='1'), mode='auto') as client:
                self.assertIsNone(client.initialize_result)  # It negotiated through server/discover.
                return client.instructions

        instructions, tools, listed, made, got = asyncio.run(session('claude-ai'))
        self.assertIn("# The user's skills and personas", instructions)
        self.assertIn('- deploy: Deploy the app to staging', instructions)
        self.assertTrue(tools['skills'].annotations.read_only_hint)
        self.assertTrue(tools['skill_edit'].annotations.destructive_hint)
        self.assertIn('skill deploy: Deploy the app to staging', listed.content[0].text)
        self.assertIn('Kept skill from-claude-ai', made.content[0].text)
        self.assertTrue((self.data / 'skills' / 'from-claude-ai' / 'SKILL.md').is_file())
        self.assertIn('Do it.', got.content[0].text)
        instructions = asyncio.run(session('claude-code'))[0]
        self.assertNotIn("# The user's skills and personas", instructions)
        self.assertIn('Imprint stores local project context', instructions)
        # The same through server/discover (protocol 2026-07-28), which names the client in _meta.
        self.assertIn('- deploy: Deploy the app', asyncio.run(discovered('claude-ai')))
        self.assertNotIn("# The user's skills", asyncio.run(discovered('claude-code')))


if __name__ == '__main__':
    unittest.main()
