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
    provider → {"status": 200, "body": {"pull": …, "push": …, "device": …}}  sealed

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

DEFAULT_RELAY_HOST = "sync.alexandruleca.com"
DEFAULT_BATCH_SIZE = 500
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
UNFINISHED = "The other device disconnected before the sync finished; it runs again shortly"
STOPPED_ANSWERING = "The other device stopped answering"
TAMPERED = ("Sync stopped: a frame failed authentication (altered, dropped or replayed between the machines); "
            "nothing from it was stored")


class HandshakeError(Exception):
    """The peers could not set up an encrypted session."""


class NoAnswer(HandshakeError):
    """Nobody answered the key exchange: the other machine is offline, or no
    longer uses this room. Its error event carries ``no_answer``."""


class PeerNotice(Exception):
    """The peer sent a plaintext notice instead of a sealed frame."""


# ── Relay addressing ──────────────────────────────────────────────

# The share code is the room ID and the PIN in one argument. Releases before
# protocol 2 sent the PIN in clear and need it as a separate --pin, so they
# refuse the code before connecting instead of leaking its secret.
_CODE_RE = re.compile(r"([A-Za-z0-9_]{1,64})-([A-Za-z0-9]{8,64})")


@dataclass(frozen=True)
class Relay:
    """Where a session meets its peer."""

    scheme: str  # "wss" or "ws"
    host: str    # host[:port]

    def url(self, room_id: str, role: str) -> str:
        return f"{self.scheme}://{self.host}/{room_id}?role={role}"

    def target(self, code: str) -> str:
        """What the other machine passes to ``imprint sync``: the bare share
        code on the default relay, the code behind the address on any other."""
        if (self.scheme, self.host) == ("wss", DEFAULT_RELAY_HOST):
            return code
        return f"{self.scheme}://{self.host}/{code}"

    def __str__(self) -> str:
        return f"{self.scheme}://{self.host}"


def _split_scheme(raw: str) -> tuple[str, str]:
    for prefix, scheme in (("wss://", "wss"), ("https://", "wss"), ("ws://", "ws"), ("http://", "ws")):
        if raw.startswith(prefix):
            return scheme, raw[len(prefix):]
    return "", raw


def _default_scheme(host: str) -> str:
    # Plain WebSocket only for a relay on this machine.
    name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    local = name in ("localhost", "[::1]") or re.fullmatch(r"127(\.\d{1,3}){3}", name)
    return "ws" if local else "wss"


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


def share_code(room_id: str, pin: str) -> str:
    return f"{room_id}-{pin}"


def parse_code(raw: str) -> tuple[Relay, str, str]:
    """Split what the joining side was given into (relay, room ID, PIN).
    Accepts the code ``imprint sync serve`` prints — ``<room>-<pin>`` — on
    its own or behind ``<host>/`` or ``ws(s)://<host>/``."""
    scheme, rest = _split_scheme((raw or "").strip())
    host, _, code = rest.rpartition("/")
    if host:
        relay = Relay(scheme or _default_scheme(host), _checked_host(host))
    else:
        relay = Relay(scheme or "wss", DEFAULT_RELAY_HOST)
    match = _CODE_RE.fullmatch(code)
    if not match:
        raise ValueError("expected the code shown by `imprint sync serve`, e.g. 7f3a9c21-Ab3xY9Kq "
                         "or wss://<host>/7f3a9c21-Ab3xY9Kq")
    return relay, match.group(1), match.group(2)


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


def save_trusted_device(hello: dict) -> bool:
    """Append or refresh a device in the trust list. Returns False when the
    device has no fingerprint to remember."""
    fp = hello.get("fingerprint")
    if not fp:
        return False
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
    return True


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


def _expect_decision(session_id: str) -> asyncio.Future:
    """Register a pending decision for a session; create it before yielding
    the event that asks for it, so a front-end can answer straight away."""
    future = asyncio.get_running_loop().create_future()
    _approvals[session_id] = future
    return future


