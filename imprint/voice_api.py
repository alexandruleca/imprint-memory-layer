"""Loopback voice sessions. The browser never reaches the native model server."""
from __future__ import annotations

import asyncio
import base64
import contextlib
from collections import deque
import difflib
import json
import logging
import os
from pathlib import Path
import re
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
import httpx

from . import config, config_schema, lfm_audio as lfm, voice_runtime, voice_setup, wake
from .http_security import local_host, local_origin, loopback_address
from .voice_memory import INSTRUCTIONS, VoiceMemoryError, _json

router = APIRouter(prefix="/api/voice")
log = logging.getLogger(__name__)
MAX_MESSAGE = 2_000_000
MAX_AUDIO = 1_440_044  # 30 seconds, mono PCM16/24 kHz plus WAV header
STARTUP_TIMEOUT = 120  # Includes full asset verification as well as native load.
TURN_TIMEOUT = 120  # Inactivity, refreshed as an active reply makes progress.
CHAT_SETUP_TIMEOUT = 1800  # A first-time model download is not a reply.
PLAYBACK_AHEAD = 10 * 24000  # PCM samples; pause production until playback catches up.
# A voice conversation keeps every heard message in memory, as Chat stores
# them, for its summary and conversation_history (ADR 0141); never on disk.
HISTORY_LIMIT = 400  # messages; a 30-minute session rarely comes near
TOOL_RESULT_CHARS = 4000
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
        # Both are let go before the first await, so a start that runs meanwhile
        # launches a new worker instead of adopting the one being stopped.
        client, self.client = self.client, None
        process, self.process = self.process, None
        if client:
            await client.aclose()
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
        self.worker = Worker()
        # One worker request at a time: a question heard during a reply is
        # transcribed between its sentences (ADR 0145).
        self.speech = asyncio.Lock()
        # The one voice conversation. It outlives the window that holds it
        # (ADR 0145); `claimed` covers a window connecting to it.
        self.conversation: Conversation | None = None
        self.claimed = False

    @property
    def active(self) -> bool:
        """A window holds the conversation, or is connecting to it."""
        return self.claimed or bool(self.current() and self.current().socket)

    def current(self) -> Conversation | None:
        """The conversation, unless it has been without a window and idle for too long."""
        conversation = self.conversation
        if (conversation and not self.claimed and conversation.socket is None and not conversation.busy()
                and time.monotonic() - conversation.detached_at > DETACHED_KEEP):
            self.conversation = conversation = None
        return conversation

    def issue(self, workspace: str, origin: str, engine: str, voice: str = "echo", chat: str | None = None) -> str:
        now = time.monotonic()
        self.leases = {key: value for key, value in self.leases.items() if value["expires"] > now}
        if len(self.leases) >= 8:
            raise HTTPException(429, "Too many pending voice sessions")
        token = secrets.token_urlsafe(32)
        self.leases[token] = {"workspace": workspace, "origin": origin, "engine": engine, "voice": voice, "chat": chat,
                              "expires": now + 30}
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
    if sessions.active or (sessions.current() and sessions.current().busy()):
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
        # A chat from Chat history to carry on (ADR 0146); none starts afresh.
        chat = payload.get("chat")
        if (not isinstance(workspace, str) or config.validate_workspace_name(workspace)
                or workspace not in config.get_known_workspaces() or not isinstance(engine, str)
                or engine not in {"chat", "lfm"}
                or (chat is not None and (not isinstance(chat, str) or not 0 < len(chat) <= 64))):
            raise ValueError()
    except (ValueError, AttributeError):
        raise HTTPException(400, "Invalid workspace, voice engine or chat") from None
    if engine == "chat" and config_schema.resolve("chat.provider")[0] != "local":
        raise HTTPException(409, "Select a local chat provider or use the small voice model")
    if voice_setup.setup.snapshot()["busy"]:
        raise HTTPException(409, "Echo setup is still running")
    if not status()["installed"] or not status()["supported"]:
        raise HTTPException(409, "Build and configure the local voice runtime first")
    if chat is not None:
        from . import chat_store
        if await asyncio.to_thread(chat_store.get_session, chat, workspace) is None:
            raise HTTPException(404, "That chat no longer exists")
    # The capability pins the look's voice as well; a new look takes a new session.
    voice = look_voice(config_schema.resolve("voice.avatar")[0])
    return {"token": sessions.issue(workspace, origin, engine, voice, chat), "workspace": workspace, "expires_in": 30}


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
    return INSTRUCTIONS + await asyncio.to_thread(_guidelines) + _json({"workspace": workspace, "evidence": evidence})


# Estimated tokens of the user's guidelines the speech model's prompt holds
# (ADR 0143): a smaller share than a chat's, beside the retrieved evidence.
SPEECH_GUIDELINES = 750


def _guidelines() -> str:
    from . import core_memories
    try:
        return core_memories.guidance(SPEECH_GUIDELINES, searchable=False).lstrip("\n")
    except Exception:
        return ""


def tool_label(name: str, args: object) -> str:
    """What Echo shows while a tool runs; the history lookup in plain words."""
    from . import chat_memory
    if name == "ask_user":
        return "Asking you"  # the question itself follows (ADR 0159)
    if name != chat_memory.TOOL:
        return f"Checking memory · {name}"
    query = args.get("query") if isinstance(args, dict) else None
    if isinstance(query, str) and query.strip():
        return f"Searching earlier in this conversation for “{query.strip()[:60]}”"
    return "Reading earlier in this conversation"


async def chat_events(question: str, history: list, workspace: str, cancelled: threading.Event,
                      summary: dict | None = None, ask=None):
    """Bounded thread bridge; cancellation reaches chat before its next token/tool.
    ``ask`` lets the reply ask the user and wait for the answer (ADR 0159)."""
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
            for event in chat.stream_reply(history, question, workspace=workspace, cancel_event=cancelled,
                                            read_only=True, summary=summary, **({"ask": ask} if ask else {})):
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
    """Per-turn audio credit, released only by completed client playback, or
    for good once the window playing it goes away (ADR 0145)."""
    def __init__(self):
        self.sent = 0
        self.played = 0
        self.released = False
        self.changed = asyncio.Event()

    async def reserve(self, encoded: str) -> None:
        while not self.released and self.sent - self.played >= PLAYBACK_AHEAD:
            self.changed.clear()
            await self.changed.wait()
        self.sent += len(base64.b64decode(encoded, validate=True)) // 2

    def acknowledge(self, samples: object) -> None:
        if type(samples) is not int or not self.played <= samples <= self.sent:
            raise VoiceMemoryError("Invalid voice playback progress.")
        self.played = samples
        self.changed.set()

    def release(self) -> None:
        self.released = True
        self.changed.set()


