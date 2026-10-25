"""Architecture decision records stay consistent (docs/adr/README.md, "How to
write and maintain ADRs"): every record is indexed with its current status,
numbers are contiguous, headers and sections follow one structure, and
supersede/amend links are reciprocated."""

import re
import unittest
from pathlib import Path

import yaml

ADR = Path(__file__).resolve().parent.parent / "docs" / "adr"
STATUSES = ("Proposed", "Accepted", "Amended", "Superseded", "Rejected")
IN_FORCE = ("Accepted", "Amended")
FIELDS = ("Status", "Date", "Recorded", "Retrospective", "Scope", "Supersedes", "Superseded by",
          "Amends", "Amended by", "Related", "Restructured")
INVERSE = {"Supersedes": "Superseded by", "Superseded by": "Supersedes",
           "Amends": "Amended by", "Amended by": "Amends"}
SECTIONS = ("Context", "Decision", "Rejected alternatives", "Consequences", "Invalidation conditions", "Validation")
FILENAME = re.compile(r"^(\d{4})-[a-z0-9]+(?:-[a-z0-9]+)*\.md$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RECORD_LINK = re.compile(r"\[ADR (\d{4})\]\(((\d{4})-[a-z0-9-]+\.md)\)")
FIELD = re.compile(r"^- ([A-Z][a-z]+(?: [a-z]+)?):(?: (.*))?$")


def prose(text):
    """Text outside fenced code blocks and inline code spans."""
    text = re.sub(r"^```.*?^```", "", text, flags=re.S | re.M)
    return re.sub(r"`[^`\n]*`", "", text)


class Record:
    def __init__(self, path):
        self.path = path
        self.number = int(path.name[:4])
        text = path.read_text(encoding="utf-8")
        assert text.startswith("---\n"), path.name
        end = text.index("\n---\n", 4)
        self.meta = yaml.safe_load(text[4:end])
        self.body = text[end + 5:].lstrip("\n")
        lines = self.body.split("\n")
        header = []
        while lines and (lines[0].startswith("- ") or lines[0].startswith("  - ")):
            header.append(lines.pop(0))
        self.fields, self.order = {}, []
        for line in header:
            match = FIELD.match(line)
            if match:
                name = match.group(1)
                self.order.append(name)
                self.fields.setdefault(name, []).extend([match.group(2)] if match.group(2) else [])
            else:
                assert line.startswith("  - ") and self.order, f"{path.name}: stray header line {line!r}"
                self.fields[self.order[-1]].append(line[4:])
        self.status = (self.fields.get("Status") or [None])[0]
        self.sections = re.findall(r"^## (.+)$", prose(self.body), flags=re.M)

    def links(self, field):
        found = set()
        for item in self.fields.get(field, []):
            match = RECORD_LINK.match(item)
            assert match, f"{self.path.name}: {field} item must start with an ADR link: {item!r}"
            found.add(int(match.group(1)))
        return found

    @property
    def title(self):
        return self.meta["title"].split(" — ", 1)[1]


def records():
    return {record.number: record for record in map(Record, sorted(ADR.glob("[0-9][0-9][0-9][0-9]-*.md")))}


def index_section(name):
    text = (ADR / "README.md").read_text(encoding="utf-8")
    match = re.search(rf"^## {re.escape(name)}\n(.*?)(?=^## |\Z)", text, flags=re.S | re.M)
    assert match, f"README.md has no '## {name}' section"
    return match.group(1)


class AdrRecordTests(unittest.TestCase):
    def setUp(self):
        self.records = records()

    def test_files_are_numbered_contiguously_and_match_their_titles(self):
        names = sorted(path.name for path in ADR.glob("*.md") if path.name not in ("README.md", "template.md"))
        for name in names:
            self.assertRegex(name, FILENAME)
        self.assertEqual(sorted(self.records), list(range(1, len(names) + 1)))
        for number, record in self.records.items():
            with self.subTest(record=record.path.name):
                self.assertIsInstance(record.meta, dict)
                self.assertIsInstance(record.meta.get("title"), str)
                self.assertRegex(record.meta["title"], rf"^ADR {number:04d} — \S")
                self.assertIsInstance(record.meta.get("description"), str)
                self.assertTrue(record.meta["description"].strip())
                self.assertNotRegex(prose(record.body), r"(?m)^# ", "the site renders the title; no body heading")

    def test_headers_use_the_fixed_fields_in_order_and_the_status_vocabulary(self):
        for record in self.records.values():
            with self.subTest(record=record.path.name):
                self.assertEqual(len(record.order), len(set(record.order)), "repeated header field")
                self.assertLessEqual(set(record.order), set(FIELDS))
                self.assertEqual(record.order, sorted(record.order, key=FIELDS.index))
                self.assertEqual(record.order[:2], ["Status", "Date"])
                self.assertIn(record.status, STATUSES)
                self.assertEqual(len(record.fields["Status"]), 1)
                for field in ("Date", "Recorded", "Restructured"):
                    for value in record.fields.get(field, []):
                        self.assertRegex(value, DATE)
                self.assertEqual("Recorded" in record.fields, "Retrospective" in record.fields,
                                 "a retrospective record states both when it was recorded and from what")
                for value in record.fields.get("Retrospective", []):
                    self.assertTrue(value.startswith("reconstructed from "))

    def test_supersede_and_amend_links_resolve_and_are_reciprocated(self):
        for record in self.records.values():
            for field, inverse in INVERSE.items():
                for number in record.links(field):
                    with self.subTest(record=record.path.name, field=field, target=number):
                        self.assertNotEqual(number, record.number)
                        self.assertIn(number, self.records)
                        self.assertIn(record.number, self.records[number].links(inverse),
                                      f"ADR {number:04d} must list ADR {record.number:04d} under {inverse}")
            for item in sum(record.fields.values(), []):
                for match in RECORD_LINK.finditer(item):
                    self.assertEqual(match.group(1), match.group(3))
                    self.assertTrue((ADR / match.group(2)).exists(), match.group(2))
            with self.subTest(record=record.path.name, check="status"):
                self.assertEqual(record.status == "Superseded", bool(record.links("Superseded by")))
                self.assertEqual(record.status == "Amended",
                                 bool(record.links("Amended by")) and not record.links("Superseded by"))

    def test_records_have_the_required_sections_in_order(self):
        for record in self.records.values():
            required = [s for s in SECTIONS if s != "Invalidation conditions" or record.status in IN_FORCE]
            with self.subTest(record=record.path.name):
                for section in required:
                    self.assertEqual(record.sections.count(section), 1, f"needs one '## {section}'")
                positions = [record.sections.index(s) for s in required]
                self.assertEqual(positions, sorted(positions), "required sections out of order")
                if record.status in IN_FORCE:
                    conditions = record.body.split("## Invalidation conditions\n", 1)[1].split("\n## ", 1)[0]
                    self.assertTrue(conditions.strip())

    def test_decision_log_lists_every_record_with_its_current_status(self):
        rows = re.findall(r"^\| \[(\d{4})\]\(([^)]+)\) \| ([^|]+) \| ([^|]+) \| ([^|]+) \|$",
                          index_section("Decision log"), flags=re.M)
        self.assertEqual([int(row[0]) for row in rows], sorted(self.records))
        for number, target, date, status, title in rows:
            record = self.records[int(number)]
            with self.subTest(record=record.path.name):
                self.assertEqual(target, record.path.name)
                self.assertEqual(date, record.fields["Date"][0])
                self.assertEqual(title, record.title)
                self.assertEqual(status.split()[0], record.status)
                linked = {int(n) for n, _, _ in RECORD_LINK.findall(status)}
                self.assertEqual(linked, record.links("Superseded by") | record.links("Amended by"))

    def test_current_decisions_list_exactly_the_records_in_force(self):
        def entries(section):
            listed = {}
            for line in index_section(section).splitlines():
                match = re.match(r"^- " + RECORD_LINK.pattern, line)
                if match:
                    self.assertNotIn(int(match.group(1)), listed, f"ADR {match.group(1)} listed twice")
                    listed[int(match.group(1))] = line
            return listed
        current = entries("Current decisions")
        self.assertEqual(set(current), {n for n, r in self.records.items() if r.status in IN_FORCE})
        for number, line in current.items():
            with self.subTest(record=self.records[number].path.name):
                self.assertEqual({int(n) for n, _, _ in RECORD_LINK.findall(line)} - {number},
                                 self.records[number].links("Amended by"))
        self.assertEqual(set(entries("Open proposals")), {n for n, r in self.records.items() if r.status == "Proposed"})

    def test_relative_links_resolve(self):
        for page in sorted(ADR.glob("*.md")):
            for target in re.findall(r"\]\(([^)\s]+)\)", prose(page.read_text(encoding="utf-8"))):
                if re.match(r"^(?:[a-z][a-z0-9+.-]*:|#)", target):
                    continue
                with self.subTest(page=page.name, target=target):
                    self.assertTrue((page.parent / target.split("#", 1)[0]).exists())


if __name__ == "__main__":
    unittest.main()