async def _await_decision(session_id: str, future: asyncio.Future, cancel: asyncio.Event) -> str:
    """The front-end's answer, or CancelledError if the session is
    cancelled first."""
    cancelled = asyncio.create_task(cancel.wait())
    try:
        done, _ = await asyncio.wait({future, cancelled}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        cancelled.cancel()
        _approvals.pop(session_id, None)
    if future not in done:
        raise asyncio.CancelledError("Session cancelled by user")
    return future.result()


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
            raise PeerNotice(_notice(frame))
        return self.cipher.open(frame).decode()

    async def recv(self, timeout: float | None = None) -> dict:
        msg = json.loads(await self.recv_line(timeout))
        if not isinstance(msg, dict):
            raise ValueError("malformed sync frame")
        return msg


def _connect(relay: Relay, room_id: str, role: str):
    # max_queue bounds what a fast sender can make this side buffer. A session
    # that ends early leaves frames unread, so the close reply may never be
    # read either: keep the close wait short.
    return ws_connect(relay.url(room_id, role), max_size=MAX_FRAME_BYTES, max_queue=2, close_timeout=2)


def _printable(value: object, limit: int = 200) -> str:
    """Peer- and relay-supplied text made safe to show: no control or
    escape characters, bounded length."""
    return "".join(c if c.isprintable() else " " for c in str(value))[:limit]


_DEVICE_ID = re.compile(r"[A-Za-z0-9_-]{8,128}")
_HELLO_FIELDS = {"hostname": 128, "user": 64, "os": 32}


def _peer_info(hello: dict) -> dict:
    """A peer's identity — the consumer's sealed HELLO, or the device in
    the provider's sealed grant. Raises HandshakeError when the fields are
    not what a peer sends."""
    if not isinstance(hello, dict):
        raise HandshakeError("Peer sent a malformed greeting")
    fingerprint = hello.get("fingerprint")
    if not isinstance(fingerprint, str) or not _DEVICE_ID.fullmatch(fingerprint):
        raise HandshakeError("Peer sent a malformed greeting")
    info = {"fingerprint": fingerprint}
    for key, limit in _HELLO_FIELDS.items():
        value = hello.get(key, "")
        if not isinstance(value, str):
            raise HandshakeError("Peer sent a malformed greeting")
        info[key] = _printable(value, limit)
    return info


# Notices are unauthenticated (anyone between the machines can send one), so
# they are shown as fixed local text, never as what the frame says.
_NOTICES = {
    400: "the other machine could not read this machine's key exchange",
    403: "wrong PIN",
    426: OUTDATED,
}


def _notice(frame: object) -> str:
    msg = _plain(frame)
    status = msg.get("status") if msg else None
    return _NOTICES.get(status, "the other machine ended the handshake")


def _plain(frame: object) -> dict | None:
    """A text frame as a JSON object, or None."""
    if not isinstance(frame, str):
        return None
    try:
        msg = json.loads(frame)
    except ValueError:
        return None
    return msg if isinstance(msg, dict) else None


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
    return channel, _peer_info(hello)


async def _join(ws, room_id: str, pin: str, identity: dict) -> _Channel:
    """Consumer side: run the key exchange and send this device's sealed
    HELLO. Raises HandshakeError."""
    exchange = KeyExchange("consumer", pin, room_id)
    await ws.send(json.dumps({"method": "PAKE", "version": PROTOCOL, "msg": exchange.message}))
    try:
        frame = await asyncio.wait_for(ws.recv(), 30)
    except asyncio.TimeoutError:
        raise NoAnswer("The other machine did not answer the key exchange") from None
    reply = _plain(frame)
    if reply is None or reply.get("method") != "PAKE":
        if reply is not None and reply.get("version") == PROTOCOL:
            raise HandshakeError(f"Handshake rejected: {_notice(frame)}")
        raise HandshakeError("The other machine runs an older Imprint without encrypted sync; " + OUTDATED)
    try:
        cipher = exchange.finish(reply.get("msg"))
    except KeyExchangeError as e:
        raise HandshakeError(f"The other machine sent a {e}") from None
    channel = _Channel(ws, cipher)
    await channel.send({"method": "HELLO", **identity})
    return channel


# ── Data streaming (sync, not async — called from thread) ────────

MEMORY_FIELDS = ["_mid", "content", "project", "type", "tags", "source",
                 "chunk_index", "source_mtime", "timestamp", "llm_tagged", "tagged_at"]


def memory_record(pl: dict, identity: str) -> dict:
    """A stored memory as a stream record: its payload fields and vector."""
    from .device import TRANSFER_FIELDS
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
        "embedding_identity": identity,
        **{key: pl[key] for key in TRANSFER_FIELDS if key in pl},
        # The classification and when it was set (ADR 0051); absent
        # until a memory is tagged after creation.
        **{key: pl[key] for key in ("llm_tagged", "tagged_at") if pl.get(key)},
    }
    vec = pl.get("_vector")
    if vec is not None:
        rec["vector"] = vec
    return rec


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
            with kg._connection(workspace) as conn:
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
        identity = config.embedding_identity()
        for pl in vs._scroll_all(MEMORY_FIELDS + TRANSFER_FIELDS, workspace=workspace, with_vectors=True):
            buf.append(memory_record(pl, identity))
            if len(buf) >= batch_size:
                seq += 1
                emit({"kind": "batch", "dataset": "memories", "seq": seq, "records": buf})
                buf = []
        if buf:
            seq += 1
            emit({"kind": "batch", "dataset": "memories", "seq": seq, "records": buf})

    # Stream facts
    if fact_total > 0 and kg is not None:
        with kg._connection(workspace) as conn:
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

    from . import source_snapshots
    for frame in source_snapshots.export_parts(workspace):
        emit(frame)
    emit({"kind": "done"})
    return lines


