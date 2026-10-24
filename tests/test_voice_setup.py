import contextlib
import ctypes
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from fastapi import FastAPI
from starlette.testclient import TestClient

from imprint import voice_api as voice, voice_setup as setup, lfm_audio as lfm
from imprint.personaplex import PrototypeError


class SetupBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for p in [patch.dict(os.environ, IMPRINT_DATA_DIR=str(self.root), IMPRINT_VOICE_RUNTIME_DIR=""),
                  patch.object(setup, "setup", setup.Setup()), patch.object(voice, "sessions", voice.Sessions()),
                  patch.object(voice.voice_runtime, "supported", return_value=True)]:
            p.start(); self.addCleanup(p.stop)
        app = FastAPI(); app.include_router(voice.router)
        self.client = TestClient(app, base_url="http://127.0.0.1:8420", client=("127.0.0.1", 1234))
        self.addCleanup(self.client.close)
        self.origin = {"Origin": "http://127.0.0.1:8420"}

    def test_default_cache_does_not_rewrite_user_configuration(self):
        (self.root / "config.json").write_text('{"chat":{"provider":"local"}}')
        before = (self.root / "config.json").read_bytes()
        self.assertEqual(setup.runtime_root(), self.root / "voice")
        self.assertFalse(self.client.get("/api/voice/setup").json()["installed"])
        self.assertEqual((self.root / "config.json").read_bytes(), before)

    def test_setup_mutations_require_exact_origin_and_accept_no_paths_or_commands(self):
        with patch.object(setup.setup, "start", return_value={"stage": "checking"}) as start:
            for origin in [None, "null", "https://evil.test", "http://127.0.0.1:3100"]:
                headers = {"Origin": origin} if origin else {}
                self.assertEqual(self.client.post("/api/voice/setup", headers=headers).status_code, 403)
                self.assertEqual(self.client.post("/api/voice/setup/cancel", headers=headers).status_code, 403)
            for body in [b'{"path":"/tmp"}', b"x" * 5000]:
                self.assertEqual(self.client.post("/api/voice/setup", headers=self.origin, content=body).status_code, 400)
            start.assert_not_called()
            self.assertEqual(self.client.post("/api/voice/setup", headers=self.origin).status_code, 200)
            start.assert_called_once_with()

    def test_setup_and_voice_cannot_run_concurrently(self):
        voice.sessions.active = True
        with patch.object(setup.setup, "start") as start:
            self.assertEqual(self.client.post("/api/voice/setup", headers=self.origin).status_code, 409)
            start.assert_not_called()
        voice.sessions.active = False
        with patch.object(setup.setup, "snapshot", return_value={"busy": True}):
            response = self.client.post("/api/voice/sessions", json={"engine": "lfm"}, headers=self.origin)
            self.assertEqual(response.status_code, 409)


