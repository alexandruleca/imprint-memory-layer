"""WebSocket-based sync protocol for the Imprint UI.

Reimplements the Go CLI sync protocol (cmd/sync.go) in pure Python,
using async generators that yield SSE-friendly event dicts. The FastAPI
layer wraps these generators in StreamingResponse.

Protocol: newline-delimited JSON over WSS via a relay server.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import platform
import secrets
import socket
import tempfile
import time
from pathlib import Path
from typing import AsyncGenerator, Callable

import websockets
from websockets.asyncio.client import connect as ws_connect

from . import config

# ── Constants ─────────────────────────────────────────────────────

DEFAULT_RELAY_HOST = "imprint.alexandruleca.com"
DEFAULT_BATCH_SIZE = 500
EMBED_CHUNK = 32
PIN_CHARSET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"

# Active sessions keyed by session_id, value is an asyncio.Event that
# gets set when cancellation is requested.
_sessions: dict[str, asyncio.Event] = {}


# ── Helpers ───────────────────────────────────────────────────────

def generate_room_id() -> str:
    return secrets.token_hex(4)


def generate_pin() -> str:
    return "".join(secrets.choice(PIN_CHARSET) for _ in range(8))


def pin_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def _session_id() -> str:
    return secrets.token_hex(8)


def get_device_identity() -> dict:
    data_dir = config.get_data_dir()
    fp_path = data_dir / "device_id.txt"
    fingerprint = ""
    if fp_path.exists():
        fingerprint = fp_path.read_text().strip()
    if not fingerprint:
        fingerprint = secrets.token_hex(4)
        data_dir.mkdir(parents=True, exist_ok=True)
        fp_path.write_text(fingerprint + "\n")
    return {
        "hostname": socket.gethostname(),
        "user": os.environ.get("USER") or os.environ.get("USERNAME") or "",
        "os": platform.system().lower(),
        "fingerprint": fingerprint,
    }


def _relay_url(room_id: str, role: str) -> str:
    return f"wss://{DEFAULT_RELAY_HOST}/{room_id}?role={role}"


def cancel_session(session_id: str) -> None:
    ev = _sessions.get(session_id)
    if ev:
        ev.set()


def _check_cancel(cancel: asyncio.Event | None) -> None:
    if cancel and cancel.is_set():
        raise asyncio.CancelledError("Session cancelled by user")


# ── Data streaming (sync, not async — called from thread) ────────

def stream_export_lines(batch_size: int = DEFAULT_BATCH_SIZE) -> list[str]:
    """Generate JSONL lines for a full export (memories + facts with vectors).

    Returns list of JSON strings (one per line). Mirrors the Go
    syncExportStreamScript embedded Python.
    """
    from . import vectorstore as vs

    try:
        from . import imprint_graph as kg
    except Exception:
        kg = None

    client, coll = vs._ensure_collection()
    info = client.get_collection(coll)
    mem_total = int(info.points_count or 0)

    fact_total = 0
    if kg is not None:
        try:
            conn = kg._get_conn()
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
        for pl in vs._scroll_all([
            "_mid", "content", "project", "type", "tags", "source",
            "chunk_index", "source_mtime", "timestamp",
        ], with_vectors=True):
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
        conn = kg._get_conn()
        rows = conn.execute(
            "SELECT subject, predicate, object, valid_from, ended, source FROM facts"
        ).fetchall()
        records = [
            {
                "subject": r["subject"],
                "predicate": r["predicate"],
                "object": r["object"],
                "valid_from": r["valid_from"],
                "ended": r["ended"],
                "source": r["source"] or "",
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
    on_progress: Callable[[str, int, int], None] | None = None,
) -> dict:
    """Process a JSONL buffer file, importing memories and facts.

    Mirrors the Go syncImportStreamScript embedded Python.
    Returns stats dict: {memories: {inserted, skipped}, facts: {inserted, skipped}}.
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
            payloads.append(p)
        chunk = len(payloads) if use_precomputed else EMBED_CHUNK
        store_fn = vs.store_batch_precomputed if use_precomputed else vs.store_batch
        for i in range(0, len(payloads), chunk):
            ins, sk = store_fn(payloads[i:i + chunk])
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
        conn = kg._get_conn()
        ins = 0
        sk = 0
        for r in records:
            row = conn.execute(
                "SELECT 1 FROM facts WHERE subject=? AND predicate=? AND object=? AND valid_from=?",
                (r.get("subject", ""), r.get("predicate", ""), r.get("object", ""), r.get("valid_from", 0)),
            ).fetchone()
            if row:
                sk += 1
                continue
            conn.execute(
                "INSERT INTO facts (subject, predicate, object, valid_from, ended, source) VALUES (?,?,?,?,?,?)",
                (
                    r.get("subject", ""),
                    r.get("predicate", ""),
                    r.get("object", ""),
                    r.get("valid_from", 0),
                    r.get("ended"),
                    r.get("source", "") or "",
                ),
            )
            ins += 1
        conn.commit()
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