# ── Questions run to completion (ADR 0145) ────────────────────────────────
# A conversation belongs to the API, not to the window that asked: closing,
# hiding or switching windows only detaches it, and speech during a reply
# waits its turn. Only the user's explicit stop cancels a reply.
DETACHED_KEEP = 1800  # seconds a conversation is kept without a window
QUEUE_LIMIT = 4  # questions waiting behind the one being answered
KEPT_TURNS = 8  # recent exchanges a returning window is shown
SPEECH_WORDS = re.compile(r"[a-z0-9']+")
# What recognizers write for sounds that are not words: "[cough]", "(laughs)", "*sigh*".
SOUND_LABELS = re.compile(r"\[[^\]]*\]|\([^)]*\)|\*[^*]*\*")
FILLERS = {"um", "umm", "uh", "uhh", "uhm", "hmm", "hm", "mm", "mmm", "mhm", "ah", "ahh", "oh", "er", "erm", "eh", "huh"}


# A question Echo asks waits this long for an answer, as chat.ANSWER_TIMEOUT
# (ADR 0159); kept here so importing voice does not load the chat agent.
ANSWER_WAIT = 30 * 60


def spoken_question(event: dict) -> str:
    """A question Echo asks, as said aloud: the question, then its choices."""
    question = str(event.get("question", "")).strip()
    options = [str(option) for option in event.get("options") or []]
    if not options:
        return question
    choices = options[0] if len(options) == 1 else f"{', '.join(options[:-1])} or {options[-1]}"
    return f"{question} You can say {choices}{', or something else' if event.get('allow_other', True) else ''}."


def speech_words(text: str) -> list[str]:
    return [word.strip("'") for word in SPEECH_WORDS.findall(SOUND_LABELS.sub(" ", text.lower())) if word.strip("'")]


def unheard(text: str) -> bool:
    """Nothing to answer: no words, or only fillers and sound labels such as "[cough]"."""
    return all(word in FILLERS for word in speech_words(text))


def echoes(text: str, answer: str) -> bool:
    """Speech that began while Echo played its answer and is mostly one run of
    that answer's words: Echo hearing itself through the speakers."""
    heard, said = speech_words(text), speech_words(answer)
    if len(heard) < 3 or not said:
        return False
    match = difflib.SequenceMatcher(None, said, heard, autojunk=False).find_longest_match(0, len(said), 0, len(heard))
    return match.size >= .8 * len(heard)


def saving_enabled() -> bool:
    """Echo's conversations are saved in Chat history unless the user turned that off (ADR 0146)."""
    return bool(config_schema.resolve("voice.save_conversations")[0])


def stored_chat(chat: str, workspace: str) -> tuple[dict | None, list[dict], dict | None]:
    """A chat from Chat history as a voice conversation holds it: the session,
    its messages numbered as stored, and its running summary (ADR 0141)."""
    from . import chat_store
    session = chat_store.get_session(chat, workspace)
    if session is None:
        return None, [], None
    history = [{key: message[key] for key in ("role", "content", "tool_name", "tool_args", "seq")
                if message.get(key) is not None}
               for message in chat_store.get_messages(chat, workspace)]
    summary = chat_store.get_summary(chat, workspace)
    if summary is not None:
        summary = {key: summary.get(key) for key in ("text", "covers", "retry_after")}
    return session, history, summary


def exchanges(history: list[dict]) -> list[tuple[str, str]]:
    """Each question with the answer that followed it; tool results are left out."""
    pairs: list[list[str]] = []
    for message in history:
        if message["role"] == "user":
            pairs.append([message["content"], ""])
        elif message["role"] == "assistant" and pairs:
            pairs[-1][1] = f"{pairs[-1][1]}\n\n{message['content']}".strip()
    return [(question, answer) for question, answer in pairs]


class Turn:
    """One question and its answer, recorded apart from any window."""
    def __init__(self, identifier: int, message: dict):
        self.id = identifier
        self.message = message
        self.spoken = bool(message.get("audio"))
        self.question = "" if self.spoken else message.get("text", "").strip()
        self.replay = message.get("of") if message.get("type") == "replay" else None
        self.flag = threading.Event()  # set by an explicit cancel
        self.heard: asyncio.Task | None = None  # a spoken question's transcription
        self.waited = False  # queued behind another turn
        self.ends = False  # a wake conversation's goodbye, taken when its turn comes
        self.state = "queued"  # queued, running, done, cancelled, failed, ignored
        self.answer = ""
        self.detail = ""
        self.error = ""
        self.away = False  # finished with no window attached
        self.loaded = False  # an exchange of the chat this conversation carries on (ADR 0146)
        self.kept = False  # its exchange is in the history
        # The question its reply asked the user, with the answer once given (ADR 0159).
        self.asking: dict | None = None

    def snapshot(self) -> dict:
        snapshot = {"id": self.id, "user": self.question, "answer": self.answer, "state": self.state,
                    "detail": self.detail, "error": self.error, "away": self.away}
        if self.asking is not None:
            snapshot["question"] = self.asking  # with its answer once given (ADR 0159)
        return snapshot


