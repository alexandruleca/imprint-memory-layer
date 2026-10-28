"""Local, bounded multilingual audio ingestion (ADR 0095).

No audio or model is loaded on import. Completed redacted source snapshots
are the transcript cache; no second database or raw transcript cache exists.
"""
from __future__ import annotations

import hashlib
from importlib.metadata import PackageNotFoundError, version
import itertools
import json
import math
import os
from pathlib import Path
import shutil
import threading
import time
from typing import NamedTuple

from . import config
from .config_schema import resolve

AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".aiff", ".aif", ".wma"}
REPO = "Systran/faster-whisper-small"
REVISION = "536b0662742c02347bc0e980a01041f333bce120"
FILES = {
    "model.bin": (483546902, "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671"),
    "config.json": (2370, "b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828"),
    "tokenizer.json": (2203239, "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab"),
    "vocabulary.txt": (459861, "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913"),
}


class ModelSpec(NamedTuple):
    repo: str
    revision: str
    files: dict


MODELS = {
    "small": ModelSpec(REPO, REVISION, FILES),
    "large-v3-turbo": ModelSpec("mobiuslabsgmbh/faster-whisper-large-v3-turbo", "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf", {
        "model.bin": (1617884929, "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da"),
        "config.json": (2263, "b0253ea6c0d3bea6b1e19e91a02acfd3b53f4467362efcb5a3e6b16c9b3a9b7e"),
        "preprocessor_config.json": (340, "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711"),
        "tokenizer.json": (2710337, "297b13372ac43916285644fb9687add3cc62ee2a1adb60da3dc25cc94c1871fd"),
        "vocabulary.json": (1068114, "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1"),
    }),
}
RATE = 16000
WINDOW = 30 * RATE
OVERLAP = RATE
MAX_SEGMENTS = 50000
MAX_TEXT = 2_000_000
TRANSCRIPTION_VERSION = 1
_inference = threading.Lock()
_model = None
_model_root = None


def settings() -> dict:
    options = {key: resolve(key)[0] for key in ("audio.enabled", "audio.model", "audio.language",
            "audio.max_file_size_mb", "audio.max_duration_minutes", "redact.privacy_filter")}
    if options['audio.model'] not in MODELS:
        raise ValueError('audio.model must be large-v3-turbo or small')
    return options


def profile(options=None) -> str:
    """Only audio sources depend on speech settings and runtime versions."""
    options = settings() if options is None else options
    spec = MODELS[str(options['audio.model'])]
    versions = {}
    for name in ("faster-whisper", "ctranslate2", "av", "onnxruntime"):
        try:
            versions[name] = version(name)
        except PackageNotFoundError:
            versions[name] = "unavailable"
    payload = [TRANSCRIPTION_VERSION, RATE, WINDOW, OVERLAP, spec.repo, spec.revision, versions, options]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def report(phase, done=0, total=0, unit="seconds"):
    from .progress import write_progress, read_progress
    current = read_progress() or {}
    write_progress(current.get("command", "ingest"), int(done), int(total),
                   current.get("stored", 0), current.get("skipped", 0),
                   current.get("started_at", time.time()), current.get("projects", []),
                   phase=phase, unit=unit)


def model_root(spec: ModelSpec) -> Path:
    return config.get_data_dir() / "models" / (spec.repo.rsplit('/', 1)[-1] + '-' + spec.revision[:12])


def ensure_model(spec: ModelSpec) -> Path:
    """Pin every model asset; incomplete downloads never become model files."""
    import httpx
    from filelock import FileLock
    from huggingface_hub.utils import build_hf_headers
    from .downloads import verify_archive

    root = model_root(spec)
    root.mkdir(parents=True, exist_ok=True)
    total = sum(size for size, _ in spec.files.values())
    done = 0
    report("preparing_transcription_model", unit="bytes")
    with FileLock(str(root / "download.lock"), timeout=1800):
        remaining = sum(size for name, (size, _) in spec.files.items() if not (root / name).exists())
        if shutil.disk_usage(root).free < remaining + 32 * 1024 * 1024:
            raise ValueError("Not enough free space for the selected transcription model")
        for name, (size, digest) in spec.files.items():
            path = root / name
            if path.exists():
                # An existing corrupt model needs a visible repair, not use.
                if path.stat().st_size != size:
                    raise ValueError(f"Damaged transcription model: remove {root} and retry audio ingestion")
                verify_archive(path, digest)
                done += size
                continue
            part = root / (name + ".part")
            try:
                received = 0
                hasher = hashlib.sha256()
                with httpx.stream("GET", f"https://huggingface.co/{spec.repo}/resolve/{spec.revision}/{name}",
                                  headers=build_hf_headers(token=config.hub_token()),
                                  follow_redirects=True, timeout=httpx.Timeout(60, read=120)) as response:
                    response.raise_for_status()
                    with part.open("wb") as output:
                        for chunk in response.iter_bytes(1024 * 1024):
                            received += len(chunk)
                            if received > size:
                                raise ValueError("Transcription model download exceeded its pinned size")
                            output.write(chunk); hasher.update(chunk)
                            report("downloading_transcription_model", done + received, total, "bytes")
                if received != size or hasher.hexdigest() != digest:
                    raise ValueError("Transcription model checksum/size mismatch; retry audio ingestion")
                os.replace(part, path)
                done += size
            finally:
                part.unlink(missing_ok=True)
    return root


def load_model(spec: ModelSpec):
    global _model, _model_root
    root = model_root(spec)
    if _model is None or _model_root != root:
        from faster_whisper import WhisperModel
        _model = None  # Release the previous model before loading a different one.
        verified_root = ensure_model(spec)
        report("loading_transcription_model")
        _model = WhisperModel(str(verified_root), device="cpu", compute_type="int8", cpu_threads=4,
                             num_workers=1, local_files_only=True)
        _model_root = root
    return _model


