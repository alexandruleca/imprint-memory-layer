"""'Hey Echo': the wake word's detector (ADR 0137).

The desktop app's hidden listener page sends the first 2 s of each burst of
speech it hears. Silero VAD (shipped inside faster-whisper) confirms it is
speech, faster-whisper ``tiny.en`` transcribes it with the phrase given as
``hotwords``, and a text matcher decides. After a wake, the rest of that
utterance is transcribed and becomes Echo's first question.

Audio is parsed from memory and never written; no transcript of a probe that
did not wake Echo is returned, logged or kept. Nothing is loaded on import.
"""
from __future__ import annotations

import collections
import io
import re
import time
import wave
from pathlib import Path

from .audio_ingest import ModelSpec, ensure_model, model_root

RATE = 16000
PHRASE = "Hey Echo"
# The listener sends 2.0 s; a little slack for its pre-roll and rounding.
PROBE_SECONDS = 2.5
UTTERANCE_SECONDS = 30
PROBE_TOKENS = 10
QUESTION_TOKENS = 200
MODEL = ModelSpec("Systran/faster-whisper-tiny.en", "0d3d19a32d3338f10357c0889762bd8d64bbdeba", {
    "model.bin": (75537502, "1a5afae06a4db91c975c9a9d78be5cc110ee4ea022ad57d55492e4550e936b2a"),
    "config.json": (2317, "14b1b421a90349bc551b881461426b561a874049cb9e4c4864f2ca384f6a7cc5"),
    "tokenizer.json": (2128466, "929c5252409436dce1b38a75d1abbcb5e132d170d8e324e4e04ed915fa2d22df"),
    "vocabulary.txt": (422309, "ff77588746d3a2595d32ab5b69ffd7b95ce2441ac57533cb66fc3eb575a115cf"),
})
TOTAL_BYTES = sum(size for size, _ in MODEL.files.values())
# Probe CPU per minute, about 5 % of one core with the listener page; halved on battery.
BUDGET = 2.5


def root() -> Path:
    return model_root(MODEL)


def installed() -> bool:
    """Cheap check for status polling; the worker verifies the hashes."""
    folder = root()
    return all((folder / name).is_file() and (folder / name).stat().st_size == size
               for name, (size, _) in MODEL.files.items())


def download(progress=None) -> Path:
    """The user's explicit Turn on: fetch and verify the pinned model."""
    return ensure_model(MODEL, progress=progress)


def verify() -> Path:
    """Use only an installed, intact model; never download from here."""
    from .downloads import verify_archive
    folder = root()
    for name, (size, digest) in MODEL.files.items():
        path = folder / name
        if not path.is_file() or path.stat().st_size != size:
            raise FileNotFoundError(f"The wake model is not installed: {name}")
        verify_archive(path, digest)
    return folder


# ── Text ──────────────────────────────────────────────────────────────────
# A greeting, then Echo. Small models write "hey" as "he", "here" or "hay",
# and Echo as "Ecco", "Eko" or "Ico", or run the two together ("Hayeko").
GREETINGS = {"hey", "hay", "hi", "hai", "ok", "okay", "hello", "he", "here"}
NAME = re.compile(r"^(?:e|i|a)(?:c|cc|ch|k|kk|ck|kh)o(?:s|es|'s)?$")
MERGED = re.compile(r"^(?:hey|hay|hi|he|hai|here)[eai]?(?:cho|cco|co|ko|kko|cko|kho)$")
WORD = re.compile(r"[a-z']+")
END_PHRASES = {
    "stop", "cancel", "never mind", "nevermind", "that's all", "that is all", "that's it", "thanks", "thank you",
    "goodbye", "bye", "bye bye", "ok stop", "okay stop", "stop listening", "go to sleep",
}


def _tokens(text: str) -> list[tuple[str, int, int]]:
    lowered = text.lower().replace("-", " ")
    return [(m.group(0).strip("'"), m.start(), m.end()) for m in WORD.finditer(lowered) if m.group(0).strip("'")]


