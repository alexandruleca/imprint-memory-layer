"""Live peer sync over a WebSocket relay.

The single implementation of the sync protocol. Both front-ends drive the
same async-generator sessions, which yield event dicts:

- the CLI (``imprint sync``, rendered by :mod:`imprint.cli_sync`), and
- the dashboard API (``/api/sync/serve`` and ``/api/sync/receive`` as SSE).

The relay only pairs a provider and a consumer by room ID and forwards
frames between them (``cmd/relay.go``). Sessions are end-to-end encrypted
(:mod:`imprint.sync_crypto`): the relay sees the room ID, the two
key-exchange messages and the sizes and timing of ciphertext frames, never
the PIN, the devices or the data.

Handshake (protocol 2), consumer first::

    consumer → {"method": "PAKE", "version": 2, "msg": <SPAKE2 A>}      text
    provider → {"method": "PAKE", "version": 2, "msg": <SPAKE2 B>}      text
    consumer → {"method": "HELLO", hostname, user, os, fingerprint}    sealed
    provider → {"status": 200, "body": {"pull": …, "push": …}}         sealed

A sealed frame that fails to open means a wrong PIN (at HELLO) or a frame
altered in transit; either ends the session. Before the session is sealed a
peer may send a text notice ``{"status": N, "body": …, "version": 2}``,
e.g. for a wrong PIN; notices are unauthenticated and only ever end a
session.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import re
import secrets
import socket
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncGenerator, Callable

import websockets
from websockets.asyncio.client import connect as ws_connect

from . import config
from .sync_crypto import PROTOCOL, Cipher, FrameError, KeyExchange, KeyExchangeError

# ── Constants ─────────────────────────────────────────────────────

DEFAULT_RELAY_HOST = "imprint.alexandruleca.com"
DEFAULT_BATCH_SIZE = 500
EMBED_CHUNK = 32
PIN_CHARSET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
# A batch of 500 memories with 1024-dim vectors is roughly 15 MB of JSON.
MAX_FRAME_BYTES = 64 * 1024 * 1024

# Active sessions keyed by session_id, value is an asyncio.Event that
# gets set when cancellation is requested.
_sessions: dict[str, asyncio.Event] = {}

# Pending approvals keyed by session_id. Resolved by submit_approval() with
# one of: "accept" | "trust" | "reject". The serve_session generator awaits
# this before granting the consumer access. The CLI resolves it from a TTY
# prompt, the dashboard from /api/sync/approve.
_approvals: dict[str, asyncio.Future] = {}

# Refusals sent by a provider whose policy rejects a request.
REFUSED_PULL = "provider is serving receive-only: pulls are refused"
REFUSED_PUSH = "provider is serving send-only: pushes are refused"

OUTDATED = "update Imprint on both machines: live sync is now end-to-end encrypted"
TAMPERED = ("Sync stopped: a frame failed authentication (altered, dropped or replayed between the machines); "
            "nothing from it was stored")


class HandshakeError(Exception):
    """The peers could not set up an encrypted session."""


class PeerNotice(Exception):
    """The peer sent a plaintext notice instead of a sealed frame."""


# ── Relay addressing ──────────────────────────────────────────────

_ROOM_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


@dataclass(frozen=True)
class Relay:
    """Where a session meets its peer."""

    scheme: str  # "wss" or "ws"
    host: str    # host[:port]

    def url(self, room_id: str, role: str) -> str:
        return f"{self.scheme}://{self.host}/{room_id}?role={role}"

    def target(self, room_id: str) -> str:
        """What the other machine passes to ``imprint sync``: the bare room
        ID on the default relay, the full address on any other."""
        if (self.scheme, self.host) == ("wss", DEFAULT_RELAY_HOST):
            return room_id
        return f"{self.scheme}://{self.host}/{room_id}"

    def __str__(self) -> str:
        return f"{self.scheme}://{self.host}"


def _split_scheme(raw: str) -> tuple[str, str]:
    for prefix, scheme in (("wss://", "wss"), ("https://", "wss"), ("ws://", "ws"), ("http://", "ws")):
        if raw.startswith(prefix):
            return scheme, raw[len(prefix):]
    return "", raw


def _default_scheme(host: str) -> str:
    # Plain WebSocket only for a relay on this machine.
    return "ws" if host.startswith(("localhost", "127.")) else "wss"


def _checked_host(host: str) -> str:
    if not host or any(c in host for c in "/?#@ \t"):
        raise ValueError(f"invalid relay host {host!r}")
    return host


def parse_relay(raw: str | None) -> Relay:
    """Normalise a ``--relay`` value: empty means the default relay; an
    explicit ws(s):// or http(s):// scheme is honoured; a bare host uses
    ``ws`` for localhost/127.* and ``wss`` otherwise."""
    raw = (raw or "").strip()
    if not raw:
        return Relay("wss", DEFAULT_RELAY_HOST)
    scheme, host = _split_scheme(raw)
    host = _checked_host(host.rstrip("/"))
    return Relay(scheme or _default_scheme(host), host)


def parse_target(raw: str) -> tuple[Relay, str]:
    """Split what the joining side was given into (relay, room ID). Accepts
    ``<room>``, ``<host>/<room>`` and ``ws(s)://<host>/<room>``."""
    scheme, rest = _split_scheme((raw or "").strip())
    host, _, room_id = rest.rpartition("/")
    if host:
        relay = Relay(scheme or _default_scheme(host), _checked_host(host))
    else:
        relay = Relay(scheme or "wss", DEFAULT_RELAY_HOST)
    if not _ROOM_RE.fullmatch(room_id):
        raise ValueError("expected a room ID, <host>/<room-id> or wss://<host>/<room-id>")
    return relay, room_id