def windows(path: str, limit_seconds: int):
    """Decode only a bounded window in memory, including for hour-long files."""
    import av
    import numpy as np

    buffer = np.empty(WINDOW, dtype=np.float32)
    used = offset = decoded = 0
    # Forbid playlist/network demuxers and external MP4 data references.
    options = {"protocol_whitelist": "file,pipe", "enable_drefs": "0",
               "format_whitelist": "wav,mp3,flac,ogg,mov,mp4,m4a,3gp,3g2,mj2,aiff,aac,asf"}
    with av.open(path, mode="r", options=options) as container:
        if not container.streams.audio:
            raise ValueError("The file has no audio stream")
        duration = float(container.duration / av.time_base) if container.duration else 0
        if duration > limit_seconds:
            raise ValueError("Audio exceeds audio.max_duration_minutes")
        resampler = av.AudioResampler(format="fltp", layout="mono", rate=RATE)
        for frame in itertools.chain(container.decode(audio=0), [None]):
            if frame is not None:
                frame.pts = None
            for converted in resampler.resample(frame):
                samples = converted.to_ndarray().reshape(-1)
                decoded += len(samples)
                if decoded > limit_seconds * RATE:
                    raise ValueError("Decoded audio exceeds audio.max_duration_minutes")
                cursor = 0
                while cursor < len(samples):
                    count = min(WINDOW - used, len(samples) - cursor)
                    buffer[used:used + count] = samples[cursor:cursor + count]
                    used += count; cursor += count
                    if used == WINDOW:
                        yield buffer.copy(), offset / RATE, duration
                        buffer[:OVERLAP] = buffer[-OVERLAP:]
                        used = OVERLAP
                        offset += WINDOW - OVERLAP
        if used and (offset == 0 or used > OVERLAP):
            yield buffer[:used].copy(), offset / RATE, duration


def cached(source_hash: str, identity: str):
    from . import source_snapshots
    from .conversation_source import read
    workspace = config.get_active_workspace()
    for meta in source_snapshots.list_metadata(workspace):
        details = meta.get("details", {})
        if (details.get("media") == "audio" and details.get("audio_profile") == identity
                and meta.get("provenance", {}).get("source_hash") == source_hash):
            snapshot = source_snapshots.get(meta["source"], meta["project"], workspace)
            if snapshot:
                segments = [{"text": e["text"], "start": e["audio_start"], "end": e["audio_end"],
                             "language": e.get("language", "")} for e in read(snapshot["text"])]
                return segments
    return None


def redact_segments(segments):
    from .redaction import redact_all
    from .source_memory import redaction_edits, remap
    raw = "\n".join(segment["text"] for segment in segments)
    clean = redact_all([raw])[0]
    edits = redaction_edits(clean, raw)
    cursor, output = 0, []
    for segment in segments:
        end = cursor + len(segment["text"])
        output.append({**segment, "text": clean[remap(cursor, edits):remap(end, edits, right=True)]})
        cursor = end + 1
    return output


def transcribe(path: str, *, force=False):
    from .source_state import fingerprint
    options = settings()
    spec = MODELS[str(options['audio.model'])]
    if not options['audio.enabled']:
        raise ValueError("Audio ingestion is off; enable audio.enabled in Settings")
    size_limit = int(options['audio.max_file_size_mb'])
    duration_limit = int(options['audio.max_duration_minutes'])
    if size_limit < 1 or duration_limit < 1:
        raise ValueError("Audio size and duration limits must be positive")
    if Path(path).stat().st_size > size_limit * 1024 * 1024:
        raise ValueError("Audio exceeds audio.max_file_size_mb")
    language = str(options['audio.language'])
    if language not in {"auto", "ro", "en"}:
        raise ValueError("audio.language must be auto, ro or en")
    identity = profile(options)
    metadata = {'audio_profile': identity, 'transcription_model': spec.repo, 'model_revision': spec.revision}
    before = fingerprint(path)
    if not force:
        result = cached(before["source_hash"], identity)
        if result is not None:
            return redact_segments(result), metadata
    # One CPU model at a time, also when the document scanner uses threads.
    with _inference:
        model = None
        result, last_end, characters = [], 0.0, 0
        for samples, offset, duration in windows(path, duration_limit * 60):
            # Validate/decode the first window before downloading any model.
            model = model or load_model(spec)
            report("transcribing_audio", offset, math.ceil(duration))
            segments, info = model.transcribe(samples, task="transcribe", language=None if language == "auto" else language,
                                              beam_size=5, vad_filter=True, word_timestamps=True,
                                              condition_on_previous_text=False)
            for segment in segments:
                # Word timestamps let the next window retain boundary speech
                # while removing words already emitted in its one-second overlap.
                words = [word for word in (segment.words or []) if offset + word.end > last_end + .02]
                if not words:
                    continue
                start, end = offset + words[0].start, offset + words[-1].end
                text = "".join(word.word for word in words).strip()
                if not all(math.isfinite(value) for value in (start, end)) or not 0 <= start <= end <= offset + len(samples) / RATE + .1:
                    raise ValueError("The transcription model returned invalid timestamps")
                if text:
                    characters += len(text)
                    if characters > MAX_TEXT or len(result) >= MAX_SEGMENTS:
                        raise ValueError("Audio transcript exceeds its text/segment budget")
                    result.append({"text": text, "start": round(start, 3), "end": round(end, 3),
                                   "language": info.language})
                    last_end = end
            report("transcribing_audio", offset + len(samples) / RATE, math.ceil(duration))
        if fingerprint(path) != before:
            raise ValueError("Audio changed during transcription; prior index retained")
        return redact_segments(result), metadata