class Conversation:
    """The voice conversation: every heard message, the running summary and
    recent turns. A window attaches to watch and hear it, and detaching never
    stops a reply; the reply's text is kept, and its audio waits for nobody.
    While saving is on, its exchanges are also saved as a chat in Chat
    history, or added to the chat it carries on (ADR 0146)."""

    def __init__(self, lease: dict):
        self.workspace, self.engine, self.voice = lease["workspace"], lease["engine"], lease["voice"]
        # Every message of every answered question, numbered, and the running summary (ADR 0141).
        self.history: list[dict] = []
        self.summary: dict | None = None
        self.turns: dict[int, Turn] = {}
        self.queue: deque[Turn] = deque()
        self.current: Turn | None = None
        self.task: asyncio.Task | None = None
        self.runner: asyncio.Task | None = None
        self.socket = None  # the attached window's sender
        self.playback = PlaybackWindow()
        self.last_id = 0
        self.successor: dict | None = None  # another look, model or chat, once the work in progress is done
        self.detached_at = time.monotonic()
        self.ended = False
        self.chat: str | None = None  # its chat in Chat history, once saved or when carried on
        self.title = ""  # the title of the chat it carries on
        self.continued = False
        # Read when it starts: turning saving off stops it at once, turning it
        # on saves the next conversation (ADR 0146).
        self.saving = saving_enabled()
        # The question the reply in progress asked the user, and that turn
        # (ADR 0159): the next thing the user says or taps answers it.
        self.question = None
        self.questioner: Turn | None = None

    def pose(self, turn: Turn, event: dict):
        """Called on the reply's thread when it asks the user: what it waits on."""
        from .chat_replies import Question
        question = Question(event)
        self.question, self.questioner = question, turn
        if turn.flag.is_set():
            question.close()
        return question

    def answering(self):
        """The question waiting for the user's answer, if any."""
        question = self.question
        if question is None or question.closed or self.questioner is not self.current:
            return None
        return question

    def close_question(self, turn: Turn | None = None) -> None:
        """Stop waiting for an answer: the reply was stopped, failed or ended."""
        if self.question is not None and (turn is None or self.questioner is turn):
            self.question.close()

    async def load(self, chat: str | None) -> None:
        """Start afresh, or carry on a chat from Chat history: its messages and
        summary become this conversation's, and its last exchanges show as
        turns. A chat deleted meanwhile starts afresh."""
        self.history, self.summary, self.chat, self.title, self.continued = [], None, None, "", False
        if chat is None:
            return
        session, history, summary = await asyncio.to_thread(stored_chat, chat, self.workspace)
        if session is None:
            return
        self.chat, self.title, self.continued = chat, session["title"], True
        self.history, self.summary = history[-HISTORY_LIMIT:], summary
        # Below zero, so they never take an id the window numbers its questions with.
        shown = exchanges(history)[-KEPT_TURNS:]
        for index, (question, answer) in enumerate(shown):
            turn = Turn(index - len(shown), {"text": question})
            turn.answer, turn.state, turn.loaded, turn.kept = answer, "done", True, True
            self.turns[turn.id] = turn

    def carries_on(self, lease: dict) -> bool:
        """The lease asks for this conversation: the same workspace, and no other chat."""
        return lease["workspace"] == self.workspace and lease.get("chat") in (None, self.chat)

    def matches(self, lease: dict) -> bool:
        return self.carries_on(lease) and (self.engine, self.voice) == (lease["engine"], lease["voice"])

    async def switch(self, lease: dict) -> None:
        """Take another look or model, or start the conversation the lease asks
        for. A new look or model keeps the conversation and its chat, now that
        it is saved (ADR 0146, amending ADR 0071)."""
        carried = self.carries_on(lease)
        self.workspace, self.engine, self.voice = lease["workspace"], lease["engine"], lease["voice"]
        if carried:
            return
        if lease.get("chat") is not None:
            self.turns.clear()  # another chat's exchanges take their place
        await self.load(lease.get("chat"))
        self.saving = saving_enabled()

    def busy(self) -> bool:
        return self.current is not None or bool(self.queue)

    def snapshot(self) -> dict:
        shown = [turn.snapshot() for turn in self.turns.values() if turn.replay is None and turn.state not in {"ignored", "answer"}]
        return {"workspace": self.workspace, "engine": self.engine, "voice": self.voice,
                "attached": self.socket is not None, "busy": self.busy(), "last_id": self.last_id,
                "turns": shown[-KEPT_TURNS:], "chat": self.carried(), "saving": self.saving}

    def carried(self) -> dict | None:
        """The chat from Chat history this conversation carries on, if any."""
        return {"id": self.chat, "title": self.title} if self.continued else None

    def ready(self, resumed: bool) -> dict:
        snapshot = self.snapshot()
        return {"type": "ready", "workspace": self.workspace, "engine": self.engine, "voice": self.voice,
                "effect": VOICES[self.voice][1], "resumed": resumed, "last_id": self.last_id,
                "busy": snapshot["busy"], "turns": snapshot["turns"], "chat": snapshot["chat"],
                "saving": snapshot["saving"]}

    def remember(self, entries: list[dict]) -> None:
        """Add an exchange to the history and, while saving is on, to its chat
        in Chat history. Runs off the event loop, except for a stopped reply."""
        for entry in entries:
            self.history.append({**entry, "seq": (self.history[-1]["seq"] if self.history else 0) + 1})
        del self.history[:max(0, len(self.history) - HISTORY_LIMIT)]
        self.saving = self.saving and saving_enabled()
        if not self.saving:
            return
        from . import chat_store
        try:
            if self.chat is None:
                self.chat = chat_store.create_session(self.workspace, kind="voice")
            for entry in entries:
                chat_store.append_message(self.chat, role=entry["role"], content=entry["content"],
                                          tool_name=entry.get("tool_name"), tool_args=entry.get("tool_args"),
                                          workspace=self.workspace)
            if chat_store.last_seq(self.chat, self.workspace) != self.history[-1]["seq"]:
                # Chat added to this chat meanwhile: carry on from what it holds.
                self.history = stored_chat(self.chat, self.workspace)[1][-HISTORY_LIMIT:]
        except sqlite3.IntegrityError:
            # The user deleted the chat: nothing more of this conversation is saved.
            self.saving = False
        except (sqlite3.Error, OSError) as error:
            log.warning("Echo could not save its conversation in Chat history: %s", error)

    def remember_summary(self) -> None:
        if self.saving and self.chat is not None and self.summary is not None and saving_enabled():
            from . import chat_store
            try:
                chat_store.set_summary(self.chat, self.summary.get("text") or "", self.summary.get("covers") or 0,
                                       self.summary.get("retry_after") or 0, workspace=self.workspace)
            except (sqlite3.Error, OSError) as error:
                log.warning("Echo could not save its conversation summary: %s", error)

    def keep_partial(self, turn: Turn) -> None:
        """A reply stopped or failed partway is kept as far as it got, as Chat keeps one."""
        if turn.kept or turn.replay is not None or not turn.question.strip() or not turn.answer.strip():
            return
        turn.kept = True
        self.remember([{"role": "user", "content": turn.question}, {"role": "assistant", "content": turn.answer.strip()[:3000]}])

    def attach(self, send) -> None:
        self.socket = send
        # The new window counts the current reply's audio from here.
        self.playback.release()
        self.playback = PlaybackWindow()

    def detach(self, send) -> None:
        if self.socket is not send:
            return
        self.socket = None
        self.detached_at = time.monotonic()
        # Nobody plays the rest, so generation stops waiting for playback.
        self.playback.release()

    async def send(self, event: dict) -> None:
        send = self.socket
        if send is None:
            return
        try:
            await send(event)
        except Exception:  # noqa: BLE001 — a window that went away; the reply carries on
            self.detach(send)

    async def emit(self, turn: Turn, event: dict) -> None:
        """Record a turn's progress, then show it to the attached window."""
        kind = event["type"]
        if kind == "user":
            turn.question = event["text"]
        elif kind == "text":
            turn.answer += event["text"]
        elif kind in {"state", "tool"}:
            turn.detail = event.get("text", "")
        elif kind == "audio":
            window = self.playback
            if self.socket is None:
                return
            await window.reserve(event["data"])
            if window is not self.playback or self.socket is None:
                return  # its window went away while it waited
        await self.send({**event, "id": turn.id})

    def record(self, turn: Turn) -> None:
        self.turns[turn.id] = turn
        self.last_id = turn.id
        finished = [key for key, kept in self.turns.items() if kept.state not in {"queued", "running"}]
        for key in finished[:max(0, len(self.turns) - KEPT_TURNS)]:
            del self.turns[key]

    def ahead_of(self, turn: Turn) -> bool:
        """Another question is being answered, or waits before this one."""
        if self.current is not None and self.current is not turn:
            return True
        waiting = list(self.queue)
        return (waiting.index(turn) if turn in waiting else len(waiting)) > 0

    def drop(self, turn: Turn, state: str) -> None:
        turn.state = state
        if turn in self.queue:
            self.queue.remove(turn)

    async def intake(self, turn: Turn) -> None:
        """A new question waits behind the one being answered instead of
        replacing it; a spoken one is transcribed meanwhile, so noise, Echo
        hearing itself and "stop" are known before its turn."""
        if len(self.queue) >= QUEUE_LIMIT:
            await self.send({"type": "error", "id": turn.id, "error": "Echo already has questions waiting. Ask again once it answers them."})
            self.last_id = turn.id
            return
        self.record(turn)
        # While the reply waits for an answer, a typed message answers it; a
        # spoken one does once it is heard (ADR 0159).
        if not turn.spoken and turn.replay is None and await self.answers(turn, turn.question):
            return
        turn.waited = self.busy()
        self.queue.append(turn)
        if turn.waited and self.answering() is None:
            await self.send({"type": "queued", "id": turn.id, "position": len(self.queue)})
        if turn.spoken:
            turn.heard = asyncio.create_task(self.listen(turn))
        elif turn.replay is None and not await self.decide(turn, turn.question):
            return
        self.start()

    async def listen(self, turn: Turn) -> str | None:
        """Transcribe a spoken question once the worker is free, then decide
        whether it is one. A failed transcription is tried once more."""
        text = ""
        try:
            content = await audio_content(turn.message["audio"])
            for attempt in range(2):
                try:
                    text = ""
                    async with sessions.speech:
                        if turn.flag.is_set():
                            return None
                        async for event in sessions.worker.stream("Perform ASR.", content):
                            if event["type"] == "text":
                                text += event["text"]
                    break
                except (VoiceMemoryError, httpx.HTTPError, OSError, ValueError):
                    if attempt or turn.flag.is_set():
                        raise
        except (VoiceMemoryError, httpx.HTTPError, OSError, ValueError):
            if turn.flag.is_set():
                return None
            self.drop(turn, "failed")
            turn.error = "Echo couldn't hear that question. Please ask again."
            await self.send({"type": "error", "id": turn.id, "error": turn.error})
            return None
        return turn.question if await self.decide(turn, text.strip()) else None

    def echo_of_answer(self, question: str) -> bool:
        answers = [kept.answer for kept in self.turns.values() if kept.answer and kept.replay is None][-2:]
        # The question Echo asked was spoken aloud too (ADR 0159).
        asked = self.questioner.asking if self.questioner is not None and self.answering() is not None else None
        if asked:
            answers.append(spoken_question(asked))
        return any(echoes(question, answer) for answer in answers)

    async def answers(self, turn: Turn, text: str) -> bool:
        """Whether what the user said or typed answers the question the reply
        waits on (ADR 0159). A stop said alone still stops the reply."""
        question = self.answering()
        voiced = turn.spoken or turn.message.get("wake") is True
        if question is None or turn is self.current or turn.replay is not None or (voiced and wake.stop_phrase(text)):
            return False
        if not question.give(" ".join(text.split())[:2000]):
            return False
        self.drop(turn, "answer")
        await self.send({"type": "answering", "id": turn.id, "of": self.questioner.id})
        return True

    async def decide(self, turn: Turn, question: str) -> bool:
        """Whether a question is asked. Noise and Echo's own voice are
        ignored; a stop said alone stops the reply and what waits (ADR 0145);
        a wake conversation's goodbye ends it (ADR 0137); while the reply waits
        for the user's answer, what they say is that answer (ADR 0159)."""
        if turn.spoken and (unheard(question) or len(question) > 2000
                            or (turn.message.get("overlap") is True and self.echo_of_answer(question))):
            reason = "echo" if not unheard(question) and len(question) <= 2000 else "unheard"
            self.drop(turn, "ignored")
            await self.send({"type": "ignored", "id": turn.id, "reason": reason})
            return False
        if turn.spoken and await self.answers(turn, question):
            return False
        await self.emit(turn, {"type": "user", "text": question})
        voiced = turn.spoken or turn.message.get("wake") is True
        stop = voiced and wake.stop_phrase(question)
        end = turn.message.get("wake") is True and wake.end_phrase(question)
        if stop:
            for identifier in await self.cancel(keep=turn):
                await self.send({"type": "cancelled", "id": identifier})
            await self.send({"type": "stopped", "id": turn.id})
        if end and (stop or not self.ahead_of(turn)):
            self.drop(turn, "ignored")
            await self.send({"type": "end", "id": turn.id})
            return False
        if stop:
            self.drop(turn, "ignored")
            return False
        turn.ends = end
        return True

    def start(self) -> None:
        if self.runner is None or self.runner.done():
            self.runner = asyncio.create_task(self.run())

    async def run(self) -> None:
        """Answer queued questions in order, whichever window is attached."""
        while not self.ended:
            while self.queue and not self.ended:
                turn = self.queue.popleft()
                if turn.state != "queued":
                    continue
                self.current, turn.state = turn, "running"
                self.playback.release()
                self.playback = PlaybackWindow()
                if turn.waited:
                    await self.send({"type": "start", "id": turn.id})
                self.task = asyncio.create_task(self.answer(turn))
                await asyncio.wait({self.task})
                self.task = self.current = None
                if turn.state == "running":
                    turn.state = "done"
                turn.away = self.socket is None and turn.state in {"done", "failed"}
            if self.successor and not self.ended:
                # Another look, model, workspace or chat was chosen meanwhile (ADR 0071, 0146).
                lease, self.successor = self.successor, None
                await self.switch(lease)
                await self.send(self.ready(False))
            if self.socket is None and not sessions.claimed and not self.queue and not self.ended:
                await sessions.worker.stop()  # held only while a window or a question needs it
            if not self.queue:
                return

    async def cancel(self, identifier: int | None = None, *, keep: Turn | None = None) -> list[int]:
        """The user's stop: the current reply and every waiting question, or
        just the turn named. Native inference stops with it, prefill included."""
        stopped = []
        for turn in list(self.queue):
            if turn is keep or (identifier is not None and turn.id != identifier):
                continue
            self.drop(turn, "cancelled")
            turn.flag.set()
            if turn.heard and not turn.heard.done() and turn.heard is not asyncio.current_task():
                turn.heard.cancel()
            stopped.append(turn.id)
        current, task = self.current, self.task
        if current is not None and current is not keep and (identifier is None or current.id == identifier):
            current.flag.set()
            self.close_question(current)  # a reply waiting for an answer stops at once (ADR 0159)
            current.state = "cancelled"
            if task and task is not asyncio.current_task():
                task.cancel()
                await asyncio.wait({task})
            stopped.append(current.id)
        # A window connecting meanwhile is booting the worker for itself.
        if (identifier is None or stopped) and (self.socket is not None or not sessions.claimed):
            await sessions.worker.stop()
        return stopped

    async def end(self) -> None:
        """The conversation ends, by the user's choice: the API forgets it, and
        what was saved stays in Chat history (ADR 0146)."""
        if self.ended:
            return
        self.ended = True
        if sessions.conversation is self:
            sessions.conversation = None
        await self.cancel()
        tasks = [task for task in (self.runner, *(turn.heard for turn in self.turns.values()))
                 if task and not task.done() and task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks)
        self.history, self.turns, self.summary = [], {}, None

    async def speak(self, text: str, emit) -> None:
        """Read text aloud in bounded phrases, unless no window would hear it."""
        speaker = VOICES[self.voice][0]
        while text.strip():
            end = min(len(text), 240)
            if end < len(text):
                end = text.rfind(" ", 0, end) or end
                if end < 1:
                    end = 240
            part, text = text[:end], text[end:]
            if self.socket is None:
                continue  # the text is kept; nobody would hear the audio
            async with sessions.speech:
                async for audio in sessions.worker.stream(f"Perform TTS. Use the {speaker} voice.", part):
                    if audio["type"] == "audio":
                        await emit(audio)

    async def answer(self, turn: Turn) -> None:
        flag, budget = turn.flag, None

        async def emit(event):
            if flag.is_set():
                return
            await self.emit(turn, event)
            # An active long reply is healthy. Bound stalls rather than its
            # total generation/playback time (ADR 0092).
            if budget is not None and event["type"] != "provision":
                budget.reschedule(asyncio.get_running_loop().time() + TURN_TIMEOUT)
        try:
            async with asyncio.timeout(TURN_TIMEOUT) as budget:
                if turn.heard is not None:
                    if not turn.heard.done():
                        await emit({"type": "state", "state": "transcribing"})
                    if await turn.heard is None:
                        if turn.state == "running":
                            turn.state = "ignored"
                        return
                if turn.ends:
                    await emit({"type": "end"})
                    turn.state = "ignored"
                    return
                if turn.replay is not None:
                    original = self.turns.get(turn.replay)
                    if original is None or not original.answer.strip():
                        raise VoiceMemoryError("That answer is no longer available.")
                    await self.speak(original.answer, emit)
                    await emit({"type": "done"})
                    return
                question = turn.question
                await emit({"type": "state", "state": "thinking"})
                answer = ""
                calls: list[dict] = []  # this reply's tool calls with their results
                speaker = VOICES[self.voice][0]
                if self.engine == "lfm":
                    evidence = await memory_prompt(question, self.workspace)
                    recent = [{"role": m["role"], "content": m["content"]}
                              for m in self.history if m["role"] in ("user", "assistant")][-6:]
                    while recent and len(_json(recent)) > 3000:
                        recent = recent[2:]
                    context = _json(recent)
                    content = f"{evidence}\nRecent conversation (may be interrupted): {context}\nQuestion: {question}"
                    async with sessions.speech:
                        async for event in sessions.worker.stream(f"Respond with interleaved text and audio. Use the {speaker} voice.", content):
                            if event["type"] == "text":
                                answer += event["text"]
                            await emit(event)
                else:
                    pending = ""
                    provisioning = False
                    async for event in chat_events(question, list(self.history), self.workspace, flag, self.summary,
                                                   ask=lambda asked: self.pose(turn, asked)):
                        kind = event["type"]
                        if provisioning and kind in {"token", "tool_call"}:
                            budget.reschedule(asyncio.get_running_loop().time() + TURN_TIMEOUT)
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
                                await self.speak(phrase, emit)
                        elif kind == "tool_call":
                            calls.append(event)
                            await emit({"type": "tool", "name": event["name"],
                                        "text": tool_label(event["name"], event.get("args"))})
                        elif kind == "tool_result" and calls and "result" not in calls[-1]:
                            calls[-1] = {**calls[-1], "result": str(event.get("result", ""))[:TOOL_RESULT_CHARS]}
                        elif kind == "question":
                            # Echo asks, aloud and on screen, and waits; the next
                            # thing the user says or taps is the answer (ADR 0159).
                            if pending.strip():
                                await self.speak(pending, emit)
                                pending = ""
                            turn.asking = {key: event[key] for key in ("id", "question", "options", "allow_other")}
                            turn.detail = "Waiting for your answer"
                            await emit({"type": "question", "question_id": event["id"], "question": event["question"],
                                        "options": event["options"], "allow_other": event["allow_other"]})
                            await self.speak(spoken_question(event), emit)
                            # Waiting for the user is not a stalled reply.
                            budget.reschedule(asyncio.get_running_loop().time() + ANSWER_WAIT + TURN_TIMEOUT)
                        elif kind == "answer":
                            text = event.get("text")
                            if turn.asking is not None:
                                turn.asking = {**turn.asking, "answer": text}
                            await emit({"type": "answered", "question_id": event["id"], "text": text})
                            await emit({"type": "state", "state": "thinking"})
                        elif kind == "summary":
                            # The summary stands for messages already answered,
                            # so it holds whether or not this reply completes.
                            self.summary = {key: event.get(key) for key in ("text", "covers", "retry_after")}
                            await asyncio.to_thread(self.remember_summary)
                            await emit({"type": "state", "state": "thinking"})
                        elif kind == "analysis":
                            await emit({"type": "state", "state": "analyzing", "text": event["text"]})
                        elif kind in {"download_start", "download_progress", "download_complete", "load_start"}:
                            if kind == "download_start" and not provisioning:
                                budget.reschedule(asyncio.get_running_loop().time() + CHAT_SETUP_TIMEOUT)
                                provisioning = True
                            stage = "download" if kind in {"download_start", "download_progress"} else "load"
                            progress = {"type": "provision", "stage": stage}
                            for name in ("downloaded", "total"):
                                value = event.get(name)
                                if type(value) is int and 0 <= value <= 1_000_000_000_000:
                                    progress[name] = value
                            await emit(progress)
                    if pending.strip():
                        await self.speak(pending, emit)
                if flag.is_set():
                    self.keep_partial(turn)
                    return
                if answer.strip():
                    # A completed reply joins the history whether or not it was
                    # played to the end: its text is on screen (ADR 0145).
                    tools = [{"role": "tool", "content": call["result"], "tool_name": call["name"],
                              "tool_args": call.get("args") or {}}
                             for call in calls if "result" in call]
                    turn.kept = True
                    await asyncio.to_thread(self.remember, [{"role": "user", "content": question}, *tools,
                                                            {"role": "assistant", "content": answer[:3000]}])
                turn.state = "done"
                await emit({"type": "done"})
        except asyncio.CancelledError:
            self.keep_partial(turn)
            raise
        except Exception as error:  # noqa: BLE001 — a failed reply says so and can be asked again
            budget = None
            self.keep_partial(turn)
            turn.state = "failed"
            turn.error = (str(error) if isinstance(error, VoiceMemoryError)
                          else "Echo couldn't finish this answer. Try again, or ask a shorter question.")
            await emit({"type": "error", "error": turn.error})
            await sessions.worker.stop()
        finally:
            # A reply that ends while its question waits stops waiting with it (ADR 0159).
            self.close_question(turn)


