"""Every read tool the MCP server's results suggest is one chat can call.

Search, graph_scope and neighbors end their results with follow-ups such as
get_memory(memory_id="…"); a chat that cannot run them answers with
"[tool error] unknown tool" instead of reading the evidence."""
import inspect
import re
import unittest

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


if __name__ == "__main__":
    unittest.main()
