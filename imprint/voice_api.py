"""Loopback voice sessions. The browser never reaches the native model server."""
from __future__ import annotations

import asyncio
import base64
from collections import deque
import contextlib
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import tempfile
import threading
import time

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
import httpx

from . import config, config_schema, lfm_audio as lfm, voice_runtime, voice_setup
from .http_security import local_host, local_origin, loopback_address
from .voice_memory import INSTRUCTIONS, VoiceMemoryError, _json

router = APIRouter(prefix="/api/voice")
MAX_MESSAGE = 2_000_000
MAX_AUDIO = 1_440_044  # 30 seconds, mono PCM16/24 kHz plus WAV header
STARTUP_TIMEOUT = 120  # Includes full asset verification as well as native load.
TURN_TIMEOUT = 120  # Inactivity, refreshed as an active reply makes progress.
CHAT_SETUP_TIMEOUT = 1800  # A first-time model download is not a reply.
PLAYBACK_AHEAD = 10 * 24000  # PCM samples; pause production until playback catches up.
# Each of Echo's looks (the voice.avatar setting) speaks in its own voice
# (ADR 0071): the speaker the native model is asked for, and an effect the
# dashboard applies to reply audio.
VOICES = {"echo": (lfm.VOICES["us_male"], "robot"), "iris": (lfm.VOICES["us_female"], None), "kai": (lfm.VOICES["us_male"], None)}


def look_voice(look: object) -> str:
    """The voice of the configured look; a look without one speaks as Echo."""
    return look if isinstance(look, str) and look in VOICES else "echo"


def worker_threads() -> int:
    cores = os.cpu_count() or 1
    if hasattr(os, "sched_getaffinity"):
        cores = min(cores, len(os.sched_getaffinity(0)))
    return max(1, min(4, cores))


def runtime_root() -> Path:
    return voice_setup.runtime_root()


class Worker:
    """One owned model, held only while an explicit voice session is open."""
    def __init__(self):
        self.process: subprocess.Popen | None = None
        self.client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        try:
            async with asyncio.timeout(STARTUP_TIMEOUT):
                await self._start()
        except TimeoutError:
            await self.stop()
            raise VoiceMemoryError("Voice took too long to load. Close other applications and try again.") from None

    async def _start(self) -> None:
        if self.process and self.process.poll() is None:
            return
        await self.stop()
        root = runtime_root()
        if root is None:
            raise VoiceMemoryError("Voice is not set up. Configure voice.runtime_dir after building the local runtime.")
        binary = await asyncio.to_thread(voice_runtime.verify, root)
        token = secrets.token_urlsafe(48)
        threads = str(worker_threads())
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        command = [str(binary), "-m", str(root / lfm.MODEL), "-mm", str(root / lfm.PROJECTOR),
                   "-mv", str(root / lfm.VOCODER), "--tts-speaker-file", str(root / lfm.TOKENIZER),
                   "-t", threads, "-tb", threads, "--poll", "0", "--poll-batch", "0",
                   "-c", "4096", "-ngl", "0", "--no-mmproj-offload", "--offline",
                   "--host", "127.0.0.1", "--port", str(port), "--seed", "42"]
        environment = voice_runtime.child_environment()
        environment.update(IMPRINT_VOICE_BACKEND_TOKEN=token, IMPRINT_VOICE_LIFETIME_PIPE="1", OMP_NUM_THREADS=threads)
        # Upstream logs can echo private prompts. Never publish or retain them.
        # Popen works on both Windows Selector and Proactor loops (including
        # uvicorn/threaded test servers); asyncio subprocess pipes do not.
        self.process = subprocess.Popen(
            command, cwd=root, env=environment, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, **voice_runtime.child_options())
        self.client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", trust_env=False,
                                        headers={"Authorization": f"Bearer {token}"}, timeout=90)
        try:
            async with asyncio.timeout(90):
                while self.process.poll() is None:
                    try:
                        response = await self.client.get("/health", timeout=.5)
                        if response.status_code == 200:
                            return
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(.1)
                raise VoiceMemoryError("The local voice model could not start. Rebuild its isolated runtime.")
        except BaseException:
            await self.stop()
            raise

    async def stop(self) -> None:
        if self.client:
            await self.client.aclose()
            self.client = None
        process, self.process = self.process, None
        if process and process.stdin:
            process.stdin.close()
        if process and process.poll() is None:
            try:
                await asyncio.to_thread(process.wait, timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                await asyncio.to_thread(process.wait)

    async def stream(self, system: str, content):
        await self.start()
        assert self.client is not None
        request = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
                   "stream": True, "reset_context": True, "max_tokens": 512,
                   "modalities": ["text"] if system == "Perform ASR." else
                   (["audio"] if system.startswith("Perform TTS.") else ["audio", "text"])}
        async with self.client.stream("POST", "/v1/chat/completions", json=request) as response:
            if response.status_code != 200:
                raise VoiceMemoryError("The local voice worker rejected the turn.")
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    break
                if len(payload) > 1_000_000:
                    raise VoiceMemoryError("The voice worker exceeded its output budget.")
                event = json.loads(payload)
                if "error" in event:
                    raise VoiceMemoryError("The voice model could not process this turn. Try a shorter question.")
                choices = event.get("choices") or []
                if choices:
                    delta = choices[0].get("delta", {})
                    if delta.get("content"):
                        yield {"type": "text", "text": delta["content"]}
                    if delta.get("audio"):
                        audio = delta["audio"]
                        if audio.get("format") != "pcm" or audio.get("sample_rate") != 24000:
                            raise VoiceMemoryError("Unexpected native voice audio format.")
                        yield {"type": "audio", "data": audio["data"], "sample_rate": 24000, "format": "pcm16"}


