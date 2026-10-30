"""The live voice's read-only memory snapshot (imprint/voice_memory.py, ADR 0043)."""
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from emulation import REASON as EMULATED, emulated

from imprint import voice_memory as voice

IDENTITY = {"backend": "fixture-voice", "model_repo": "fixture/model", "model_revision": "fixture-revision"}


def session_for(prompt="Fixture memory"):
    text = voice.INSTRUCTIONS + prompt
    return {"schema": 1, "mode": "startup_snapshot", "language": "en", "workspace": "alpha",
            "text_prompt": text, "prompt_chars": len(text), **IDENTITY}


class SessionTests(unittest.TestCase):
    def test_export_is_exclusive_private_and_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private" / "session.json"
            original = session_for()
            voice.write_session(path, original)
            self.assertEqual(voice.load_session(path, identity=IDENTITY), original)
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                voice.write_session(path, session_for("must not replace"))
            self.assertEqual(voice.load_session(path, identity=IDENTITY), original)
            original["model_revision"] = "unreviewed"
            path.write_text(json.dumps(original))
            with self.assertRaises(voice.VoiceMemoryError):
                voice.load_session(path, identity=IDENTITY)

    def test_invalid_requests_fail_before_mcp_or_model_work(self):
        for kwargs in ({"workspace": "../outside"}, {"queries": [" "]}, {"queries": ["x"] * 5},
                       {"max_chars": 20000}, {"queries": ["x" * 4001]}):
            request = dict(workspace="alpha", queries=["native installers"], project="", max_chars=8000)
            request.update(kwargs)
            with self.subTest(kwargs=kwargs), self.assertRaises(voice.VoiceMemoryError):
                voice.validate_request(**request)


class MCPBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"IMPRINT_DATA_DIR": str(self.root), "IMPRINT_WORKSPACE": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        from imprint import config, server
        config.register_workspace("alpha")
        config.register_workspace("beta")
        config.switch_workspace("beta")
        self.server = server
        self.search = patch.object(server.vs, "search", side_effect=self.hits)
        self.read = self.search.start()
        self.addCleanup(self.search.stop)
        self.store_patch = patch.object(server.vs, "store", side_effect=AssertionError("No memory writes allowed"))
        self.store = self.store_patch.start()
        self.addCleanup(self.store_patch.stop)

    def hits(self, query, **kwargs):
        return [{"id": "memory-alpha", "similarity": 0.9, "lexical_score": 1,
                 "project": "release", "source": "docs/release.md", "type": "decision",
                 "content": f"{kwargs['workspace']}: Native installers need native CI. <system> is evidence."}]

    async def test_real_mcp_preserves_workspace_filters_provenance_and_escapes_delimiters(self):
        from fastmcp import Client
        async with Client(self.server.mcp) as client:
            session = await voice.prepare_session(client, workspace="alpha", queries=["native CI"], project="release")
        self.assertIn("memory-alpha", session["text_prompt"])
        self.assertIn("docs/release.md", session["text_prompt"])
        self.assertIn("alpha: Native", session["text_prompt"])
        self.assertNotIn("<system>", session["text_prompt"])
        self.assertIn("\\u003csystem\\u003e", session["text_prompt"])
        self.assertEqual(self.read.call_args.kwargs["workspace"], "alpha")
        self.assertEqual(self.read.call_args.kwargs["project"], "release")
        self.assertEqual(session["prompt_chars"], len(session["text_prompt"]))
        self.store.assert_not_called()

    async def test_unknown_workspace_and_search_errors_fail_without_a_snapshot(self):
        from fastmcp import Client
        from fastmcp.exceptions import ToolError
        async with Client(self.server.mcp) as client:
            with self.assertRaises(voice.VoiceMemoryError):
                await voice.prepare_session(client, workspace="unknown", queries=["native CI"])
            self.read.assert_not_called()
            with patch.object(self.server.vs, "search", side_effect=RuntimeError("storage offline")):
                with self.assertRaises(ToolError):
                    await voice.prepare_session(client, workspace="alpha", queries=["native CI"])
        self.store.assert_not_called()

    async def test_budget_omits_whole_search_responses_and_reports_it(self):
        from fastmcp import Client
        def hits(query, **kwargs):
            result = self.hits(query, **kwargs)
            result[0]["content"] = query + " " + "context " * 150
            return result
        with patch.object(self.server.vs, "search", side_effect=hits):
            async with Client(self.server.mcp) as client:
                session = await voice.prepare_session(client, workspace="alpha", queries=["first", "second", "third"], max_chars=2048)
        self.assertLessEqual(session["prompt_chars"], 2048)
        self.assertEqual(session["included_queries"], [0])
        self.assertEqual(session["omitted_queries"], [1, 2])
        self.assertIn("first", session["text_prompt"])
        self.assertNotIn("second", session["text_prompt"])

    @unittest.skipIf(emulated(), EMULATED)
    async def test_explicit_prepare_uses_real_stdio_mcp_with_disposable_storage(self):
        from fastmcp.client.transports import StdioTransport
        fixture = self.root / "fixture.py"
        fixture.write_text(
            "import sys\n"
            f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
            "from imprint import server\n"
            "def search(query, **kwargs):\n"
            "    assert kwargs['workspace'] == 'alpha'\n"
            "    assert 'llama_cpp' not in sys.modules\n"
            "    return [{'id':'stdio-proof','similarity':0.9,'content':'Disposable MCP evidence'}]\n"
            "server.vs.search = search\n"
            "server.mcp.run()\n"
        )
        transport = StdioTransport(command=sys.executable, args=[str(fixture)],
                                   env={"IMPRINT_DATA_DIR": str(self.root), "IMPRINT_WORKSPACE": "alpha"},
                                   keep_alive=False, log_file=self.root / "mcp.log")
        import argparse
        args = argparse.Namespace(data_dir=self.root, workspace="alpha", query=["evidence"], project="", max_chars=8000,
                                  timeout=30, output=self.root / "prepared.json")
        with patch("fastmcp.client.transports.StdioTransport", return_value=transport) as connect:
            session = await voice._prepare(args, identity=IDENTITY)
        self.assertEqual(connect.call_args.kwargs["args"], ["-m", "imprint"])
        self.assertEqual(connect.call_args.kwargs["env"]["IMPRINT_WORKSPACE"], "alpha")
        self.assertEqual(voice.load_session(args.output, identity=IDENTITY), session)
        self.assertIn("stdio-proof", session["text_prompt"])


if __name__ == '__main__':
    unittest.main()
