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

from imprint import chat, chat_store, voice_api as voice, voice_runtime
from imprint.voice_memory import VoiceMemoryError
from emulation import slack
from test_chat_stream import call_chunk, remote_provider, text_chunk, tool
from test_lfm_audio import wav_bytes


class VoiceBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        try: fixture_target = voice_runtime.target()
        except VoiceMemoryError: fixture_target = "linux-x64"
        platform_patch = patch.object(voice_runtime, "target", return_value=fixture_target)
        platform_patch.start(); self.addCleanup(platform_patch.stop)
        (self.root / "live-build.json").write_text(json.dumps({"source_revision": voice_runtime.SOURCE_REVISION,
            "patch_version": voice_runtime.PATCH_VERSION,"build_version": voice_runtime.BUILD_VERSION,"target": voice_runtime.target()}))
        binary = voice_runtime.native_binary(self.root); binary.parent.mkdir(parents=True); binary.write_bytes(b"fixture")
        for patcher in [patch.dict(os.environ, {"IMPRINT_DATA_DIR": str(self.root), "IMPRINT_VOICE_RUNTIME_DIR": str(self.root)}),
                        patch.object(voice.voice_runtime, "supported", return_value=True),
                        patch.object(voice, "sessions", voice.Sessions()),
                        patch.dict(chat_store._conns, clear=True)]:
            patcher.start(); self.addCleanup(patcher.stop)
        self.addCleanup(chat_store._close_all)  # Windows cannot delete an open database
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

    def test_a_wake_started_turn_that_asks_to_stop_ends_without_reasoning(self):
        async def native(system, content):
            yield {"type": "text", "text": "Answer."}

        with patch.object(voice.sessions.worker, "start", new_callable=AsyncMock), \
                patch.object(voice.sessions.worker, "stop", new_callable=AsyncMock), \
                patch.object(voice.sessions.worker, "stream", side_effect=native), \
                patch.object(voice, "memory_prompt", new_callable=AsyncMock, return_value="fixture") as memory:
            with self.client.websocket_connect("ws://127.0.0.1:8420/api/voice/stream", headers={"Origin": "http://127.0.0.1:8420"}) as ws:
                ws.send_json({"token": self.token(engine="lfm")})
                ws.receive_json(); ws.receive_json()
                ws.send_json({"type": "turn", "id": 1, "text": "Thanks, Echo.", "wake": True})
                self.assertEqual(ws.receive_json(), {"type": "user", "text": "Thanks, Echo.", "id": 1})
                self.assertEqual(ws.receive_json(), {"type": "end", "id": 1})
                memory.assert_not_awaited()
                # Outside a wake session the same words are an ordinary question.
                ws.send_json({"type": "turn", "id": 2, "text": "Thanks, Echo."})
                events = []
                while not events or events[-1]["type"] != "done":
                    events.append(ws.receive_json())
                memory.assert_awaited()
                ws.send_json({"type": "turn", "id": 3, "text": "stop", "wake": "yes"})
                self.assertEqual(ws.receive_json()["type"], "error")

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
                self.assertTrue(started.wait(slack(10)))
                ws.send_json({"type": "cancel"})
                self.assertEqual(ws.receive_json(), {"type": "cancelled", "id": 1})
                self.assertTrue(ended.wait(slack(10)))
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

    def test_a_voice_conversation_keeps_answered_messages_and_its_summary_and_saves_them_as_a_chat(self):
        # ADR 0141: every message, numbered as Chat stores them, tool results
        # included; the summary is kept for the next turn.
        # ADR 0145: a completed reply is kept whether or not it was played.
        # ADR 0146: Chat history holds the same messages, numbers and summary.
        seen = []

        async def events(question, history, workspace, cancelled, summary=None):
            seen.append({"question": question, "history": history, "summary": summary})
            if question == "first":
                yield {"type": "tool_call", "name": "search", "args": {"query": "billing"}}
                yield {"type": "tool_result", "name": "search", "result": "billing listens on 8101"}
            else:
                yield {"type": "tool_call", "name": "conversation_history", "args": {"query": "billing port"}}
                yield {"type": "tool_result", "name": "conversation_history", "result": "#2 tool search: 8101"}
                yield {"type": "summary", "text": "- Billing port (#1–#3).", "covers": 3, "retry_after": 0, "exchanges": 1}
            yield {"type": "token", "text": f"Answer to {question}."}
            yield {"type": "done"}

        async def native(*args):
            yield {"type": "audio", "data": "AAAAAA==", "sample_rate": 24000, "format": "pcm16"}

        with patch.object(voice.sessions.worker, "start", new_callable=AsyncMock), \
                patch.object(voice.sessions.worker, "stop", new_callable=AsyncMock), \
                patch.object(voice.sessions.worker, "stream", side_effect=native), \
                patch.object(voice, "chat_events", side_effect=events):
            with self.client.websocket_connect("ws://127.0.0.1:8420/api/voice/stream", headers={"Origin": "http://127.0.0.1:8420"}) as ws:
                ws.send_json({"token": self.token(engine="chat")}); ws.receive_json(); ws.receive_json()
                received = []
                for identifier, question, played in ((1, "first", True), (2, "second", False), (3, "third", True)):
                    ws.send_json({"type": "turn", "id": identifier, "text": question})
                    while not received or received[-1]["type"] != "done":
                        received.append(ws.receive_json())
                    if played:
                        ws.send_json({"type": "played", "id": identifier})
                    received.append({"type": "next"})
        self.assertIn({"type": "tool", "name": "conversation_history", "id": 2,
                       "text": "Searching earlier in this conversation for “billing port”"}, received)
        self.assertIn({"type": "tool", "name": "search", "id": 1, "text": "Checking memory · search"}, received)
        first_heard = [{"role": "user", "content": "first", "seq": 1},
                       {"role": "tool", "content": "billing listens on 8101", "tool_name": "search",
                        "tool_args": {"query": "billing"}, "seq": 2},
                       {"role": "assistant", "content": "Answer to first.", "seq": 3}]
        self.assertEqual(seen[1]["history"], first_heard)
        self.assertIsNone(seen[1]["summary"])
        # The second reply completed without being played to the end; it is kept, with its summary.
        self.assertEqual(seen[2]["history"], first_heard + [
            {"role": "user", "content": "second", "seq": 4},
            {"role": "tool", "content": "#2 tool search: 8101", "tool_name": "conversation_history",
             "tool_args": {"query": "billing port"}, "seq": 5},
            {"role": "assistant", "content": "Answer to second.", "seq": 6}])
        self.assertEqual(seen[2]["summary"], {"text": "- Billing port (#1–#3).", "covers": 3, "retry_after": 0})
        [saved] = chat_store.list_sessions()
        self.assertEqual((saved["kind"], saved["title"], saved["message_count"]), ("voice", "first", 9))
        history = voice.stored_chat(saved["id"], "default")[1]
        self.assertEqual(history[:6], seen[2]["history"])
        self.assertEqual([(m["role"], m["content"], m["seq"]) for m in history[6:]],
                         [("user", "third", 7), ("tool", "#2 tool search: 8101", 8), ("assistant", "Answer to third.", 9)])
        self.assertEqual(chat_store.get_summary(saved["id"])["text"], "- Billing port (#1–#3).")

    def test_with_saving_off_a_voice_conversation_writes_nothing(self):
        async def events(question, history, workspace, cancelled, summary=None):
            yield {"type": "token", "text": "Answer."}
            yield {"type": "done"}

        async def native(*args):
            yield {"type": "audio", "data": "AAAAAA==", "sample_rate": 24000, "format": "pcm16"}

        with patch.dict(os.environ, {"IMPRINT_VOICE_SAVE_CONVERSATIONS": "false"}), \
                patch.object(voice.sessions.worker, "start", new_callable=AsyncMock), \
                patch.object(voice.sessions.worker, "stop", new_callable=AsyncMock), \
                patch.object(voice.sessions.worker, "stream", side_effect=native), \
                patch.object(voice, "chat_events", side_effect=events):
            with self.client.websocket_connect("ws://127.0.0.1:8420/api/voice/stream", headers={"Origin": "http://127.0.0.1:8420"}) as ws:
                ws.send_json({"token": self.token(engine="chat")}); ws.receive_json()
                self.assertFalse(ws.receive_json()["saving"])
                ws.send_json({"type": "turn", "id": 1, "text": "hello"})
                while ws.receive_json()["type"] != "done":
                    pass
        self.assertEqual(len(voice.sessions.conversation.history), 2)
        self.assertEqual(list(self.root.glob("chat*.sqlite3")), [])

    def test_first_chat_download_has_progress_and_a_separate_budget_without_private_logs(self):
        async def events(*args):
            yield {"type": "download_start", "path": "/PRIVATE/model.gguf", "repo": "PRIVATE"}
            await asyncio.sleep(slack(.15))
            yield {"type": "download_progress", "downloaded": 12, "total": 24}
            await asyncio.sleep(slack(.15))
            yield {"type": "load_start"}
            yield {"type": "token", "text": "A local answer."}
            yield {"type": "done"}
        async def native(*args):
            yield {"type": "audio", "data": "AAAAAA==", "sample_rate": 24000, "format": "pcm16"}
        with patch.object(voice.sessions.worker, "start", new_callable=AsyncMock), \
                patch.object(voice.sessions.worker, "stop", new_callable=AsyncMock), \
                patch.object(voice.sessions.worker, "stream", side_effect=native), \
                patch.object(voice, "chat_events", side_effect=events), \
                patch.object(voice, "TURN_TIMEOUT", slack(.1)), patch.object(voice, "CHAT_SETUP_TIMEOUT", slack(5)):
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

    def test_long_reply_continues_with_playback_credit_and_an_inactivity_budget(self):
        # 80 seconds of simulated audio, produced over more than one turn
        # timeout. Playback reports release each window without saving history.
        audio = base64.b64encode(bytes(24000 * 2 * 2)).decode()

        async def native(*args):
            for _ in range(40):
                await asyncio.sleep(slack(.01))
                yield {"type": "audio", "data": audio, "sample_rate": 24000, "format": "pcm16"}

        with patch.object(voice.sessions.worker, "start", new_callable=AsyncMock), \
                patch.object(voice.sessions.worker, "stop", new_callable=AsyncMock), \
                patch.object(voice.sessions.worker, "stream", side_effect=native), \
                patch.object(chat, "stream_reply", return_value=iter([{"type": "token", "text": "A complete long reply."}])), \
                patch.object(voice, "TURN_TIMEOUT", slack(.3)):
            with self.client.websocket_connect("ws://127.0.0.1:8420/api/voice/stream", headers={"Origin": "http://127.0.0.1:8420"}) as ws:
                ws.send_json({"token": self.token(engine="chat")}); ws.receive_json(); ws.receive_json()
                ws.send_json({"type": "turn", "id": 1, "text": "Hello"})
                samples = 0
                while True:
                    event = ws.receive_json()
                    self.assertNotEqual(event["type"], "error", event)
                    if event["type"] == "done":
                        break
                    if event["type"] == "audio":
                        samples += len(base64.b64decode(event["data"])) // 2
                        ws.send_json({"type": "playback", "id": 1, "samples": samples})
                self.assertEqual(samples, 80 * 24000)

    def test_cancel_releases_generation_blocked_on_playback(self):
        blocked = threading.Event()
        audio = base64.b64encode(bytes(voice.PLAYBACK_AHEAD * 2)).decode()

        async def native(*args):
            yield {"type": "audio", "data": audio, "sample_rate": 24000, "format": "pcm16"}
            blocked.set()
            yield {"type": "audio", "data": audio, "sample_rate": 24000, "format": "pcm16"}

        with patch.object(voice.sessions.worker, "start", new_callable=AsyncMock), \
                patch.object(voice.sessions.worker, "stop", new_callable=AsyncMock) as stop, \
                patch.object(voice.sessions.worker, "stream", side_effect=native), \
                patch.object(voice, "memory_prompt", new_callable=AsyncMock, return_value="fixture"):
            with self.client.websocket_connect("ws://127.0.0.1:8420/api/voice/stream", headers={"Origin": "http://127.0.0.1:8420"}) as ws:
                ws.send_json({"token": self.token(engine="lfm")}); ws.receive_json(); ws.receive_json()
                ws.send_json({"type": "turn", "id": 1, "text": "Hello"})
                while ws.receive_json()["type"] != "audio":
                    pass
                self.assertTrue(blocked.wait(slack(10)))
                ws.send_json({"type": "cancel"})
                self.assertEqual(ws.receive_json(), {"type": "cancelled", "id": 1})
                stop.assert_awaited()
                ws.send_json({"type": "turn", "id": 2, "text": "Again"})
                # Even malformed credit for an old turn must be ignored.
                ws.send_json({"type": "playback", "id": 1, "samples": -1})
                while ws.receive_json()["type"] != "audio":
                    pass
                ws.send_json({"type": "cancel"})
                self.assertEqual(ws.receive_json(), {"type": "cancelled", "id": 2})

    def test_stalled_generation_still_times_out_and_stops_worker(self):
        async def native(*args):
            await asyncio.sleep(30)
            yield {"type": "text", "text": "too late"}

        with patch.object(voice.sessions.worker, "start", new_callable=AsyncMock), \
                patch.object(voice.sessions.worker, "stop", new_callable=AsyncMock) as stop, \
                patch.object(voice.sessions.worker, "stream", side_effect=native), \
                patch.object(voice, "memory_prompt", new_callable=AsyncMock, return_value="fixture"), \
                patch.object(voice, "TURN_TIMEOUT", .1):
            with self.client.websocket_connect("ws://127.0.0.1:8420/api/voice/stream", headers={"Origin": "http://127.0.0.1:8420"}) as ws:
                ws.send_json({"token": self.token(engine="lfm")}); ws.receive_json(); ws.receive_json()
                ws.send_json({"type": "turn", "id": 1, "text": "Hello"})
                while ws.receive_json()["type"] != "error":
                    pass
                ws.send_json({"type": "cancel"})
                self.assertEqual(ws.receive_json()["type"], "cancelled")
                stop.assert_awaited()