class Sessions:
    def __init__(self):
        self.leases: dict[str, dict] = {}
        self.active = False
        self.worker = Worker()

    def issue(self, workspace: str, origin: str, engine: str, voice: str = "echo") -> str:
        now = time.monotonic()
        self.leases = {key: value for key, value in self.leases.items() if value["expires"] > now}
        if len(self.leases) >= 8:
            raise HTTPException(429, "Too many pending voice sessions")
        token = secrets.token_urlsafe(32)
        self.leases[token] = {"workspace": workspace, "origin": origin, "engine": engine, "voice": voice, "expires": now + 30}
        return token

    def consume(self, token: str, origin: str) -> dict | None:
        lease = self.leases.pop(token, None)
        return lease if lease and lease["expires"] > time.monotonic() and lease["origin"] == origin else None


sessions = Sessions()


@router.get("/status")
def status():
    root = runtime_root()
    return {"configured": root is not None, "installed": bool(root and voice_runtime.installed(root)),
            "supported": voice_runtime.supported(),
            "busy": sessions.active, "chat_provider": config_schema.resolve("chat.provider")[0],
            "workspace": config.get_active_workspace(), "model": "LFM2.5-Audio · 1.5B", "language": "en"}


def check_setup_origin(request: Request, *, mutate=False) -> None:
    expected = f"http://{request.headers.get('host', '')}"
    origin = request.headers.get("origin")
    if (not request.client or not loopback_address(request.client.host) or not local_origin(expected)
            or (origin is not None and origin != expected) or (mutate and origin != expected)):
        raise HTTPException(403, "Voice setup requires the local dashboard origin")


async def empty_setup_body(request: Request) -> None:
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > 2:
            raise HTTPException(400, "Voice setup does not accept paths, URLs or commands")
    if body not in (b"", b"{}"):
        raise HTTPException(400, "Invalid setup control")


@router.get("/setup")
def setup_status(request: Request):
    check_setup_origin(request)
    return voice_setup.setup.snapshot()


@router.post("/setup")
async def setup_start(request: Request):
    check_setup_origin(request, mutate=True)
    await empty_setup_body(request)
    if sessions.active:
        raise HTTPException(409, "Stop your voice conversation before setting up Echo")
    if not voice_runtime.supported():
        raise HTTPException(409, "Voice is unavailable on this platform")
    return voice_setup.setup.start()


@router.post("/setup/cancel")
async def setup_cancel(request: Request):
    check_setup_origin(request, mutate=True)
    await empty_setup_body(request)
    return voice_setup.setup.cancel()


@router.post("/sessions")
async def create_session(request: Request):
    origin = request.headers.get("origin", f"http://{request.headers.get('host', '')}")
    # Voice requires same-origin even though the general dashboard API allows
    # other loopback development origins. No extension/container exception.
    if (not request.client or not loopback_address(request.client.host)
            or origin != f"http://{request.headers.get('host', '')}" or not local_origin(origin)):
        raise HTTPException(403, "Voice requires the local dashboard origin")
    body = await request.body()
    if len(body) > 2048:
        raise HTTPException(413, "Voice session request is too large")
    try:
        payload = json.loads(body)
        workspace = payload.get("workspace", config.get_active_workspace())
        engine = payload.get("engine", "chat")
        if (not isinstance(workspace, str) or config.validate_workspace_name(workspace)
                or workspace not in config.get_known_workspaces() or not isinstance(engine, str)
                or engine not in {"chat", "lfm"}):
            raise ValueError()
    except (ValueError, AttributeError):
        raise HTTPException(400, "Invalid workspace or voice engine") from None
    if engine == "chat" and config_schema.resolve("chat.provider")[0] != "local":
        raise HTTPException(409, "Select a local chat provider or use the small voice model")
    if voice_setup.setup.snapshot()["busy"]:
        raise HTTPException(409, "Echo setup is still running")
    if not status()["installed"] or not status()["supported"]:
        raise HTTPException(409, "Build and configure the local voice runtime first")
    # The capability pins the look's voice as well; a new look takes a new session.
    voice = look_voice(config_schema.resolve("voice.avatar")[0])
    return {"token": sessions.issue(workspace, origin, engine, voice), "workspace": workspace, "expires_in": 30}