# ── Serve session (provider) ─────────────────────────────────────

async def serve_session() -> AsyncGenerator[dict, None]:
    """Async generator yielding SSE events for a sync serve (provider) session."""
    sid = _session_id()
    cancel = asyncio.Event()
    _sessions[sid] = cancel

    room_id = generate_room_id()
    pin = generate_pin()

    yield {"type": "room", "room_id": room_id, "pin": pin, "session_id": sid}

    try:
        url = _relay_url(room_id, "provider")
        async with ws_connect(url, max_size=None) as ws:
            yield {"type": "status", "status": "waiting"}

            # Wait for HELLO from consumer
            raw = await ws.recv()
            _check_cancel(cancel)
            hello = json.loads(raw)

            if hello.get("method") != "HELLO":
                await ws.send(json.dumps({"status": 400, "body": "handshake required"}))
                yield {"type": "error", "message": "Peer skipped handshake"}
                return

            if not pin_equal(hello.get("pin", ""), pin):
                await ws.send(json.dumps({"status": 403, "body": "invalid PIN"}))
                yield {"type": "error", "message": "Invalid PIN from peer"}
                return

            # Accept
            await ws.send(json.dumps({"status": 200, "body": "ok"}))
            yield {
                "type": "peer_connected",
                "hostname": hello.get("hostname", ""),
                "os": hello.get("os", ""),
                "fingerprint": hello.get("fingerprint", ""),
                "user": hello.get("user", ""),
            }

            # Request loop
            while True:
                _check_cancel(cancel)
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=300)
                except asyncio.TimeoutError:
                    yield {"type": "error", "message": "Peer timed out"}
                    return
                except websockets.exceptions.ConnectionClosed:
                    break

                _check_cancel(cancel)
                req = json.loads(raw)
                path = req.get("path", "")

                if path == "/sync/pull":
                    yield {"type": "status", "status": "sending"}
                    # Export runs in thread pool (sync I/O)
                    lines = await asyncio.to_thread(stream_export_lines)
                    totals: dict[str, int] = {}
                    sent: dict[str, int] = {}
                    for line in lines:
                        _check_cancel(cancel)
                        await ws.send(line)
                        msg = json.loads(line)
                        kind = msg.get("kind")
                        if kind == "meta":
                            totals = msg.get("datasets", {})
                        elif kind == "batch":
                            ds = msg.get("dataset", "")
                            sent[ds] = sent.get(ds, 0) + len(msg.get("records", []))
                            yield {"type": "progress", "phase": "send", "dataset": ds, "done": sent[ds], "total": totals.get(ds, 0)}
                    yield {"type": "status", "status": "send_complete"}

                elif path == "/sync/push":
                    yield {"type": "status", "status": "receiving"}
                    # Buffer incoming data to temp file
                    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
                    recv_totals: dict[str, int] = {}
                    recv_done: dict[str, int] = {}
                    try:
                        while True:
                            _check_cancel(cancel)
                            raw = await ws.recv()
                            tmp.write(raw + "\n")
                            msg = json.loads(raw)
                            kind = msg.get("kind")
                            if kind == "meta":
                                recv_totals = msg.get("datasets", {})
                            elif kind == "batch":
                                ds = msg.get("dataset", "")
                                recv_done[ds] = recv_done.get(ds, 0) + len(msg.get("records", []))
                                yield {"type": "progress", "phase": "receive", "dataset": ds, "done": recv_done[ds], "total": recv_totals.get(ds, 0)}
                            elif kind == "done":
                                break
                            elif kind == "error":
                                yield {"type": "error", "message": msg.get("message", "Peer export error")}
                                tmp.close()
                                os.unlink(tmp.name)
                                return
                        tmp.close()

                        # Process buffer
                        yield {"type": "status", "status": "storing"}

                        def _on_progress(dataset: str, done: int, total: int) -> None:
                            pass  # progress emitted after thread returns

                        import_stats = await asyncio.to_thread(
                            process_import_buffer, tmp.name, _on_progress,
                        )
                        os.unlink(tmp.name)

                        # Send ack back to consumer
                        ack = json.dumps({"status": 200, "body": json.dumps(import_stats)})
                        await ws.send(ack)
                        yield {"type": "push_complete", "stats": import_stats}

                    except Exception as e:
                        tmp.close()
                        try:
                            os.unlink(tmp.name)
                        except OSError:
                            pass
                        yield {"type": "error", "message": str(e)}
                        return

                else:
                    await ws.send(json.dumps({"status": 404, "body": "unknown path"}))

        yield {"type": "done"}

    except asyncio.CancelledError:
        yield {"type": "cancelled"}
    except websockets.exceptions.ConnectionClosed:
        yield {"type": "done"}
    except Exception as e:
        yield {"type": "error", "message": str(e)}
    finally:
        _sessions.pop(sid, None)


# ── Receive session (consumer) ───────────────────────────────────

