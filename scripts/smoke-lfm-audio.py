#!/usr/bin/env python3
"""Opt-in real CPU speech smoke. Requires explicitly installed LFM audio assets.

MCP runs against disposable retrieval fixtures; the model/runner and all audio
generation/recognition are real. This is not a microphone/duplex quality test.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="imprint-lfm-smoke-") as directory:
        with patch.dict(os.environ, {"IMPRINT_DATA_DIR": directory, "IMPRINT_WORKSPACE": "default"}):
            from fastmcp import Client
            from imprint import server, lfm_audio
            from imprint import voice_memory as memory

            async def snapshot(name):
                records = [{"id": f"fixture-{name.lower()}", "similarity": 0.95,
                            "project": "voice-fixture", "source": "fixture/release.md",
                            "content": f"The release mascot is named {name}. The unrelated build label is Cedar."}]
                with patch.object(server.vs, "search", return_value=records):
                    async with Client(server.mcp) as client:
                        session = await memory.prepare_session(client, workspace="default",
                                                               queries=["release mascot name"], project="voice-fixture")
                session.update(lfm_audio.IDENTITY)
                return session

            question = "What is the name of our release mascot?"
            tts = lfm_audio.run_turn(args.runtime_dir.resolve(), output / "question", mode="tts", text=question)
            audio = output / "question" / "response.wav"
            asr = lfm_audio.run_turn(args.runtime_dir.resolve(), output / "recognition", mode="asr", audio=audio)
            if asr["text"].lower().rstrip("?.!") != question.lower().rstrip("?.!"):
                raise RuntimeError("Synthetic question was not recognized as expected; inspect recognition/result.json")
            results = {"question_tts": tts, "question_asr": asr, "answers": {}}
            for name in ("Nimbus", "Comet"):
                session_path = Path(directory) / f"{name}.json"
                memory.write_session(session_path, asyncio.run(snapshot(name)))
                response = lfm_audio.run_turn(args.runtime_dir.resolve(), output / name.lower(), mode="reply",
                                              audio=audio, session=session_path)
                if name.lower() not in response["text"].lower() or "cedar" in response["text"].lower():
                    raise RuntimeError(f"Memory-conditioned answer failed; inspect {name.lower()}/result.json")
                results["answers"][name] = response
            results["limitations"] = ["retrieval storage is a fixture", "synthetic voice input",
                                      "CPU cold turns", "no microphone, echo or interruption test",
                                      "two memory-name checks are not a grounding quality benchmark"]
            memory.write_session(output / "summary.json", results)
            print("Real TTS, ASR and two memory-conditioned speech replies passed.")
            for name, result in results["answers"].items():
                print(f"{name}: {result['text']}")
                print(f"  CPU wall time {result['elapsed_seconds']:.2f}s, "
                      f"sampled peak RSS {result['peak_rss_mib_sampled']:.0f} MiB, "
                      f"audio {result['audio']['seconds']:.2f}s")
            print(f"Evidence: {output / 'summary.json'}")


if __name__ == "__main__":
    main()