def check_socket(websocket: WebSocket) -> bool:
    origin = websocket.headers.get("origin", "")
    return bool(websocket.client and loopback_address(websocket.client.host)
                and local_host(websocket.headers.get("host", "")) and local_origin(origin)
                and origin == f"http://{websocket.headers.get('host', '')}")


async def audio_content(encoded: str) -> list:
    if len(encoded) > 1_920_060:
        raise VoiceMemoryError("Keep each spoken question under 30 seconds.")
    try:
        audio = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        raise VoiceMemoryError("Invalid microphone audio.") from None
    if len(audio) > MAX_AUDIO:
        raise VoiceMemoryError("Microphone audio exceeds the turn limit.")
    with tempfile.TemporaryDirectory(prefix="imprint-mic-") as directory:
        path = Path(directory) / "question.wav"
        path.write_bytes(audio)
        os.chmod(path, 0o600)
        info = await asyncio.to_thread(lfm.wav_info, path)
    if info["seconds"] > 30 or info["sample_rate"] != 24000 or info["format"] != "pcm16":
        raise VoiceMemoryError("Use mono PCM16 WAV at 24 kHz, at most 30 seconds.")
    return [{"type": "input_audio", "input_audio": {"data": encoded, "format": "wav"}}]


async def memory_prompt(question: str, workspace: str) -> str:
    # The actual registered MCP implementation, pinned to this session's
    # workspace. Only retrieval; no model-selected write authority.
    from . import server
    evidence = await asyncio.to_thread(server.search, query=question, workspace=workspace,
                                       detail="compact", max_chars=1600, limit=5)
    return INSTRUCTIONS + _json({"workspace": workspace, "evidence": evidence})


async def chat_events(question: str, history: list, workspace: str, cancelled: threading.Event):
    """Bounded thread bridge; cancellation reaches chat before its next token/tool."""
    from . import chat
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(16)

    def publish(event):
        future = asyncio.run_coroutine_threadsafe(queue.put(event), loop)
        while not cancelled.is_set():
            try:
                future.result(timeout=.1)
                return
            except TimeoutError:
                pass
        future.cancel()

    def produce():
        try:
            for event in chat.stream_reply(history, question, workspace=workspace,
                                            cancel_event=cancelled, read_only=True):
                if cancelled.is_set():
                    break
                publish(event)
        finally:
            if not cancelled.is_set():
                publish(None)

    task = asyncio.create_task(asyncio.to_thread(produce))
    completed = False
    try:
        while True:
            event = await queue.get()
            if event is None:
                break
            yield event
        await task
        completed = True
    finally:
        if not completed:
            cancelled.set()
        # Prefill/native model loading may finish after the socket closes.
        # The worker checks this event before any later token or tool call.
        if not task.done():
            task.add_done_callback(lambda finished: finished.exception() if not finished.cancelled() else None)


class PlaybackWindow:
    """Per-turn audio credit, released only by completed client playback."""
    def __init__(self):
        self.sent = 0
        self.played = 0
        self.changed = asyncio.Event()

    async def reserve(self, encoded: str) -> None:
        while self.sent - self.played >= PLAYBACK_AHEAD:
            self.changed.clear()
            await self.changed.wait()
        self.sent += len(base64.b64decode(encoded, validate=True)) // 2

    def acknowledge(self, samples: object) -> None:
        if type(samples) is not int or not self.played <= samples <= self.sent:
            raise VoiceMemoryError("Invalid voice playback progress.")
        self.played = samples
        self.changed.set()