@router.get("/conversation")
def conversation_status(request: Request):
    """What a returning window shows before it reconnects: the recent turns,
    whether a reply is still being written and whether a window holds it."""
    check_setup_origin(request)
    conversation = sessions.current()
    return {"conversation": conversation.snapshot() if conversation else None}


@router.post("/conversation/end")
async def conversation_end(request: Request):
    """End the conversation from a window that is not connected to it. The
    window that holds it ends it over its own connection."""
    check_setup_origin(request, mutate=True)
    await empty_setup_body(request)
    conversation = sessions.current()
    if conversation is not None and (conversation.socket is not None or sessions.claimed):
        raise HTTPException(409, "Voice is open in another window. End it there.")
    if conversation is not None:
        await conversation.end()
    return {"conversation": None}


def valid_turn(message: dict, last_id: int) -> bool:
    identifier = message.get("id")
    if type(identifier) is not int or identifier <= last_id:
        return False
    if message.get("type") == "replay":
        return type(message.get("of")) is int and set(message) <= {"type", "id", "of"}
    text, audio = message.get("text", ""), message.get("audio", "")
    return (type(message.get("wake", False)) is bool and type(message.get("overlap", False)) is bool
            and isinstance(text, str) and isinstance(audio, str)
            and bool(text.strip()) != bool(audio) and len(text) <= 2000)


