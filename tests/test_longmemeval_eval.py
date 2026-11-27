"""The LongMemEval harness scores and replays the way ai-memory's evaluator does."""
import unittest
import uuid

from evals.longmemeval import dataset, scoring
from imprint.mcp_output import compact_search
from imprint.retrieval import lexical_score


def ids(n):
    return [uuid.uuid4() for _ in range(n)]


class ScoringMatchesAiMemory(unittest.TestCase):
    """Ports of ai-memory's evals/src/retrieval/score.rs tests."""

    def test_a_top_one_hit_scores_across_all_ks(self):
        (e,) = ids(1)
        s = scoring.score_question({e}, [e], ks=(1, 5))
        self.assertEqual((s["hit@1"], s["hit@5"], s["recall@1"], s["recall_all@1"], s["recall_all@5"]), (1, 1, 1, 1, 1))

    def test_evidence_below_the_cutoff_does_not_count(self):
        e, *others = ids(4)
        s = scoring.score_question({e}, [*others, e], ks=(1, 3, 5))
        self.assertEqual((s["hit@1"], s["hit@3"], s["hit@5"], s["recall_all@3"], s["recall_all@5"]), (0, 0, 1, 0, 1))

    def test_duplicate_session_chunks_fill_one_slot_not_three(self):
        e1, e2, noise = ids(3)
        s = scoring.score_question({e1, e2}, [None, noise, noise, e1, e1, e2], ks=(0, 2, 3))
        self.assertEqual((s["hit@2"], s["recall@2"], s["recall_all@0"], s["recall_all@2"], s["recall_all@3"]),
                         (1, 0.5, 0, 0, 1))
        self.assertEqual(s["retrieved_sessions"], 3)

    def test_partial_multi_session_recall_is_fractional(self):
        e1, e2 = ids(2)
        s = scoring.score_question({e1, e2}, [e1], ks=(5,))
        self.assertEqual((s["recall@5"], s["hit@5"], s["recall_all@5"]), (0.5, 1, 0))

    def test_unattributed_hits_and_random_sessions_never_score(self):
        (e,) = ids(1)
        for ranked in ([None, None], ids(10)):
            s = scoring.score_question({e}, ranked)
            self.assertEqual([s[f"{m}@{k}"] for m in ("hit", "recall", "recall_all") for k in scoring.KS], [0] * 12)

    def test_empty_evidence_does_not_score_as_complete(self):
        s = scoring.score_question(set(), ids(1), ks=(0, 5))
        self.assertEqual((s["recall_all@0"], s["recall_all@5"], s["recall@5"]), (0, 0, 0))

    def test_nearest_rank_percentile_and_rounded_up_tokens(self):
        self.assertEqual(scoring.percentile([5, 1, 3, 2, 4], 0.5), 3)
        self.assertEqual(scoring.percentile(list(range(1, 21)), 0.95), 19)
        self.assertEqual(scoring.percentile([], 0.5), 0)
        self.assertEqual((scoring.estimate_tokens(5), scoring.estimate_tokens(8), scoring.estimate_tokens(0)), (2, 2, 0))

    def test_aggregate_macro_averages_per_type_and_overall(self):
        rows = [{"question_type": "a", "latency_ms": 10, "context_tokens": 4, **scoring.score_question({1}, [1])},
                {"question_type": "b", "latency_ms": 30, "context_tokens": 8, **scoring.score_question({1}, [2])}]
        out = scoring.aggregate(rows)
        self.assertEqual(out["overall"]["hit@1"], 0.5)
        self.assertEqual((out["a"]["hit@1"], out["b"]["hit@1"]), (1, 0))
        self.assertEqual((out["overall"]["latency_p50_ms"], out["overall"]["context_tokens_mean"]), (10, 6))