@router.websocket("/stream")
async def stream(websocket: WebSocket):
    if not check_socket(websocket):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    owned = False
    turn_task: asyncio.Task | None = None
    cancel_flag = threading.Event()
    turn_id = 0
    playback: PlaybackWindow | None = None
    send_lock = asyncio.Lock()

    async def send(event):
        async with send_lock:
            await asyncio.wait_for(websocket.send_json(event), 5)

    async def cancel(force=False):
        nonlocal turn_task
        cancel_flag.set()
        running = bool(turn_task and not turn_task.done())
        if turn_task:
            turn_task.cancel()
            await asyncio.gather(turn_task, return_exceptions=True)
            turn_task = None
        # Also stop native inference, including prefill, and discard all context.
        if force or running:
            await sessions.worker.stop()

    try:
        auth = await asyncio.wait_for(websocket.receive_text(), 5)
        if len(auth) > 512:
            raise VoiceMemoryError("Invalid voice session.")
        payload = json.loads(auth)
        if not isinstance(payload, dict) or not isinstance(payload.get("token"), str):
            raise VoiceMemoryError("Invalid voice session.")
        lease = sessions.consume(payload.get("token", ""), websocket.headers["origin"])
        if not lease:
            raise VoiceMemoryError("Voice session expired. Start again.")
        if sessions.active:
            raise VoiceMemoryError("Voice is already open in another window. Stop it there first.")
        if voice_setup.setup.snapshot()["busy"]:
            raise VoiceMemoryError("Wait for Echo setup to finish before starting voice.")
        sessions.active = owned = True
        workspace, engine = lease["workspace"], lease["engine"]
        speaker, effect = VOICES[lease["voice"]]
        history: deque = deque(maxlen=6)
        await send({"type": "connecting"})
        # A closed companion must cancel a cold model load as well as an active
        # turn. HTTP/WebSocket disconnects aren't observed while awaiting only
        # the worker's readiness probe.
        boot = asyncio.create_task(sessions.worker.start())
        disconnected = asyncio.create_task(websocket.receive_text())
        try:
            done, _ = await asyncio.wait({boot, disconnected}, return_when=asyncio.FIRST_COMPLETED)
            if disconnected in done:
                disconnected.result()  # Propagate disconnect; reject early controls.
                raise VoiceMemoryError("Wait for voice readiness before sending controls.")
            await boot
        finally:
            for task in (boot, disconnected):
                if not task.done():
                    task.cancel()
            await asyncio.gather(boot, disconnected, return_exceptions=True)
        await send({"type": "ready", "workspace": workspace, "engine": engine, "voice": lease["voice"], "effect": effect})
        deadline = time.monotonic() + 1800

        async def turn(identifier: int, message: dict, flag: threading.Event, audio_window: PlaybackWindow):
            turn_budget = None

            async def emit(event):
                if not flag.is_set():
                    if event["type"] == "audio":
                        await audio_window.reserve(event["data"])
                    await send({**event, "id": identifier})
                    # An active long reply is healthy. Bound stalls rather than
                    # its total generation/playback time (ADR 0092).
                    if turn_budget is not None and event["type"] != "provision":
                        turn_budget.reschedule(asyncio.get_running_loop().time() + TURN_TIMEOUT)
            try:
                async with asyncio.timeout(TURN_TIMEOUT) as turn_budget:
                    question = message.get("text", "").strip()
                    if message.get("audio"):
                        content = await audio_content(message["audio"])
                        await emit({"type": "state", "state": "transcribing"})
                        question = ""
                        async for event in sessions.worker.stream("Perform ASR.", content):
                            if event["type"] == "text":
                                question += event["text"]
                        question = question.strip()
                    if not question or len(question) > 2000:
                        raise VoiceMemoryError("I didn't catch a short question. Please try again.")
                    await emit({"type": "user", "text": question})
                    await emit({"type": "state", "state": "thinking"})
                    answer = ""
                    if engine == "lfm":
                        evidence = await memory_prompt(question, workspace)
                        recent = list(history)
                        while recent and len(_json(recent)) > 3000:
                            recent = recent[2:]
                        context = _json(recent)
                        content = f"{evidence}\nRecent conversation (may be interrupted): {context}\nQuestion: {question}"
                        async for event in sessions.worker.stream(f"Respond with interleaved text and audio. Use the {speaker} voice.", content):
                            if event["type"] == "text":
                                answer += event["text"]
                            await emit(event)
                    else:
                        async def synthesize(phrase: str):
                            while phrase.strip():
                                end = min(len(phrase), 240)
                                if end < len(phrase):
                                    end = phrase.rfind(" ", 0, end) or end
                                    if end < 1:
                                        end = 240
                                part, phrase = phrase[:end], phrase[end:]
                                async for audio in sessions.worker.stream(f"Perform TTS. Use the {speaker} voice.", part):
                                    if audio["type"] == "audio":
                                        await emit(audio)

                        pending = ""
                        provisioning = False
                        async for event in chat_events(question, list(history), workspace, flag):
                            kind = event["type"]
                            if provisioning and kind in {"token", "tool_call"}:
                                turn_budget.reschedule(asyncio.get_running_loop().time() + TURN_TIMEOUT)
                                provisioning = False
                            if kind == "error":
                                raise VoiceMemoryError("The chat model could not reply. Open Chat to check its setup.")
                            if kind == "token":
                                text = event["text"]
                                answer += text
                                pending += text
                                await emit({"type": "text", "text": text})
                                # Speak bounded phrases as chat streams, retaining the
                                # dashboard's local reasoning model and read tools.
                                boundary = re.search(r"[.!?]\s|\n|.{180}\s", pending)
                                if boundary and len(pending) >= 35:
                                    end = boundary.end()
                                    phrase, pending = pending[:end], pending[end:]
                                    await synthesize(phrase)
                            elif kind == "tool_call":
                                await emit({"type": "tool", "name": event["name"]})
                            elif kind == "analysis":
                                await emit({"type": "state", "state": "analyzing", "text": event["text"]})
                            elif kind in {"download_start", "download_progress", "download_complete", "load_start"}:
                                if kind == "download_start" and not provisioning:
                                    turn_budget.reschedule(asyncio.get_running_loop().time() + CHAT_SETUP_TIMEOUT)
                                    provisioning = True
                                stage = "download" if kind in {"download_start", "download_progress"} else "load"
                                progress = {"type": "provision", "stage": stage}
                                for name in ("downloaded", "total"):
                                    value = event.get(name)
                                    if type(value) is int and 0 <= value <= 1_000_000_000_000:
                                        progress[name] = value
                                await emit(progress)
                        if pending.strip():
                            await synthesize(pending)
                    if answer.strip():
                        # A client playback acknowledgement commits assistant
                        # history; generated but unheard replies are not retained.
                        awaiting[identifier] = (question, answer[:3000])
                    await emit({"type": "done"})
            except asyncio.CancelledError:
                raise
            except Exception:
                turn_budget = None
                await emit({"type": "error", "error": "Voice couldn't complete this turn. Try a shorter question or reconnect."})
                await sessions.worker.stop()

        awaiting: dict[int, tuple[str, str]] = {}
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), 120)
            except TimeoutError:
                if turn_task and not turn_task.done():
                    continue
                raise
            if len(raw) > MAX_MESSAGE:
                raise VoiceMemoryError("Voice message exceeds its size limit.")
            message = json.loads(raw)
            if not isinstance(message, dict):
                raise VoiceMemoryError("Invalid voice message.")
            kind = message.get("type")
            if kind == "cancel":
                await cancel(force=True)
                awaiting.clear()
                await send({"type": "cancelled", "id": turn_id})
            elif kind == "played":
                item = awaiting.pop(message.get("id"), None)
                if item:
                    history.extend([{"role": "user", "content": item[0]}, {"role": "assistant", "content": item[1]}])
            elif kind == "playback":
                # A cancelled turn's late acknowledgement cannot release the
                # next turn's credit. Progress never commits reply history.
                if message.get("id") == turn_id and playback is not None:
                    playback.acknowledge(message.get("samples"))
            elif kind == "turn":
                identifier = message.get("id")
                if (type(identifier) is not int or identifier <= turn_id
                        or not isinstance(message.get("text", ""), str)
                        or not isinstance(message.get("audio", ""), str)
                        or bool(message.get("text", "").strip()) == bool(message.get("audio"))
                        or len(message.get("text", "")) > 2000):
                    raise VoiceMemoryError("Invalid voice turn.")
                await cancel()
                awaiting.clear()
                turn_id = identifier
                cancel_flag = threading.Event()
                playback = PlaybackWindow()
                turn_task = asyncio.create_task(turn(turn_id, message, cancel_flag, playback))
            else:
                raise VoiceMemoryError("Unknown voice control.")
    except (WebSocketDisconnect, TimeoutError, RuntimeError):
        pass
    except VoiceMemoryError as error:
        with contextlib.suppress(Exception):
            await send({"type": "error", "error": str(error)})
    except (ValueError, TypeError):
        with contextlib.suppress(Exception):
            await send({"type": "error", "error": "Voice session unavailable or expired. Reconnect from the local dashboard."})
    finally:
        if owned:
            await cancel(force=True)
            sessions.active = False
        with contextlib.suppress(Exception):
            await websocket.close()


async def shutdown() -> None:
    voice_setup.setup.cancel()
    sessions.leases.clear()
    await sessions.worker.stop()