# ── Direction policy ──────────────────────────────────────────────

@dataclass(frozen=True)
class SyncPolicy:
    """What this machine is willing to do in a sync session, on either side.

    Each side enforces its own policy; the consumer runs only the phases
    both allow. The CLI spells it ``--send-only`` / ``--receive-only``, the
    API ``direction``: ``both``, ``send`` or ``receive``.
    """

    send: bool = True     # local memories may leave this machine
    receive: bool = True  # peer memories may be imported here

    @classmethod
    def parse(cls, direction: object) -> "SyncPolicy":
        policy = _DIRECTIONS.get(direction) if isinstance(direction, str) else None
        if policy is None:
            raise ValueError("direction must be 'both', 'send' or 'receive'")
        return policy

    def grant(self) -> dict:
        """The provider's handshake body: what the consumer may request."""
        return {"pull": self.send, "push": self.receive}

    def mode(self) -> str:
        """The policy as a provider announces it."""
        if self.send and self.receive:
            return "two-way — peers can pull and push"
        if self.send:
            return "send-only — peers can pull, pushes are refused"
        return "receive-only — peers can push, pulls are refused"


_DIRECTIONS = {
    "both": SyncPolicy(),
    "send": SyncPolicy(receive=False),
    "receive": SyncPolicy(send=False),
}


def parse_grant(body: object) -> dict:
    """Decode the provider's 200 handshake body: what this consumer may do."""
    if not isinstance(body, dict):
        raise ValueError("malformed handshake reply")
    return {"pull": body.get("pull") is True, "push": body.get("push") is True}


@dataclass(frozen=True)
class SyncPlan:
    """The phases a consumer runs and why any phase is left out."""

    pull: bool
    push: bool
    skipped: tuple[str, ...]


def plan_sync(grant: dict, local: SyncPolicy) -> SyncPlan:
    """Intersect the provider's grant with this machine's policy.

    Raises ``ValueError("nothing to sync: …")`` when no phase remains.
    """
    skipped = []
    if not local.receive:
        skipped.append("pull skipped (this machine is send-only)")
    elif not grant["pull"]:
        skipped.append("pull skipped (the provider is receive-only)")
    if not local.send:
        skipped.append("push skipped (this machine is receive-only)")
    elif not grant["push"]:
        skipped.append("push skipped (the provider is send-only)")
    plan = SyncPlan(pull=grant["pull"] and local.receive, push=grant["push"] and local.send, skipped=tuple(skipped))
    if not plan.pull and not plan.push:
        raise ValueError("nothing to sync: " + ", ".join(skipped))
    return plan


def sync_workspace(name: object = None) -> tuple[str, bool]:
    """Resolve and validate the workspace a session reads and writes.

    ``name`` falls back to the active workspace, which is captured here so
    a later dashboard switch cannot redirect a running session. Returns
    ``(workspace, exists)``; serving requires an existing workspace, a
    consumer may name a new one to receive into.
    """
    if name is not None and not isinstance(name, str):
        raise ValueError("workspace must be a string")
    workspace = (name or "").strip() or config.get_active_workspace()
    error = config.validate_workspace_name(workspace)
    if error:
        raise ValueError(f"invalid workspace {workspace!r}: {error}")
    return workspace, workspace in config.get_known_workspaces()


# ── Helpers ───────────────────────────────────────────────────────

def generate_room_id() -> str:
    return secrets.token_hex(4)


def generate_pin() -> str:
    return "".join(secrets.choice(PIN_CHARSET) for _ in range(8))


def _session_id() -> str:
    return secrets.token_hex(8)


# ── Trusted-device registry ───────────────────────────────────────

def load_trusted_devices() -> dict[str, dict]:
    """Read ``data/trusted_devices.json``.

    Format (kept from the Go CLI that first wrote it): JSON array of objects
    with ``Fingerprint`` / ``Hostname`` / ``Added`` (unix seconds). Returned
    as a dict keyed by fingerprint for O(1) lookups.
    """
    path = config.get_data_dir() / "trusted_devices.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, list):
        return {}
    out: dict[str, dict] = {}
    for d in data:
        if isinstance(d, dict) and d.get("Fingerprint"):
            out[d["Fingerprint"]] = d
    return out


