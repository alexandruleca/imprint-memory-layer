"""Long tool events are one chunk, embedded from a reduced view (ADR 0162)."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from imprint import cli_conversations as learner, config, conversation_source, embeddings, long_events, \
    source_memory, vectorstore as vs


def log(lines=600, error_at=300):
    rows = [f"step {i}: compiled module_{i}.o in {i % 7}ms" for i in range(lines)]
    rows[error_at] = "ERROR: linker could not resolve symbol imprint_open"
    return "make: entering build\n" + "\n".join(rows) + "\nbuild finished with 1 error"


def transcript(path: Path, output: str) -> Path:
    rows = [
        {'type': 'user', 'message': {'role': 'user', 'content': 'Why does the build fail?'}},
        {'type': 'assistant', 'message': {'content': [
            {'type': 'text', 'text': 'Running the build.'},
            {'type': 'tool_use', 'id': 'call1', 'name': 'Bash', 'input': {'command': 'make'}}]}},
        {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 'call1', 'content': output}]}},
        {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'The linker cannot find imprint_open.'}]}},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    return path


class ChunkSpanTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def prepare(self, output):
        return conversation_source.prepare(str(transcript(Path(self.dir.name) / "t.jsonl", output)))

    def test_a_long_tool_output_is_one_chunk_and_the_snapshot_rebuilds_exactly(self):
        document = self.prepare(log())
        text, events = document["text"], document["events"]
        long = next(e for e in events if e["kind"] == "tool_output")
        self.assertGreater(long["end"] - long["start"], config.CHUNK_HARD_MAX)
        cuts = long_events.chunk_spans(text, events)
        self.assertIn((long["start"], long["end"]), cuts)
        rows = [{"source_start": a, "source_end": b, "content": text[a:b]} for a, b in cuts]
        self.assertEqual(source_memory.reconstruct(rows, len(text)), text)
        self.assertTrue(all(b - a <= config.CHUNK_HARD_MAX for a, b in cuts if (a, b) != (long["start"], long["end"])))
        self.assertLess(len(cuts), len(source_memory.spans(text)))

    def test_without_long_tool_events_the_chunks_are_unchanged(self):
        document = self.prepare("short output")
        self.assertEqual(long_events.chunk_spans(document["text"], document["events"]),
                         source_memory.spans(document["text"]))

    def test_a_long_message_is_chunked_as_before(self):
        text = "x" * 100
        events = [{"kind": "message", "start": 0, "end": 100}]
        with patch.object(config, "CHUNK_HARD_MAX", 10), patch.object(config, "CHUNK_SIZE_PROSE", 10):
            self.assertEqual(long_events.chunk_spans(text, events), source_memory.spans(text))

    def test_an_event_longer_than_a_piece_is_split_at_lines_without_gaps(self):
        document = self.prepare(log(lines=2000))
        text, events = document["text"], document["events"]
        long = next(e for e in events if e["kind"] == "tool_output")
        with patch.object(long_events, "PIECE_MAX", 20_000):
            cuts = long_events.chunk_spans(text, events)
        pieces = [(a, b) for a, b in cuts if long["start"] <= a and b <= long["end"]]
        self.assertGreater(len(pieces), 2)
        self.assertEqual(pieces[0][0], long["start"])
        self.assertEqual(pieces[-1][1], long["end"])
        self.assertTrue(all(text[b - 1] == "\n" for a, b in pieces))
        rows = [{"source_start": a, "source_end": b, "content": text[a:b]} for a, b in cuts]
        self.assertEqual(source_memory.reconstruct(rows, len(text)), text)


class EmbeddingTextTests(unittest.TestCase):
    def chunk(self, body, kind="tool_output"):
        header = "\n--- " + json.dumps({"event": 2, "role": "tool", "kind": kind, "turn": 1}) + " ---\n"
        return header + body + "\n"

    def test_ordinary_chunks_are_embedded_as_they_are(self):
        self.assertEqual(long_events.embedding_text("plain text"), "plain text")
        short = self.chunk("brief output")
        self.assertEqual(long_events.embedding_text(short), short)

    def test_a_long_tool_output_keeps_its_head_errors_and_tail_within_a_chunk(self):
        view = long_events.embedding_text(self.chunk(log()))
        self.assertLessEqual(len(view), config.CHUNK_SIZE_PROSE + 200)
        self.assertTrue(view.startswith('--- {"event": 2'))
        self.assertIn("make: entering build", view)
        self.assertIn("ERROR: linker could not resolve symbol imprint_open", view)
        self.assertIn("build finished with 1 error", view)
        self.assertIn("similar lines …]", view)

    def test_distinct_lines_are_kept_by_head_signal_and_tail(self):
        rows = [f"{word} line of the report" for word in ("alpha beta gamma delta".split() * 400)]
        rows[700] = "Traceback (most recent call last): failure in parser"
        view = long_events.reduce("\n".join(rows), 3000)
        self.assertLessEqual(len(view), 3200)
        self.assertIn("Traceback (most recent call last): failure in parser", view)
        self.assertIn("lines omitted …]", view)
        self.assertTrue(view.startswith(rows[0]))
        self.assertTrue(view.endswith(rows[-1]))

    def test_one_huge_line_keeps_its_start_and_end(self):
        line = "{" + ", ".join(f'"k{i}": {i}' for i in range(5000)) + "}"
        view = long_events.reduce(line, 3000)
        self.assertLess(len(view), 3100)
        self.assertTrue(view.startswith('{"k0": 0'))
        self.assertTrue(view.endswith('"k4999": 4999}'))

    def test_a_long_message_event_is_not_reduced(self):
        chunk = self.chunk("words " * 3000, kind="message")
        self.assertEqual(long_events.embedding_text(chunk), chunk)

    def test_the_embedding_entry_points_use_the_view(self):
        seen = []
        import numpy as np

        def raw(texts):
            seen.extend(texts)
            return np.ones((len(texts), config.EMBEDDING_DIM), dtype=np.float32)

        chunk = self.chunk(log())
        with patch.object(embeddings, "_embed_raw", side_effect=raw):
            embeddings.embed_documents_batch([chunk, "short"], batch_size=4)
            embeddings.embed_document(chunk)
        view = long_events.embedding_text(chunk)
        self.assertEqual(sorted(seen), sorted([view, "short", view]))


class LearnTests(unittest.TestCase):
    def test_learning_stores_the_long_output_as_one_exact_chunk(self):
        with tempfile.TemporaryDirectory() as data, patch.dict("os.environ", {"IMPRINT_DATA_DIR": data}), \
                patch.object(learner, "identify", return_value=("conversation/test", "app")), \
                patch.object(learner, "owned", return_value=True), patch.object(config, "BATCH_SIZE", 8), \
                patch("imprint.workspace_policy.is_excluded", return_value=False), \
                patch.object(learner.tagger, "build_payload_tags", return_value={}), \
                patch.object(vs, "complete_conversation"):
            path = transcript(Path(data) / "t.jsonl", log())
            records = []

            def replace(rows, *args, **kwargs):
                records.extend(rows)
                return len(rows), 0

            with patch.object(vs, "replace_source", side_effect=replace):
                learner.index_transcript(str(path), 1.0)
        text = records[0]["_source_document"]["text"]
        tool = [r for r in records if r["event_kinds"] == ["tool_output"]]
        self.assertEqual(len(tool), 1)
        self.assertGreater(len(tool[0]["content"]), config.CHUNK_HARD_MAX)
        self.assertEqual(tool[0]["content"], text[tool[0]["source_start"]:tool[0]["source_end"]])
        self.assertEqual([r["chunk_index"] for r in records], list(range(len(records))))
        self.assertEqual(source_memory.reconstruct(records, len(text)), text)


if __name__ == "__main__":
    unittest.main()
