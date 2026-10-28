"""JSON files with comments and trailing commas (JSONC), as tsconfig, jsconfig
and dev container files are, are read like JSON."""
import tempfile
import unittest
from pathlib import Path

from imprint.extractors import ExtractionError, json_doc


class JsoncTests(unittest.TestCase):
    def extract(self, text):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'devcontainer.json'
            path.write_text(text)
            return json_doc.extract(str(path))

    def test_comments_and_trailing_commas_parse_and_strings_keep_their_slashes(self):
        doc = self.extract('{\n  // Vite\n  "compilerOptions": {"paths": "https://x.dev/a/*", /* b */ "strict": true,},\n'
                           '  "include": ["vite.config.ts",],\n}\n')
        self.assertIn('"paths": "https://x.dev/a/*"', doc.text)
        self.assertIn('"strict": true', doc.text)
        self.assertNotIn('Vite', doc.text)

    def test_invalid_json_still_fails_at_its_line(self):
        with self.assertRaisesRegex(ExtractionError, 'line 3'):
            self.extract('{\n  /* a\n  b */ "a": 1 "b": 2\n}')


if __name__ == '__main__':
    unittest.main()