def save_trusted_device(hello: dict) -> None:
    """Append or refresh a device in the trust list."""
    fp = hello.get("fingerprint")
    if not fp:
        return
    data_dir = config.get_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    existing = load_trusted_devices()
    existing[fp] = {
        "Fingerprint": fp,
        "Hostname": hello.get("hostname", ""),
        "Added": int(time.time()),
    }
    (data_dir / "trusted_devices.json").write_text(
        json.dumps(list(existing.values()), indent=2)
    )


def submit_approval(session_id: str, decision: str) -> bool:
    """Resolve a pending approval. Returns True if a future was waiting.

    ``decision`` is one of ``"accept"`` (one-time), ``"trust"`` (accept +
    persist fingerprint), or ``"reject"``. Unknown values are coerced to
    ``"reject"`` on the receiving end.
    """
    fut = _approvals.get(session_id)
    if fut is None or fut.done():
        return False
    fut.set_result(decision)
    return True


def get_device_identity() -> dict:
    from .device import current
    fingerprint = current()['id']
    return {
        "hostname": socket.gethostname(),
        "user": os.environ.get("USER") or os.environ.get("USERNAME") or "",
        "os": platform.system().lower(),
        "fingerprint": fingerprint,
    }


def cancel_session(session_id: str) -> None:
    ev = _sessions.get(session_id)
    if ev:
        ev.set()


def _check_cancel(cancel: asyncio.Event | None) -> None:
    if cancel and cancel.is_set():
        raise asyncio.CancelledError("Session cancelled by user")


# ── Transport ─────────────────────────────────────────────────────

class _Channel:
    """Sealed JSON frames over the relay WebSocket. Export lines are already
    serialised, so they are sealed without a second encode."""

    def __init__(self, ws, cipher: Cipher):
        self.ws = ws
        self.cipher = cipher

    async def send(self, obj: dict) -> None:
        await self.send_line(json.dumps(obj, separators=(",", ":")))

    async def send_line(self, line: str) -> None:
        await self.ws.send(self.cipher.seal(line.encode()))

    async def recv_line(self, timeout: float | None = None) -> str:
        frame = await asyncio.wait_for(self.ws.recv(), timeout)
        if isinstance(frame, str):
            raise PeerNotice(_notice_text(frame))
        return self.cipher.open(frame).decode()

    async def recv(self, timeout: float | None = None) -> dict:
        msg = json.loads(await self.recv_line(timeout))
        if not isinstance(msg, dict):
            raise ValueError("malformed sync frame")
        return msg


def _connect(relay: Relay, room_id: str, role: str):
    return ws_connect(relay.url(room_id, role), max_size=MAX_FRAME_BYTES)


def _plain(frame: object) -> dict | None:
    """A text frame as a JSON object, or None."""
    if not isinstance(frame, str):
        return None
    try:
        msg = json.loads(frame)
    except ValueError:
        return None
    return msg if isinstance(msg, dict) else None


def _notice_text(frame: str) -> str:
    msg = _plain(frame)
    body = msg.get("body") if msg else None
    return str(body if body else frame)[:200]


async def _notify(ws, status: int, body: str) -> None:
    """Send a plaintext notice before the session is sealed."""
    await ws.send(json.dumps({"status": status, "body": body, "version": PROTOCOL}))


async def _accept(ws, room_id: str, pin: str) -> tuple[_Channel, dict]:
    """Provider side: answer the consumer's key exchange and open its sealed
    HELLO. Raises HandshakeError."""
    opening = _plain(await ws.recv())
    if opening is None or opening.get("method") not in ("PAKE", "HELLO"):
        await _notify(ws, 400, "expected a key exchange")
        raise HandshakeError("Peer skipped the handshake")
    if opening.get("method") == "HELLO" or opening.get("version") != PROTOCOL:
        await _notify(ws, 426, OUTDATED)
        raise HandshakeError("The other machine runs an older Imprint without encrypted sync; " + OUTDATED)
    exchange = KeyExchange("provider", pin, room_id)
    try:
        cipher = exchange.finish(opening.get("msg"))
    except KeyExchangeError as e:
        await _notify(ws, 400, "malformed key exchange")
        raise HandshakeError(f"Peer sent a {e}") from None
    await ws.send(json.dumps({"method": "PAKE", "version": PROTOCOL, "msg": exchange.message}))
    channel = _Channel(ws, cipher)
    try:
        hello = await channel.recv(timeout=30)
    except FrameError:
        await _notify(ws, 403, "wrong PIN")
        raise HandshakeError("Wrong PIN from peer") from None
    except (asyncio.TimeoutError, PeerNotice, ValueError):
        raise HandshakeError("Peer did not complete the handshake") from None
    if hello.get("method") != "HELLO":
        raise HandshakeError("Peer skipped the handshake")
    return channel, hello


