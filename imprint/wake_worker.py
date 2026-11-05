"""The wake word's worker process (ADR 0137).

The API starts it while a desktop listener is connected and stops it when the
listener goes. It runs at a lower priority, holds Silero and Whisper, and talks
bounded JSON lines:

  in:  {"id": n, "kind": "probe" | "utterance", "audio": "<base64 16 kHz PCM16 WAV>"}
  out: {"ready": true} | {"id": n, "wake": bool, "cpu": seconds} | {"id": n, "text": "…"}
       | {"id": n, "error": "…"} | {"error": "…"} (then it exits)

It exits when its stdin closes. Nothing it hears is written or logged.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import sys
import time

MAX_LINE = 1_400_000  # a 30 s utterance, base64, with room for the envelope


def lower_priority() -> None:
    # Windows sets BELOW_NORMAL_PRIORITY_CLASS when the API starts the process.
    if hasattr(os, "nice"):
        try:
            os.nice(10)
        except OSError:
            pass


def main() -> int:
    from . import wake
    out = sys.stdout

    def emit(event: dict) -> None:
        out.write(json.dumps(event) + "\n")
        out.flush()

    lower_priority()
    try:
        detector = wake.Detector(wake.verify())
    except Exception:  # noqa: BLE001 — a missing or damaged model, or a broken runtime
        emit({"error": "The wake model is missing or damaged. Turn “Hey Echo” off and on again."})
        return 1
    emit({"ready": True})
    for line in iter(lambda: sys.stdin.buffer.readline(MAX_LINE + 1), b""):
        if len(line) > MAX_LINE:
            emit({"error": "Message too large."})
            return 1
        try:
            message = json.loads(line)
            identifier, kind = message["id"], message["kind"]
            if type(identifier) is not int or kind not in {"probe", "utterance"}:
                raise ValueError
            audio = wake.samples(base64.b64decode(message["audio"], validate=True),
                                 wake.PROBE_SECONDS if kind == "probe" else wake.UTTERANCE_SECONDS)
        except (ValueError, KeyError, TypeError, binascii.Error):
            emit({"error": "Invalid message."})
            return 1
        try:
            if kind == "probe":
                started = time.process_time()
                woke = detector.probe(audio)
                emit({"id": identifier, "wake": woke, "cpu": round(time.process_time() - started, 3)})
            else:
                emit({"id": identifier, "text": detector.question(audio)})
        except Exception:  # noqa: BLE001 — one bad clip must not end listening
            emit({"id": identifier, "error": "Could not check this audio."})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
