"""Docs tables render whole on the website and on GitHub.

A blank line inside a table ends it, and every later row renders as text
full of pipes (the ADR log broke that way from ADR 0083 on). A `|` inside a
code span still splits a GitHub-flavoured table cell unless it is escaped
as `\\|`, so such a row gains cells and shifts its columns.
"""

import re
import unittest
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"
_SEPARATOR = re.compile(r"\s*\|(\s*:?-+:?\s*\|)+\s*$")


def _cells(row: str) -> int:
    return len(re.split(r"(?<!\\)\|", row.strip())) - 2


def _tables(text: str):
    """(first line number, rows) for each table outside code fences."""
    rows, start, fence = [], 0, False
    for number, line in enumerate(text.split("\n"), 1):
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
        if not fence and line.lstrip().startswith("|"):
            if not rows:
                start = number
            rows.append((number, line))
            continue
        if rows:
            yield start, rows
            rows = []
    if rows:
        yield start, rows


class DocsTableTests(unittest.TestCase):
    def test_no_table_is_split_by_a_blank_line(self):
        for page in sorted(DOCS.rglob("*.md")):
            lines = page.read_text(encoding="utf-8").split("\n")
            for index in range(1, len(lines) - 1):
                with self.subTest(page=str(page.relative_to(DOCS)), line=index + 1):
                    following = lines[index + 2] if index + 2 < len(lines) else ""
                    split = (not lines[index].strip() and lines[index - 1].lstrip().startswith("|")
                             and lines[index + 1].lstrip().startswith("|") and not _SEPARATOR.match(following))
                    self.assertFalse(split, "a blank line ends this table; the rows after it render as text")

    def test_every_row_has_its_header_s_cells(self):
        for page in sorted(DOCS.rglob("*.md")):
            for start, rows in _tables(page.read_text(encoding="utf-8")):
                header = _cells(rows[0][1])
                for number, row in rows[1:]:
                    if _SEPARATOR.match(row):
                        continue
                    with self.subTest(page=str(page.relative_to(DOCS)), line=number):
                        self.assertEqual(_cells(row), header, "escape a | inside a cell as \\| (code spans too)")


if __name__ == "__main__":
    unittest.main()
