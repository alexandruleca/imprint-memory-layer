"""Docs frontmatter parses as YAML: the website build reads every page's
frontmatter, and an unquoted colon in a title fails the whole deploy."""

import unittest
from pathlib import Path

import yaml

DOCS = Path(__file__).resolve().parent.parent / "docs"


class DocsFrontmatterTests(unittest.TestCase):
    def test_every_page_has_a_titled_yaml_frontmatter(self):
        pages = [page for page in sorted(DOCS.rglob("*.md")) if page.read_text(encoding="utf-8").startswith("---\n")]
        self.assertTrue(pages)
        for page in pages:
            with self.subTest(page=str(page.relative_to(DOCS))):
                text = page.read_text(encoding="utf-8")
                meta = yaml.safe_load(text[4:text.index("\n---", 4)])
                self.assertIsInstance(meta, dict)
                self.assertIsInstance(meta.get("title"), str)


if __name__ == "__main__":
    unittest.main()