@router.websocket("/stream")
async def stream(websocket: WebSocket):
    if not check_socket(websocket):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    conversation: Conversation | None = None
    claimed = False
    send_lock = asyncio.Lock()

    async def send(event):
        async with send_lock:
            await asyncio.wait_for(websocket.send_json(event), 5)

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
        sessions.claimed = claimed = True
        # A window that comes back picks up the conversation it left, its
        # reply included (ADR 0145). Another look or model carries it on; an
        # idle one in another workspace, or asked to carry on another chat,
        # gives way to a new conversation (ADR 0146); a busy one finishes first.
        conversation = sessions.current()
        if conversation is not None and not conversation.matches(lease) and not conversation.busy():
            if conversation.carries_on(lease):
                await conversation.switch(lease)
            else:
                await conversation.end()
                conversation = None
        resumed = conversation is not None
        if conversation is None:
            conversation = sessions.conversation = Conversation(lease)
            await conversation.load(lease.get("chat"))
        elif not conversation.matches(lease):
            conversation.successor = lease
        await send({"type": "connecting"})
        # A closed companion must cancel a cold model load. HTTP/WebSocket
        # disconnects aren't observed while awaiting only the worker's
        # readiness probe.
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
        conversation.attach(send)
        sessions.claimed = claimed = False
        await send(conversation.ready(resumed))
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), 120)
            except TimeoutError:
                if conversation.busy():
                    continue
                raise
            if len(raw) > MAX_MESSAGE:
                raise VoiceMemoryError("Voice message exceeds its size limit.")
            message = json.loads(raw)
            if not isinstance(message, dict):
                raise VoiceMemoryError("Invalid voice message.")
            kind = message.get("type")
            if kind == "cancel":
                identifier = message.get("id")
                if identifier is not None and type(identifier) is not int:
                    raise VoiceMemoryError("Invalid voice control.")
                stopped = await conversation.cancel(identifier)
                for turn_id in stopped or [identifier if identifier is not None else conversation.last_id]:
                    await send({"type": "cancelled", "id": turn_id})
            elif kind == "played":
                pass  # A completed reply is kept when it completes (ADR 0145).
            elif kind == "playback":
                # A finished or cancelled turn's late acknowledgement cannot
                # release the next turn's credit.
                current = conversation.current
                if current is not None and message.get("id") == current.id:
                    conversation.playback.acknowledge(message.get("samples"))
            elif kind in {"turn", "replay"}:
                if not valid_turn(message, conversation.last_id):
                    raise VoiceMemoryError("Invalid voice turn.")
                await conversation.intake(Turn(message["id"], message))
            elif kind == "answer":
                # A choice tapped on the question Echo asked (ADR 0159). A
                # question no longer waiting ignores it: the answer came first.
                text, identifier = message.get("text"), message.get("question_id")
                if not isinstance(identifier, str) or not isinstance(text, str) or not text.strip() or len(text) > 2000:
                    raise VoiceMemoryError("Invalid voice answer.")
                question = conversation.answering()
                if question is not None and question.id == identifier:
                    question.give(" ".join(text.split()))
            elif kind == "end":
                await conversation.end()
                break
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
        if claimed:
            sessions.claimed = False
        if conversation is not None and not conversation.ended:
            conversation.detach(send)
            if conversation.socket is None and not conversation.busy():
                if not any(turn.state != "ignored" and not turn.loaded for turn in conversation.turns.values()):
                    # Nothing was asked, so there is nothing to come back to.
                    conversation.ended = True
                    if sessions.conversation is conversation:
                        sessions.conversation = None
                if not sessions.claimed:
                    await sessions.worker.stop()  # held again when a window or a question needs it
        with contextlib.suppress(Exception):
            await websocket.close()


