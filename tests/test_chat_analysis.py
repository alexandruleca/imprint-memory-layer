import json
import os
import re
import tempfile
import threading
import unittest
from unittest.mock import patch

from imprint import chat, config_schema
from imprint.chat_analysis import AnalysisCancelled, SectionAnalysis, WORKER_SYSTEM
from test_chat_stream import LocalModel, ProviderStream, call_chunk, llama_binding, text_chunk, tool


def exhaust(generator):
    events = []
    while True:
        try:
            events.append(next(generator))
        except StopIteration as result:
            return events, result.value


class SectionAnalysisTests(unittest.TestCase):
    def analyzer(self, workers=2, cancelled=lambda: False, generate=None):
        self.requests = []
        self.active = self.maximum = 0

        def produce(messages, output):
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            self.requests.append(messages)
            content = json.loads(messages[-1]["content"])
            try:
                yield content["previous_notes"] + (" TAIL FACT" if "TAIL FACT" in content["section"] else " finding")
            finally:
                self.active -= 1

        return SectionAnalysis(context=2400, workers=workers, max_tokens=100,
                               measure=lambda messages, tools: len(json.dumps(messages, ensure_ascii=False)),
                               generate=generate or produce, cancelled=cancelled)

    def test_short_questions_do_not_start_workers(self):
        analysis = self.analyzer()
        original = [{"role": "system", "content": "instructions"}, {"role": "user", "content": "Question?"}]
        events, messages = exhaust(analysis.prepare(original, lambda: None))
        self.assertEqual((events, messages, self.requests, analysis.sources), ([], original, [], {}))

    def test_two_sequential_workers_cover_every_character_and_keep_tail_evidence(self):
        analysis = self.analyzer()
        text = ("heading\nαβγ😀 " * 650) + "TAIL FACT"
        original = [{"role": "system", "content": "instructions"}, {"role": "user", "content": text}]
        events, messages = exhaust(analysis.prepare(original, lambda: None))
        self.assertIn("TAIL FACT", messages[-1]["content"])
        self.assertIn("Worker 1", messages[-1]["content"])
        self.assertIn("Worker 2", messages[-1]["content"])
        self.assertEqual(self.maximum, 1)
        self.assertEqual(self.active, 0)
        self.assertTrue(all(event["type"] == "analysis" for event in events))
        previous_end = 0
        reconstructed = []
        for request in self.requests:
            self.assertTrue(analysis.fits(request, None, 300))
            section = json.loads(request[-1]["content"])
            start, end = map(int, section["source"].split(":")[1].split("-"))
            self.assertEqual(start, previous_end)
            reconstructed.append(section["section"][start - section["overlap_start"]:])
            previous_end = end
        self.assertEqual("".join(reconstructed), text)
        self.assertTrue(any(json.loads(request[-1]["content"])["previous_notes"] for request in self.requests[2:]))
        self.assertEqual(analysis.read(next(iter(analysis.sources)), len(text) - 9, 9).splitlines()[1], "TAIL FACT")
        self.assertEqual(original[-1]["content"], text)

    def test_worker_count_can_be_changed_without_limiting_source_coverage(self):
        analysis = self.analyzer(workers=3)
        _, messages = exhaust(analysis.prepare([{"role": "user", "content": "x " * 6000}], lambda: None))
        self.assertIn("Worker 3", messages[0]["content"])
        self.assertEqual(analysis.sections, len(self.requests))

    def test_history_is_dropped_before_starting_workers(self):
        analysis = self.analyzer()
        messages = [{"role": "system", "content": "instructions"}, {"role": "user", "content": "x" * 5000},
                    {"role": "assistant", "content": "old"}, {"role": "user", "content": "new"}]
        _, fitted = exhaust(analysis.prepare(messages, lambda: None))
        self.assertEqual(fitted, [messages[0], messages[-1]])
        self.assertEqual(self.requests, [])

    def test_large_tool_result_keeps_call_pair_and_analyzes_its_tail(self):
        analysis = self.analyzer()
        call = {"role": "assistant", "content": "", "tool_calls": [{"id": "one"}]}
        messages = [{"role": "user", "content": "Find tail fact"}, call,
                    {"role": "tool", "tool_call_id": "one", "content": "x " * 5000 + "TAIL FACT"}]
        _, fitted = exhaust(analysis.prepare(messages, lambda: None))
        self.assertEqual(fitted[1], call)
        self.assertEqual(fitted[2]["tool_call_id"], "one")
        self.assertIn("TAIL FACT", fitted[2]["content"])

    def test_disabled_workers_fail_explicitly_on_oversized_input(self):
        analysis = self.analyzer(workers=0)
        with self.assertRaisesRegex(ValueError, "enable chat.analysis_workers"):
            exhaust(analysis.prepare([{"role": "user", "content": "x" * 5000}], lambda: None))
        self.assertEqual(self.requests, [])

    def test_cancellation_closes_worker_and_prevents_following_sections(self):
        cancelled = threading.Event()
        closed = []

        def produce(*args):
            try:
                cancelled.set()
                yield "partial"
            finally:
                closed.append(True)

        analysis = self.analyzer(cancelled=cancelled.is_set, generate=produce)
        with self.assertRaises(AnalysisCancelled):
            exhaust(analysis.prepare([{"role": "user", "content": "x" * 5000}], lambda: None))
        self.assertEqual(closed, [True])
        self.assertEqual(analysis.sections, 0)

    def test_empty_findings_and_section_limit_do_not_silently_skip_evidence(self):
        def empty(*args):
            yield " "
        analysis = self.analyzer(generate=empty)
        with self.assertRaisesRegex(ValueError, "no findings"):
            exhaust(analysis.prepare([{"role": "user", "content": "x" * 5000}], lambda: None))
        analysis = self.analyzer()
        with patch("imprint.chat_analysis.MAX_SECTIONS", 1), self.assertRaisesRegex(ValueError, "section reply limit"):
            exhaust(analysis.prepare([{"role": "user", "content": "x" * 5000}], lambda: None))

    def test_original_reads_are_reply_scoped_and_validate_ranges(self):
        first, second = self.analyzer(), self.analyzer()
        first.sources["chat-source-1"] = "private original"
        with self.assertRaisesRegex(ValueError, "unknown source"):
            second.read("chat-source-1")
        for start, size in ((-1, 10), (True, 10), (100, 10), (0, 4001), (0, 0)):
            with self.assertRaises(ValueError):
                first.read("chat-source-1", start, size)

    def test_separate_replies_never_reuse_source_ids(self):
        first, second = self.analyzer(), self.analyzer()
        for analysis in (first, second):
            exhaust(analysis.prepare([{"role": "user", "content": "evidence " * 900}], lambda: None))
        self.assertTrue(set(first.sources).isdisjoint(second.sources))