def _wake_end(text: str) -> int | None:
    """Where the wake phrase ends in ``text``, if it opens the text."""
    tokens = _tokens(text)[:4]
    if tokens and MERGED.match(tokens[0][0]):
        return tokens[0][2]
    for index, (token, _, _) in enumerate(tokens[:3]):
        if token in GREETINGS and index + 1 < len(tokens) and NAME.match(tokens[index + 1][0]):
            return tokens[index + 1][2]
    return None


def matches(text: str) -> bool:
    """A greeting immediately followed by Echo within the first four words.
    "Here you go", "Hey, I go" and "He echoed" do not match; "Hey Eggo"
    is heard as "Hey Echo" and does."""
    return _wake_end(text) is not None


def strip_wake(text: str) -> str:
    """The question after the wake phrase, or "" when nothing followed it."""
    end = _wake_end(text)
    rest = text if end is None else text[end:]
    return rest.lstrip(" \t\n,.;:!?-—").strip()[:2000]


def end_phrase(text: str) -> bool:
    """A whole utterance asking Echo to stop: "stop", "thanks, Echo", "that's all"."""
    words = [w for w, _, _ in _tokens(text)]
    if words and words[-1] in {"echo", "ecco", "eko"}:
        words = words[:-1]
    return 0 < len(words) <= 4 and " ".join(words) in END_PHRASES


# ── Audio ─────────────────────────────────────────────────────────────────
def samples(data: bytes, max_seconds: float):
    """Mono PCM16 16 kHz WAV bytes to float32 samples, in memory."""
    import numpy as np
    try:
        with wave.open(io.BytesIO(data)) as reader:
            if reader.getnchannels() != 1 or reader.getsampwidth() != 2 or reader.getframerate() != RATE:
                raise ValueError("Use mono PCM16 WAV at 16 kHz.")
            frames = reader.getnframes()
            if frames > max_seconds * RATE:
                raise ValueError("The audio is longer than allowed.")
            raw = reader.readframes(frames)
    except (wave.Error, EOFError) as error:
        raise ValueError("Invalid WAV audio.") from error
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768


class Budget:
    """Probe CPU-seconds per minute. Over budget, only a burst that follows a
    pause is checked, up to twice the budget; the rest are skipped."""
    def __init__(self, per_minute: float = BUDGET, clock=time.monotonic):
        self.per_minute = per_minute
        self.clock = clock
        self.spent: collections.deque[tuple[float, float]] = collections.deque()

    def used(self) -> float:
        now = self.clock()
        while self.spent and self.spent[0][0] <= now - 60:
            self.spent.popleft()
        return sum(cost for _, cost in self.spent)

    def allows(self, quiet: float) -> bool:
        used = self.used()
        return used < self.per_minute or (quiet >= 1.0 and used < 2 * self.per_minute)

    def spend(self, cost: float) -> None:
        self.spent.append((self.clock(), max(0.0, float(cost))))


class Detector:
    """Silero VAD + Whisper tiny.en on one thread."""
    def __init__(self, folder: Path, threads: int = 1):
        from faster_whisper import WhisperModel
        from faster_whisper.vad import get_vad_model
        self.vad = get_vad_model()
        self.model = WhisperModel(str(folder), device="cpu", compute_type="int8", cpu_threads=threads,
                                  num_workers=1, local_files_only=True)

    def speech_seconds(self, audio) -> float:
        import numpy as np
        padded = np.pad(audio, (0, (-len(audio)) % 512)).astype(np.float32)
        if not len(padded):
            return 0.0
        probabilities = np.asarray(self.vad(padded)).reshape(-1)
        return float((probabilities > .5).sum() * 512 / RATE)

    def transcribe(self, audio, limit: int) -> str:
        parts, _ = self.model.transcribe(audio, language="en", task="transcribe", beam_size=1, best_of=1,
                                         temperature=0.0, without_timestamps=True,
                                         condition_on_previous_text=False, vad_filter=False,
                                         hotwords=PHRASE, max_new_tokens=limit)
        return "".join(part.text for part in parts).strip()

    def probe(self, audio) -> bool:
        if self.speech_seconds(audio) < .2:
            return False
        return matches(self.transcribe(audio, PROBE_TOKENS))

    def question(self, audio) -> str:
        return strip_wake(self.transcribe(audio, QUESTION_TOKENS))
