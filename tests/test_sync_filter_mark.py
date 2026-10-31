"""The sending device filters, the receiver trusts the mark (ADR 0122).

A device whose Privacy Filter runs on a GPU goes over the memories it sends
and marks the ones the filter leaves unchanged; a receiver with the same
filter skips the model for those but still applies the rules and keeps the
most redacted version. Workspaces stand in for the two devices; the model is
a stand-in that finds the word SECRET."""
import json
import os
import re
import tempfile
import unittest
from unittest.mock import patch

import test_indexing_retrieval as indexing
import test_redaction
from imprint import config, imprint_graph as kg, model_profiles, privacy_filter, sync_delta, sync_ws, vectorstore as vs

R = test_redaction.R
SECRETS = test_redaction.SECRETS
IDENTITY = model_profiles.PRIVACY_FILTER_IDENTITY


class FakeModel:
    """Privacy Filter spans: every SECRET word; remembers what it read."""

    def __init__(self):
        self.read = []

    def spans(self, text):
        self.read.append(text)
        return [(m.start(), m.end(), "secret") for m in re.finditer(r"SECRET", text)]


class FilterMarkTests(unittest.TestCase):
    setUp_storage = indexing.StorageTests.setUp
    cleanup = indexing.StorageTests.cleanup
    sync_buffer = test_redaction.WritePathTests.sync_buffer
    rows = test_redaction.WritePathTests.rows
    contents = test_redaction.WritePathTests.contents

    def setUp(self):
        self.setUp_storage()
        self.addCleanup(kg._close_all)
        self.model = FakeModel()
        for patcher in (patch.object(privacy_filter, "spans", side_effect=self.model.spans),
                        patch.dict(os.environ, {"IMPRINT_WORKSPACE": "", "IMPRINT_REDACT_PRIVACY_FILTER": "1"})):
            patcher.start()
            self.addCleanup(patcher.stop)
        config.register_workspace("team")

    def gpu(self, on=True):
        return patch.object(privacy_filter, "accelerated", return_value=on)

    def record(self, content, chunk_index=0, vector=(0., 1., 0.), **extra):
        return {"id": vs._make_id(content, "peer", "peer/notes.md", chunk_index), "content": content,
                "project": "peer", "source": "peer/notes.md", "chunk_index": chunk_index, "vector": list(vector),
                "embedding_identity": config.embedding_identity(),
                "recorded_device": {"id": "sender0001", "name": "sender", "os": "linux"}, **extra}

    def receive(self, records, workspace="default"):
        self.model.read.clear()
        return sync_ws.process_import_buffer(self.sync_buffer(records), workspace)["memories"]

    # ── Receiver ─────────────────────────────────────────────────
    def test_a_record_marked_with_this_filter_skips_the_model(self):
        marked = self.record("Release notes, nothing to hide", privacy_filter=IDENTITY)
        unmarked = self.record("Ship SECRET on Friday", chunk_index=1)
        other = self.record("Another SECRET plan", chunk_index=2, privacy_filter=IDENTITY.replace("q4", "q8"))
        stats = self.receive([marked, unmarked, other])
        self.assertEqual(stats, {"inserted": 3, "updated": 0, "skipped": 0})
        self.assertEqual(self.model.read, ["Ship SECRET on Friday", "Another SECRET plan"])
        self.assertEqual(self.contents(), sorted(["Release notes, nothing to hide",
                                                  f"Ship {R('secret', 'SECRET')} on Friday",
                                                  f"Another {R('secret', 'SECRET')} plan"]))
        # The mark is a transfer field, never stored.
        self.assertFalse(any(vs.FILTER_MARK in row.payload for row in self.rows()))
        kept = {row.payload["content"]: row for row in self.rows()}["Release notes, nothing to hide"]
        self.assertEqual((kept.payload["_mid"], kept.vector["dense"]), (marked["id"], [0., 1., 0.]))

    def test_the_rules_still_run_on_a_marked_record(self):
        token = SECRETS["github-token"]
        marked = self.record(f"Deploy with {token} SECRET", privacy_filter=IDENTITY, vector=(0., 0., 1.))
        self.assertEqual(self.receive([marked]), {"inserted": 1, "updated": 0, "skipped": 0})
        self.assertEqual(self.model.read, [])  # The model trusted the sender: SECRET stays.
        expected = f"Deploy with {R('github-token', token)} SECRET"
        (row,) = self.rows()
        self.assertEqual(row.payload["content"], expected)
        # Changed by the rules: the ID of its new content, embedded again.
        self.assertEqual(row.payload["_mid"], vs._make_id(expected, "peer", "peer/notes.md", 0))
        self.assertEqual(row.vector["dense"], [1., 0., 0.])

    def test_a_marked_record_still_takes_a_stricter_version_held_here(self):
        stricter = f"Ship {R('secret', 'Friday')} release"
        vs.store_batch([{"content": stricter, "project": "peer", "source": "peer/notes.md", "chunk_index": 0}])
        self.receive([self.record("Ship Friday release", privacy_filter=IDENTITY)])
        self.assertEqual(self.contents(), [stricter])

    def test_with_the_filter_off_the_mark_changes_nothing(self):
        with patch.dict(os.environ, {"IMPRINT_REDACT_PRIVACY_FILTER": "0"}):
            self.receive([self.record("A SECRET", privacy_filter=IDENTITY), self.record("B SECRET", 1)])
        self.assertEqual(self.model.read, [])
        self.assertEqual(self.contents(), ["A SECRET", "B SECRET"])

    def test_a_marked_stream_passes_the_stream_check(self):
        """Receivers check frames, not record fields; an older release's
        importer copies the fields it knows, so it ignores the mark."""
        batch = {"kind": "batch", "dataset": "memories", "seq": 1,
                 "records": [self.record("x", privacy_filter=IDENTITY)]}
        self.assertTrue(sync_ws._stream_line_ok(batch, after_meta=True))

    # ── Sender ───────────────────────────────────────────────────
    def sent(self, workspace="default"):
        records = []
        for line in sync_ws.stream_export_lines(workspace):
            frame = json.loads(line)
            if frame.get("kind") == "batch" and frame["dataset"] == "memories":
                records += frame["records"]
        return {r["content"]: r for r in records}

    def store_unfiltered(self, texts):
        """Memories stored before this device turned its filter on."""
        with patch.dict(os.environ, {"IMPRINT_REDACT_PRIVACY_FILTER": "0"}):
            vs.store_batch([{"content": text, "project": "p", "source": "notes.md", "chunk_index": i}
                            for i, text in enumerate(texts)])

    def test_a_gpu_sender_marks_what_its_filter_leaves_unchanged(self):
        self.store_unfiltered(["Clean note", "Has SECRET inside"])
        with self.gpu():
            sent = self.sent()
        self.assertEqual(sent["Clean note"][vs.FILTER_MARK], IDENTITY)
        # Changed by the filter: sent as stored, with its stored ID and vector, unmarked.
        changed = sent["Has SECRET inside"]
        self.assertNotIn(vs.FILTER_MARK, changed)
        self.assertEqual(changed["id"], vs._make_id("Has SECRET inside", "p", "notes.md", 1))
        # The receiver filters that one only, relabels it and embeds it again.
        self.assertEqual(self.receive(list(sent.values()), "team"), {"inserted": 2, "updated": 0, "skipped": 0})
        self.assertEqual(self.model.read, ["Has SECRET inside"])
        rows = {row.payload["content"]: row for row in self.rows("team")}
        redacted = rows[f"Has {R('secret', 'SECRET')} inside"]
        self.assertEqual(redacted.payload["_mid"], vs._make_id(redacted.payload["content"], "p", "notes.md", 1))
        self.assertEqual(redacted.vector["dense"], [1., 0., 0.])
        self.assertEqual(rows["Clean note"].payload["_mid"], sent["Clean note"]["id"])

    def test_rules_newer_than_the_stored_text_also_leave_a_record_unmarked(self):
        token = SECRETS["npm-token"]
        client, coll = vs._ensure_collection()
        from qdrant_client import models
        # Stored by a release whose rules missed this token format.
        client.upsert(coll, [models.PointStruct(id=vs._point_uuid("a" * 16), vector={"dense": [0., 1., 0.]},
                                                payload={"_mid": "a" * 16, "content": f"npm {token}", "project": "p",
                                                         "source": "n.md", "chunk_index": 0})])
        with self.gpu():
            self.assertNotIn(vs.FILTER_MARK, self.sent()[f"npm {token}"])

    def test_a_cpu_sender_or_a_filter_off_sends_unmarked(self):
        self.store_unfiltered(["Clean note"])
        with self.gpu(False):
            self.assertNotIn(vs.FILTER_MARK, self.sent()["Clean note"])
        self.assertEqual(self.model.read, [])
        with self.gpu(), patch.dict(os.environ, {"IMPRINT_REDACT_PRIVACY_FILTER": "0"}):
            self.assertNotIn(vs.FILTER_MARK, self.sent()["Clean note"])

    def test_a_sender_whose_filter_fails_sends_unmarked(self):
        self.store_unfiltered(["Clean note"])
        with self.gpu(), patch.object(privacy_filter, "spans", side_effect=RuntimeError("CUDA out of memory")):
            self.assertNotIn(vs.FILTER_MARK, self.sent()["Clean note"])

    def test_a_source_chunks_prefix_is_not_filtered(self):
        records = [{"content": "[SECRET/app.py]\nprint(1)", "source_prefix": "[SECRET/app.py]\n"},
                   {"content": "[a.py]\nSECRET", "source_prefix": "[a.py]\n"}]
        with self.gpu():
            marked = vs.filtered_for_peer(records)
        self.assertEqual([r.get(vs.FILTER_MARK) for r in marked], [IDENTITY, None])
        self.assertEqual(self.model.read, ["print(1)", "SECRET"])

    def test_paired_sync_chunks_are_marked_too(self):
        self.store_unfiltered(["Clean note"])
        mid = vs._make_id("Clean note", "p", "notes.md", 0)
        with self.gpu():
            (lines,) = list(sync_delta.export_chunks("default", {"memories": [mid]}))
        (record,) = json.loads(lines[1])["records"]
        self.assertEqual(record[vs.FILTER_MARK], IDENTITY)

    def test_full_sync_sends_each_batch_as_it_is_filtered(self):
        self.store_unfiltered(["one", "two", "three"])
        with self.gpu():
            lines = sync_ws.export_lines("default", batch_size=1)
            self.assertEqual(json.loads(next(lines))["kind"], "meta")
            self.assertEqual(len(json.loads(next(lines))["records"]), 1)
            self.assertEqual(len(self.model.read), 1)  # The rest is not filtered yet.
            rest = [json.loads(line) for line in lines]
        self.assertEqual(rest[-1], {"kind": "done"})
        self.assertEqual(len(self.model.read), 3)


if __name__ == "__main__":
    unittest.main()