class WorkerSettingTests(unittest.TestCase):
    def test_default_override_environment_and_reset_use_disposable_config(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"IMPRINT_DATA_DIR": directory}):
            with patch.dict(os.environ, {}, clear=True):
                os.environ["IMPRINT_DATA_DIR"] = directory
                self.assertEqual(config_schema.resolve("chat.analysis_workers"), (2, "default"))
                self.assertEqual(config_schema.set_value("chat.analysis_workers", "3"), 3)
                self.assertEqual(config_schema.resolve("chat.analysis_workers"), (3, "config"))
                os.environ["IMPRINT_CHAT_ANALYSIS_WORKERS"] = "1"
                self.assertEqual(config_schema.resolve("chat.analysis_workers"), (1, "env"))
                del os.environ["IMPRINT_CHAT_ANALYSIS_WORKERS"]
                config_schema.reset_value("chat.analysis_workers")
                self.assertEqual(config_schema.resolve("chat.analysis_workers"), (2, "default"))
                for invalid in ("-1", "9", "1.5", "true"):
                    with self.assertRaises(ValueError):
                        config_schema.set_value("chat.analysis_workers", invalid)
                for invalid in (-1, 9, True, 1.5):
                    with self.assertRaises(ValueError):
                        config_schema.analysis_worker_count(invalid)


class WorkerSettingAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_settings_api_exposes_default_choices_and_validates_changes(self):
        import httpx
        from imprint.api import app
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"IMPRINT_DATA_DIR": directory}):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)),
                                         base_url="http://127.0.0.1:8420") as client:
                settings = (await client.get("/api/config")).json()["settings"]
                setting = next(item for item in settings if item["key"] == "chat.analysis_workers")
                self.assertEqual((setting["value"], setting["default"]), (2, 2))
                self.assertEqual([choice["value"] for choice in setting["choices"]], list(range(9)))
                self.assertEqual((await client.put("/api/config/chat.analysis_workers", json={"value": "4"})).status_code, 200)
                self.assertEqual(config_schema.resolve("chat.analysis_workers"), (4, "config"))
                self.assertEqual((await client.put("/api/config/chat.analysis_workers", json={"value": "9"})).status_code, 400)
                self.assertEqual(config_schema.resolve("chat.analysis_workers"), (4, "config"))
                self.assertEqual((await client.delete("/api/config/chat.analysis_workers")).status_code, 200)
                self.assertEqual(config_schema.resolve("chat.analysis_workers"), (2, "default"))