class VoiceRunsToCompletionTests(unittest.TestCase):
    """A question runs until it is answered or explicitly stopped (ADR 0145)."""
    token = VoiceBoundaryTests.token

    def setUp(self):
        VoiceBoundaryTests.setUp(self)
        # One event loop for every window, as the API has; without it each
        # socket gets its own loop, which ends with the socket.
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
    ORIGIN = {"Origin": "http://127.0.0.1:8420"}

    def connect(self, engine="chat"):
        token = self.token(engine=engine)
        ws = self.client.websocket_connect("ws://127.0.0.1:8420/api/voice/stream", headers=self.ORIGIN)
        return token, ws

    def open(self, ws, token):
        ws.send_json({"token": token})
        self.assertEqual(ws.receive_json()["type"], "connecting")
        ready = ws.receive_json()
        self.assertEqual(ready["type"], "ready")
        return ready

    def until(self, ws, done):
        events = []
        while not events or not done(events[-1]):
            events.append(ws.receive_json())
        return events

    def snapshot(self):
        response = self.client.get("/api/voice/conversation")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["conversation"]

    def wait_for(self, check):
        import time
        deadline = time.monotonic() + slack(10)
        while not (result := check()) and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertTrue(result)
        return result

    def fixtures(self, answers, transcripts=None, spoken=None):
        """chat_events answering each question from `answers`: tokens, event
        dicts, and threading.Events it waits on until set. The worker
        transcribes audio by `transcripts` and speaks every phrase briefly."""
        flags, histories = {}, {}

        async def events(question, history, workspace, cancelled, summary=None):
            flags[question], histories[question] = cancelled, history
            for part in answers[question]:
                if isinstance(part, threading.Event):
                    while not part.is_set():
                        await asyncio.sleep(.01)
                elif isinstance(part, dict):
                    yield part
                else:
                    yield {"type": "token", "text": part}
            yield {"type": "done"}

        async def native(system, content):
            if system == "Perform ASR.":
                yield {"type": "text", "text": (transcripts or {})[content[0]["input_audio"]["data"]]}
                return
            if spoken is not None:
                spoken.append(content)
            yield {"type": "audio", "data": "AAAAAA==", "sample_rate": 24000, "format": "pcm16"}
        worker = voice.sessions.worker
        for patcher in (patch.object(worker, "start", new_callable=AsyncMock),
                        patch.object(worker, "stop", new_callable=AsyncMock),
                        patch.object(worker, "stream", side_effect=native),
                        patch.object(voice, "chat_events", side_effect=events)):
            patcher.start(); self.addCleanup(patcher.stop)
        return flags, histories

    def speech(self, text, transcripts):
        """A distinct short WAV the fixture worker hears as `text`."""
        samples = (0.1, -0.1) + (0.01 * (len(transcripts) + 1),)
        encoded = base64.b64encode(wav_bytes(samples)).decode()
        transcripts[encoded] = text
        return encoded

    def test_a_reply_outlives_its_window_and_the_next_window_shows_it(self):
        release = threading.Event()
        _, histories = self.fixtures({"Where is billing?": ["Billing is on ", release, "port 8101."], "And staging?": ["Same port."]})
        token, socket = self.connect()
        with socket as ws:
            self.assertFalse(self.open(ws, token)["resumed"])
            ws.send_json({"type": "turn", "id": 1, "text": "Where is billing?"})
            self.until(ws, lambda event: event["type"] == "text")
        # The window closed mid-reply: the reply goes on and is kept.
        release.set()
        finished = self.wait_for(lambda: (lambda shown: shown if shown and shown["turns"][-1]["state"] == "done" else None)(self.snapshot()))
        self.assertEqual(finished["turns"][-1], {"id": 1, "user": "Where is billing?", "answer": "Billing is on port 8101.",
                                                 "state": "done", "detail": "", "error": "", "away": True})
        self.assertFalse(finished["attached"])
        voice.sessions.worker.stop.assert_awaited()  # nobody holds it while nothing runs
        token, socket = self.connect()
        with socket as ws:
            ready = self.open(ws, token)
            self.assertTrue(ready["resumed"])
            self.assertEqual((ready["last_id"], ready["turns"][-1]["answer"], ready["turns"][-1]["away"]), (1, "Billing is on port 8101.", True))
            ws.send_json({"type": "turn", "id": 2, "text": "And staging?"})
            self.until(ws, lambda event: event["type"] == "done")
        self.assertEqual([m["content"] for m in histories["And staging?"]], ["Where is billing?", "Billing is on port 8101."])

    def test_a_window_that_comes_back_mid_reply_sees_its_progress_and_the_rest(self):
        release = threading.Event()
        self.fixtures({"Long one?": ["The first part. ", release, "The second part."]})
        token, socket = self.connect()
        with socket as ws:
            self.open(ws, token)
            ws.send_json({"type": "turn", "id": 1, "text": "Long one?"})
            self.until(ws, lambda event: event["type"] == "text")
        token, socket = self.connect()
        with socket as ws:
            ready = self.open(ws, token)
            self.assertTrue(ready["resumed"] and ready["busy"])
            self.assertEqual((ready["turns"][-1]["state"], ready["turns"][-1]["answer"]), ("running", "The first part. "))
            release.set()
            events = self.until(ws, lambda event: event["type"] == "done")
        self.assertIn({"type": "text", "text": "The second part.", "id": 1}, events)
        self.assertTrue(any(event["type"] == "audio" for event in events))  # it speaks again for the window that came back
        self.assertFalse(self.snapshot()["turns"][-1]["away"])

    def test_a_question_during_a_reply_waits_its_turn_instead_of_cancelling_it(self):
        release = threading.Event()
        flags, _ = self.fixtures({"first": ["First answer.", release], "second": ["Second answer."]})
        token, socket = self.connect()
        with socket as ws:
            self.open(ws, token)
            ws.send_json({"type": "turn", "id": 1, "text": "first"})
            self.until(ws, lambda event: event["type"] == "text")
            ws.send_json({"type": "turn", "id": 2, "text": "second"})
            self.assertEqual(ws.receive_json(), {"type": "queued", "id": 2, "position": 1})
            self.assertEqual(ws.receive_json(), {"type": "user", "text": "second", "id": 2})
            release.set()
            events = self.until(ws, lambda event: event["type"] == "done" and event["id"] == 2)
        order = [(event["type"], event["id"]) for event in events if event["type"] in {"done", "start", "cancelled"}]
        self.assertEqual(order, [("done", 1), ("start", 2), ("done", 2)])
        self.assertFalse(flags["first"].is_set())
        self.assertEqual([turn["state"] for turn in self.snapshot()["turns"]], ["done", "done"])

    def test_noise_is_ignored_and_a_spoken_stop_stops_the_reply_and_what_waits(self):
        release, transcripts = threading.Event(), {}
        flags, _ = self.fixtures({"first": ["Working on it.", release], "queued": ["never"]}, transcripts)
        token, socket = self.connect()
        with socket as ws:
            self.open(ws, token)
            ws.send_json({"type": "turn", "id": 1, "text": "first"})
            self.until(ws, lambda event: event["type"] == "text")
            for identifier, heard in ((2, "[cough]"), (3, "Um..."), (4, "")):
                ws.send_json({"type": "turn", "id": identifier, "audio": self.speech(heard, transcripts)})
                self.assertEqual(ws.receive_json(), {"type": "queued", "id": identifier, "position": 1})
                self.assertEqual(ws.receive_json(), {"type": "ignored", "id": identifier, "reason": "unheard"})
            ws.send_json({"type": "turn", "id": 5, "text": "queued"})
            self.until(ws, lambda event: event["type"] == "user")
            self.assertFalse(flags["first"].is_set())  # still answering
            ws.send_json({"type": "turn", "id": 6, "audio": self.speech("Stop, please.", transcripts)})
            events = self.until(ws, lambda event: event["type"] == "stopped")
        self.assertIn({"type": "user", "text": "Stop, please.", "id": 6}, events)
        self.assertEqual(sorted(event["id"] for event in events if event["type"] == "cancelled"), [1, 5])
        self.assertEqual(events[-1], {"type": "stopped", "id": 6})
        self.assertTrue(flags["first"].is_set())
        self.assertNotIn("queued", flags)
        self.assertEqual([turn["state"] for turn in self.snapshot()["turns"]], ["cancelled", "cancelled"])

    def test_echo_hearing_itself_while_it_speaks_is_not_a_question(self):
        transcripts = {}
        self.fixtures({"port?": ["Billing listens on port 8101 in staging."], "Which port is billing on?": ["8101."]}, transcripts)
        token, socket = self.connect()
        with socket as ws:
            self.open(ws, token)
            ws.send_json({"type": "turn", "id": 1, "text": "port?"})
            self.until(ws, lambda event: event["type"] == "done")
            ws.send_json({"type": "turn", "id": 2, "overlap": True, "audio": self.speech("listens on port 8101 in staging", transcripts)})
            self.assertEqual(self.until(ws, lambda event: event["type"] == "ignored")[-1], {"type": "ignored", "id": 2, "reason": "echo"})
            ws.send_json({"type": "turn", "id": 3, "overlap": True, "audio": self.speech("Which port is billing on?", transcripts)})
            events = self.until(ws, lambda event: event["type"] == "done")
        self.assertIn({"type": "text", "text": "8101.", "id": 3}, events)

    def test_only_an_explicit_cancel_stops_and_one_waiting_question_can_be_withdrawn(self):
        release = threading.Event()
        flags, _ = self.fixtures({"first": ["One.", release], "second": ["Two."], "third": ["Three."], "fourth": ["Four.", threading.Event()]})
        token, socket = self.connect()
        with socket as ws:
            self.open(ws, token)
            ws.send_json({"type": "turn", "id": 1, "text": "first"})
            self.until(ws, lambda event: event["type"] == "text")
            for identifier, text in ((2, "second"), (3, "third")):
                ws.send_json({"type": "turn", "id": identifier, "text": text})
                self.until(ws, lambda event: event["type"] == "user")
            ws.send_json({"type": "cancel", "id": 2})
            self.assertEqual(ws.receive_json(), {"type": "cancelled", "id": 2})
            self.assertFalse(flags["first"].is_set())
            release.set()
            events = self.until(ws, lambda event: event["type"] == "done" and event["id"] == 3)
            self.assertNotIn(2, [event["id"] for event in events])
            ws.send_json({"type": "turn", "id": 4, "text": "fourth"})
            self.until(ws, lambda event: event["type"] == "text")
            ws.send_json({"type": "cancel"})
            self.assertEqual(ws.receive_json(), {"type": "cancelled", "id": 4})
        self.assertNotIn("second", flags)
        self.assertTrue(flags["fourth"].is_set())

    def test_ending_the_conversation_forgets_it_and_the_next_window_starts_afresh(self):
        self.fixtures({"hello": ["Hi."]})
        token, socket = self.connect()
        with socket as ws:
            self.open(ws, token)
            ws.send_json({"type": "turn", "id": 1, "text": "hello"})
            self.until(ws, lambda event: event["type"] == "done")
            ws.send_json({"type": "end"})
            with self.assertRaises(WebSocketDisconnect):
                ws.receive_json()
        self.assertIsNone(self.snapshot())
        token, socket = self.connect()
        with socket as ws:
            ready = self.open(ws, token)
        self.assertEqual((ready["resumed"], ready["turns"], ready["last_id"]), (False, [], 0))
        self.assertIsNone(self.snapshot())  # nothing was asked in that one

    def test_a_window_without_the_conversation_ends_it_unless_another_window_holds_it(self):
        self.fixtures({"hello": ["Hi."]})
        token, socket = self.connect()
        with socket as ws:
            self.open(ws, token)
            ws.send_json({"type": "turn", "id": 1, "text": "hello"})
            self.until(ws, lambda event: event["type"] == "done")
            self.assertEqual(self.client.post("/api/voice/conversation/end", headers=self.ORIGIN).status_code, 409)
        self.assertIsNotNone(self.snapshot())
        self.assertEqual(self.client.post("/api/voice/conversation/end").status_code, 403)
        response = self.client.post("/api/voice/conversation/end", headers=self.ORIGIN)
        self.assertEqual((response.status_code, response.json()), (200, {"conversation": None}))
        self.assertIsNone(self.snapshot())

    def test_another_model_takes_over_once_the_reply_in_progress_is_answered_and_keeps_the_conversation(self):
        release = threading.Event()
        self.fixtures({"first": ["Done soon.", release]})
        token, socket = self.connect("chat")
        with socket as ws:
            self.open(ws, token)
            ws.send_json({"type": "turn", "id": 1, "text": "first"})
            self.until(ws, lambda event: event["type"] == "text")
        token, socket = self.connect("lfm")
        with socket as ws:
            ready = self.open(ws, token)
            self.assertEqual((ready["resumed"], ready["engine"], ready["busy"]), (True, "chat", True))
            release.set()
            events = self.until(ws, lambda event: event["type"] == "ready")
        self.assertEqual([event["type"] for event in events if event["type"] in {"done", "ready"}], ["done", "ready"])
        self.assertEqual((events[-1]["engine"], events[-1]["resumed"]), ("lfm", False))
        # Saved conversations carry on in another look or model (ADR 0146).
        self.assertEqual([m["content"] for m in voice.sessions.conversation.history], ["first", "Done soon."])
        self.assertEqual(len(chat_store.list_sessions()), 1)
        token, socket = self.connect("chat")  # an idle conversation switches at once
        with socket as ws:
            ready = self.open(ws, token)
        self.assertEqual((ready["resumed"], ready["engine"], ready["last_id"]), (True, "chat", 1))
        self.assertEqual(len(voice.sessions.conversation.history), 2)

    def test_a_finished_answer_can_be_heard_again_without_asking_again(self):
        spoken = []
        self.fixtures({"hello": ["Hello there, this is the whole answer."]}, spoken=spoken)
        token, socket = self.connect()
        with socket as ws:
            self.open(ws, token)
            ws.send_json({"type": "turn", "id": 1, "text": "hello"})
            self.until(ws, lambda event: event["type"] == "done")
            spoken.clear()
            ws.send_json({"type": "replay", "id": 2, "of": 1})
            events = self.until(ws, lambda event: event["type"] == "done")
        self.assertEqual("".join(spoken), "Hello there, this is the whole answer.")
        self.assertTrue(all(event["id"] == 2 for event in events))
        self.assertEqual(len(voice.sessions.conversation.history), 2)
        self.assertEqual([turn["id"] for turn in self.snapshot()["turns"]], [1])

    def test_a_failed_reply_says_so_and_the_next_question_still_runs(self):
        self.fixtures({"broken": [{"type": "error", "error": "model crashed"}], "again": ["Fine now."]})
        token, socket = self.connect()
        with socket as ws:
            self.open(ws, token)
            ws.send_json({"type": "turn", "id": 1, "text": "broken"})
            error = self.until(ws, lambda event: event["type"] == "error")[-1]
            self.assertEqual(error, {"type": "error", "id": 1, "error": "The chat model could not reply. Open Chat to check its setup."})
            ws.send_json({"type": "turn", "id": 2, "text": "again"})
            self.until(ws, lambda event: event["type"] == "done")
        self.assertEqual([(turn["state"], turn["error"] != "") for turn in self.snapshot()["turns"]], [("failed", True), ("done", False)])

    def test_a_conversation_without_a_window_is_forgotten_after_a_while(self):
        self.fixtures({"hello": ["Hi."]})
        token, socket = self.connect()
        with socket as ws:
            self.open(ws, token)
            ws.send_json({"type": "turn", "id": 1, "text": "hello"})
            self.until(ws, lambda event: event["type"] == "done")
        self.assertIsNotNone(self.snapshot())
        # Below zero: on Windows the monotonic clock may not have moved yet.
        with patch.object(voice, "DETACHED_KEEP", -1):
            self.assertIsNone(self.snapshot())
        self.assertEqual(self.client.get("/api/voice/conversation", headers={"Origin": "https://evil.test"}).status_code, 403)

    # ── Saved in Chat history, and carried on from it (ADR 0146) ──────────
    def saved(self):
        """Each chat in Chat history: its kind and its messages as (role, content)."""
        return [(session["kind"], [(m["role"], m["content"]) for m in chat_store.get_messages(session["id"])])
                for session in chat_store.list_sessions()]

    def test_answered_questions_are_saved_as_a_voice_chat_and_noise_and_replays_are_not(self):
        transcripts = {}
        self.fixtures({"first": ["One."], "second": ["Two."]}, transcripts)
        token, socket = self.connect()
        with socket as ws:
            self.assertTrue(self.open(ws, token)["saving"])
            ws.send_json({"type": "turn", "id": 1, "text": "first"})
            self.until(ws, lambda event: event["type"] == "done")
            ws.send_json({"type": "turn", "id": 2, "audio": self.speech("[cough]", transcripts)})
            self.until(ws, lambda event: event["type"] == "ignored")
            ws.send_json({"type": "replay", "id": 3, "of": 1})
            self.until(ws, lambda event: event["type"] == "done")
            ws.send_json({"type": "turn", "id": 4, "text": "second"})
            self.until(ws, lambda event: event["type"] == "done")
        self.assertEqual(self.saved(), [("voice", [("user", "first"), ("assistant", "One."),
                                                   ("user", "second"), ("assistant", "Two.")])])
        self.assertEqual(chat_store.list_sessions()[0]["title"], "first")

    def test_a_stopped_reply_is_saved_as_far_as_it_got_and_the_next_question_sees_it(self):
        _, histories = self.fixtures({"long": ["The first part.", threading.Event()], "next": ["Next."]})
        token, socket = self.connect()
        with socket as ws:
            self.open(ws, token)
            ws.send_json({"type": "turn", "id": 1, "text": "long"})
            self.until(ws, lambda event: event["type"] == "text")
            ws.send_json({"type": "cancel"})
            self.until(ws, lambda event: event["type"] == "cancelled")
            ws.send_json({"type": "turn", "id": 2, "text": "next"})
            self.until(ws, lambda event: event["type"] == "done")
        self.assertEqual([m["content"] for m in histories["next"]], ["long", "The first part."])
        self.assertEqual(self.saved(), [("voice", [("user", "long"), ("assistant", "The first part."),
                                                   ("user", "next"), ("assistant", "Next.")])])

    def test_turning_saving_off_stops_saving_at_once_and_on_saves_the_next_conversation(self):
        self.fixtures({"kept": ["Saved."], "private": ["Not saved."], "later": ["Saved later."]})
        token, socket = self.connect()
        with socket as ws:
            self.open(ws, token)
            ws.send_json({"type": "turn", "id": 1, "text": "kept"})
            self.until(ws, lambda event: event["type"] == "done")
            with patch.dict(os.environ, {"IMPRINT_VOICE_SAVE_CONVERSATIONS": "false"}):
                ws.send_json({"type": "turn", "id": 2, "text": "private"})
                self.until(ws, lambda event: event["type"] == "done")
            ws.send_json({"type": "turn", "id": 3, "text": "later"})  # on again: this conversation stays unsaved
            self.until(ws, lambda event: event["type"] == "done")
        self.assertEqual(self.saved(), [("voice", [("user", "kept"), ("assistant", "Saved.")])])
        self.assertFalse(self.snapshot()["saving"])
        self.client.post("/api/voice/conversation/end", headers=self.ORIGIN)
        token, socket = self.connect()
        with socket as ws:
            self.assertTrue(self.open(ws, token)["saving"])
            ws.send_json({"type": "turn", "id": 1, "text": "later"})
            self.until(ws, lambda event: event["type"] == "done")
        self.assertEqual(len(self.saved()), 2)

    def stored(self, *exchanges, summary=None):
        """A chat in Chat history with these (question, answer) exchanges."""
        sid = chat_store.create_session()
        for question, answer in exchanges:
            chat_store.append_message(sid, "user", question)
            chat_store.append_message(sid, "assistant", answer)
        if summary:
            chat_store.set_summary(sid, summary, 2)
        return sid

    def test_a_chat_from_history_is_carried_on_and_its_new_exchanges_are_added_to_it(self):
        sid = self.stored(("Where is billing?", "Port 8101."), ("And auth?", "Port 8102."), summary="- Billing (#1–#2).")
        _, histories = self.fixtures({"And staging?": ["Same ports."]})
        flags = {}

        async def events(question, history, workspace, cancelled, summary=None):
            flags["summary"], flags["history"] = summary, history
            yield {"type": "token", "text": "Same ports."}
            yield {"type": "done"}
        voice.chat_events.side_effect = events
        token = self.token(engine="chat", chat=sid)
        with self.client.websocket_connect("ws://127.0.0.1:8420/api/voice/stream", headers=self.ORIGIN) as ws:
            ready = self.open(ws, token)
            self.assertEqual(ready["chat"], {"id": sid, "title": "Where is billing?"})
            self.assertEqual(ready["last_id"], 0)
            self.assertEqual([(t["id"], t["user"], t["answer"], t["state"]) for t in ready["turns"]],
                             [(-2, "Where is billing?", "Port 8101.", "done"), (-1, "And auth?", "Port 8102.", "done")])
            ws.send_json({"type": "turn", "id": 1, "text": "And staging?"})
            self.until(ws, lambda event: event["type"] == "done")
        self.assertEqual([(m["content"], m["seq"]) for m in flags["history"]],
                         [("Where is billing?", 1), ("Port 8101.", 2), ("And auth?", 3), ("Port 8102.", 4)])
        self.assertEqual(flags["summary"], {"text": "- Billing (#1–#2).", "covers": 2, "retry_after": 0})
        self.assertEqual(self.saved(), [("chat", [("user", "Where is billing?"), ("assistant", "Port 8101."),
                                                  ("user", "And auth?"), ("assistant", "Port 8102."),
                                                  ("user", "And staging?"), ("assistant", "Same ports.")])])

    def test_another_chat_takes_the_place_of_an_idle_conversation(self):
        sid = self.stored(("Old question?", "Old answer."))
        self.fixtures({"hello": ["Hi."]})
        token, socket = self.connect()
        with socket as ws:
            self.open(ws, token)
            ws.send_json({"type": "turn", "id": 1, "text": "hello"})
            self.until(ws, lambda event: event["type"] == "done")
        token = self.token(engine="chat", chat=sid)
        with self.client.websocket_connect("ws://127.0.0.1:8420/api/voice/stream", headers=self.ORIGIN) as ws:
            ready = self.open(ws, token)
        self.assertEqual((ready["resumed"], ready["chat"]["id"]), (False, sid))
        self.assertEqual([turn["user"] for turn in ready["turns"]], ["Old question?"])
        # The conversation it replaced was saved; it stays in Chat history.
        self.assertEqual(sorted(kind for kind, _ in self.saved()), ["chat", "voice"])

    def test_a_chat_that_does_not_exist_cannot_be_carried_on(self):
        for chat, status in (("missing", 404), (7, 400), ("", 400), ("x" * 65, 400)):
            response = self.client.post("/api/voice/sessions", json={"engine": "chat", "chat": chat}, headers=self.ORIGIN)
            self.assertEqual(response.status_code, status, chat)

    def test_deleting_the_chat_stops_saving_the_conversation(self):
        self.fixtures({"first": ["One."], "second": ["Two."]})
        token, socket = self.connect()
        with socket as ws:
            self.open(ws, token)
            ws.send_json({"type": "turn", "id": 1, "text": "first"})
            self.until(ws, lambda event: event["type"] == "done")
            chat_store.delete_session(chat_store.list_sessions()[0]["id"])
            ws.send_json({"type": "turn", "id": 2, "text": "second"})
            self.until(ws, lambda event: event["type"] == "done")
        self.assertEqual(self.saved(), [])
        self.assertFalse(self.snapshot()["saving"])

    def test_what_counts_as_noise_an_echo_or_a_stop(self):
        from imprint import wake
        for text in ("", "  ", "[cough]", "(clears throat)", "Um.", "hmm, uh", "*sigh*"):
            self.assertTrue(voice.unheard(text), text)
        for text in ("Stop", "what", "Um, which port?"):
            self.assertFalse(voice.unheard(text), text)
        answer = "Billing listens on port 8101 in staging, and 8102 in production."
        self.assertTrue(voice.echoes("listens on port 8101 in staging", answer))
        self.assertFalse(voice.echoes("which port is billing on", answer))
        self.assertFalse(voice.echoes("8101", answer))  # too short to tell
        for text in ("Stop.", "stop please", "Cancel!", "Never mind, Echo.", "Echo, stop", "OK stop", "stop talking"):
            self.assertTrue(wake.stop_phrase(text), text)
        for text in ("Stop the billing service", "don't stop", "Thanks", "please continue", ""):
            self.assertFalse(wake.stop_phrase(text), text)


class VoiceValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_audio_window_waits_for_playback_and_rejects_invented_progress(self):
        window = voice.PlaybackWindow()
        audio = base64.b64encode(bytes(voice.PLAYBACK_AHEAD * 2)).decode()
        await window.reserve(audio)
        pending = asyncio.create_task(window.reserve("AAAAAA=="))
        await asyncio.sleep(0)
        self.assertFalse(pending.done())
        for invalid in (True, -1, window.sent + 1, "24000"):
            with self.assertRaises(VoiceMemoryError):
                window.acknowledge(invalid)
        window.acknowledge(window.sent)
        await asyncio.wait_for(pending, slack(10))
        self.assertEqual(window.sent - window.played, 2)
        with self.assertRaises(VoiceMemoryError):
            window.acknowledge(0)

    async def test_a_released_window_stops_waiting_for_playback(self):
        window = voice.PlaybackWindow()
        window.sent = voice.PLAYBACK_AHEAD
        pending = asyncio.create_task(window.reserve("AAAAAA=="))
        await asyncio.sleep(0)
        self.assertFalse(pending.done())
        window.release()  # the window playing it went away (ADR 0145)
        await asyncio.wait_for(pending, slack(10))
        await asyncio.wait_for(window.reserve("AAAAAA=="), slack(10))

    async def test_cancelling_audio_wait_drops_blocked_generation(self):
        window = voice.PlaybackWindow()
        window.sent = voice.PLAYBACK_AHEAD
        pending = asyncio.create_task(window.reserve("AAAAAA=="))
        await asyncio.sleep(0)
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertEqual(window.sent, voice.PLAYBACK_AHEAD)

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
            with self.assertRaisesRegex(VoiceMemoryError, "too long to load"):
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
            await asyncio.wait_for(voice.stream(socket), slack(10))
            self.assertTrue(cancelled.is_set())
            self.assertFalse(registry.active)
            stop.assert_awaited()

    async def test_microphone_wav_limits_and_encoding(self):
        content = await voice.audio_content(base64.b64encode(wav_bytes()).decode())
        self.assertEqual(content[0]["input_audio"]["format"], "wav")
        for value in ("not base64", base64.b64encode(b"not audio" * 10).decode(), "A" * 1_920_061):
            with self.assertRaises(VoiceMemoryError):
                await voice.audio_content(value)

    async def test_chat_bridge_uses_same_model_with_read_only_and_pinned_workspace(self):
        cancellation = threading.Event()
        with patch.object(chat, "stream_reply", return_value=iter([{"type": "token", "text": "Hello."}])) as reply:
            events = [event async for event in voice.chat_events("hello", [], "alpha", cancellation)]
        self.assertEqual(events, [{"type": "token", "text": "Hello."}])
        self.assertFalse(cancellation.is_set())
        self.assertEqual(reply.call_args.kwargs, {"workspace": "alpha", "cancel_event": cancellation, "read_only": True,
                                                  "summary": None})


