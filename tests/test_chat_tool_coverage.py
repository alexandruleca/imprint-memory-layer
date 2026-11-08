"""Every read tool the MCP server's results suggest is one chat can call.

Search, graph_scope and neighbors end their results with follow-ups such as
get_memory(memory_id="…"); a chat that cannot run them answers with
"[tool error] unknown tool" instead of reading the evidence."""
import inspect
import re
import unittest
from unittest.mock import patch

from imprint import chat, server

# Tools that change state or this session, or plan with their own model:
# chat offers its own write tools and recall would nest a model in a model.
NOT_FOR_CHAT = {"store", "delete", "purge_sources", "kg_edit", "ingest_url", "ingest_content",
                "use_workspace", "device_context", "recall"}


class CoverageTests(unittest.TestCase):
    def test_every_suggested_read_tool_is_offered(self):
        source = inspect.getsource(server)
        mcp_tools = set(re.findall(r'@mcp\.tool\([^\n]*\)\s*\ndef (\w+)', source))
        suggested = {name for name in mcp_tools if re.search(rf'\b{name}\(', source.split('@mcp.tool', 1)[1])}
        self.assertIn("get_memory", suggested)
        missing = sorted((suggested - NOT_FOR_CHAT) - set(chat.TOOLS))
        self.assertEqual(missing, [], f"suggested by results but not offered to chat: {missing}")

    def test_voice_gets_every_read_tool(self):
        offered = {schema["function"]["name"] for schema in chat._tool_schemas(read_only=True)}
        for name in ("search", "get_memory", "neighbors", "graph_scope", "file_chunks", "read_source", "kg_query"):
            self.assertIn(name, offered)
        self.assertNotIn("store", offered)

    def test_get_memory_takes_either_spelling_of_the_id(self):
        calls = []
        original = server.get_memory
        server.get_memory = lambda **kw: calls.append(kw) or "ok"
        try:
            self.assertEqual(chat._run_tool("get_memory", {"id": "abc"}, "default", True), "ok")
            self.assertEqual(chat._run_tool("get_memory", chat._typed_args("get_memory", {"memory_id": "def", "max_chars": "200"}), "default", True), "ok")
            self.assertIn("missing required memory_id", chat._run_tool("get_memory", {}, "default", True))
        finally:
            server.get_memory = original
        self.assertEqual([c["memory_id"] for c in calls], ["abc", "def"])
        self.assertEqual(calls[1]["max_chars"], 200)


def _hits(*ids):
    return "Workspace: default.\n" + "".join(
        f"[{n}] id:{memory} sim:0.5 | project:p | source:{memory}.md\ntext of {memory}\n\n" for n, memory in enumerate(ids, 1))


class GuessedFilterTests(unittest.TestCase):
    """The model's tag filters are guesses; a wrong one must not hide the answer."""

    def run_search(self, filtered, unfiltered, **filters):
        calls = []

        def search(**kw):
            calls.append(kw)
            return filtered if any(k in kw for k in ("domain", "layer", "lang", "kind", "type")) else unfiltered
        with patch.object(server, "search", side_effect=search), \
                patch.object(server, "_validate_ws", return_value=("default", None)):
            return chat._tool_search("qdrant port", workspace="default", **filters), calls

    def test_results_the_guessed_filters_left_out_are_shown_after_the_filtered_ones(self):
        footer = "More results may exist: offset=3; use filters."
        result, calls = self.run_search(_hits("aaaaaaaa11", "bbbbbbbb22"), _hits("aaaaaaaa11", "cccccccc33", "dddddddd44") + footer,
                                        domain="infra,db", project="p")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1], {"query": "qdrant port", "workspace": "default", "project": "p"})
        head, tail = result.split("Also matching without domain=infra,db")
        self.assertIn("bbbbbbbb22", head)
        self.assertIn("cccccccc33", tail)
        self.assertIn("dddddddd44", tail)
        self.assertNotIn("aaaaaaaa11", tail)
        self.assertNotIn("More results may exist", tail)

    def test_without_guessed_filters_search_runs_once_and_unchanged(self):
        result, calls = self.run_search("", _hits("aaaaaaaa11"), project="p", limit=5)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result, _hits("aaaaaaaa11"))

    def test_nothing_is_added_when_the_filters_left_nothing_out(self):
        result, _ = self.run_search(_hits("aaaaaaaa11"), _hits("aaaaaaaa11"), layer="docs")
        self.assertNotIn("Also matching", result)


class RepeatedCallTests(unittest.TestCase):
    def test_a_read_call_repeated_with_the_same_arguments_is_not_run_again(self):
        ran = {}
        first = {"name": "search", "args": {"query": "qdrant", "limit": 5}}
        self.assertEqual(chat._repeated(first, ran), "")
        again = {"name": "search", "args": {"limit": 5, "query": "qdrant"}}
        note = chat._repeated(again, ran)
        self.assertTrue(note.startswith("[repeated call] search already ran"))
        self.assertEqual(chat._repeated({"name": "search", "args": {"query": "qdrant port"}}, ran), "")

    def test_writes_and_questions_always_run(self):
        ran = {}
        for call in ({"name": "store", "args": {"content": "x"}}, {"name": "ask_user", "args": {"question": "Which?"}}):
            self.assertEqual(chat._repeated(call, ran), "")
            self.assertEqual(chat._repeated(call, ran), "")

    def test_a_repeated_call_note_is_not_new_evidence(self):
        from imprint.chat_research import ResearchBudget
        budget = ResearchBudget(seconds=30, tokens=5000, patience=2)
        self.assertFalse(budget.observe([("[repeated call] search already ran with these arguments", False)]))


if __name__ == "__main__":
    unittest.main()