class AnalysisChatIntegrationTests(unittest.TestCase):
    def test_remote_large_tool_result_uses_same_provider_and_keeps_tail_evidence(self):
        from unittest.mock import MagicMock
        client = MagicMock()
        requests = []

        def response(**kwargs):
            requests.append(kwargs)
            if kwargs["tools"] is None:
                content = json.loads(kwargs["messages"][-1]["content"])
                notes = content["previous_notes"] + (" TAIL FACT" if "TAIL FACT" in content["section"] else " evidence")
                return ProviderStream([text_chunk(notes)])
            if kwargs["messages"][-1]["role"] == "tool":
                self.assertIn("TAIL FACT", kwargs["messages"][-1]["content"])
                return ProviderStream([text_chunk("Synthesized tail evidence.")])
            return ProviderStream([call_chunk(0, "file_chunks", '{"source":"large.py"}', id="one")])

        client.chat.completions.create.side_effect = response
        run = lambda **kwargs: "source evidence " * 3500 + "TAIL FACT"
        with patch.object(chat, "_is_remote_provider", return_value=True), \
                patch.object(chat, "_get_chat_provider", return_value="ollama"), \
                patch.object(chat, "_get_openai_client", return_value=client), \
                patch.dict(chat.TOOLS, tool("file_chunks", run)), \
                patch.dict(os.environ, {"IMPRINT_CHAT_ANALYSIS_WORKERS": "2", "IMPRINT_CHAT_N_CTX": "8192"}):
            events = list(chat.stream_reply([], "Find the tail fact", workspace="alpha"))
        self.assertFalse([event for event in events if event["type"] == "error"], events)
        self.assertEqual(next(event["result"] for event in events if event["type"] == "tool_result"), run())
        workers = [request for request in requests if request["tools"] is None]
        self.assertGreater(len(workers), 1)
        self.assertTrue(all(request["timeout"] == 120 for request in workers))
        self.assertEqual(events[-2], {"type": "assistant_message", "text": "Synthesized tail evidence."})

    def test_local_coordinator_can_reread_original_after_tool_free_workers(self):
        text = "Compare these sections:\n" + "source evidence " * 3500 + "TAIL FACT"

        class Model(LocalModel):
            def tokenize(self, text, **kwargs):
                self.prompt = json.loads(text)
                return [0] * (len(text) // 4 + 1)

            def generate(self, tokens, **sampling):
                self.requests.append({**self.prompt, **sampling})
                messages = self.prompt["messages"]
                if messages[0]["content"] == WORKER_SYSTEM:
                    result = "Findings with source references."
                elif messages[-1]["role"] == "tool":
                    result = "Verified TAIL FACT."
                else:
                    source_id = re.search(r"Section analysis of (chat-source-[a-z0-9-]+)", messages[-1]["content"]).group(1)
                    result = (f'<tool_call><function=read_chat_section><parameter=source_id>{source_id}</parameter>'
                              f'<parameter=start>{len(text) - 9}</parameter><parameter=max_chars>9</parameter>'
                              '</function></tool_call>')
                self.pieces = [result.encode()]
                yield 0
                yield -1

        model = Model()
        with patch.object(chat, "_is_remote_provider", return_value=False), \
                patch.object(chat, "_model_missing", return_value=False), patch.object(chat, "_llm", model), \
                patch.object(chat, "load_model", return_value=(model, None)), llama_binding(), \
                patch.dict(os.environ, {"IMPRINT_CHAT_TOOL_FORMAT": "qwen-xml", "IMPRINT_CHAT_ANALYSIS_WORKERS": "2"}):
            events = list(chat.stream_reply([], text, workspace="alpha", read_only=True))
        self.assertFalse([event for event in events if event["type"] == "error"], events)
        self.assertIn("Verified TAIL FACT.", [event["text"] for event in events if event["type"] == "assistant_message"])
        result = next(event for event in events if event["type"] == "tool_result")
        self.assertIn("TAIL FACT", result["result"])
        workers = [request for request in model.requests if request["messages"][0]["content"] == WORKER_SYSTEM]
        self.assertGreater(len(workers), 1)
        self.assertTrue(all(request["tools"] is None for request in workers))
        self.assertNotIn("read_chat_section", chat.TOOLS)


if __name__ == "__main__":
    unittest.main()
