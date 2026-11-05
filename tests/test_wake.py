"""'Hey Echo' (ADR 0137): the matcher, audio bounds, the probe budget, the
worker's protocol and the API's wake routes. A fake detector keeps these fast;
one test runs the real tiny.en model when IMPRINT_WAKE_MODEL_DIR names a folder
holding it (and macOS `say` can speak the clips)."""
import asyncio
import base64
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import wave

from fastapi import FastAPI
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from imprint import audio_ingest, voice_api, wake, wake_worker

ORIGIN = "http://127.0.0.1:8420"


def wav16(seconds: float, rate: int = 16000, channels: int = 1, amplitude: float = .3) -> bytes:
    import numpy as np
    count = int(seconds * rate)
    tone = (amplitude * np.sin(np.arange(count) * 2 * np.pi * 220 / rate) * 32767).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(channels); writer.setsampwidth(2); writer.setframerate(rate)
        writer.writeframes(np.repeat(tone, channels).tobytes())
    return buffer.getvalue()


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


class MatcherTests(unittest.TestCase):
    WAKES = ["Hey Echo", "Hey, Echo.", "Hey Echo, what did we decide yesterday?", "hey, Ecco", "Hi Echo, are you there?",
             "OK Echo", "Okay, Echo!", "Hello Echo", "Hayeko, open the dashboard.", "He echo", "Here, echo.", "Hey Ico",
             "Oh, hey Echo, one thing", "Hey Echos what's the status", "HEY ECHO"]
    STAYS = ["Hey everyone, let's get started.", "The echo in this room is terrible.", "Hey Alexa, what's the weather?",
             "Hey Jessica, can you check the logs?", "Did you hear the echo?", "Hey, let's check the echo cancellation settings.",
             "Yesterday we decided to ship on Friday.", "Hey Siri, set a timer.", "Hey Kai, what did we decide?",
             "The Amazon Echo is on sale this week.", "Head of engineering said hello.", "That thread became an echo chamber.",
             "Here you go.", "Hey, I go.", "He echoed my point.", "Hey, co-workers, listen", "Hey, Ecto.", "", "...",
             "Well I said hey and then Echo answered"]

    def test_a_greeting_then_echo_opens_the_utterance(self):
        for text in self.WAKES:
            self.assertTrue(wake.matches(text), text)
        for text in self.STAYS:
            self.assertFalse(wake.matches(text), text)

    def test_the_question_is_what_follows_the_phrase(self):
        self.assertEqual(wake.strip_wake("Hey Echo, what did we decide yesterday?"), "what did we decide yesterday?")
        self.assertEqual(wake.strip_wake("Hayeko, open the dashboard."), "open the dashboard.")
        self.assertEqual(wake.strip_wake("Hey, Echo."), "")
        self.assertEqual(wake.strip_wake("Oh, hey Echo — how many memories?"), "how many memories?")
        self.assertEqual(len(wake.strip_wake("Hey Echo " + "x" * 5000)), 2000)

    def test_end_phrases_are_whole_short_utterances(self):
        for text in ["Stop.", "stop", "Thanks, Echo.", "Thank you", "That's all.", "Never mind", "Goodbye Echo", "bye"]:
            self.assertTrue(wake.end_phrase(text), text)
        for text in ["Stop the deploy and tell me why", "Thanks for the summary of yesterday's decisions", "Echo", "",
                     "what did we decide"]:
            self.assertFalse(wake.end_phrase(text), text)