async def _join(ws, room_id: str, pin: str, identity: dict) -> _Channel:
    """Consumer side: run the key exchange and send this device's sealed
    HELLO. Raises HandshakeError."""
    exchange = KeyExchange("consumer", pin, room_id)
    await ws.send(json.dumps({"method": "PAKE", "version": PROTOCOL, "msg": exchange.message}))
    try:
        frame = await asyncio.wait_for(ws.recv(), 30)
    except asyncio.TimeoutError:
        raise HandshakeError("The other machine did not answer the key exchange") from None
    reply = _plain(frame)
    if reply is None or reply.get("method") != "PAKE":
        if reply is not None and reply.get("version") == PROTOCOL:
            raise HandshakeError(f"Handshake rejected: {_notice_text(frame)}")
        raise HandshakeError("The other machine runs an older Imprint without encrypted sync; " + OUTDATED)
    try:
        cipher = exchange.finish(reply.get("msg"))
    except KeyExchangeError as e:
        raise HandshakeError(f"The other machine sent a {e}") from None
    channel = _Channel(ws, cipher)
    await channel.send({"method": "HELLO", **identity})
    return channel


# ── Data streaming (sync, not async — called from thread) ────────

def stream_export_lines(workspace: str, batch_size: int = DEFAULT_BATCH_SIZE) -> list[str]:
    """Generate JSONL lines for a full export (memories + facts with vectors)
    of ``workspace``.

    The workspace is explicit: this runs inside the long-lived API process,
    whose active workspace can change mid-session.
    """
    from . import vectorstore as vs

    try:
        from . import imprint_graph as kg
    except Exception:
        kg = None

    client, coll = vs._ensure_collection(workspace)
    info = client.get_collection(coll)
    mem_total = int(info.points_count or 0)

    fact_total = 0
    if kg is not None:
        try:
            conn = kg._get_conn(workspace)
            fact_total = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        except Exception:
            fact_total = 0

    lines: list[str] = []

    def emit(obj: dict) -> None:
        lines.append(json.dumps(obj, separators=(",", ":")))

    emit({"kind": "meta", "datasets": {"memories": mem_total, "facts": fact_total}, "vectors": True})

    # Stream memories with vectors
    if mem_total > 0:
        buf: list[dict] = []
        seq = 0
        from .device import TRANSFER_FIELDS
        for pl in vs._scroll_all([
            "_mid", "content", "project", "type", "tags", "source",
            "chunk_index", "source_mtime", "timestamp",
        ] + TRANSFER_FIELDS, workspace=workspace, with_vectors=True):
            rec = {
                "id": pl.get("_mid", ""),
                "content": pl.get("content", ""),
                "project": pl.get("project", ""),
                "type": pl.get("type", ""),
                "tags": pl.get("tags", {}),
                "source": pl.get("source", ""),
                "chunk_index": pl.get("chunk_index", 0),
                "source_mtime": pl.get("source_mtime", 0),
                "timestamp": pl.get("timestamp", 0),
                "embedding_identity": config.embedding_identity(),
                **{key: pl[key] for key in TRANSFER_FIELDS if key in pl},
            }
            vec = pl.get("_vector")
            if vec is not None:
                rec["vector"] = vec
            buf.append(rec)
            if len(buf) >= batch_size:
                seq += 1
                emit({"kind": "batch", "dataset": "memories", "seq": seq, "records": buf})
                buf = []
        if buf:
            seq += 1
            emit({"kind": "batch", "dataset": "memories", "seq": seq, "records": buf})

    # Stream facts
    if fact_total > 0 and kg is not None:
        conn = kg._get_conn(workspace)
        rows = conn.execute(
            "SELECT subject, predicate, object, valid_from, ended, source, recorded_device FROM facts"
        ).fetchall()
        records = [
            {
                "subject": r["subject"],
                "predicate": r["predicate"],
                "object": r["object"],
                "valid_from": r["valid_from"],
                "ended": r["ended"],
                "source": r["source"] or "",
                "recorded_device": json.loads(r['recorded_device'] or '{}'),
            }
            for r in rows
        ]
        for i in range(0, len(records), batch_size):
            seq = i // batch_size + 1
            emit({"kind": "batch", "dataset": "facts", "seq": seq, "records": records[i:i + batch_size]})

    emit({"kind": "done"})
    return lines


