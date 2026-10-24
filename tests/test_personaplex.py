import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from imprint import personaplex as voice
from imprint import _personaplex_runtime as runtime


def session_for(prompt="Fixture memory"):
    text = voice.INSTRUCTIONS + prompt
    return {"schema": 1, "mode": "startup_snapshot", "language": "en", "workspace": "alpha",
            "text_prompt": text, "prompt_chars": len(text), "source_revision": voice.SOURCE_REVISION,
            "model_repo": voice.MODEL_REPO, "model_revision": voice.MODEL_REVISION}


class SessionTests(unittest.TestCase):
    def test_export_is_exclusive_private_and_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private" / "session.json"
            original = session_for()
            voice.write_session(path, original)
            self.assertEqual(voice.load_session(path), original)
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                voice.write_session(path, session_for("must not replace"))
            self.assertEqual(voice.load_session(path), original)
            original["model_revision"] = "unreviewed"
            path.write_text(json.dumps(original))
            with self.assertRaises(voice.PrototypeError):
                voice.load_session(path)

    def test_invalid_requests_fail_before_mcp_or_model_work(self):
        for kwargs in ({"workspace": "../outside"}, {"queries": [" "]}, {"queries": ["x"] * 5},
                       {"max_chars": 20000}, {"queries": ["x" * 4001]}):
            request = dict(workspace="alpha", queries=["native installers"], project="", max_chars=8000)
            request.update(kwargs)
            with self.subTest(kwargs=kwargs), self.assertRaises(voice.PrototypeError):
                voice.validate_request(**request)

    def test_source_revision_and_checksum_are_checked_before_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            with patch.object(voice.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "wrong\n")):
                with self.assertRaisesRegex(voice.PrototypeError, "revision"):
                    voice.validate_source(source)
            results = [subprocess.CompletedProcess([], 0, voice.SOURCE_REVISION),
                       subprocess.CompletedProcess([], 1)]
            with patch.object(voice.subprocess, "run", side_effect=results):
                with self.assertRaisesRegex(voice.PrototypeError, "modifications"):
                    voice.validate_source(source)
            (source / "moshi" / "moshi").mkdir(parents=True)
            (source / "moshi" / "moshi" / "server.py").write_text("changed source")
            results[1] = subprocess.CompletedProcess([], 0)
            with patch.object(voice.subprocess, "run", side_effect=results):
                with self.assertRaisesRegex(voice.PrototypeError, "checksum"):
                    voice.validate_source(source)

    def test_serve_keeps_memory_out_of_process_arguments_and_stops_only_owned_child(self):
        import argparse
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "session.json"
            voice.write_session(path, session_for("PRIVATE MEMORY CONTENT"))
            args = argparse.Namespace(session=path, source=root, runtime_python=Path(sys.executable),
                                      port=8998, cpu_offload=True, ssl_dir=None)
            child = Mock()
            child.wait.side_effect = [KeyboardInterrupt, subprocess.TimeoutExpired("runtime", 10), 0]
            with patch.object(voice, "validate_source"), patch.object(voice.subprocess, "Popen", return_value=child) as start, \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(voice._serve(args), 130)
            self.assertNotIn("PRIVATE MEMORY", repr(start.call_args))
            self.assertNotIn("PRIVATE MEMORY", output.getvalue())
            self.assertIn("-I", start.call_args.args[0])
            self.assertIn("--cpu-offload", start.call_args.args[0])
            child.terminate.assert_called_once_with()
            child.kill.assert_called_once_with()


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
            with self.assertRaises(voice.PrototypeError):
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
            session = await voice._prepare(args)
        self.assertEqual(connect.call_args.kwargs["args"], ["-m", "imprint"])
        self.assertEqual(connect.call_args.kwargs["env"]["IMPRINT_WORKSPACE"], "alpha")
        self.assertEqual(voice.load_session(args.output), session)
        self.assertIn("stdio-proof", session["text_prompt"])


class RuntimeAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp import web
        self.logs = []
        logs = self.logs

        class Logger:
            def log(self, level, message):
                logs.append(message)

        class State:
            async def handle_chat(self, request):
                Logger().log("info", "text prompt: " + request.query["text_prompt"])
                socket = web.WebSocketResponse()
                await socket.prepare(request)
                await socket.send_json({"prompt": request.query["text_prompt"], "voice": request.query["voice_prompt"]})
                async for message in socket:
                    if message.type == web.WSMsgType.BINARY:
                        await socket.send_bytes(message.data)
                return socket

        self.run_app = Mock()
        self.server = types.SimpleNamespace(ServerState=State, ColorizedLog=Logger, hf_hub_download=Mock(return_value="cached"),
                                            web=types.SimpleNamespace(run_app=self.run_app))
        self.download = self.server.hf_hub_download
        self.session = session_for("PRIVATE SNAPSHOT")
        runtime.install_adapter(self.server, self.session, port=8998, tls=False)
        app = web.Application()
        async def root(request):
            return web.Response(text="Fixture UI")
        app.router.add_get("/", root)
        app.router.add_get("/api/chat", self.server.ServerState().handle_chat)
        self.server.web.run_app(app, port=8998)
        self.assertEqual(self.run_app.call_args.kwargs["host"], "127.0.0.1")
        self.assertIsNone(self.run_app.call_args.kwargs["access_log"])
        from aiohttp.test_utils import TestClient, TestServer
        from aiohttp import CookieJar
        self.client = TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        self.headers = {"Host": "127.0.0.1:8998", "Origin": "http://127.0.0.1:8998"}

    async def test_browser_session_injects_context_and_carries_binary_audio_without_prompt_logs(self):
        page = await self.client.get("/", headers={"Host": self.headers["Host"]})
        self.assertEqual(page.status, 200)
        self.assertIn("HttpOnly", page.headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", page.headers["Set-Cookie"])
        self.assertEqual(page.headers["X-Frame-Options"], "DENY")
        socket = await self.client.ws_connect("/api/chat?voice_prompt=NATF2.pt&text_prompt=browser-value", headers=self.headers)
        received = await socket.receive_json()
        self.assertEqual(received["prompt"], self.session["text_prompt"])
        self.assertEqual(received["voice"], "NATF2.pt")
        await socket.send_bytes(b"\x01fixture-opus-frame")
        self.assertEqual((await socket.receive()).data, b"\x01fixture-opus-frame")
        await socket.close()
        self.assertNotIn("PRIVATE SNAPSHOT", " ".join(self.logs))

    async def test_foreign_host_origin_missing_cookie_and_voice_traversal_are_rejected(self):
        for headers in ({"Host": "attacker.test"}, {**self.headers, "Origin": "https://attacker.test"}):
            response = await self.client.get("/", headers=headers)
            self.assertEqual(response.status, 403)
        response = await self.client.get("/api/chat?voice_prompt=NATF2.pt", headers=self.headers)
        self.assertEqual(response.status, 403)
        await self.client.get("/", headers={"Host": self.headers["Host"]})
        response = await self.client.get("/api/chat?voice_prompt=NATF2.pt", headers={"Host": self.headers["Host"]})
        self.assertEqual(response.status, 403)
        response = await self.client.get("/api/chat?voice_prompt=../../other.pt", headers=self.headers)
        self.assertEqual(response.status, 400)

    async def test_model_reads_are_pinned_cache_only_and_reject_other_repositories(self):
        self.server.hf_hub_download(voice.MODEL_REPO, "model.safetensors")
        self.assertEqual(self.download.call_args.kwargs, {"revision": voice.MODEL_REVISION, "local_files_only": True})
        with self.assertRaises(RuntimeError):
            self.server.hf_hub_download("unreviewed/repo", "model")

    async def test_source_loader_delays_only_the_verified_startup_block(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "server.py"
            path.write_text("def main():\n    raise AssertionError('not started during import')\nwith torch.no_grad():\n    main()\n")
            with patch.dict(sys.modules):
                module = runtime.load_server(path)
            with self.assertRaisesRegex(AssertionError, "not started"):
                module.main()
            path.write_text("print('unexpected import side effects')\n")
            with self.assertRaisesRegex(RuntimeError, "startup block"):
                runtime.load_server(path)


if __name__ == "__main__":
    unittest.main()
