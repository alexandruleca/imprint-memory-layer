import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from imprint.extractors import dispatch_by_ext


class JsonConfigIngestionTests(unittest.TestCase):
    def test_jsonc_configs_preserve_comments_trailing_commas_and_strings(self):
        text = '{\n // TypeScript configuration\n "url": "https://host/a//b",\n "paths": ["/*"], /* comment */\n}\n'
        with tempfile.TemporaryDirectory() as folder:
            for name in ['tsconfig.json', 'tsconfig.app.json', 'tsconfig.node.json',
                         'jsconfig.json', 'jsconfig.web.json', 'settings.jsonc']:
                with self.subTest(name=name):
                    path = Path(folder) / name
                    path.write_text(text)
                    doc, = dispatch_by_ext(str(path))
                    self.assertEqual(doc.text, text)
                    self.assertEqual(doc.chunk_mode, 'code')

    def test_json_document_setting_includes_jsonc_in_directory_scans(self):
        from imprint.cli_index import _enabled_doc_formats
        with patch('imprint.config_schema.resolve', return_value=('json', 'default')):
            self.assertIn('.jsonc', _enabled_doc_formats())

    def test_malformed_json_is_indexed_verbatim_and_names_its_error(self):
        # A fixture or a half-edited config is still text in the repository (ADR 0156).
        with tempfile.TemporaryDirectory() as folder:
            for name in ['export.json', 'tsconfig.json', 'settings.jsonc']:
                with self.subTest(name=name):
                    path = Path(folder) / name
                    path.write_text('{"bad": }')
                    doc, = dispatch_by_ext(str(path))
                    self.assertEqual(doc.text, '{"bad": }')
                    self.assertEqual(doc.chunk_mode, 'code')
                    self.assertEqual(doc.metadata['source_format'], 'invalid_json')
                    self.assertIn('Expecting value', doc.metadata['json_error'])