class SetupWorkerTests(unittest.TestCase):
    def test_missing_prerequisite_is_reported_before_any_download(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(setup.threading, "Thread"), \
                patch.object(setup.voice_runtime, "verify", side_effect=PrototypeError("not installed")), \
                patch.object(setup, "prerequisites", return_value="cmake"), \
                patch.object(setup.voice_runtime, "install") as install, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(setup.run_worker(Path(directory), "a" * 24), 2)
            install.assert_not_called()
        self.assertEqual(json.loads(output.getvalue().splitlines()[-1]), {"stage": "blocked", "problem": "cmake"})

    def test_cached_runtime_needs_no_network_or_build_tools(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(setup.threading, "Thread"), \
                patch.object(setup.voice_runtime, "verify"), patch.object(setup, "prerequisites") as preflight, \
                patch.object(setup.voice_runtime, "install") as install, contextlib.redirect_stdout(io.StringIO()) as output:
            unrelated = Path(directory) / ".voice-user-note.part"
            unrelated.write_text("keep")
            self.assertEqual(setup.run_worker(Path(directory), "a" * 24), 0)
            self.assertEqual(unrelated.read_text(), "keep")
            preflight.assert_not_called(); install.assert_not_called()
        self.assertEqual(json.loads(output.getvalue().splitlines()[-1])["stage"], "ready")

    def test_progress_tracks_verified_cache_and_real_bytes_and_retries_keep_complete_files(self):
        data = {"one.gguf": b"first fixture", "two.gguf": b"second fixture"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); events = []
            (root / "one.gguf").write_bytes(data["one.gguf"])
            def checksum(project, revision, asset): return hashlib.sha256(data[asset]).hexdigest()
            with patch.object(lfm, "expected_digest", side_effect=checksum), \
                    patch.object(lfm.urllib.request, "urlopen", return_value=io.BytesIO(data["two.gguf"])) as network:
                lfm._install_assets(root, {key: len(value) for key, value in data.items()}, progress=events.append)
                network.assert_called_once()
            total = sum(map(len, data.values()))
            self.assertTrue(any(e["stage"] == "downloading" and e["downloaded"] == total for e in events))
            self.assertTrue(all(e["total"] == total for e in events))
            self.assertEqual((root / "two.gguf").read_bytes(), data["two.gguf"])
            with patch.object(lfm, "expected_digest", side_effect=checksum), patch.object(lfm.urllib.request, "urlopen") as network:
                lfm._install_assets(root, {key: len(value) for key, value in data.items()}, progress=events.append)
                network.assert_not_called()

    def test_cancel_cleans_only_its_partial_files_and_does_not_accept_late_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in [".voice-owned-1.part", ".voice-other-1.part", "verified.gguf"]:
                (root / name).write_bytes(b"fixture")
            job = setup.Setup(); job.state["stage"] = "cancelling"
            process = Mock(stdout=io.BytesIO(b'{"stage":"ready","downloaded":999999999999}\n'), stdin=io.BytesIO())
            job.process = process
            job._read(process, root, "owned")
            self.assertEqual(job.state["stage"], "cancelled")
            self.assertFalse((root / ".voice-owned-1.part").exists())
            self.assertTrue((root / ".voice-other-1.part").exists())
            self.assertTrue((root / "verified.gguf").exists())
            self.assertIsNone(job.process)

    def test_repeated_start_joins_the_same_background_job(self):
        job = setup.Setup(); job.process = Mock()
        with patch.object(setup.subprocess, "Popen") as launch:
            self.assertTrue(job.start()["busy"])
            launch.assert_not_called()

    def test_owner_pipe_eof_stops_native_setup_child_and_its_descendant(self):
        # Real native OS execution, entirely disposable: no downloads or profiles.
        script = """import subprocess,sys,threading,time
from imprint.voice_setup import watch_owner
child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
threading.Thread(target=watch_owner,daemon=True).start()
print(child.pid,flush=True)
time.sleep(120)
"""
        process = subprocess.Popen([sys.executable, "-c", script], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, **setup.voice_runtime.child_options())
        descendant = None; handle = None; descendant_done = False
        try:
            descendant = int(process.stdout.readline())
            if os.name == "nt":
                kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel.OpenProcess.restype = ctypes.c_void_p
                kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
                kernel.CloseHandle.argtypes = [ctypes.c_void_p]
                handle = kernel.OpenProcess(0x100001, False, descendant)
                self.assertTrue(handle)
            process.stdin.close()
            process.wait(timeout=15)
            if os.name == "nt":
                self.assertEqual(kernel.WaitForSingleObject(handle, 5000), 0)
            else:
                for _ in range(50):
                    state = subprocess.run(["ps", "-o", "stat=", "-p", str(descendant)], capture_output=True, text=True).stdout.strip()
                    if not state or state.startswith("Z"): break
                    time.sleep(.1)
                else: self.fail("Setup descendant survived owner pipe EOF")
            descendant_done = True
        finally:
            if handle: kernel.CloseHandle(handle)
            if process.poll() is None: process.kill(); process.wait()
            for stream in [process.stdin, process.stdout, process.stderr]: stream.close()
            if descendant and not descendant_done:
                with contextlib.suppress(OSError): os.kill(descendant, 9)


if __name__ == "__main__":
    unittest.main()