class ReplayMatchesAiMemory(unittest.TestCase):
    def test_session_uuid_is_their_uuid_v5(self):
        # ai-memory: Uuid::new_v5(&NAMESPACE_OID, "lme-<question>\n<raw>")
        self.assertEqual(scoring.stored_session_uuid("q1", "answer_1"),
                         uuid.uuid5(uuid.NAMESPACE_OID, "lme-q1\nanswer_1"))
        self.assertNotEqual(scoring.stored_session_uuid("q1", "s"), scoring.stored_session_uuid("q2", "s"))

    def test_turn_text_prefixes_dates_and_caps_like_the_hook_replay(self):
        date = "2023/05/20 (Sat) 02:21"
        self.assertEqual(scoring.turn_text("user", date, "hi"), f"[session date: {date}] hi")
        long_reply = "é" * 3000  # 6,000 bytes
        reply = scoring.turn_text("assistant", date, long_reply)
        self.assertEqual(reply, f"[session date: {date}] " + "é" * 1000)
        self.assertLessEqual(len(scoring.turn_text("user", date, "x" * 40_000).encode()), 16 * 1024)
        self.assertIsNone(scoring.turn_text("system", date, "x"))

    def test_abstention_questions_are_left_out_after_the_sample_prefix(self):
        qs = [{"question_id": i, "question_type": t, "haystack_sessions": [], "haystack_session_ids": [],
               "haystack_dates": []} for i, t in [("a", "x"), ("b_abs", "x"), ("c", "y"), ("d", "x")]]
        scored, abstention = dataset.select(qs, sample=3)
        self.assertEqual(([q["question_id"] for q in scored], abstention), (["a", "c"], 1))
        scored, _ = dataset.select(qs, stratify=1)
        self.assertEqual([q["question_id"] for q in scored], ["a", "c"])


class ImprintOutputIsReadExactly(unittest.TestCase):
    def results(self):
        rows = []
        for i in range(4):
            key = uuid.uuid4()
            rows.append({"id": f"{i:016x}", "similarity": 0.5 if i != 1 else 0.05, "lexical_score": 0,
                         "project": "lme-q", "type": "finding", "source": scoring.session_source(key),
                         "content": f"[session date: 2023/01/0{i}] turn {i} " + "word " * (400 if i == 0 else 3)})
        return rows

    def test_hits_snippets_and_skipped_numbers(self):
        rows = self.results()
        text = compact_search(rows, "turn word", "lme-q", 0, 10, 4000, 0.2)
        hits = scoring.parse_hits(text)
        # Row 1 is under the relevance threshold: Imprint shows [1], [3], [4].
        self.assertEqual([h["id"] for h in hits], [rows[0]["id"], rows[2]["id"], rows[3]["id"]])
        self.assertEqual([h["source"] for h in hits], [rows[0]["source"], rows[2]["source"], rows[3]["source"]])
        self.assertEqual(hits[2]["snippet"], rows[3]["content"])
        self.assertNotIn("expand with get_memory", hits[0]["snippet"])
        self.assertIn(hits[0]["snippet"], rows[0]["content"])
        self.assertEqual(len(hits[0]["snippet"]), 900)

    def test_a_budget_footer_is_not_part_of_the_last_snippet(self):
        rows = [dict(r, similarity=0.5) for r in self.results()] * 3
        rows = [dict(r, id=f"{i:016x}") for i, r in enumerate(rows)]
        text = compact_search(rows, "turn word", "lme-q", 0, 10, 1500, 0.2)
        self.assertIn("More results may exist", text)
        hits = scoring.parse_hits(text)
        self.assertTrue(hits)
        self.assertNotIn("More results", hits[-1]["snippet"])

    def test_session_provenance_cannot_match_a_query_term(self):
        source = scoring.session_source(uuid.uuid4())
        base = {"content": "nothing relevant here"}
        for query in ["s", "s3", "what did I say in session 2023", source, source.replace("-", "")]:
            self.assertEqual(lexical_score(query, {**base, "source": source}), lexical_score(query, base), query)


if __name__ == "__main__":
    unittest.main()
