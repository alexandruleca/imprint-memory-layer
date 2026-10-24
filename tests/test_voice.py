import asyncio
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import tarfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from imprint import chat, voice_api as voice, voice_runtime
from imprint.personaplex import PrototypeError
from test_lfm_audio import wav_bytes


class VoiceBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "live-build.json").write_text("{}")
        for patcher in [patch.dict(os.environ, {"IMPRINT_DATA_DIR": str(self.root), "IMPRINT_VOICE_RUNTIME_DIR": str(self.root)}),
                        patch.object(voice.voice_runtime, "supported", return_value=True),
                        patch.object(voice, "sessions", voice.Sessions())]:
            patcher.start(); self.addCleanup(patcher.stop)
        app = FastAPI()
        app.include_router(voice.router)
        self.client = TestClient(app, base_url="http://127.0.0.1:8420", client=("127.0.0.1", 1234))
        self.addCleanup(self.client.close)

    def token(self, **body):
        response = self.client.post("/api/voice/sessions", json=body, headers={"Origin": "http://127.0.0.1:8420"})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["token"]

    def test_capabilities_are_single_use_expiring_and_origin_bound(self):
        registry = voice.sessions
        token = registry.issue("alpha", "http://localhost:8420", "lfm")
        self.assertIsNone(registry.consume(token, "http://localhost:7777"))
        self.assertIsNone(registry.consume(token, "http://localhost:8420"))
        token = registry.issue("alpha", "http://localhost:8420", "lfm")
        self.assertEqual(registry.consume(token, "http://localhost:8420")["workspace"], "alpha")
        self.assertIsNone(registry.consume(token, "http://localhost:8420"))
        token = registry.issue("alpha", "http://localhost:8420", "lfm")
        registry.leases[token]["expires"] = 0
        self.assertIsNone(registry.consume(token, "http://localhost:8420"))

    def test_session_boundary_rejects_remote_origins_unknown_workspaces_and_remote_chat(self):
        for headers in ({"Origin": "https://evil.test"}, {"Origin": "http://127.0.0.1:3100"}, {"Origin": "null"}):
            self.assertEqual(self.client.post("/api/voice/sessions", json={}, headers=headers).status_code, 403)
        for body in ({"workspace": "missing"}, {"engine": "remote"}, {"engine": []}, [], {"workspace": []}):
            self.assertEqual(self.client.post("/api/voice/sessions", json=body).status_code, 400)
        with patch.dict(os.environ, {"IMPRINT_CHAT_PROVIDER": "openai"}):
            self.assertEqual(self.client.post("/api/voice/sessions", json={"engine": "chat"}).status_code, 409)
            self.token(engine="lfm")

    def test_websocket_rejects_origin_without_starting_model(self):
        with patch.object(voice.sessions.worker, "start", new_callable=AsyncMock) as start:
            for headers in ({}, {"Origin": "https://evil.test"}, {"Origin": "http://127.0.0.1:3100"}):
                with self.assertRaises(WebSocketDisconnect):
                    with self.client.websocket_connect("ws://127.0.0.1:8420/api/voice/stream", headers=headers):
                        pass
            start.assert_not_awaited()

    def test_stream_pins_workspace_acknowledges_history_and_cancels_owned_work(self):
        worker = voice.sessions.worker
        seen = []

        async def native(system, content):
            seen.append(content)
            yield {"type": "text", "text": "Nimbus."}
            yield {"type": "audio", "data": "AAAAAA==", "sample_rate": 24000, "format": "pcm16"}

        with patch.object(worker, "start", new_callable=AsyncMock), \
                patch.object(worker, "stop", new_callable=AsyncMock) as stop, \
                patch.object(worker, "stream", side_effect=native), \
                patch.object(voice, "memory_prompt", new_callable=AsyncMock, return_value="fixture") as memory:
            token = self.token(engine="lfm")
            with self.client.websocket_connect("ws://127.0.0.1:8420/api/voice/stream", headers={"Origin": "http://127.0.0.1:8420"}) as ws:
                ws.send_json({"token": token})
                self.assertEqual(ws.receive_json()["type"], "connecting")
                self.assertEqual(ws.receive_json()["workspace"], "default")
                for identifier in (1, 2):
                    ws.send_json({"type": "turn", "id": identifier, "text": "Mascot?"})
                    events = []
                    while not events or events[-1]["type"] != "done":
                        events.append(ws.receive_json())
                    self.assertTrue(any(event["type"] == "audio" for event in events))
                    if identifier == 1:
                        ws.send_json({"type": "played", "id": 1})
                self.assertIn("Nimbus.", seen[1])
                memory.assert_awaited_with("Mascot?", "default")
                ws.send_json({"type": "cancel"})
                self.assertEqual(ws.receive_json()["type"], "cancelled")
            self.assertFalse(voice.sessions.active)
            self.assertGreaterEqual(stop.await_count, 2)

    def test_cancel_stops_pending_turn_and_late_output_is_not_sent(self):
        started = threading.Event()
        ended = threading.Event()

        async def native(system, content):
            started.set()
            try:
                await asyncio.sleep(30)
                yield {"type": "text", "text": "stale"}
            finally:
                ended.set()

        with patch.object(voice.sessions.worker, "start", new_callable=AsyncMock), \
                patch.object(voice.sessions.worker, "stop", new_callable=AsyncMock) as stop, \
                patch.object(voice.sessions.worker, "stream", side_effect=native), \
                patch.object(voice, "memory_prompt", new_callable=AsyncMock, return_value="fixture"):
            with self.client.websocket_connect("ws://127.0.0.1:8420/api/voice/stream", headers={"Origin": "http://127.0.0.1:8420"}) as ws:
                ws.send_json({"token": self.token(engine="lfm")})
                ws.receive_json(); ws.receive_json()
                ws.send_json({"type": "turn", "id": 1, "text": "Hello"})
                ws.receive_json(); ws.receive_json()
                self.assertTrue(started.wait(2))
                ws.send_json({"type": "cancel"})
                self.assertEqual(ws.receive_json(), {"type": "cancelled", "id": 1})
                self.assertTrue(ended.wait(2))
                stop.assert_awaited()

    def test_chat_speaks_long_tail_in_bounded_phrases_without_losing_text(self):
        spoken = []
        reply = ("a" * 220 + " ") * 14

        async def native(system, content):
            self.assertTrue(system.startswith("Perform TTS."))
            spoken.append(content)
            yield {"type": "audio", "data": "AAAAAA==", "sample_rate": 24000, "format": "pcm16"}

        with patch.object(voice.sessions.worker, "start", new_callable=AsyncMock), \
                patch.object(voice.sessions.worker, "stop", new_callable=AsyncMock), \
                patch.object(voice.sessions.worker, "stream", side_effect=native), \
                patch.object(chat, "stream_reply", return_value=iter([{"type": "token", "text": reply}, {"type": "done"}])):
            token = self.token(engine="chat")
            with self.client.websocket_connect("ws://127.0.0.1:8420/api/voice/stream", headers={"Origin": "http://127.0.0.1:8420"}) as ws:
                ws.send_json({"token": token}); ws.receive_json(); ws.receive_json()
                ws.send_json({"type": "turn", "id": 1, "text": "Hello"})
                while ws.receive_json()["type"] != "done":
                    pass
        self.assertEqual("".join(spoken).strip(), reply.strip())
        self.assertTrue(all(len(part) <= 240 for part in spoken))

    def test_first_chat_download_has_progress_and_a_separate_budget_without_private_logs(self):
        async def events(*args):
            yield {"type": "install_start"}
            yield {"type": "install_progress", "line": "PRIVATE /user/profile/install.log"}
            await asyncio.sleep(.15)
            yield {"type": "download_start", "path": "/PRIVATE/model.gguf", "repo": "PRIVATE"}
            yield {"type": "download_progress", "downloaded": 12, "total": 24}
            await asyncio.sleep(.15)
            yield {"type": "load_start"}
            yield {"type": "token", "text": "A local answer."}
            yield {"type": "done"}
        async def native(*args):
            yield {"type": "audio", "data": "AAAAAA==", "sample_rate": 24000, "format": "pcm16"}
        with patch.object(voice.sessions.worker, "start", new_callable=AsyncMock), \
                patch.object(voice.sessions.worker, "stop", new_callable=AsyncMock), \
                patch.object(voice.sessions.worker, "stream", side_effect=native), \
                patch.object(voice, "chat_events", side_effect=events), \
                patch.object(voice, "TURN_TIMEOUT", .1), patch.object(voice, "CHAT_SETUP_TIMEOUT", 5):
            with self.client.websocket_connect("ws://127.0.0.1:8420/api/voice/stream", headers={"Origin": "http://127.0.0.1:8420"}) as ws:
                ws.send_json({"token": self.token(engine="chat")}); ws.receive_json(); ws.receive_json()
                ws.send_json({"type": "turn", "id": 1, "text": "Hello"})
                received = []
                while not received or received[-1]["type"] != "done":
                    event = ws.receive_json(); received.append(event)
                    self.assertNotEqual(event["type"], "error", event)
        self.assertIn({"type": "provision", "stage": "download", "downloaded": 12, "total": 24, "id": 1}, received)
        self.assertNotIn("PRIVATE", json.dumps(received))
        self.assertTrue(any(event["type"] == "audio" for event in received))


class VoiceValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_startup_budget_includes_asset_verification_before_launch(self):
        import time
        worker = voice.Worker()
        def slow_verification(root):
            time.sleep(.05)
            return root / "worker"
        with patch.object(voice, "runtime_root", return_value=Path("unused")), \
                patch.object(voice, "STARTUP_TIMEOUT", .01), \
                patch.object(voice.voice_runtime, "verify", side_effect=slow_verification), \
                patch.object(voice.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(PrototypeError, "too long to load"):
                await worker.start()
            launch.assert_not_called()
            self.assertIsNone(worker.process)

    async def test_threads_respect_small_cpu_allocations(self):
        with patch.object(voice.os, "cpu_count", return_value=3), \
                patch.object(voice.os, "sched_getaffinity", return_value={0, 1, 2}, create=True):
            self.assertEqual(voice.worker_threads(), 3)
        with patch.object(voice.os, "cpu_count", return_value=16), \
                patch.object(voice.os, "sched_getaffinity", return_value={0, 1}, create=True):
            self.assertEqual(voice.worker_threads(), 2)

    async def test_closing_during_cold_start_cancels_model_load_and_releases_session(self):
        registry = voice.Sessions()
        token = registry.issue("default", "http://127.0.0.1:8420", "lfm")
        socket = SimpleNamespace(headers={"host": "127.0.0.1:8420", "origin": "http://127.0.0.1:8420"},
                                 client=SimpleNamespace(host="127.0.0.1"), accept=AsyncMock(), close=AsyncMock(),
                                 send_json=AsyncMock(), receive_text=AsyncMock(side_effect=[json.dumps({"token": token}), WebSocketDisconnect()]))
        cancelled = asyncio.Event()
        async def slow_start():
            try:
                await asyncio.sleep(30)
            finally:
                cancelled.set()
        with patch.object(voice, "sessions", registry), patch.object(registry.worker, "start", side_effect=slow_start), \
                patch.object(registry.worker, "stop", new_callable=AsyncMock) as stop:
            await asyncio.wait_for(voice.stream(socket), 1)
            self.assertTrue(cancelled.is_set())
            self.assertFalse(registry.active)
            stop.assert_awaited()

    async def test_microphone_wav_limits_and_encoding(self):
        content = await voice.audio_content(base64.b64encode(wav_bytes()).decode())
        self.assertEqual(content[0]["input_audio"]["format"], "wav")
        for value in ("not base64", base64.b64encode(b"not audio" * 10).decode(), "A" * 1_920_061):
            with self.assertRaises(PrototypeError):
                await voice.audio_content(value)

    async def test_chat_bridge_uses_same_model_with_read_only_and_pinned_workspace(self):
        cancellation = threading.Event()
        with patch.object(chat, "stream_reply", return_value=iter([{"type": "token", "text": "Hello."}])) as reply:
            events = [event async for event in voice.chat_events("hello", [], "alpha", cancellation)]
        self.assertEqual(events, [{"type": "token", "text": "Hello."}])
        self.assertFalse(cancellation.is_set())
        self.assertEqual(reply.call_args.kwargs, {"workspace": "alpha", "cancel_event": cancellation, "read_only": True})


class VoiceChatPolicyTests(unittest.TestCase):
    def test_cancellation_closes_stream_and_prevents_tool_execution(self):
        event = threading.Event()
        closed = []

        def tokens(*args):
            try:
                yield '<tool_call>{"name":"store","args":{"content":"No write"}}</tool_call>'
                event.set()
            finally:
                closed.append(True)

        with patch.object(chat, "_is_remote_provider", return_value=True), \
                patch.object(chat, "_get_chat_provider", return_value="ollama"), \
                patch.object(chat, "_stream_remote", side_effect=tokens), patch.dict(chat.TOOLS, {"store": Mock()}) as tools:
            list(chat.stream_reply([], "hello", cancel_event=event))
            tools["store"].assert_not_called()
        self.assertTrue(closed)

    def test_voice_rejects_writes_and_uses_session_workspace_over_model_workspace(self):
        calls = [iter(['<tool_call>{"name":"store","args":{"content":"x"}}</tool_call>']),
                 iter(['<tool_call>{"name":"status","args":{"workspace":"other"}}</tool_call>']), iter(['Done.'])]
        write, read = Mock(), Mock(return_value="all good")
        with patch.object(chat, "_is_remote_provider", return_value=True), \
                patch.object(chat, "_get_chat_provider", return_value="ollama"), \
                patch.object(chat, "_stream_remote", side_effect=calls), patch.dict(chat.TOOLS, {"store": write, "status": read}):
            list(chat.stream_reply([], "hello", workspace="alpha", read_only=True))
        write.assert_not_called()
        read.assert_called_once_with(workspace="alpha")


class LiveRuntimeIntegrityTests(unittest.TestCase):
    @patch.object(voice_runtime, "target", return_value="linux-x64")
    def test_worker_verification_rejects_binary_source_and_patch_drift(self, _target):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "live-source.tar.gz"
            with tarfile.open(archive, "w:gz") as output:
                entry = tarfile.TarInfo(f"llama.cpp-{voice_runtime.SOURCE_REVISION}/tools/liquid-audio/server.cpp")
                entry.size = len(b"original")
                output.addfile(entry, io.BytesIO(b"original"))
            binary = voice_runtime.native_binary(root)
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"native fixture")
            metadata = {"source_revision": voice_runtime.SOURCE_REVISION, "patch_version": voice_runtime.PATCH_VERSION,
                        "target": voice_runtime.target(), "build_version": voice_runtime.BUILD_VERSION,
                        "patch_sha256": hashlib.sha256(b"patched fixture").hexdigest(),
                        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}
            (root / "live-build.json").write_text(json.dumps(metadata))
            with patch.object(voice_runtime.lfm, "verify_weights"), \
                    patch.object(voice_runtime, "SOURCE_SHA256", hashlib.sha256(archive.read_bytes()).hexdigest()), \
                    patch.object(voice_runtime, "patched_server", return_value="patched fixture"):
                self.assertEqual(voice_runtime.verify(root), binary)
                binary.write_bytes(b"modified")
                with self.assertRaises(PrototypeError):
                    voice_runtime.verify(root)
                binary.write_bytes(b"native fixture")
                with patch.object(voice_runtime, "patched_server", return_value="new patch"):
                    with self.assertRaises(PrototypeError):
                        voice_runtime.verify(root)
                archive.write_bytes(b"changed archive")
                with self.assertRaises(PrototypeError):
                    voice_runtime.verify(root)

    def test_source_patch_and_invalid_build_fail_before_execution(self):
        with self.assertRaises(PrototypeError):
            voice_runtime.patched_server("unrecognized upstream")
        with patch.object(voice_runtime.lfm, "_platform"), \
                patch.object(voice_runtime.lfm, "install") as download:
            with self.assertRaises(PrototypeError):
                voice_runtime.install(Path("unused"), jobs=99)
            with patch.object(voice_runtime.shutil, "which", return_value=None):
                with self.assertRaises(PrototypeError):
                    voice_runtime.install(Path("unused"))
            download.assert_not_called()

    def test_native_targets_include_windows_and_both_mac_architectures(self):
        for system, machine, expected in [("Windows", "AMD64", "windows-x64"), ("Darwin", "arm64", "macos-arm64"),
                                          ("Darwin", "x86_64", "macos-x64"), ("Linux", "x86_64", "linux-x64")]:
            with patch.object(voice_runtime.platform, "system", return_value=system), \
                    patch.object(voice_runtime.platform, "machine", return_value=machine):
                self.assertEqual(voice_runtime.target(), expected)
                self.assertTrue(voice_runtime.supported())
                self.assertEqual(voice_runtime.native_binary(Path("cache")).suffix, ".exe" if system == "Windows" else "")
        with patch.object(voice_runtime.platform, "system", return_value="Windows"), \
                patch.object(voice_runtime.platform, "machine", return_value="arm64"):
            self.assertFalse(voice_runtime.supported())


if __name__ == "__main__":
    unittest.main()