def process_import_buffer(
    path: str,
    workspace: str,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> dict:
    """Process a JSONL buffer file, importing memories and facts into
    ``workspace``.

    Returns stats dict: {memories: {inserted, updated, skipped}, facts: {inserted, skipped}}.
    ``updated`` counts memories this workspace had whose newer classification
    replaced the local one (ADR 0051).
    ``on_progress`` is called after each stored batch; an exception it raises
    stops the import there. Every memory must carry its vector (every
    release that streams memories sends them); none is embedded here.
    """
    from . import vectorstore as vs

    try:
        from . import imprint_graph as kg
    except Exception:
        kg = None

    stats = {
        "memories": {"inserted": 0, "updated": 0, "skipped": 0},
        "facts": {"inserted": 0, "skipped": 0},
    }
    totals: dict[str, int] = {"memories": 0, "facts": 0}

    def progress(dataset: str) -> None:
        if on_progress:
            done = sum(stats[dataset].values())
            on_progress(dataset, done, totals.get(dataset, 0))

    def import_memories(records: list[dict]) -> None:
        if not records:
            return
        from .device import TRANSFER_FIELDS, prepare
        payloads = []
        for r in records:
            if not isinstance(r.get("vector"), list):
                raise ValueError("received a memory without its vector")
            p = {
                "content": r.get("content", ""),
                "project": r.get("project", ""),
                "type": r.get("type", ""),
                "tags": r.get("tags", {}),
                "source": r.get("source", ""),
                "chunk_index": r.get("chunk_index", 0),
                "source_mtime": r.get("source_mtime", 0),
                "vector": r["vector"],
                "id": r.get("id"),
                "embedding_identity": r.get("embedding_identity"),
            }
            p.update({key: r[key] for key in TRANSFER_FIELDS + ["llm_tagged", "tagged_at"] if key in r})
            payloads.append(prepare(p, imported=True))
        ins, upd, sk = vs.store_batch_precomputed(payloads, workspace=workspace)
        stats["memories"]["inserted"] += int(ins)
        stats["memories"]["updated"] += int(upd)
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

    from .source_snapshots import Importer
    snapshot_import = Importer(workspace)
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
                continue
            if kind == "done":
                break
            if kind == "source_snapshot":
                snapshot_import.feed(msg)
                continue
            if kind != "batch":
                continue
            dataset = msg.get("dataset", "memories")
            records = msg.get("records") or []
            if dataset == "memories":
                import_memories(records)
            elif dataset == "facts":
                import_facts(records)

    snapshot_import.finish()
    if snapshot_import.imported:
        stats['sources'] = {'imported': snapshot_import.imported}
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
# A pulled stream that fails to store is kept, so the import can finish
# later without the peer (whose room closes once it has sent everything).
# One pending download at a time: the serving side rotates its room ID per
# run, so keying by room would orphan files. The first line records the
# destination workspace; files from releases that predate --workspace have
# none and belong to the active workspace.

def pending_buffer_path() -> Path:
    return config.get_data_dir() / "sync-buffer.jsonl"


def _temp_buffer() -> tuple[int, str]:
    """A scratch file for one stream, in the data directory (which holds
    the same memories) rather than a shared temp directory; owner-only
    (0600) where files carry permission bits."""
    directory = config.get_data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    return tempfile.mkstemp(prefix="sync-", suffix=".jsonl.tmp", dir=directory)


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
        if meta.get("kind") != "meta":
            raise ValueError("it does not start with a stream header")
        workspace = first.get("workspace") if first.get("kind") == "buffer" else None
        if workspace is not None and config.validate_workspace_name(workspace):
            raise ValueError("it names an invalid workspace")
        datasets = meta.get("datasets") or {}
        memories, facts = int(datasets.get("memories", 0)), int(datasets.get("facts", 0))
    except FileNotFoundError:
        return None
    except Exception as e:
        raise ValueError(f"unreadable sync buffer {path} ({e}); discard it with --fresh") from None
    return PendingDownload(path, workspace or config.get_active_workspace(), memories, facts,
                           stat.st_size, stat.st_mtime)


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
        if not cancel.is_set():
            raise  # the task was cancelled (a time limit, the paired worker, shutdown): its owner decides
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
    except Exception:
        await channel.send({"kind": "error", "message": "the other machine could not export its memories"})
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


async def _receive_stream(channel: _Channel, out, phase: str, cancel: asyncio.Event, first: dict | None = None):
    """Write the peer's stream to ``out`` until ``done``, yielding
    ``progress`` events. Raises ``RuntimeError`` when the peer sends an
    error line or anything other than stream lines (e.g. a refusal).
    ``first``: the stream's opening frame, when the caller read it."""
    totals: dict[str, int] = {}
    received: dict[str, int] = {}
    while True:
        _check_cancel(cancel)
        msg, first = first or await channel.recv(), None
        kind = msg.get("kind")
        if kind == "error":
            raise RuntimeError(_printable(msg.get("message", "peer export error")))
        if not _stream_line_ok(msg, bool(totals)):
            raise RuntimeError(_printable(msg.get("body") or "unexpected frame in data stream"))
        # Re-serialised, so the buffer holds exactly one JSON value per line.
        out.write(json.dumps(msg, separators=(",", ":")) + "\n")
        if kind == "meta":
            totals = msg["datasets"]
        elif kind == "batch":
            ds = msg["dataset"]
            received[ds] = received.get(ds, 0) + len(msg["records"])
            yield {"type": "progress", "phase": phase, "dataset": ds, "done": received[ds], "total": totals.get(ds, 0)}
        elif kind == "done":
            return


def _stream_line_ok(msg: dict, after_meta: bool) -> bool:
    """A data stream is one meta line (with vectors), batches of records,
    source snapshot parts, then done. The importer checks the parts' order,
    size and checksum."""
    kind = msg.get("kind")
    if kind == "meta":
        datasets = msg.get("datasets")
        return (not after_meta and msg.get("vectors") is True and isinstance(datasets, dict)
                and all(k in ("memories", "facts") and type(v) is int and v >= 0 for k, v in datasets.items()))
    if not after_meta:
        return False
    if kind == "batch":
        records = msg.get("records")
        if msg.get("dataset") not in ("memories", "facts") or not isinstance(records, list):
            return False
        if not all(isinstance(r, dict) for r in records):
            return False
        return msg["dataset"] == "facts" or all(isinstance(r.get("vector"), list) for r in records)
    if kind == "source_snapshot":
        return (isinstance(msg.get("transfer"), str) and isinstance(msg.get("data"), str)
                and type(msg.get("part")) is int and type(msg.get("parts")) is int)
    return kind == "done"


async def serve_session(
    workspace: str,
    policy: SyncPolicy = SyncPolicy(),
    relay: Relay = Relay("wss", DEFAULT_RELAY_HOST),
    *,
    remember: bool = False,
    pairing: dict | None = None,
) -> AsyncGenerator[dict, None]:
    """Async generator yielding events for a sync serve (provider) session
    of ``workspace``.

    The provider enforces ``policy`` whatever the consumer requests: a
    request outside the grant is refused and ends the session.
    """
    sid = _session_id()
    cancel = asyncio.Event()
    _sessions[sid] = cancel

    unfinished = False
    room_id = pairing['room'] if pairing else generate_room_id()
    pin = pairing['secret'] if pairing else generate_pin()
    code = share_code(room_id, pin)

    if pairing is None:
        yield {"type": "room", "room_id": room_id, "pin": pin, "code": code, "session_id": sid,
               "workspace": workspace, "relay": str(relay), "target": relay.target(code), **policy.grant()}

    try:
        async with _connect(relay, room_id, "provider") as ws:
            yield {"type": "status", "status": "waiting"}

            try:
                channel, peer_info = await _accept(ws, room_id, pin)
            except HandshakeError as e:
                yield {"type": "error", "message": str(e)}
                return
            _check_cancel(cancel)

            if pairing:
                from . import sync_pairs
                yield {"type": "status", "status": "comparing"}
                exchange = await sync_pairs.exchange(channel, pairing, peer_info, provider=True)
                if not exchange:
                    yield {'type': 'idle'}
                    return
                pairing.update(exchange)

            # Fingerprint trust check. Auto-accept previously-trusted devices.
            fp = peer_info["fingerprint"]
            trusted = load_trusted_devices()

            if pairing or fp in trusted:
                yield {"type": "auto_accepted", **peer_info}
            else:
                # Ask the front-end (TTY via CLI, UI via /api/sync/approve)
                # to decide. Block here until a decision arrives or the
                # session is cancelled.
                decided = _expect_decision(sid)
                yield {"type": "approval_required", **peer_info}
                try:
                    decision = await _await_decision(sid, decided, cancel)
                except asyncio.CancelledError:
                    await channel.send({"status": 408, "body": "cancelled"})
                    raise
                if decision not in ("accept", "trust"):
                    await channel.send({"status": 403, "body": "rejected by provider"})
                    yield {"type": "error", "message": "Rejected by provider"}
                    return
                if decision == "trust":
                    try:
                        if save_trusted_device(peer_info):
                            yield {"type": "trusted", **peer_info}
                    except Exception as exc:
                        yield {"type": "warning", "message": f"Could not persist trust: {exc}"}

            # A paired sync that sends only differences ends when it is
            # reconciled; the other device leaving earlier is a failure.
            unfinished = bool(pairing and pairing.get("_delta"))

            # Accept; the body tells the consumer which phases it may request
            # and who it is talking to, so it can decide what to send.
            await channel.send({"status": 200, "body": {**policy.grant(), "device": get_device_identity(),
                                                       "pairing_version": 1 if remember and not pairing else 0}})
            yield {"type": "peer_connected", **peer_info}

            # Request loop: each phase at most once per session.
            served: set[str] = set()
            while True:
                _check_cancel(cancel)
                try:
                    req = await channel.recv(timeout=300)
                except asyncio.TimeoutError:
                    yield {"type": "error", "message": "Peer timed out"}
                    return
                except websockets.exceptions.ConnectionClosed:
                    if unfinished:
                        yield {"type": "error", "message": UNFINISHED}
                        return
                    break

                _check_cancel(cancel)
                path = req.get("path", "")
                if path in served:
                    await channel.send({"status": 409, "body": "each phase runs once per session"})
                    yield {"type": "error", "message": "Peer repeated a request; session ended"}
                    return
                served.add(path)

                if path == '/sync/pair':
                    if not remember or pairing:
                        await channel.send({'status': 403, 'body': 'Automatic pairing was not enabled on this device'})
                        return
                    from .sync_pairs import accept_pair
                    pair_id = await accept_pair(channel, req, workspace, peer_info, policy, relay)
                    yield {'type': 'paired', 'pair_id': pair_id, **peer_info}

                elif path == "/sync/delta":
                    pull, push = req.get("pull"), req.get("push")
                    if (not unfinished or type(pull) is not bool or type(push) is not bool
                            or pull and not policy.send or push and not policy.receive):
                        await channel.send({"status": 403, "body": "this pairing does not allow that"})
                        yield {"type": "error", "message": "The other device asked for a sync this pairing does not allow"}
                        return
                    await channel.send({"status": 200})
                    yield {"type": "status", "status": "reconciling"}
                    from . import sync_delta
                    result: dict = {}
                    async for event in sync_delta.reconcile(channel, workspace, pairing["_manifest"], provider=True,
                                                            provider_sends=pull, consumer_sends=push,
                                                            cancel=cancel, result=result):
                        yield event
                    unfinished = False
                    if push:
                        yield {"type": "push_complete", "stats": result.get("received")}

                elif path == "/sync/pull":
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
                    fd, tmp_path = _temp_buffer()
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
                            await channel.send({"status": 500, "body": "the other machine could not store the push"})
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
        if not cancel.is_set():
            raise  # the task was cancelled (a time limit, the paired worker, shutdown): its owner decides
        yield {"type": "cancelled"}
    except websockets.exceptions.ConnectionClosed:
        if pairing and pairing.get("_delta") and unfinished:
            yield {"type": "error", "message": UNFINISHED}
        else:
            yield {"type": "done"}
    except (FrameError, PeerNotice):
        yield {"type": "error", "message": TAMPERED}
    except asyncio.TimeoutError:
        yield {"type": "error", "message": STOPPED_ANSWERING}
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
    policy: SyncPolicy = _DIRECTIONS["receive"],
    buffer: Path | None = None,
    *,
    remember: bool = False,
    pairing: dict | None = None,
) -> AsyncGenerator[dict, None]:
    """Async generator yielding events for a sync receive (consumer)
    session on ``workspace``.

    Runs only the phases in the plan: the provider's grant intersected with
    ``policy`` (receive-only unless the caller opts in to sending). Before
    anything is pushed, a ``confirm_push`` event names the receiving device
    and waits for :func:`submit_approval`; declining skips the push. A
    workspace that does not exist yet is registered when the pull starts.

    ``buffer``: where a pulled stream that failed to store is kept, so the
    import can be resumed (:func:`resume_session`); without it a failed
    download is discarded.
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
                yield {"type": "error", "message": str(e), **({"no_answer": True} if isinstance(e, NoAnswer) else {})}
                return
            yield {"type": "status", "status": "handshake_sent"}

            if pairing:
                from . import sync_pairs
                yield {"type": "status", "status": "comparing"}
                # Identity in the sealed grant is verified below as well. The
                # paired high-entropy secret authenticates this control phase.
                exchange = await sync_pairs.exchange(channel, pairing, pairing['peer'], provider=False)
                if not exchange:
                    yield {'type': 'idle'}
                    return
                pairing.update(exchange)

            # The provider may be waiting on its user to approve this device.
            try:
                resp = await channel.recv()
            except PeerNotice as notice:
                yield {"type": "error", "message": f"Handshake rejected: {notice}"}
                return
            if resp.get("status") != 200:
                body = _printable(resp.get("body") or "rejected")
                yield {"type": "error", "message": f"Handshake rejected: {body}"}
                return

            yield {"type": "status", "status": "handshake_ok"}

            body = resp.get("body")
            try:
                grant = parse_grant(body)
                provider = _peer_info(body.get("device"))
                if pairing and provider['fingerprint'] != pairing['peer']['fingerprint']:
                    raise ValueError('Paired device identity changed')
                plan = plan_sync(grant, policy)
            except (ValueError, HandshakeError) as e:
                yield {"type": "error", "message": str(e)}
                return
            yield {"type": "peer_connected", **provider}

            exists = workspace in config.get_known_workspaces()
            if not exists and not plan.pull:
                yield {"type": "error", "message": f"workspace {workspace!r} does not exist and this sync only pushes: nothing to send"}
                return

            if plan.push and not pairing:
                # Nothing leaves this machine until its user sees who gets it.
                decided = _expect_decision(sid)
                yield {"type": "confirm_push", "workspace": workspace, **provider}
                if await _await_decision(sid, decided, cancel) != "accept":
                    skipped = plan.skipped + ("push skipped (you chose not to send)",)
                    if not plan.pull:
                        yield {"type": "error", "message": "nothing to sync: " + ", ".join(skipped)}
                        return
                    plan = SyncPlan(pull=True, push=False, skipped=skipped)
            yield {"type": "plan", "pull": plan.pull, "push": plan.push, "skipped": list(plan.skipped)}

            if not exists:
                config.register_workspace(workspace)
                yield {"type": "workspace_created", "workspace": workspace}

            if remember and not pairing:
                if body.get('pairing_version') != 1:
                    yield {'type': 'warning', 'message': 'The other device did not enable automatic pairing; this sync is one-time'}
                else:
                    from .sync_pairs import request_pair
                    pair_id = await request_pair(channel, workspace, provider, plan, relay)
                    yield {'type': 'paired', 'pair_id': pair_id, **provider}

            pull_stats: dict | None = None
            push_stats: dict | None = None

            if pairing and pairing.get("_delta"):
                # ── Paired: only what each side lacks, in stored chunks ──
                yield {"type": "status", "status": "reconciling"}
                await channel.send({"path": "/sync/delta", "pull": plan.pull, "push": plan.push})
                answer = await channel.recv(timeout=60)
                if answer.get("status") != 200:
                    yield {"type": "error", "message": f"Sync refused: {_printable(answer.get('body') or 'refused')}"}
                    return
                from . import sync_delta
                result: dict = {}
                async for event in sync_delta.reconcile(channel, workspace, pairing["_manifest"], provider=False,
                                                        provider_sends=plan.pull, consumer_sends=plan.push,
                                                        cancel=cancel, result=result):
                    yield event
                if plan.pull:
                    pull_stats = result.get("received")
                    yield {"type": "pull_complete", "stats": pull_stats}

            elif plan.pull:
                # ── Phase 1: Pull remote data ─────────────────────
                yield {"type": "status", "status": "pulling"}
                await channel.send({"method": "GET", "path": "/sync/pull"})

                # Each run downloads into a file of its own, so concurrent
                # runs never share one; it is published as the resumable
                # download only if storing it fails.
                fd, path = _temp_buffer()
                try:
                    with os.fdopen(fd, "w") as out:
                        out.write(json.dumps({"kind": "buffer", "workspace": workspace}) + "\n")
                        async for event in _receive_stream(channel, out, "pull", cancel):
                            yield event
                    yield {"type": "status", "status": "storing"}
                    result: dict = {}
                    try:
                        async for event in _store(path, workspace, "store", cancel, result):
                            yield event
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        if buffer is None:
                            yield {"type": "error", "message": f"Storing failed: {e}"}
                        else:
                            os.replace(path, buffer)
                            yield {"type": "error", "message": f"Storing failed: {e}; the download is kept for the next run"}
                        return
                except RuntimeError as e:
                    yield {"type": "error", "message": f"Pull failed: {e}"}
                    return
                finally:
                    try:
                        os.unlink(path)
                    except OSError:
                        pass
                pull_stats = result["stats"]
                yield {"type": "pull_complete", "stats": pull_stats}

            if plan.push and not (pairing and pairing.get("_delta")):
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
                        yield {"type": "warning", "message": f"Push not stored by provider (status {_printable(ack.get('status'), 8)}): {_printable(body)}"}

        yield {"type": "done", "pull_stats": pull_stats, "push_stats": push_stats}

    except asyncio.CancelledError:
        if not cancel.is_set():
            raise  # the task was cancelled (a time limit, the paired worker, shutdown): its owner decides
        yield {"type": "cancelled"}
    except websockets.exceptions.ConnectionClosed:
        yield {"type": "error", "message": "Connection closed unexpectedly"}
    except (FrameError, PeerNotice):
        yield {"type": "error", "message": TAMPERED}
    except asyncio.TimeoutError:
        yield {"type": "error", "message": STOPPED_ANSWERING}
    except Exception as e:
        yield {"type": "error", "message": str(e)}
    finally:
        _sessions.pop(sid, None)
