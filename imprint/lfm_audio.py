"""Pinned local LFM2.5-Audio experiment. English, CPU, offline turns (ADR 0042)."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import stat
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

from .downloads import expected_digest, verify_archive
from . import personaplex as memory

MODEL_REPO = "LiquidAI/LFM2.5-Audio-1.5B-GGUF"
REVISION = "7d525f883a077e20afb782f2ff618edcae0e39e4"
IDENTITY = {"backend": "lfm-audio", "model_repo": MODEL_REPO,
            "model_revision": REVISION, "source_revision": REVISION}
MODEL = "LFM2.5-Audio-1.5B-Q4_0.gguf"
PROJECTOR = "mmproj-LFM2.5-Audio-1.5B-Q4_0.gguf"
TOKENIZER = "tokenizer-LFM2.5-Audio-1.5B-Q4_0.gguf"
VOCODER = "vocoder-LFM2.5-Audio-1.5B-Q4_0.gguf"
ARCHIVE = "llama-liquid-audio-ubuntu-x64.zip"
RUNNER_DIRECTORY = "llama-liquid-audio-ubuntu-x64"
ASSETS = {MODEL: 695750880, PROJECTOR: 219511136, TOKENIZER: 50546112,
          VOCODER: 108986560, ARCHIVE: 13241089}
VOICES = {"us_female": "US female", "us_male": "US male", "uk_female": "UK female", "uk_male": "UK male"}


def _platform() -> None:
    if platform.system() != "Linux" or platform.machine().lower() not in {"x86_64", "amd64"}:
        raise memory.PrototypeError("This experimental runner is enabled only on Linux x64, including WSL2.")


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def extract_runner(root: Path) -> None:
    verify_archive(root / ARCHIVE, expected_digest("lfm-audio-q4", REVISION, ARCHIVE))
    destination = root / "runner"
    if destination.is_symlink():
        raise memory.PrototypeError("Runner directory must not be a symlink.")
    destination.mkdir(mode=0o700, exist_ok=True)
    with zipfile.ZipFile(root / ARCHIVE) as archive:
        entries = archive.infolist()
        if sum(item.file_size for item in entries) > 256 * 1024 * 1024:
            raise memory.PrototypeError("Runner archive exceeds the extraction budget.")
        for entry in entries:
            path = Path(entry.filename)
            if (path.is_absolute() or ".." in path.parts or "\\" in entry.filename
                    or stat.S_ISLNK(entry.external_attr >> 16)
                    or not (destination / path).resolve().is_relative_to(destination.resolve())):
                raise memory.PrototypeError("Unsafe runner archive entry.")
        for entry in entries:
            path = destination / entry.filename
            if entry.is_dir():
                path.mkdir(mode=0o700, parents=True, exist_ok=True)
                continue
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            # Replace managed files atomically; never follow an existing symlink.
            fd, name = tempfile.mkstemp(dir=path.parent)
            try:
                with os.fdopen(fd, "wb") as output, archive.open(entry) as source:
                    while chunk := source.read(1024 * 1024):
                        output.write(chunk)
                os.chmod(name, 0o700 if path.name.startswith("llama-") else 0o600)
                os.replace(name, path)
            finally:
                Path(name).unlink(missing_ok=True)


def _install_assets(root: Path, assets: dict[str, int]) -> None:
    from filelock import FileLock
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with FileLock(str(root / ".install.lock")):
        for asset, size in assets.items():
            target = root / asset
            expected = expected_digest("lfm-audio-q4", REVISION, asset)
            if target.is_file() and target.stat().st_size == size and _digest(target) == expected:
                print(f"Verified cached {asset}", flush=True)
                continue
            remote = f"runners/{asset}" if asset == ARCHIVE else asset
            url = f"https://huggingface.co/{MODEL_REPO}/resolve/{REVISION}/{remote}"
            fd, name = tempfile.mkstemp(dir=root, suffix=".part")
            print(f"Downloading {asset} ({size} bytes)", flush=True)
            try:
                with os.fdopen(fd, "wb") as output, urllib.request.urlopen(url, timeout=180) as response:
                    received = 0
                    while chunk := response.read(1024 * 1024):
                        received += len(chunk)
                        if received > size:
                            raise memory.PrototypeError("Download exceeds its pinned size.")
                        output.write(chunk)
                if received != size:
                    raise memory.PrototypeError("Incomplete runtime download.")
                verify_archive(Path(name), expected)
                os.replace(name, target)
            finally:
                Path(name).unlink(missing_ok=True)


def install_weights(root: Path) -> None:
    """Portable model data only; never downloads/executes the Linux CLI."""
    _install_assets(root, {name: size for name, size in ASSETS.items() if name != ARCHIVE})


def verify_weights(root: Path) -> None:
    for asset, size in ASSETS.items():
        if asset == ARCHIVE:
            continue
        path = root / asset
        if path.is_symlink() or not path.is_file() or path.stat().st_size != size:
            raise memory.PrototypeError("Voice weights are missing or invalid; run the explicit install command.")
        verify_archive(path, expected_digest("lfm-audio-q4", REVISION, asset))


def install(root: Path) -> None:
    _platform()
    _install_assets(root, ASSETS)
    extract_runner(root)


def verify_runtime(root: Path) -> Path:
    _platform()
    for asset, size in ASSETS.items():
        path = root / asset
        if not path.is_file() or path.stat().st_size != size:
            raise memory.PrototypeError("Runtime assets are missing; run the explicit install command.")
        verify_archive(path, expected_digest("lfm-audio-q4", REVISION, asset))
    with zipfile.ZipFile(root / ARCHIVE) as archive:
        for entry in archive.infolist():
            if entry.is_dir():
                continue
            path = root / "runner" / entry.filename
            if (path.is_symlink() or not path.is_file()
                    or _digest(path) != hashlib.sha256(archive.read(entry)).hexdigest()):
                raise memory.PrototypeError("Extracted runner changed; run install to repair it.")
    return root / "runner" / RUNNER_DIRECTORY / "llama-liquid-audio-cli"


def wav_info(path: Path) -> dict:
    if not 44 <= path.stat().st_size <= 24 * 1024 * 1024:
        raise memory.PrototypeError("WAV must contain audio and be at most 24 MiB.")
    data = path.read_bytes()
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise memory.PrototypeError("Expected a RIFF WAV file.")
    offset, fmt, audio = 12, None, None
    while offset + 8 <= len(data):
        kind, size = struct.unpack_from("<4sI", data, offset)
        start = offset + 8
        if start + size > len(data):
            raise memory.PrototypeError("Truncated WAV chunk.")
        if kind == b"fmt " and size >= 16:
            fmt = struct.unpack_from("<HHIIHH", data, start)
        if kind == b"data":
            audio = data[start:start + size]
        offset = start + size + (size % 2)
    if not fmt or not audio:
        raise memory.PrototypeError("WAV is missing format or audio data.")
    encoding, channels, rate, _, align, bits = fmt
    if ((encoding, bits) not in {(1, 16), (3, 32)} or channels != 1 or not 8000 <= rate <= 48000
            or align != channels * bits // 8 or len(audio) % align):
        raise memory.PrototypeError("Use mono PCM16 or float32 WAV at 8–48 kHz.")
    count, energy = len(audio) // align, 0.0
    for (sample,) in struct.iter_unpack("<h" if encoding == 1 else "<f", audio):
        value = sample / 32768 if encoding == 1 else sample
        if not math.isfinite(value):
            raise memory.PrototypeError("Nonfinite audio samples.")
        energy += value * value
    return {"sample_rate": rate, "seconds": count / rate, "rms": math.sqrt(energy / count),
            "format": "pcm16" if encoding == 1 else "float32"}


def run_turn(root: Path, output: Path, *, text: str = "", audio: Path | None = None,
             session: Path | None = None, mode: str = "reply", voice: str = "us_female",
             threads: int = 4, timeout: float = 120, max_tokens: int = 256) -> dict:
    if (not 1 <= threads <= 16 or not 1 <= timeout <= 300 or not 16 <= max_tokens <= 1024
            or len(text) > 4000 or mode not in {"reply", "tts", "asr"} or voice not in VOICES):
        raise memory.PrototypeError("Invalid turn limits.")
    if (mode == "tts" and (not text.strip() or audio or session)) or (mode == "asr" and (not audio or text or session)):
        raise memory.PrototypeError("TTS requires text only; ASR requires audio only.")
    if mode == "reply" and (bool(text.strip()) == bool(audio)):
        raise memory.PrototypeError("Supply exactly one of --text or --audio.")
    if audio and wav_info(audio)["seconds"] > 30:
        raise memory.PrototypeError("Prototype input audio is limited to 30 seconds.")
    snapshot = memory.load_session(session, identity=IDENTITY) if session else None
    context = snapshot["text_prompt"] if snapshot else ""
    runner = verify_runtime(root)
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    sound = output / "response.wav"
    if mode == "tts":
        system = f"Perform TTS. Use the {VOICES[voice]} voice."
    else:
        system = "Perform ASR." if mode == "asr" else "Respond with interleaved text and audio."
    command = [str(runner), "-m", str(root / MODEL), "-mm", str(root / PROJECTOR),
               "-mv", str(root / VOCODER), "--tts-speaker-file", str(root / TOKENIZER),
               "-sys", system, "--output", str(sound), "-t", str(threads), "-c", "4096",
               "-n", str(max_tokens), "--seed", "42", "--offline", "--no-escape", "-ngl", "0",
               "--no-mmproj-offload"]
    if audio:
        command.extend(["--audio", str(audio.resolve())])
    with tempfile.TemporaryDirectory(prefix="imprint-lfm-turn-") as directory:
        prompt = Path(directory) / "request.txt"
        if context or text:
            prompt.write_text(context + ("\nQuestion: " + text if mode == "reply" and text else text)
                              + ("\nAnswer the following spoken question using the evidence above."
                                 if mode == "reply" and audio and context else ""), encoding="utf-8")
            command.extend(["-f", str(prompt)])
        log = Path(directory) / "runner.log"
        environment = {key: value for key, value in os.environ.items()
                       if key in {"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "LD_LIBRARY_PATH"}}
        environment["OMP_NUM_THREADS"] = str(threads)
        start, peak = time.monotonic(), 0
        with log.open("wb") as stream:
            process = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT,
                                       env=environment, cwd=root, start_new_session=True)
            try:
                while process.poll() is None:
                    if time.monotonic() - start > timeout:
                        raise memory.PrototypeError("Native audio turn timed out.")
                    try:
                        status = Path(f"/proc/{process.pid}/status").read_text()
                        rss = next((int(line.split()[1]) for line in status.splitlines() if line.startswith("VmRSS:")), 0)
                        peak = max(peak, rss)
                    except OSError:
                        pass
                    time.sleep(0.02)
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
        elapsed = time.monotonic() - start
        if process.returncode:
            raise memory.PrototypeError(f"Native audio runner failed (exit {process.returncode}); no prompt was logged publicly.")
        logs = log.read_text(encoding="utf-8", errors="replace")
        if mode == "reply":
            # The runner echoes input prompts even with --log-disable. Parse
            # generated text only after its actual output-audio section.
            anchor = f"\n=== GENERATED AUDIO ===\nSaved to {sound}\n"
            logs = logs.rsplit(anchor, 1)[1] if anchor in logs else ""
        marker = "\n=== GENERATED TEXT ===\n"
        transcript = logs.rsplit(marker, 1)[1].strip() if marker in logs else ""
        info = wav_info(sound) if sound.exists() else None
        if mode != "asr" and (not info or info["rms"] <= 1e-6):
            raise memory.PrototypeError("Runner produced no non-silent speech.")
        if mode != "tts" and not transcript:
            raise memory.PrototypeError("Runner produced no generated transcript.")
        result = {"schema": 1, "backend": "lfm-audio", "model_revision": REVISION,
                  "mode": mode, "device": "cpu", "threads": threads, "elapsed_seconds": elapsed,
                  "peak_rss_mib_sampled": peak / 1024, "text": transcript, "audio": info,
                  "snapshot_workspace": snapshot["workspace"] if snapshot else None,
                  "includes_runtime_verification_time": False}
        memory.write_session(output / "result.json", result)
        if sound.exists():
            sound.chmod(0o600)
        return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("install", help="Explicitly download and verify the pinned CPU runtime")
    setup.add_argument("--runtime-dir", type=Path, required=True)
    prepare = commands.add_parser("prepare", help="Prepare an English read-only MCP memory snapshot")
    prepare.add_argument("--data-dir", type=Path, required=True)
    prepare.add_argument("--workspace", required=True)
    prepare.add_argument("--query", action="append", required=True)
    prepare.add_argument("--project", default="")
    prepare.add_argument("--max-chars", type=int, default=8000)
    prepare.add_argument("--timeout", type=float, default=120)
    prepare.add_argument("--output", type=Path, required=True)
    for mode in ("reply", "tts", "asr"):
        turn = commands.add_parser(mode)
        turn.add_argument("--runtime-dir", type=Path, required=True)
        turn.add_argument("--output-dir", type=Path, required=True)
        if mode != "asr":
            turn.add_argument("--text", default="")
        if mode != "tts":
            turn.add_argument("--audio", type=Path)
        if mode == "reply":
            turn.add_argument("--session", type=Path)
        if mode == "tts":
            turn.add_argument("--voice", choices=sorted(VOICES), default="us_female")
        turn.add_argument("--threads", type=int, default=4)
        turn.add_argument("--timeout", type=float, default=120)
        turn.add_argument("--max-tokens", type=int, default=256)
    args = parser.parse_args(argv)
    try:
        if args.command == "install":
            install(args.runtime_dir.resolve())
        elif args.command == "prepare":
            if not 1 <= args.timeout <= 600:
                raise memory.PrototypeError("MCP timeout must be 1–600 seconds.")
            result = asyncio.run(memory._prepare(args, identity=IDENTITY))
            print(f"Prepared LFM memory snapshot: {result['prompt_chars']} characters; omitted queries: {result['omitted_queries']}")
        else:
            result = run_turn(args.runtime_dir.resolve(), args.output_dir.resolve(), text=getattr(args, "text", ""),
                              audio=getattr(args, "audio", None), session=getattr(args, "session", None),
                              mode=args.command, voice=getattr(args, "voice", "us_female"), threads=args.threads,
                              timeout=args.timeout, max_tokens=args.max_tokens)
            print(result["text"] or "Speech synthesized.")
            print(f"Results: {args.output_dir}")
            print(f"CPU turn: {result['elapsed_seconds']:.2f}s; sampled peak RSS: {result['peak_rss_mib_sampled']:.0f} MiB")
        return 0
    except memory.PrototypeError as error:
        print(str(error), file=sys.stderr)
    except Exception as error:
        print(f"LFM audio experiment failed ({type(error).__name__}); no memory content was printed.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