class AudioTests(unittest.TestCase):
    def test_probes_are_mono_16k_pcm16_and_bounded_in_memory(self):
        with patch("tempfile.NamedTemporaryFile", side_effect=AssertionError("no files")), \
                patch("tempfile.TemporaryDirectory", side_effect=AssertionError("no files")):
            self.assertEqual(len(wake.samples(wav16(2.0), wake.PROBE_SECONDS)), 32000)
            for bad in (wav16(1, rate=24000), wav16(1, channels=2), wav16(3.0), b"RIFF garbage", b""):
                with self.assertRaises(ValueError):
                    wake.samples(bad, wake.PROBE_SECONDS)

    def test_the_budget_checks_bursts_after_a_pause_and_stops_at_twice_its_size(self):
        now = [0.0]
        budget = wake.Budget(2.5, clock=lambda: now[0])
        self.assertTrue(budget.allows(0))
        budget.spend(2.6)
        self.assertFalse(budget.allows(0.3))
        self.assertTrue(budget.allows(1.2))
        budget.spend(2.5)
        self.assertFalse(budget.allows(5))
        now[0] = 61
        self.assertTrue(budget.allows(0))


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.dict(os.environ, {"IMPRINT_DATA_DIR": self.temp.name})
        patcher.start(); self.addCleanup(patcher.stop)

    def test_the_pin_is_tiny_en_at_its_measured_revision_and_hashes(self):
        self.assertEqual(wake.MODEL.repo, "Systran/faster-whisper-tiny.en")
        self.assertEqual(wake.MODEL.revision, "0d3d19a32d3338f10357c0889762bd8d64bbdeba")
        self.assertEqual(wake.MODEL.files["model.bin"][0], 75537502)
        self.assertTrue(all(len(digest) == 64 for _, digest in wake.MODEL.files.values()))
        self.assertEqual(wake.root().parent, Path(self.temp.name) / "models")

    def test_status_checks_sizes_and_the_worker_verifies_hashes_without_downloading(self):
        self.assertFalse(wake.installed())
        with self.assertRaises(FileNotFoundError):
            wake.verify()
        wake.root().mkdir(parents=True)
        for name, (size, _) in wake.MODEL.files.items():
            with open(wake.root() / name, "wb") as handle:
                handle.truncate(size)
        self.assertTrue(wake.installed())
        with patch.object(audio_ingest, "ensure_model", side_effect=AssertionError("no download")), \
                self.assertRaises(ValueError):
            wake.verify()

    def test_download_reports_to_its_caller_not_the_job_progress_record(self):
        seen = []
        with patch.object(audio_ingest, "report", side_effect=AssertionError("job progress")), \
                patch("imprint.wake.ensure_model", side_effect=lambda spec, progress: progress("downloading_transcription_model", 5, 9, "bytes")):
            wake.download(progress=lambda *args: seen.append(args))
        self.assertEqual(seen, [("downloading_transcription_model", 5, 9, "bytes")])


class FakeDetector:
    def __init__(self, folder=None):
        pass

    def probe(self, audio):
        return len(audio) > 16000

    def question(self, audio):
        return "what did we decide"


class WorkerTests(unittest.TestCase):
    def run_worker(self, lines, detector=FakeDetector, verify=lambda: Path("/model")):
        stdin = type("In", (), {"buffer": io.BytesIO(b"".join(json.dumps(line).encode() + b"\n" for line in lines))})()
        out = io.StringIO()
        with patch.object(sys, "stdin", stdin), patch.object(sys, "stdout", out), \
                patch.object(wake, "Detector", detector), patch.object(wake, "verify", verify), \
                patch.object(wake_worker, "lower_priority"):
            code = wake_worker.main()
        return code, [json.loads(line) for line in out.getvalue().splitlines()]

    def test_it_answers_probes_and_utterances_and_ends_with_its_input(self):
        code, events = self.run_worker([{"id": 1, "kind": "probe", "audio": b64(wav16(0.5))},
                                        {"id": 2, "kind": "probe", "audio": b64(wav16(2.0))},
                                        {"id": 2, "kind": "utterance", "audio": b64(wav16(4.0))}])
        self.assertEqual(code, 0)
        self.assertEqual(events[0], {"ready": True})
        self.assertEqual([(e["id"], e["wake"]) for e in events[1:3]], [(1, False), (2, True)])
        self.assertEqual(events[3], {"id": 2, "text": "what did we decide"})

    def test_a_bad_message_or_a_missing_model_ends_it(self):
        code, events = self.run_worker([{"id": "x", "kind": "probe", "audio": ""}])
        self.assertEqual((code, events[-1]), (1, {"error": "Invalid message."}))
        code, events = self.run_worker([{"id": 1, "kind": "probe", "audio": b64(wav16(3.0))}])
        self.assertEqual(code, 1)

        def missing():
            raise FileNotFoundError("no model")
        code, events = self.run_worker([], verify=missing)
        self.assertEqual(code, 1)
        self.assertIn("missing", events[0]["error"])

    def test_the_real_process_reports_a_missing_model_and_exits(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, "-m", "imprint.wake_worker"], input=b"", capture_output=True, timeout=120,
                                    cwd=Path(__file__).resolve().parents[1], env={**os.environ, "IMPRINT_DATA_DIR": directory})
        self.assertEqual(result.returncode, 1)
        self.assertIn("error", json.loads(result.stdout.splitlines()[0]))


class FakeWorker:
    started = 0
    asked: list = []

    async def start(self):
        FakeWorker.started += 1

    async def ask(self, message, timeout):
        FakeWorker.asked.append(message["kind"])
        if message["kind"] == "probe":
            return {"id": message["id"], "wake": len(base64.b64decode(message["audio"])) > 40000, "cpu": 1.4}
        return {"id": message["id"], "text": "what did we decide"}

    async def stop(self):
        pass


class WakeRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        FakeWorker.started, FakeWorker.asked = 0, []
        for patcher in [patch.dict(os.environ, {"IMPRINT_DATA_DIR": self.temp.name}),
                        patch.object(voice_api, "wake_sessions", voice_api.WakeSessions()),
                        patch.object(voice_api, "wake_setup", voice_api.WakeSetup()),
                        patch.object(voice_api, "WakeWorker", FakeWorker)]:
            patcher.start(); self.addCleanup(patcher.stop)
        app = FastAPI()
        app.include_router(voice_api.router)
        self.client = TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 1234))
        self.addCleanup(self.client.close)

    def token(self, **body):
        response = self.client.post("/api/voice/wake/sessions", json=body, headers={"Origin": ORIGIN})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["token"]

    def connect(self):
        return self.client.websocket_connect("ws://127.0.0.1:8420/api/voice/wake/stream", headers={"Origin": ORIGIN})

    def test_the_model_downloads_only_on_request_with_byte_progress(self):
        status = self.client.get("/api/voice/wake/status").json()
        self.assertEqual((status["installed"], status["model"], status["bytes"], status["idle_seconds"]),
                         (False, "tiny.en", wake.TOTAL_BYTES, 15))
        self.assertEqual(status["setup"]["stage"], "idle")
        self.assertEqual(self.client.post("/api/voice/wake/sessions", json={}, headers={"Origin": ORIGIN}).status_code, 409)

        def download(progress):
            progress("downloading_transcription_model", 1234, wake.TOTAL_BYTES, "bytes")
            return wake.root()
        with patch.object(wake, "download", side_effect=download):
            self.assertEqual(self.client.post("/api/voice/wake/setup", headers={"Origin": "http://evil.example"}).status_code, 403)
            self.client.post("/api/voice/wake/setup", headers={"Origin": ORIGIN})
            for _ in range(200):
                if self.client.get("/api/voice/wake/status").json()["setup"]["stage"] == "ready":
                    break
                time.sleep(.01)
        self.assertEqual(self.client.get("/api/voice/wake/status").json()["setup"]["downloaded"], wake.TOTAL_BYTES)

    def test_cancel_stops_the_download(self):
        def download(progress):
            for _ in range(500):
                progress("downloading_transcription_model", 1, 2, "bytes")
                time.sleep(.01)
        with patch.object(wake, "download", side_effect=download):
            self.client.post("/api/voice/wake/setup", headers={"Origin": ORIGIN})
            self.client.post("/api/voice/wake/setup/cancel", headers={"Origin": ORIGIN})
            for _ in range(300):
                if self.client.get("/api/voice/wake/status").json()["setup"]["stage"] == "cancelled":
                    break
                time.sleep(.01)
        self.assertEqual(self.client.get("/api/voice/wake/status").json()["setup"]["stage"], "cancelled")

    def test_sessions_need_the_local_origin(self):
        with patch.object(wake, "installed", return_value=True):
            self.assertEqual(self.client.post("/api/voice/wake/sessions", json={}, headers={"Origin": "http://evil.example"}).status_code, 403)
            self.assertEqual(self.client.post("/api/voice/wake/sessions", json={"battery": "yes"}, headers={"Origin": ORIGIN}).status_code, 400)
            token = self.token()
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect("ws://127.0.0.1:8420/api/voice/wake/stream", headers={"Origin": "http://evil.example"}) as ws:
                ws.send_json({"token": token}); ws.receive_json()
        self.assertEqual(FakeWorker.started, 0)

    def test_a_wake_is_answered_yes_or_no_and_only_its_utterance_is_transcribed(self):
        with patch.object(wake, "installed", return_value=True):
            token = self.token()
        with self.connect() as ws:
            ws.send_json({"token": token})
            self.assertEqual(ws.receive_json(), {"type": "ready"})
            ws.send_json({"type": "probe", "id": 1, "audio": b64(wav16(1.0)), "quiet": 3})
            self.assertEqual(ws.receive_json(), {"type": "probe", "id": 1, "wake": False})
            ws.send_json({"type": "probe", "id": 2, "audio": b64(wav16(2.0)), "quiet": 3})
            self.assertEqual(ws.receive_json(), {"type": "probe", "id": 2, "wake": True})
            ws.send_json({"type": "utterance", "id": 2, "audio": b64(wav16(4.0))})
            self.assertEqual(ws.receive_json(), {"type": "question", "id": 2, "text": "what did we decide"})
            # Each wake transcribes one utterance; a probe that did not wake gets none.
            ws.send_json({"type": "utterance", "id": 1, "audio": b64(wav16(4.0))})
            self.assertEqual(ws.receive_json()["type"], "error")
        self.assertFalse(voice_api.wake_sessions.active)
        self.assertEqual(FakeWorker.asked, ["probe", "probe", "utterance"])

    def test_the_budget_skips_bursts_without_a_pause_and_one_window_listens(self):
        with patch.object(wake, "installed", return_value=True):
            token, second = self.token(battery=True), self.token()
        with self.connect() as ws:
            ws.send_json({"token": token}); ws.receive_json()
            ws.send_json({"type": "probe", "id": 1, "audio": b64(wav16(1.0)), "quiet": 0})
            self.assertNotIn("skipped", ws.receive_json())
            # On battery the budget is 1.25 CPU-s a minute; the first probe spent 1.4.
            ws.send_json({"type": "probe", "id": 2, "audio": b64(wav16(1.0)), "quiet": 0.2})
            self.assertEqual(ws.receive_json(), {"type": "probe", "id": 2, "wake": False, "skipped": True})
            ws.send_json({"type": "probe", "id": 3, "audio": b64(wav16(1.0)), "quiet": 2})
            self.assertNotIn("skipped", ws.receive_json())
            with self.connect() as other:
                other.send_json({"token": second})
                self.assertIn("already listening", other.receive_json()["error"])
            ws.send_json({"type": "probe", "id": 3, "audio": b64(wav16(1.0))})
            self.assertEqual(ws.receive_json()["type"], "error")

    def test_probes_are_bounded(self):
        with patch.object(wake, "installed", return_value=True):
            token = self.token()
        with self.connect() as ws:
            ws.send_json({"token": token}); ws.receive_json()
            ws.send_json({"type": "probe", "id": 1, "audio": b64(wav16(4.0))})
            self.assertEqual(ws.receive_json()["type"], "error")
        self.assertNotIn("probe", FakeWorker.asked)


class WorkerProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_worker_that_fails_to_load_reports_why(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"IMPRINT_DATA_DIR": directory}):
            worker = voice_api.WakeWorker()
            with self.assertRaises(voice_api.VoiceMemoryError) as raised:
                await worker.start()
        self.assertIn("missing", str(raised.exception))
        self.assertIsNone(worker.process)


@unittest.skipUnless(os.environ.get("IMPRINT_WAKE_MODEL_DIR") and shutil.which("say"),
                     "set IMPRINT_WAKE_MODEL_DIR to a folder with tiny.en (macOS)")
class RealModelTests(unittest.TestCase):
    def speak(self, folder, text):
        path = Path(folder) / f"{abs(hash(text))}.wav"
        subprocess.run(["say", "-v", "Samantha", "-o", str(path), "--file-format=WAVE", "--data-format=LEI16@16000", text], check=True)
        import numpy as np
        audio = wake.samples(path.read_bytes(), 30)
        return np.concatenate([np.zeros(3840, np.float32), audio])

    def test_tiny_en_wakes_on_the_phrase_and_transcribes_the_question(self):
        detector = wake.Detector(Path(os.environ["IMPRINT_WAKE_MODEL_DIR"]))
        with tempfile.TemporaryDirectory() as folder:
            question = self.speak(folder, "Hey Echo, what did we decide yesterday?")
            self.assertTrue(detector.probe(question[:32000]))
            self.assertIn("decide", detector.question(question).lower())
            self.assertFalse(detector.probe(self.speak(folder, "The echo in this room is terrible.")[:32000]))
            self.assertFalse(detector.probe(__import__("numpy").zeros(32000, "float32")))

    def test_the_worker_process_verifies_the_installed_model_and_answers(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as folder, \
                patch.dict(os.environ, {"IMPRINT_DATA_DIR": data}):
            wake.root().mkdir(parents=True)
            for name in wake.MODEL.files:
                os.symlink(Path(os.environ["IMPRINT_WAKE_MODEL_DIR"]) / name, wake.root() / name)
            question = self.speak(folder, "Hey Echo, how many memories are in this workspace?")

            async def converse():
                worker = voice_api.WakeWorker()
                await worker.start()
                try:
                    def encode(audio):
                        clip = io.BytesIO()
                        with wave.open(clip, "wb") as writer:
                            writer.setnchannels(1); writer.setsampwidth(2); writer.setframerate(16000)
                            writer.writeframes((audio * 32767).astype("<i2").tobytes())
                        return b64(clip.getvalue())
                    probe = await worker.ask({"id": 1, "kind": "probe", "audio": encode(question[:32000])}, 30)
                    text = await worker.ask({"id": 1, "kind": "utterance", "audio": encode(question)}, 30)
                    return probe, text
                finally:
                    await worker.stop()
            probe, text = asyncio.run(converse())
        self.assertTrue(probe["wake"])
        self.assertGreater(probe["cpu"], 0)
        self.assertIn("memories", text["text"].lower())


if __name__ == "__main__":
    unittest.main()
