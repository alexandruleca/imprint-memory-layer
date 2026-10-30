"""Explicit Echo provisioning, with bounded progress and an owned process tree."""
from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading

from . import config, config_schema, lfm_audio, voice_runtime, voice_bundle
from .voice_memory import VoiceMemoryError

TOTAL_BYTES = sum(size for name, size in lfm_audio.ASSETS.items() if name != lfm_audio.ARCHIVE)
STAGES = {"checking", "verifying", "downloading", "engine", "preparing", "building", "finishing"}


def runtime_root() -> Path:
    explicit, _ = config_schema.resolve("voice.runtime_dir")
    return Path(explicit).expanduser().resolve() if explicit else (config.get_data_dir() / "voice").resolve()


def prerequisites(root: Path) -> str | None:
    if voice_bundle.available():
        voice_bundle.verify()
        return "disk" if shutil.disk_usage(root).free < 2_000_000_000 else None
    if not shutil.which("cmake"):
        return "cmake"
    if os.name == "nt":
        compiler = shutil.which("cl")
        finder = Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Microsoft Visual Studio/Installer/vswhere.exe"
        if not compiler and finder.is_file():
            result = subprocess.run([str(finder), "-latest", "-products", "*", "-requires",
                                     "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"],
                                    capture_output=True, timeout=15, **voice_runtime.child_options())
            compiler = result.returncode == 0 and bool(result.stdout.strip())
    elif sys.platform == "darwin":
        result = subprocess.run(["xcrun", "--find", "clang"], capture_output=True, timeout=15)
        compiler = result.returncode == 0 and bool(result.stdout.strip())
    else:
        compiler = any(shutil.which(name) for name in ("c++", "g++", "clang++"))
    if not compiler:
        return "compiler"
    if shutil.disk_usage(root).free < 4_000_000_000:
        return "disk"
    return None


def stop_worker_tree() -> None:
    """Called inside the isolated setup child when its owner closes stdin."""
    if os.name == "nt":
        try:
            subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32/taskkill.exe"),
                            "/PID", str(os.getpid()), "/T", "/F"],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
        finally:
            os._exit(1)
    elif os.getpgrp() == os.getpid():
        os.killpg(os.getpgrp(), signal.SIGTERM)
    os._exit(1)


def watch_owner() -> None:
    sys.stdin.buffer.read(1)
    stop_worker_tree()


def run_worker(root: Path, job_id: str) -> int:
    from filelock import FileLock, Timeout
    # Keep the original stream: legacy installer prints are hidden, only bounded
    # JSON progress is forwarded to the API. No microphone or model inference.
    stream = sys.stdout
    def emit(event):
        stream.write(json.dumps(event, ensure_ascii=True) + "\n"); stream.flush()
    threading.Thread(target=watch_owner, daemon=True, name="voice-setup-owner").start()
    try:
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
        with FileLock(str(root / ".setup.lock"), timeout=0):
            # No other managed setup owns these partials while this lock is held.
            for partial in root.glob(".voice-*.part"):
                if re.fullmatch(r"\.voice-[a-f0-9]{24}-[a-z0-9_]{6,32}\.part", partial.name):
                    partial.unlink(missing_ok=True)
            emit({"stage": "checking"})
            try:
                voice_runtime.verify(root)
            except (VoiceMemoryError, ValueError):
                pass
            else:
                emit({"stage": "ready", "downloaded": TOTAL_BYTES})
                return 0
            try:
                problem = prerequisites(root)
            except VoiceMemoryError:
                emit({"stage": "error", "problem": "engine"})
                return 1
            if problem:
                emit({"stage": "blocked", "problem": problem})
                return 2
            with open(os.devnull, "w") as quiet, contextlib.redirect_stdout(quiet):
                if voice_bundle.available():
                    voice_bundle.install(root, progress=emit, download_prefix=f".voice-{job_id}-")
                else:
                    voice_runtime.install(root, jobs=min(4, os.cpu_count() or 1), progress=emit,
                                          download_prefix=f".voice-{job_id}-")
            emit({"stage": "finishing"})
            voice_runtime.verify(root)
            emit({"stage": "ready", "downloaded": TOTAL_BYTES})
            return 0
    except Timeout:
        emit({"stage": "blocked", "problem": "in_use"})
    except subprocess.CalledProcessError:
        emit({"stage": "error", "problem": "build"})
    except (OSError, ValueError, VoiceMemoryError, subprocess.TimeoutExpired):
        emit({"stage": "error", "problem": "download"})
    return 1