# ── "Hey Echo" (ADR 0137) ─────────────────────────────────────────────────
# The desktop app's hidden listener sends the first 2 s of each burst of speech
# here; a low-priority worker process answers yes or no. Probes stay in memory.
WAKE_PROBE_TEXT = 4 * ((44 + int(wake.PROBE_SECONDS * wake.RATE) * 2 + 2) // 3)
WAKE_UTTERANCE_TEXT = 4 * ((44 + wake.UTTERANCE_SECONDS * wake.RATE * 2 + 2) // 3)
WAKE_ANSWER_TIMEOUT = 20
WAKE_FOLLOW = 35  # seconds a positive probe waits for the rest of its utterance
WAKE_START_TIMEOUT = 60


class WakeCancelled(Exception):
    pass


class WakeSetup:
    """The model download behind Turn on, with real byte progress."""
    def __init__(self):
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.cancelled = threading.Event()
        self.state = {"stage": "idle", "downloaded": 0, "total": wake.TOTAL_BYTES, "problem": None}

    def snapshot(self) -> dict:
        with self.lock:
            return {**self.state, "busy": self.thread is not None}

    def start(self) -> dict:
        with self.lock:
            if self.thread is None:
                self.cancelled.clear()
                self.state = {"stage": "downloading", "downloaded": 0, "total": wake.TOTAL_BYTES, "problem": None}
                self.thread = threading.Thread(target=self._run, daemon=True, name="wake-model-download")
                self.thread.start()
        return self.snapshot()

    def _progress(self, phase, done=0, total=0, unit="bytes") -> None:
        if self.cancelled.is_set():
            raise WakeCancelled()
        if phase == "downloading_transcription_model":
            with self.lock:
                self.state["downloaded"] = max(0, min(wake.TOTAL_BYTES, int(done)))

    def _run(self) -> None:
        stage, problem = "ready", None
        try:
            wake.download(progress=self._progress)
        except WakeCancelled:
            stage = "cancelled"
        except ValueError as error:
            stage, problem = "error", "disk" if "space" in str(error) else "damaged" if "Damaged" in str(error) else "download"
        except Exception:  # noqa: BLE001 — network and file errors end as a retryable failure
            stage, problem = "error", "download"
        with self.lock:
            self.state.update(stage=stage, problem=problem,
                              downloaded=wake.TOTAL_BYTES if stage == "ready" else self.state["downloaded"])
            self.thread = None

    def cancel(self) -> dict:
        self.cancelled.set()
        return self.snapshot()


class WakeWorker:
    """The owned worker process (imprint/wake_worker.py), one at a time."""
    def __init__(self):
        self.process: subprocess.Popen | None = None
        self.events: asyncio.Queue | None = None

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self.events = asyncio.Queue()
        environment = voice_runtime.child_environment()
        environment.update(IMPRINT_DATA_DIR=str(config.get_data_dir().resolve()), OMP_NUM_THREADS="1")
        options = voice_runtime.child_options()
        if os.name == "nt":
            options["creationflags"] |= subprocess.BELOW_NORMAL_PRIORITY_CLASS
        self.process = subprocess.Popen(
            [sys.executable, "-m", "imprint.wake_worker"], cwd=Path(__file__).resolve().parent.parent,
            env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **options)
        process, events = self.process, self.events

        def read() -> None:
            try:
                for line in iter(lambda: process.stdout.readline(1_000_000), b""):
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(event, dict):
                        loop.call_soon_threadsafe(events.put_nowait, event)
            except (OSError, ValueError):
                pass
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(events.put_nowait, None)

        threading.Thread(target=read, daemon=True, name="wake-worker-output").start()
        try:
            first = await asyncio.wait_for(events.get(), WAKE_START_TIMEOUT)
        except TimeoutError:
            first = None
        if not first or first.get("ready") is not True:
            await self.stop()
            raise VoiceMemoryError((first or {}).get("error") or "The wake listener could not start.")

    async def ask(self, message: dict, timeout: float) -> dict:
        assert self.process is not None and self.process.stdin is not None and self.events is not None
        line = (json.dumps(message) + "\n").encode()
        stdin = self.process.stdin

        def write() -> None:
            stdin.write(line)
            stdin.flush()
        try:
            await asyncio.to_thread(write)
            while True:
                event = await asyncio.wait_for(self.events.get(), timeout)
                if event is None or ("error" in event and "id" not in event):
                    raise VoiceMemoryError("The wake listener stopped.")
                if event.get("id") == message["id"]:
                    return event
        except (OSError, ValueError, TimeoutError):
            raise VoiceMemoryError("The wake listener stopped.") from None

    async def stop(self) -> None:
        process, self.process = self.process, None
        if not process:
            return
        if process.stdin:
            with contextlib.suppress(OSError):
                process.stdin.close()
        try:
            await asyncio.to_thread(process.wait, timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            await asyncio.to_thread(process.wait)
        if process.stdout:
            with contextlib.suppress(OSError):
                process.stdout.close()


class WakeSessions:
    def __init__(self):
        self.leases: dict[str, dict] = {}
        self.active = False
        self.worker: WakeWorker | None = None

    def issue(self, origin: str, battery: bool) -> str:
        now = time.monotonic()
        self.leases = {key: value for key, value in self.leases.items() if value["expires"] > now}
        if len(self.leases) >= 4:
            raise HTTPException(429, "Too many pending wake sessions")
        token = secrets.token_urlsafe(32)
        self.leases[token] = {"origin": origin, "battery": battery, "expires": now + 30}
        return token

    def consume(self, token: str, origin: str) -> dict | None:
        lease = self.leases.pop(token, None)
        return lease if lease and lease["expires"] > time.monotonic() and lease["origin"] == origin else None


wake_setup = WakeSetup()
wake_sessions = WakeSessions()


def wake_idle_seconds() -> int:
    try:
        return max(5, min(120, int(config_schema.resolve("voice.wake.idle_seconds")[0])))
    except (TypeError, ValueError):
        return 15


def wake_status() -> dict:
    return {"installed": wake.installed(), "model": "tiny.en", "bytes": wake.TOTAL_BYTES,
            "setup": wake_setup.snapshot(), "listening": wake_sessions.active, "idle_seconds": wake_idle_seconds()}


@router.get("/wake/status")
def wake_status_route(request: Request):
    check_setup_origin(request)
    return wake_status()


@router.post("/wake/setup")
async def wake_setup_start(request: Request):
    """Turn on's download of the pinned wake model; never started by itself."""
    check_setup_origin(request, mutate=True)
    await empty_setup_body(request)
    if not wake.installed():
        wake_setup.start()
    return wake_status()


@router.post("/wake/setup/cancel")
async def wake_setup_cancel(request: Request):
    check_setup_origin(request, mutate=True)
    await empty_setup_body(request)
    wake_setup.cancel()
    return wake_status()


@router.post("/wake/sessions")
async def create_wake_session(request: Request):
    origin = request.headers.get("origin", f"http://{request.headers.get('host', '')}")
    if (not request.client or not loopback_address(request.client.host)
            or origin != f"http://{request.headers.get('host', '')}" or not local_origin(origin)):
        raise HTTPException(403, "Listening requires the local dashboard origin")
    body = await request.body()
    if len(body) > 256:
        raise HTTPException(413, "Wake session request is too large")
    try:
        payload = json.loads(body or b"{}")
        battery = payload.get("battery", False)
        if type(battery) is not bool:
            raise ValueError()
    except (ValueError, AttributeError):
        raise HTTPException(400, "Invalid wake session request") from None
    if not wake.installed():
        raise HTTPException(409, "Turn on “Hey Echo” to download its model first")
    return {"token": wake_sessions.issue(origin, battery), "expires_in": 30}


@router.websocket("/wake/stream")
async def wake_stream(websocket: WebSocket):
    if not check_socket(websocket):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    owned = False
    worker: WakeWorker | None = None

    async def send(event):
        await asyncio.wait_for(websocket.send_json(event), 5)

    try:
        auth = await asyncio.wait_for(websocket.receive_text(), 5)
        payload = json.loads(auth) if len(auth) <= 512 else None
        lease = wake_sessions.consume(payload.get("token", ""), websocket.headers["origin"]) \
            if isinstance(payload, dict) and isinstance(payload.get("token"), str) else None
        if not lease:
            raise VoiceMemoryError("Wake session expired. Start listening again.")
        if wake_sessions.active:
            raise VoiceMemoryError("Another window is already listening.")
        wake_sessions.active = owned = True
        worker = wake_sessions.worker = WakeWorker()
        await worker.start()
        budget = wake.Budget(wake.BUDGET / 2 if lease["battery"] else wake.BUDGET)
        positives: dict[int, float] = {}
        last = 0
        await send({"type": "ready"})
        while True:
            raw = await websocket.receive_text()
            if len(raw) > WAKE_UTTERANCE_TEXT + 256:
                raise VoiceMemoryError("Wake message exceeds its size limit.")
            message = json.loads(raw)
            if not isinstance(message, dict):
                raise VoiceMemoryError("Invalid wake message.")
            kind, identifier, audio = message.get("type"), message.get("id"), message.get("audio")
            if kind == "power" and type(message.get("battery")) is bool:
                budget.per_minute = wake.BUDGET / 2 if message["battery"] else wake.BUDGET
                continue
            if type(identifier) is not int or not isinstance(audio, str):
                raise VoiceMemoryError("Invalid wake message.")
            if kind == "probe":
                quiet = message.get("quiet", 0)
                if (identifier <= last or len(audio) > WAKE_PROBE_TEXT
                        or type(quiet) not in (int, float) or not 0 <= quiet <= 86400):
                    raise VoiceMemoryError("Invalid wake probe.")
                last = identifier
                if not budget.allows(quiet):
                    await send({"type": "probe", "id": identifier, "wake": False, "skipped": True})
                    continue
                answer = await worker.ask({"id": identifier, "kind": "probe", "audio": audio}, WAKE_ANSWER_TIMEOUT)
                cost = answer.get("cpu")
                budget.spend(cost if type(cost) in (int, float) else 0.5)
                woke = answer.get("wake") is True
                if woke:
                    positives = {key: value for key, value in positives.items()
                                 if time.monotonic() - value <= WAKE_FOLLOW}
                    positives[identifier] = time.monotonic()
                await send({"type": "probe", "id": identifier, "wake": woke})
            elif kind == "utterance":
                started = positives.pop(identifier, None)
                if started is None or time.monotonic() - started > WAKE_FOLLOW or len(audio) > WAKE_UTTERANCE_TEXT:
                    raise VoiceMemoryError("Only an utterance that woke Echo is transcribed.")
                answer = await worker.ask({"id": identifier, "kind": "utterance", "audio": audio}, 60)
                text = answer.get("text")
                await send({"type": "question", "id": identifier, "text": text[:2000] if isinstance(text, str) else ""})
            else:
                raise VoiceMemoryError("Unknown wake message.")
    except (WebSocketDisconnect, TimeoutError, RuntimeError):
        pass
    except VoiceMemoryError as error:
        with contextlib.suppress(Exception):
            await send({"type": "error", "error": str(error)})
    except (ValueError, TypeError, AttributeError):
        with contextlib.suppress(Exception):
            await send({"type": "error", "error": "Wake session unavailable. Start listening again."})
    finally:
        if worker:
            await worker.stop()
        if owned:
            wake_sessions.active = False
            wake_sessions.worker = None
        with contextlib.suppress(Exception):
            await websocket.close()


async def shutdown() -> None:
    voice_setup.setup.cancel()
    wake_setup.cancel()
    sessions.leases.clear()
    if sessions.conversation is not None:
        await sessions.conversation.end()
    wake_sessions.leases.clear()
    await sessions.worker.stop()
    if wake_sessions.worker:
        await wake_sessions.worker.stop()
