#!/usr/bin/env python3
"""Opt-in real local HTTP/WebSocket streaming, ASR and cancellation smoke.

Uses a disposable workspace and retrieval fixture, synthetic speech, and the
actual LFM model. The Chat route uses a deterministic reasoning fixture to avoid
provisioning another model or touching user chat history. No microphone opens.
"""
import argparse
import asyncio
import base64
import json
import os
from pathlib import Path
import socket
import tempfile
import time
import wave
from unittest.mock import patch


async def run(runtime: Path, output: Path):
    import httpx
    import uvicorn
    from fastapi import FastAPI
    from websockets.asyncio.client import connect
    from imprint import config, server, voice_api as voice, chat

    app = FastAPI()
    app.include_router(voice.router)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    address = f"http://127.0.0.1:{sock.getsockname()[1]}"
    runner = uvicorn.Server(uvicorn.Config(app, log_level="error", proxy_headers=False, ws_max_size=2_000_000))
    serving = asyncio.create_task(runner.serve(sockets=[sock]))
    results = {"model": "LFM2.5-Audio-1.5B-Q4", "platform": "Linux x64 CPU", "checks": {}}
    try:
        while not runner.started:
            await asyncio.sleep(.02)
        start = time.monotonic()
        await voice.sessions.worker.start()
        results["worker_start_seconds_including_verification"] = time.monotonic() - start
        client = voice.sessions.worker.client
        assert client is not None
        assert (await client.get("/health", headers={"Authorization": "Bearer wrong"})).status_code == 403
        assert (await client.get("/health", headers={"Origin": address})).status_code == 403
        results["checks"]["native_auth_and_browser_origin_denial"] = True
        pcm = bytearray()
        async for event in voice.sessions.worker.stream("Perform TTS. Use the US female voice.",
                                                       "What is the name of our release mascot?"):
            if event["type"] == "audio":
                pcm.extend(base64.b64decode(event["data"]))
        with wave.open(str(output / "question.wav"), "wb") as sound:
            sound.setnchannels(1); sound.setsampwidth(2); sound.setframerate(24000); sound.writeframes(pcm)
        question = base64.b64encode((output / "question.wav").read_bytes()).decode()
        config.register_workspace("other")
        records = [{"id": "fixture-nimbus", "similarity": .99, "project": "voice-fixture", "source": "fixture/mascot",
                    "content": "The release mascot is named Nimbus. The unrelated build label is Cedar."}]
        async with httpx.AsyncClient(base_url=address, trust_env=False, headers={"Origin": address}) as http:
            assert (await http.post("/api/voice/sessions", json={}, headers={"Origin": "https://evil.test"})).status_code == 403
            with patch.object(server.vs, "search", return_value=records) as retrieval:
                token = (await http.post("/api/voice/sessions", json={"engine": "lfm", "workspace": "default"})).json()["token"]
                config.switch_workspace("other")
                async with connect(address.replace("http:", "ws:") + "/api/voice/stream", origin=address, max_size=2_000_000) as ws:
                    await ws.send(json.dumps({"token": token}))
                    assert json.loads(await ws.recv())["type"] == "connecting"
                    assert json.loads(await ws.recv())["type"] == "ready"
                    start = time.monotonic(); first = None; text = ""; generated = bytearray(); heard = ""
                    await ws.send(json.dumps({"type": "turn", "id": 1, "audio": question}))
                    while True:
                        event = json.loads(await asyncio.wait_for(ws.recv(), 120))
                        if event["type"] == "error":
                            raise RuntimeError(event["error"])
                        if event["type"] == "user":
                            heard = event["text"]
                        if event["type"] == "text":
                            text += event["text"]
                        if event["type"] == "audio":
                            first = first or time.monotonic() - start
                            generated.extend(base64.b64decode(event["data"]))
                        if event["type"] == "done":
                            break
                    assert "mascot" in heard.lower() and "nimbus" in text.lower() and "cedar" not in text.lower()
                    assert first is not None and len(generated) > 4800
                    assert retrieval.call_args.kwargs["workspace"] == "default"
                    results["speech_question"] = {"recognized": heard, "answer": text, "first_audio_seconds": first,
                                                  "whole_turn_seconds": time.monotonic() - start,
                                                  "audio_seconds": len(generated) / 48000}
                    await ws.send(json.dumps({"type": "played", "id": 1}))
                    await ws.send(json.dumps({"type": "turn", "id": 2, "text": "Tell me a long story about Nimbus."}))
                    while True:
                        event = json.loads(await asyncio.wait_for(ws.recv(), 60))
                        if event["type"] == "audio":
                            break
                        if event["type"] == "error":
                            raise RuntimeError(event["error"])
                    before = voice.sessions.worker.process
                    start = time.monotonic()
                    await ws.send(json.dumps({"type": "cancel"}))
                    while json.loads(await asyncio.wait_for(ws.recv(), 5))["type"] != "cancelled":
                        pass
                    results["cancel_ack_seconds"] = time.monotonic() - start
                    assert before is not None and before.returncode is not None
                    assert voice.sessions.worker.process is None
                    results["checks"]["mid_speech_cancellation_stops_owned_process"] = True
                    await ws.send(json.dumps({"type": "turn", "id": 3, "text": "What is two plus two?"}))
                    after = ""; n = 0
                    while True:
                        event = json.loads(await asyncio.wait_for(ws.recv(), 60))
                        if event.get("id") not in {None, 3}:
                            raise RuntimeError("Stale generation after cancellation")
                        if event["type"] == "text": after += event["text"]
                        if event["type"] == "audio": n += 1
                        if event["type"] == "error": raise RuntimeError(event["error"])
                        if event["type"] == "done": break
                    assert "four" in after.lower() and n > 0
                    results["checks"]["fresh_turn_after_cancel"] = True
            # Let socket cleanup finish before obtaining the next exclusive lease.
            for _ in range(100):
                if not voice.sessions.active: break
                await asyncio.sleep(.02)
            def reasoning(prior, message, **kwargs):
                assert kwargs["workspace"] == "default" and kwargs["read_only"] is True
                yield {"type": "token", "text": "Nimbus is the release mascot. Hello from your chat model."}
                yield {"type": "done"}
            with patch.object(chat, "stream_reply", side_effect=reasoning):
                token = (await http.post("/api/voice/sessions", json={"engine": "chat", "workspace": "default"})).json()["token"]
                async with connect(address.replace("http:", "ws:") + "/api/voice/stream", origin=address) as ws:
                    await ws.send(json.dumps({"token": token})); await ws.recv(); await ws.recv()
                    await ws.send(json.dumps({"type": "turn", "id": 1, "text": "Hello"}))
                    audio_chunks, text = 0, ""
                    while True:
                        event = json.loads(await asyncio.wait_for(ws.recv(), 60))
                        if event["type"] == "text": text += event["text"]
                        if event["type"] == "audio": audio_chunks += 1
                        if event["type"] == "error": raise RuntimeError(event["error"])
                        if event["type"] == "done": break
                    assert audio_chunks > 0 and "chat model" in text
                    results["checks"]["chat_routing_with_real_streamed_tts_and_fixture_reasoning"] = True
        results["checks"]["workspace_pinned_across_dashboard_switch"] = True
        results["limitations"] = ["synthetic microphone input", "retrieval storage fixture", "chat reasoning fixture",
                                   "no human intelligibility rating", "no acoustic echo/room-noise evaluation",
                                   "no native Windows/macOS or transparent desktop execution"]
    finally:
        runner.should_exit = True
        await voice.shutdown()
        await asyncio.wait_for(serving, 5)
        sock.close()
    output.joinpath("summary.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="imprint-live-smoke-") as directory, patch.dict(os.environ, {
        "IMPRINT_DATA_DIR": directory, "IMPRINT_VOICE_RUNTIME_DIR": str(args.runtime_dir.resolve()),
        "IMPRINT_CHAT_PROVIDER": "local", "IMPRINT_WORKSPACE": "",
    }):
        asyncio.run(run(args.runtime_dir.resolve(), output))


if __name__ == "__main__":
    main()