class Setup:
    def __init__(self):
        self.lock = threading.RLock()
        self.process: subprocess.Popen | None = None
        self.state = {"stage": "idle", "downloaded": 0, "total": TOTAL_BYTES, "problem": None}

    def snapshot(self) -> dict:
        with self.lock:
            return {**self.state, "busy": self.process is not None,
                    "installed": voice_runtime.installed(runtime_root()),
                    "bundled_engine": voice_bundle.available(),
                    "supported": voice_runtime.supported(), "platform": sys.platform}

    def start(self) -> dict:
        with self.lock:
            if self.process is not None:
                return self.snapshot()
            root, job_id = runtime_root(), secrets.token_hex(12)
            self.state = {"stage": "checking", "downloaded": 0, "total": TOTAL_BYTES, "problem": None}
            environment = voice_runtime.child_environment()
            environment["IMPRINT_DATA_DIR"] = str(config.get_data_dir().resolve())
            try:
                self.process = subprocess.Popen(
                    [sys.executable, "-m", "imprint.voice_setup", "--worker", str(root), "--job-id", job_id],
                    cwd=Path(__file__).resolve().parent.parent, env=environment,
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    **voice_runtime.child_options())
            except OSError:
                self.state.update(stage="error", problem="setup")
                return self.snapshot()
            threading.Thread(target=self._read, args=(self.process, root, job_id), daemon=True,
                             name="voice-setup-progress").start()
            return self.snapshot()

    def _read(self, process: subprocess.Popen, root: Path, job_id: str) -> None:
        try:
            assert process.stdout is not None
            while line := process.stdout.readline(4097):
                if len(line) > 4096:
                    continue
                try:
                    event = json.loads(line)
                    if not isinstance(event, dict) or event.get("stage") not in STAGES | {"ready", "blocked", "error"}:
                        continue
                    with self.lock:
                        if self.state["stage"] == "cancelling":
                            continue
                        self.state["stage"] = event["stage"]
                        if type(event.get("downloaded")) is int:
                            self.state["downloaded"] = max(0, min(TOTAL_BYTES, event["downloaded"]))
                        self.state["problem"] = event.get("problem") if event.get("problem") in {
                            "cmake", "compiler", "disk", "in_use", "build", "download", "setup", "engine"} else None
                except (ValueError, TypeError):
                    continue
            process.wait()
        finally:
            if process.stdin:
                process.stdin.close()
            if process.stdout:
                process.stdout.close()
            for partial in root.glob(f".voice-{job_id}-*.part"):
                with contextlib.suppress(OSError):
                    partial.unlink()
            with self.lock:
                stage = self.state["stage"]
                if stage == "cancelling":
                    self.state["stage"] = "cancelled"
                elif stage not in {"ready", "blocked", "error"}:
                    self.state.update(stage="error", problem="download")
                self.process = None

    def cancel(self) -> dict:
        with self.lock:
            if self.process is not None:
                self.state["stage"] = "cancelling"
                if self.process.stdin:
                    with contextlib.suppress(OSError):
                        self.process.stdin.close()
            return self.snapshot()


setup = Setup()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    args = parser.parse_args()
    if len(args.job_id) != 24 or any(c not in "0123456789abcdef" for c in args.job_id):
        parser.error("Invalid managed setup identifier")
    raise SystemExit(run_worker(args.worker.resolve(), args.job_id))