def process_import_buffer(
    path: str,
    workspace: str,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> dict:
    """Process a JSONL buffer file, importing memories and facts into
    ``workspace``.

    Returns stats dict: {memories: {inserted, skipped}, facts: {inserted, skipped}}.
    ``on_progress`` is called after each stored chunk; an exception it raises
    stops the import there.
    """
    from . import vectorstore as vs

    try:
        from . import imprint_graph as kg
    except Exception:
        kg = None

    stats = {
        "memories": {"inserted": 0, "skipped": 0},
        "facts": {"inserted": 0, "skipped": 0},
    }
    totals: dict[str, int] = {"memories": 0, "facts": 0}
    has_vectors = False

    def progress(dataset: str) -> None:
        if on_progress:
            done = stats[dataset]["inserted"] + stats[dataset]["skipped"]
            on_progress(dataset, done, totals.get(dataset, 0))

    def import_memories(records: list[dict]) -> None:
        if not records:
            return
        from .device import TRANSFER_FIELDS, prepare
        use_precomputed = has_vectors and records[0].get("vector") is not None
        payloads = []
        for r in records:
            p = {
                "content": r.get("content", ""),
                "project": r.get("project", ""),
                "type": r.get("type", ""),
                "tags": r.get("tags", {}),
                "source": r.get("source", ""),
                "chunk_index": r.get("chunk_index", 0),
                "source_mtime": r.get("source_mtime", 0),
            }
            if use_precomputed:
                p["vector"] = r["vector"]
                p["id"] = r.get("id")
                p["embedding_identity"] = r.get("embedding_identity")
            p.update({key: r[key] for key in TRANSFER_FIELDS if key in r})
            payloads.append(prepare(p, imported=True))
        chunk = len(payloads) if use_precomputed else EMBED_CHUNK
        store_fn = vs.store_batch_precomputed if use_precomputed else vs.store_batch
        for i in range(0, len(payloads), chunk):
            ins, sk = store_fn(payloads[i:i + chunk], workspace=workspace)
            stats["memories"]["inserted"] += int(ins)
            stats["memories"]["skipped"] += int(sk)
            progress("memories")

    def import_facts(records: list[dict]) -> None:
        if not records:
            return
        if kg is None:
            stats["facts"]["skipped"] += len(records)
            progress("facts")
            return
        ins = 0
        sk = 0
        for r in records:
            if kg.import_fact(r, workspace):
                ins += 1
            else:
                sk += 1
        stats["facts"]["inserted"] += ins
        stats["facts"]["skipped"] += sk
        progress("facts")

    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:
                continue
            kind = msg.get("kind")
            if kind == "meta":
                ds = msg.get("datasets") or {}
                for k, v in ds.items():
                    totals[k] = int(v)
                if msg.get("vectors"):
                    has_vectors = True
                continue
            if kind == "done":
                break
            if kind != "batch":
                continue
            dataset = msg.get("dataset", "memories")
            records = msg.get("records") or []
            if dataset == "memories":
                import_memories(records)
            elif dataset == "facts":
                import_facts(records)

    return stats


class _StopImport(Exception):
    """Raised from the progress callback to stop an import thread."""


async def _store(path: str, workspace: str, phase: str, cancel: asyncio.Event, result: dict):
    """Import ``path`` into ``workspace`` on a worker thread, yielding
    ``progress`` events. Sets ``result["stats"]`` when done. Cancellation
    (task or session) stops the worker after its current chunk."""
    loop = asyncio.get_running_loop()
    updates: asyncio.Queue = asyncio.Queue()
    stop = threading.Event()

    def report(dataset: str, done: int, total: int) -> None:
        if stop.is_set():
            raise _StopImport()
        loop.call_soon_threadsafe(updates.put_nowait, (dataset, done, total))

    worker = asyncio.ensure_future(asyncio.to_thread(process_import_buffer, path, workspace, report))
    # A worker stopped by a task cancellation finishes unobserved.
    worker.add_done_callback(lambda f: f.cancelled() or f.exception())
    try:
        while not worker.done() or not updates.empty():
            if cancel.is_set():
                stop.set()
            try:
                dataset, done, total = await asyncio.wait_for(updates.get(), timeout=0.25)
            except asyncio.TimeoutError:
                continue
            yield {"type": "progress", "phase": phase, "dataset": dataset, "done": done, "total": total}
        try:
            result["stats"] = worker.result()
        except _StopImport:
            raise asyncio.CancelledError("Session cancelled by user") from None
    except asyncio.CancelledError:
        stop.set()
        raise


# ── Resumable downloads (CLI consumer) ────────────────────────────
#
# The CLI keeps a pulled stream on disk until it is stored, so an
# interrupted import can finish later without the peer (whose room closes
# once it has sent everything). One pending download at a time: the serving
# side rotates its room ID per run, so keying by room would orphan files.
# The first line records the destination workspace; files from releases
# that predate --workspace have none and belong to the active workspace.

def pending_buffer_path() -> Path:
    return config.get_data_dir() / "sync-buffer.jsonl"


@dataclass(frozen=True)
class PendingDownload:
    path: Path
    workspace: str
    memories: int
    facts: int
    size: int
    modified: float


def pending_download(path: Path) -> PendingDownload | None:
    """Describe a completed, unstored download, or None if there is none."""
    try:
        stat = path.stat()
        with open(path) as f:
            first = json.loads(f.readline())
            meta = json.loads(f.readline()) if first.get("kind") == "buffer" else first
    except FileNotFoundError:
        return None
    except (OSError, ValueError, AttributeError) as e:
        raise ValueError(f"unreadable sync buffer {path}: {e}; discard it with --fresh") from e
    if meta.get("kind") != "meta":
        raise ValueError(f"sync buffer {path} does not start with a stream header; discard it with --fresh")
    workspace = first.get("workspace") if first.get("kind") == "buffer" else None
    datasets = meta.get("datasets") or {}
    return PendingDownload(path, workspace or config.get_active_workspace(), int(datasets.get("memories", 0)),
                           int(datasets.get("facts", 0)), stat.st_size, stat.st_mtime)


def discard_pending(path: Path) -> list[Path]:
    """Remove a pending download and any partial one; returns what went."""
    removed = []
    for p in (path, path.with_name(path.name + ".tmp")):
        try:
            p.unlink()
            removed.append(p)
        except FileNotFoundError:
            pass
    return removed


async def resume_session(pending: PendingDownload) -> AsyncGenerator[dict, None]:
    """Store a pending download locally; no network or PIN needed."""
    sid = _session_id()
    cancel = asyncio.Event()
    _sessions[sid] = cancel
    yield {"type": "session", "session_id": sid, "workspace": pending.workspace}
    try:
        if config.register_workspace(pending.workspace):
            yield {"type": "workspace_created", "workspace": pending.workspace}
        yield {"type": "status", "status": "storing"}
        result: dict = {}
        async for event in _store(str(pending.path), pending.workspace, "store", cancel, result):
            yield event
        pending.path.unlink(missing_ok=True)
        yield {"type": "pull_complete", "stats": result["stats"]}
        yield {"type": "done", "pull_stats": result["stats"], "push_stats": None}
    except asyncio.CancelledError:
        yield {"type": "cancelled"}
    except Exception as e:
        yield {"type": "error", "message": f"Storing failed: {e}; the download is kept for the next run"}
    finally:
        _sessions.pop(sid, None)


# ── Serve session (provider) ─────────────────────────────────────

async def _send_export(channel: _Channel, workspace: str, phase: str, cancel: asyncio.Event):
    """Stream this workspace to the peer, yielding ``progress`` events."""
    try:
        lines = await asyncio.to_thread(stream_export_lines, workspace)
    except Exception as e:
        await channel.send({"kind": "error", "message": f"the other machine could not export: {e}"})
        raise
    totals: dict[str, int] = {}
    sent: dict[str, int] = {}
    for line in lines:
        _check_cancel(cancel)
        await channel.send_line(line)
        msg = json.loads(line)
        kind = msg.get("kind")
        if kind == "meta":
            totals = msg.get("datasets", {})
        elif kind == "batch":
            ds = msg.get("dataset", "")
            sent[ds] = sent.get(ds, 0) + len(msg.get("records", []))
            yield {"type": "progress", "phase": phase, "dataset": ds, "done": sent[ds], "total": totals.get(ds, 0)}


async def _receive_stream(channel: _Channel, out, phase: str, cancel: asyncio.Event):
    """Write the peer's stream to ``out`` until ``done``, yielding
    ``progress`` events. Raises ``RuntimeError`` when the peer sends an
    error line or anything other than stream lines (e.g. a refusal)."""
    totals: dict[str, int] = {}
    received: dict[str, int] = {}
    while True:
        _check_cancel(cancel)
        line = await channel.recv_line()
        msg = json.loads(line)
        kind = msg.get("kind") if isinstance(msg, dict) else None
        if kind == "error":
            raise RuntimeError(msg.get("message", "peer export error"))
        if kind not in ("meta", "batch", "done"):
            raise RuntimeError(str(msg.get("body") if isinstance(msg, dict) and msg.get("body") else "unexpected frame in data stream"))
        out.write(line + "\n")
        if kind == "meta":
            totals = msg.get("datasets", {})
        elif kind == "batch":
            ds = msg.get("dataset", "")
            received[ds] = received.get(ds, 0) + len(msg.get("records", []))
            yield {"type": "progress", "phase": phase, "dataset": ds, "done": received[ds], "total": totals.get(ds, 0)}
        elif kind == "done":
            return


async def serve_session(
    workspace: str,
    policy: SyncPolicy = SyncPolicy(),
    relay: Relay = Relay("wss", DEFAULT_RELAY_HOST),
) -> AsyncGenerator[dict, None]:
    """Async generator yielding events for a sync serve (provider) session
    of ``workspace``.

    The provider enforces ``policy`` whatever the consumer requests: a
    request outside the grant is refused and ends the session.
    """
    sid = _session_id()
    cancel = asyncio.Event()
    _sessions[sid] = cancel

    room_id = generate_room_id()
    pin = generate_pin()

    yield {"type": "room", "room_id": room_id, "pin": pin, "session_id": sid,
           "workspace": workspace, "relay": str(relay), "target": relay.target(room_id), **policy.grant()}

    try:
        async with _connect(relay, room_id, "provider") as ws:
            yield {"type": "status", "status": "waiting"}

            try:
                channel, hello = await _accept(ws, room_id, pin)
            except HandshakeError as e:
                yield {"type": "error", "message": str(e)}
                return
            _check_cancel(cancel)

            # Fingerprint trust check. Auto-accept previously-trusted devices.
            fp = hello.get("fingerprint", "")
            trusted = load_trusted_devices()
            peer_info = {
                "hostname": hello.get("hostname", ""),
                "os": hello.get("os", ""),
                "fingerprint": fp,
                "user": hello.get("user", ""),
            }

            if fp and fp in trusted:
                yield {"type": "auto_accepted", **peer_info}
            else:
                # Ask the front-end (TTY via CLI, UI via /api/sync/approve)
                # to decide. Block here until a decision arrives or the
                # session is cancelled.
                loop = asyncio.get_running_loop()
                approval_fut = loop.create_future()
                _approvals[sid] = approval_fut
                yield {"type": "approval_required", **peer_info}

                cancel_task = asyncio.create_task(cancel.wait())
                try:
                    done, _ = await asyncio.wait(
                        {approval_fut, cancel_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    cancel_task.cancel()
                    _approvals.pop(sid, None)

                if approval_fut not in done:
                    await channel.send({"status": 408, "body": "cancelled"})
                    _check_cancel(cancel)  # raises CancelledError
                    return

                decision = approval_fut.result()
                if decision not in ("accept", "trust"):
                    await channel.send({"status": 403, "body": "rejected by provider"})
                    yield {"type": "error", "message": "Rejected by provider"}
                    return
                if decision == "trust":
                    try:
                        save_trusted_device(hello)
                        yield {"type": "trusted", **peer_info}
                    except Exception as exc:
                        yield {"type": "warning", "message": f"Could not persist trust: {exc}"}

            # Accept; the body tells the consumer which phases it may request.
            await channel.send({"status": 200, "body": policy.grant()})
            yield {"type": "peer_connected", **peer_info}

            # Request loop
            while True:
                _check_cancel(cancel)
                try:
                    req = await channel.recv(timeout=300)
                except asyncio.TimeoutError:
                    yield {"type": "error", "message": "Peer timed out"}
                    return
                except websockets.exceptions.ConnectionClosed:
                    break

                _check_cancel(cancel)
                path = req.get("path", "")

                if path == "/sync/pull":
                    if not policy.send:
                        await channel.send({"status": 403, "body": REFUSED_PULL})
                        yield {"type": "refused", "request": "pull", "message": REFUSED_PULL}
                        break
                    yield {"type": "status", "status": "sending"}
                    async for event in _send_export(channel, workspace, "send", cancel):
                        yield event
                    yield {"type": "status", "status": "send_complete"}

                elif path == "/sync/push":
                    if not policy.receive:
                        await channel.send({"status": 403, "body": REFUSED_PUSH})
                        yield {"type": "refused", "request": "push", "message": REFUSED_PUSH}
                        break
                    yield {"type": "status", "status": "receiving"}
                    fd, tmp_path = tempfile.mkstemp(prefix="imprint-sync-", suffix=".jsonl")
                    try:
                        try:
                            with os.fdopen(fd, "w") as out:
                                async for event in _receive_stream(channel, out, "receive", cancel):
                                    yield event
                        except RuntimeError as e:
                            yield {"type": "error", "message": str(e)}
                            return

                        yield {"type": "status", "status": "storing"}
                        result: dict = {}
                        try:
                            async for event in _store(tmp_path, workspace, "store", cancel, result):
                                yield event
                        except asyncio.CancelledError:
                            raise
                        except Exception as e:
                            await channel.send({"status": 500, "body": f"import failed: {e}"})
                            yield {"type": "error", "message": f"Storing failed: {e}"}
                            return

                        # Ack the consumer with what was stored.
                        await channel.send({"status": 200, "body": result["stats"]})
                        yield {"type": "push_complete", "stats": result["stats"]}
                    finally:
                        try:
                            os.unlink(tmp_path)
                        except OSError:
                            pass

                else:
                    await channel.send({"status": 404, "body": "unknown path"})

        yield {"type": "done"}

    except asyncio.CancelledError:
        yield {"type": "cancelled"}
    except websockets.exceptions.ConnectionClosed:
        yield {"type": "done"}
    except (FrameError, PeerNotice):
        yield {"type": "error", "message": TAMPERED}
    except Exception as e:
        yield {"type": "error", "message": str(e)}
    finally:
        _sessions.pop(sid, None)


# ── Receive session (consumer) ───────────────────────────────────

async def receive_session(
    relay: Relay,
    room_id: str,
    pin: str,
    workspace: str,
    policy: SyncPolicy = SyncPolicy(),
    buffer: Path | None = None,
) -> AsyncGenerator[dict, None]:
    """Async generator yielding events for a sync receive (consumer)
    session on ``workspace``.

    Runs only the phases in the plan: the provider's grant intersected with
    ``policy``. A workspace that does not exist yet is registered just
    before the first pulled record is stored.

    ``buffer`` keeps the pulled stream at that path until it is stored, so
    a failed import can be resumed (:func:`resume_session`); without it the
    stream goes to a temporary file.
    """
    sid = _session_id()
    cancel = asyncio.Event()
    _sessions[sid] = cancel

    yield {"type": "session", "session_id": sid, "workspace": workspace}

    identity = get_device_identity()

    try:
        async with _connect(relay, room_id, "consumer") as ws:
            yield {"type": "status", "status": "connected"}

            try:
                channel = await _join(ws, room_id, pin, identity)
            except HandshakeError as e:
                yield {"type": "error", "message": str(e)}
                return
            yield {"type": "status", "status": "handshake_sent"}

            # The provider may be waiting on its user to approve this device.
            try:
                resp = await channel.recv()
            except PeerNotice as notice:
                yield {"type": "error", "message": f"Handshake rejected: {notice}"}
                return
            if resp.get("status") != 200:
                body = resp.get("body", "rejected")
                yield {"type": "error", "message": f"Handshake rejected: {body}"}
                return

            yield {"type": "status", "status": "handshake_ok"}

            try:
                plan = plan_sync(parse_grant(resp.get("body")), policy)
            except ValueError as e:
                yield {"type": "error", "message": str(e)}
                return
            yield {"type": "plan", "pull": plan.pull, "push": plan.push, "skipped": list(plan.skipped)}

            if workspace not in config.get_known_workspaces():
                if not plan.pull:
                    yield {"type": "error", "message": f"workspace {workspace!r} does not exist and this sync only pushes: nothing to send"}
                    return
                config.register_workspace(workspace)
                yield {"type": "workspace_created", "workspace": workspace}

            pull_stats: dict | None = None
            push_stats: dict | None = None

            if plan.pull:
                # ── Phase 1: Pull remote data ─────────────────────
                yield {"type": "status", "status": "pulling"}
                await channel.send({"method": "GET", "path": "/sync/pull"})

                if buffer is None:
                    fd, path = tempfile.mkstemp(prefix="imprint-sync-", suffix=".jsonl")
                    partial = path
                    out = os.fdopen(fd, "w")
                else:
                    path, partial = str(buffer), str(buffer) + ".tmp"
                    buffer.parent.mkdir(parents=True, exist_ok=True)
                    out = open(partial, "w")
                try:
                    with out:
                        out.write(json.dumps({"kind": "buffer", "workspace": workspace}) + "\n")
                        async for event in _receive_stream(channel, out, "pull", cancel):
                            yield event
                    if partial != path:
                        # Only a complete stream ever sits at the resumable path.
                        os.replace(partial, path)
                except BaseException as e:
                    try:
                        os.unlink(partial)
                    except OSError:
                        pass
                    if isinstance(e, RuntimeError):
                        yield {"type": "error", "message": f"Pull failed: {e}"}
                        return
                    raise

                yield {"type": "status", "status": "storing"}
                result: dict = {}
                try:
                    async for event in _store(path, workspace, "store", cancel, result):
                        yield event
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    kept = "; the download is kept for the next run" if buffer is not None else ""
                    yield {"type": "error", "message": f"Storing failed: {e}{kept}"}
                    return
                finally:
                    if buffer is None or "stats" in result:
                        try:
                            os.unlink(path)
                        except OSError:
                            pass
                pull_stats = result["stats"]
                yield {"type": "pull_complete", "stats": pull_stats}

            if plan.push:
                # ── Phase 2: Push local data ──────────────────────
                yield {"type": "status", "status": "pushing"}
                await channel.send({"method": "POST", "path": "/sync/push"})
                async for event in _send_export(channel, workspace, "push", cancel):
                    yield event

                # Wait for the provider to store it.
                try:
                    ack = await channel.recv(timeout=300)
                except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed) as e:
                    yield {"type": "warning", "message": f"Push ack failed: {str(e) or 'timed out'}"}
                else:
                    body = ack.get("body")
                    if ack.get("status") == 200:
                        push_stats = body
                        yield {"type": "push_complete", "stats": push_stats}
                    else:
                        yield {"type": "warning", "message": f"Push not stored by provider (status {ack.get('status')}): {body}"}

        yield {"type": "done", "pull_stats": pull_stats, "push_stats": push_stats}

    except asyncio.CancelledError:
        yield {"type": "cancelled"}
    except websockets.exceptions.ConnectionClosed:
        yield {"type": "error", "message": "Connection closed unexpectedly"}
    except (FrameError, PeerNotice):
        yield {"type": "error", "message": TAMPERED}
    except Exception as e:
        yield {"type": "error", "message": str(e)}
    finally:
        _sessions.pop(sid, None)