async def receive_session(room_id: str, pin: str) -> AsyncGenerator[dict, None]:
    """Async generator yielding SSE events for a sync receive (consumer) session."""
    sid = _session_id()
    cancel = asyncio.Event()
    _sessions[sid] = cancel

    yield {"type": "session", "session_id": sid}

    identity = get_device_identity()

    try:
        url = _relay_url(room_id, "consumer")
        async with ws_connect(url, max_size=None) as ws:
            yield {"type": "status", "status": "connected"}

            # Send HELLO
            hello = json.dumps({
                "method": "HELLO",
                "hostname": identity["hostname"],
                "user": identity["user"],
                "os": identity["os"],
                "fingerprint": identity["fingerprint"],
                "pin": pin,
            })
            await ws.send(hello)
            yield {"type": "status", "status": "handshake_sent"}

            # Read handshake response
            raw = await asyncio.wait_for(ws.recv(), timeout=30)
            resp = json.loads(raw)
            if resp.get("status") != 200:
                body = resp.get("body", "rejected")
                yield {"type": "error", "message": f"Handshake rejected: {body}"}
                return

            yield {"type": "status", "status": "handshake_ok"}

            # ── Phase 1: Pull remote data ─────────────────────────
            yield {"type": "status", "status": "pulling"}
            pull_req = json.dumps({
                "method": "GET",
                "path": "/sync/pull",
                "body": {"batch_size": DEFAULT_BATCH_SIZE},
            })
            await ws.send(pull_req)

            # Buffer to temp file
            tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
            pull_totals: dict[str, int] = {}
            pull_done: dict[str, int] = {}
            has_vectors = False

            try:
                while True:
                    _check_cancel(cancel)
                    raw = await ws.recv()
                    tmp.write(raw + "\n")
                    msg = json.loads(raw)
                    kind = msg.get("kind")
                    if kind == "meta":
                        pull_totals = msg.get("datasets", {})
                        has_vectors = msg.get("vectors", False)
                    elif kind == "batch":
                        ds = msg.get("dataset", "")
                        pull_done[ds] = pull_done.get(ds, 0) + len(msg.get("records", []))
                        yield {"type": "progress", "phase": "pull", "dataset": ds, "done": pull_done[ds], "total": pull_totals.get(ds, 0)}
                    elif kind == "done":
                        break
                    elif kind == "error":
                        yield {"type": "error", "message": msg.get("message", "Remote export error")}
                        tmp.close()
                        os.unlink(tmp.name)
                        return
                tmp.close()
            except Exception as e:
                tmp.close()
                try:
                    os.unlink(tmp.name)
                except OSError:
                    pass
                yield {"type": "error", "message": f"Pull failed: {e}"}
                return

            # Process buffer
            yield {"type": "status", "status": "storing"}
            progress_events: list[dict] = []

            def _on_pull_progress(dataset: str, done: int, total: int) -> None:
                progress_events.append({"dataset": dataset, "done": done, "total": total})

            pull_stats = await asyncio.to_thread(
                process_import_buffer, tmp.name, _on_pull_progress,
            )
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

            yield {"type": "pull_complete", "stats": pull_stats}

            # ── Phase 2: Push local data ──────────────────────────
            yield {"type": "status", "status": "pushing"}
            push_req = json.dumps({
                "method": "POST",
                "path": "/sync/push",
                "body": {"batch_size": DEFAULT_BATCH_SIZE},
            })
            await ws.send(push_req)

            # Stream export
            lines = await asyncio.to_thread(stream_export_lines)
            push_totals: dict[str, int] = {}
            push_sent: dict[str, int] = {}
            for line in lines:
                _check_cancel(cancel)
                await ws.send(line)
                msg = json.loads(line)
                kind = msg.get("kind")
                if kind == "meta":
                    push_totals = msg.get("datasets", {})
                elif kind == "batch":
                    ds = msg.get("dataset", "")
                    push_sent[ds] = push_sent.get(ds, 0) + len(msg.get("records", []))
                    yield {"type": "progress", "phase": "push", "dataset": ds, "done": push_sent[ds], "total": push_totals.get(ds, 0)}

            # Wait for ack
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=300)
                ack = json.loads(raw)
                push_stats = {}
                if ack.get("status") == 200:
                    body = ack.get("body", "{}")
                    if isinstance(body, str):
                        push_stats = json.loads(body)
                    else:
                        push_stats = body
                yield {"type": "push_complete", "stats": push_stats}
            except Exception:
                yield {"type": "push_complete", "stats": {}}

        yield {"type": "done", "pull_stats": pull_stats, "push_stats": push_stats if "push_stats" in dir() else {}}

    except asyncio.CancelledError:
        yield {"type": "cancelled"}
    except websockets.exceptions.ConnectionClosed:
        yield {"type": "error", "message": "Connection closed unexpectedly"}
    except Exception as e:
        yield {"type": "error", "message": str(e)}
    finally:
        _sessions.pop(sid, None)