class VoiceChatPolicyTests(unittest.TestCase):
    def test_cancellation_closes_stream_and_prevents_tool_execution(self):
        event = threading.Event()

        def chunks():
            yield call_chunk(0, "store", '{"content": "No write"}', id="a")
            event.set()

        write = Mock()
        client, provider = remote_provider(chunks())
        with provider[0], provider[1], provider[2], patch.dict(chat.TOOLS, tool("store", write)):
            list(chat.stream_reply([], "hello", cancel_event=event))
        write.assert_not_called()
        self.assertTrue(client.streams[0].closed)

    def test_voice_offers_read_tools_rejects_writes_and_keeps_session_workspace(self):
        write, read = Mock(), Mock(return_value="all good")
        client, provider = remote_provider([call_chunk(0, "store", '{"content": "x"}', id="a")],
                                           [call_chunk(0, "status", '{"workspace": "other"}', id="b")],
                                           [text_chunk("Done.")])
        with provider[0], provider[1], provider[2], patch.dict(chat.TOOLS, {**tool("store", write), **tool("status", read)}):
            list(chat.stream_reply([], "hello", workspace="alpha", read_only=True))
        write.assert_not_called()
        read.assert_called_once_with(workspace="alpha")
        offered = [t["function"]["name"] for t in client.chat.completions.create.call_args_list[0].kwargs["tools"]]
        self.assertEqual(offered, ["search", "kg_query", "status", "wake_up", "list_sources", "file_summary", "file_chunks", "read_source"])


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
                with self.assertRaises(VoiceMemoryError):
                    voice_runtime.verify(root)
                binary.write_bytes(b"native fixture")
                with patch.object(voice_runtime, "patched_server", return_value="new patch"):
                    with self.assertRaises(VoiceMemoryError):
                        voice_runtime.verify(root)
                archive.write_bytes(b"changed archive")
                with self.assertRaises(VoiceMemoryError):
                    voice_runtime.verify(root)

    def test_source_patch_and_invalid_build_fail_before_execution(self):
        with self.assertRaises(VoiceMemoryError):
            voice_runtime.patched_server("unrecognized upstream")
        with patch.object(voice_runtime.lfm, "_platform"), \
                patch.object(voice_runtime.lfm, "install") as download:
            with self.assertRaises(VoiceMemoryError):
                voice_runtime.install(Path("unused"), jobs=99)
            with patch.object(voice_runtime.shutil, "which", return_value=None):
                with self.assertRaises(VoiceMemoryError):
                    voice_runtime.install(Path("unused"))
            download.assert_not_called()

    def test_native_targets_include_windows_and_apple_silicon(self):
        for system, machine, expected in [("Windows", "AMD64", "windows-x64"), ("Darwin", "arm64", "macos-arm64"),
                                          ("Linux", "x86_64", "linux-x64")]:
            with patch.object(voice_runtime.platform, "system", return_value=system), \
                    patch.object(voice_runtime.platform, "machine", return_value=machine):
                self.assertEqual(voice_runtime.target(), expected)
                self.assertTrue(voice_runtime.supported())
                self.assertEqual(voice_runtime.native_binary(Path("cache")).suffix, ".exe" if system == "Windows" else "")
        # Intel Macs are not supported (ADR 0111).
        for system, machine in [("Windows", "arm64"), ("Darwin", "x86_64")]:
            with patch.object(voice_runtime.platform, "system", return_value=system), \
                    patch.object(voice_runtime.platform, "machine", return_value=machine):
                self.assertFalse(voice_runtime.supported())
        with self.assertRaises(VoiceMemoryError):
            voice_runtime.native_binary(Path("cache"), "macos-x64")


