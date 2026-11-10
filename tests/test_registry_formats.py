"""A persona as Codex's TOML custom agent and back (ADR 0171)."""
import tomllib
import unittest

from imprint import registry
from imprint.registry_formats import CODEX, MARKDOWN, codex_agent, parsed, persona_from_codex, rendered


def persona_text(body='You review code.', **meta):
    import yaml
    fields = {'name': 'reviewer', 'description': 'Reviews diffs for bugs', **meta}
    return '---\n' + yaml.safe_dump(fields, sort_keys=False, allow_unicode=True) + '---\n\n' + body + '\n'


class CodexAgentTests(unittest.TestCase):
    def test_instructions_of_any_text_survive_the_round_trip(self):
        for body in ('Plain.', "Quotes ' and '' and ''' and \"\"\" and \\backslash\\n.",
                     'Ünïcödé — and a tab\there.', 'Ends with a quote\'', 'Line one\r\nline two',
                     'A bell \x07 and a delete \x7f.', 'Lone\rcarriage return.'):
            with self.subTest(body=body):
                agent = tomllib.loads(codex_agent(persona_text(body)))
                self.assertEqual(agent['developer_instructions'].replace('\r\n', '\n'),
                                 body.replace('\r\n', '\n') + '\n')
                self.assertEqual((agent['name'], agent['description']), ('reviewer', 'Reviews diffs for bugs'))

    def test_codex_settings_ride_in_the_front_matter_and_only_scalars_carry_over(self):
        text = persona_text(codex={'model': 'gpt-5-codex', 'model_reasoning_effort': 'high', 'web': True,
                                   'depth': 2, 'name': 'not-this', 'features': {'x': 1}, 'bad key': 'x'},
                            tools='Read, Grep', model='sonnet')
        agent = tomllib.loads(codex_agent(text))
        self.assertEqual(agent, {'name': 'reviewer', 'description': 'Reviews diffs for bugs', 'model': 'gpt-5-codex',
                                 'model_reasoning_effort': 'high', 'web': True, 'depth': 2,
                                 'developer_instructions': 'You review code.\n'})

    def test_a_description_with_quotes_and_newlines_is_one_toml_line(self):
        agent = tomllib.loads(codex_agent(persona_text(description='Says "hi"\n  across lines')))
        self.assertEqual(agent['description'], 'Says "hi" across lines')

    def test_back_from_codex_keeps_the_fields_codex_does_not_hold(self):
        base = persona_text(tools='Read, Grep', color='blue')
        edited = codex_agent(base).replace('You review code.', 'You review code carefully.')
        text = persona_from_codex(edited, base)
        # Only the instructions changed: the front matter is the base's, byte for byte.
        self.assertEqual(text, base.replace('You review code.', 'You review code carefully.'))
        renamed = persona_from_codex(edited.replace('name = "reviewer"', 'name = "critic"') + 'model = "o4"\n', base)
        meta, body = registry.front_matter(renamed)
        self.assertEqual(list(meta), ['name', 'description', 'tools', 'color', 'codex'])
        self.assertEqual((meta['name'], meta['tools'], meta['codex'], body.strip()),
                         ('critic', 'Read, Grep', {'model': 'o4'}, 'You review code carefully.'))
        self.assertEqual(registry.validate('persona', {'PERSONA.md': renamed})['name'], 'critic')

    def test_a_file_that_is_not_an_agent_is_refused(self):
        for text, problem in (('name = ', 'not valid TOML'), ('name = "a"\ndescription = "b"\n', 'developer_instructions'),
                              ('name = "a"\ndescription = "b"\ndeveloper_instructions = "c"\n[mcp_servers.x]\nurl = "u"\n',
                               'mcp_servers')):
            with self.subTest(problem=problem), self.assertRaisesRegex(ValueError, problem):
                persona_from_codex(text)

    def test_skills_and_markdown_folders_hold_items_as_they_are(self):
        files = {'PERSONA.md': persona_text()}
        self.assertIs(rendered('persona', MARKDOWN, files), files)
        self.assertIs(parsed('skill', CODEX, files), files)
        self.assertEqual(parsed('persona', CODEX, rendered('persona', CODEX, files), files), files)


if __name__ == '__main__':
    unittest.main()