class VoiceLookTests(unittest.TestCase):
    """Each look speaks in its own voice, pinned to the session (ADR 0071)."""
    setUp = VoiceBoundaryTests.setUp
    token = VoiceBoundaryTests.token

    def test_every_look_has_a_voice_and_an_unknown_look_speaks_as_echo(self):
        from imprint import config_schema
        looks = {choice.value for choice in next(s for s in config_schema.SETTINGS if s.key == "voice.avatar").choices}
        self.assertEqual(looks, set(voice.VOICES))
        self.assertEqual(voice.look_voice("kai"), "kai")
        for unknown in ("robot", "", None, ["iris"]):
            self.assertEqual(voice.look_voice(unknown), "echo")

    def test_the_configured_look_is_pinned_to_the_capability(self):
        for look, expected in (("kai", "kai"), ("iris", "iris"), ("echo", "echo"), ("someone-else", "echo")):
            with self.subTest(look=look), patch.dict(os.environ, {"IMPRINT_VOICE_AVATAR": look}):
                token = self.token(engine="lfm")
                self.assertEqual(voice.sessions.consume(token, "http://127.0.0.1:8420")["voice"], expected)

    def test_both_reasoners_ask_the_native_model_for_the_look_speaker(self):
        for engine in ("chat", "lfm"):
            for look, (speaker, effect) in voice.VOICES.items():
                prompts = []

                async def native(system, content):
                    prompts.append(system)
                    yield {"type": "text", "text": "Hello."}
                    yield {"type": "audio", "data": "AAAAAA==", "sample_rate": 24000, "format": "pcm16"}
                with self.subTest(engine=engine, look=look), patch.dict(os.environ, {"IMPRINT_VOICE_AVATAR": look}), \
                        patch.object(voice.sessions.worker, "start", new_callable=AsyncMock), \
                        patch.object(voice.sessions.worker, "stop", new_callable=AsyncMock), \
                        patch.object(voice.sessions.worker, "stream", side_effect=native), \
                        patch.object(voice, "memory_prompt", new_callable=AsyncMock, return_value="fixture"), \
                        patch.object(chat, "stream_reply", return_value=iter([{"type": "token", "text": "Hello."}, {"type": "done"}])):
                    token = self.token(engine=engine)
                    with self.client.websocket_connect("ws://127.0.0.1:8420/api/voice/stream", headers={"Origin": "http://127.0.0.1:8420"}) as ws:
                        ws.send_json({"token": token})
                        ws.receive_json()
                        ready = ws.receive_json()
                        self.assertEqual((ready["type"], ready["voice"], ready["effect"]), ("ready", look, effect))
                        # The look changes; the conversation carries on and so does its numbering.
                        ws.send_json({"type": "turn", "id": ready["last_id"] + 1, "text": "Hello"})
                        while ws.receive_json()["type"] != "done":
                            pass
                    self.assertTrue(prompts)
                    self.assertIn(f"Use the {speaker} voice.", prompts[-1])


if __name__ == "__main__":
    unittest.main()
