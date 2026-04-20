"""FastAPI backend for Imprint dashboard.

Exposes data endpoints for the Next.js UI.
Adds endpoints for CLI command execution and config management.

Usage:
    python -m imprint.api [--port 8420]
    # Or via uvicorn:
    uvicorn imprint.api:app --host 127.0.0.1 --port 8420
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from fastapi import FastAPI, Query, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import config
from . import vectorstore as vs
from . import imprint_graph as kg
from . import chat as chat_mod
from . import chat_store
from . import queue as job_queue

from .cli_viz import (
    build_overview,
    build_project_detail,
    build_node_page,
    build_stats,
    build_cross_project_similarity,
    build_timeline,
    build_kg_data,
    build_source_lineage,
    build_topic_overview,
    build_topic_detail,
    build_graph_scope,
    get_neighbors,
    get_memory,
    check_for_changes,
    _get_overview,
    _get_project_detail,
    _build_global_filter,
)

app = FastAPI(title="Imprint API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)


@app.on_event("startup")
async def _start_queue_dispatcher():
    job_queue.recover_on_startup()
    asyncio.create_task(job_queue.dispatcher_loop())
    asyncio.create_task(job_queue.reaper_loop())


# ── Filter helpers ─────────────────────────────────────────────

def _parse_filters(
    type: list[str] | None = Query(None),
    lang: list[str] | None = Query(None),
    domain: list[str] | None = Query(None),
    layer: list[str] | None = Query(None),
) -> dict | None:
    f = {}
    if type:
        f["type"] = type
    if lang:
        f["lang"] = lang
    if domain:
        f["domain"] = domain
    if layer:
        f["layer"] = layer
    return f if f else None


# ── Data endpoints ─────────────────────────────────────────────

@app.get("/api/overview")
def api_overview(
    type: list[str] | None = Query(None),
    lang: list[str] | None = Query(None),
    domain: list[str] | None = Query(None),
    layer: list[str] | None = Query(None),
):
    filters = _parse_filters(type, lang, domain, layer)
    return _get_overview(filters)


@app.get("/api/project/{name:path}")
def api_project(name: str):
    return _get_project_detail(unquote(name))


@app.get("/api/nodes")
def api_nodes(
    project: str = "",
    type: str = "",
    domain: str = "",
    lang: str = "",
    limit: int = 500,
    offset: str = "",
):
    return build_node_page(
        project=project, type_=type, domain=domain,
        lang=lang, limit=limit, offset_id=offset,
    )


@app.get("/api/search")
def api_search(
    q: str = "",
    project: str = "",
    type: str = "",
    domain: str = "",
    limit: int = 30,
):
    if not q:
        return {"nodes": [], "total": 0}
    from .cli_viz import search_nodes
    return search_nodes(q, project=project, type_=type, domain=domain, limit=limit)


@app.get("/api/neighbors")
def api_neighbors(id: str = "", k: int = 10):
    if not id:
        return {"source": "", "neighbors": []}
    return get_neighbors(id, k)


@app.get("/api/stats")
def api_stats():
    return build_stats()


@app.get("/api/version")
def api_version():
    """Return the installed imprint CLI version.

    Shells out to ``imprint version`` once per process and caches the result.
    Falls back to ``"unknown"`` when the binary can't be located or invoked.
    """
    global _CACHED_VERSION
    if _CACHED_VERSION is not None:
        return {"version": _CACHED_VERSION}
    try:
        out = subprocess.check_output(
            [_find_imprint_binary(), "version"],
            stderr=subprocess.STDOUT, timeout=5, text=True,
        ).strip()
        # Output is "imprint <version>"; strip the prefix if present.
        _CACHED_VERSION = out.split(" ", 1)[1] if out.startswith("imprint ") else out
    except Exception:
        _CACHED_VERSION = "unknown"
    return {"version": _CACHED_VERSION}


_CACHED_VERSION: str | None = None


@app.get("/api/cross-project")
def api_cross_project():
    return build_cross_project_similarity()


@app.get("/api/memory/{mid:path}")
def api_memory(mid: str):
    return get_memory(unquote(mid))


@app.get("/api/timeline")
def api_timeline(project: str = "", limit: int = 500):
    return build_timeline(project=project, limit=limit)


# ── Knowledge graph ────────────────────────────────────────────

@app.get("/api/kg")
def api_kg(subject: str = "", limit: int = 200):
    return build_kg_data(subject=subject, limit=limit)


@app.get("/api/kg/entity/{entity:path}")
def api_kg_entity(entity: str):
    return build_kg_data(subject=unquote(entity), limit=50)


# ── Sources ───────────────────────────────────────────────────

@app.get("/api/sources")
def api_sources(
    project: str = "",
    lang: str = "",
    layer: str = "",
    limit: int = 100,
):
    """List all indexed source files with chunk counts."""
    sources = vs.list_sources(
        project=project, lang=lang, layer=layer, limit=limit,
    )
    return {
        "sources": [{"source": s, "chunks": c} for s, c in sources],
        "total": len(sources),
    }


@app.get("/api/sources/summary/{source_key:path}")
def api_source_summary(source_key: str, project: str = ""):
    """Summary metadata for a single source file."""
    summary = vs.get_source_summary(unquote(source_key), project=project)
    if summary is None:
        return {"error": "source not found", "source": source_key}
    return summary


@app.get("/api/source/{source_key:path}")
def api_source(source_key: str):
    return build_source_lineage(unquote(source_key))


# ── Topics ─────────────────────────────────────────────────────

@app.get("/api/topics")
def api_topics(
    type: list[str] | None = Query(None),
    lang: list[str] | None = Query(None),
    domain: list[str] | None = Query(None),
    layer: list[str] | None = Query(None),
):
    filters = _parse_filters(type, lang, domain, layer)
    return build_topic_overview(filters)


@app.get("/api/topic/{name:path}")
def api_topic(name: str):
    return build_topic_detail(unquote(name))


@app.get("/api/graph")
def api_graph(scope: str = "root", depth: int = 1):
    return build_graph_scope(unquote(scope), max(1, min(int(depth or 1), 3)))


# ── Workspaces ─────────────────────────────────────────────────

@app.get("/api/workspaces")
def api_workspaces():
    return {
        "active": config.get_active_workspace(),
        "workspaces": config.get_known_workspaces(),
    }


@app.post("/api/workspace/switch")
async def api_workspace_switch(request: Request):
    body = await request.json()
    name = body.get("workspace", "")
    if not name:
        return {"error": "workspace name required"}
    err = config.validate_workspace_name(name)
    if err:
        return {"error": err}
    config.switch_workspace(name)
    vs._client = None
    _reset_after_wipe()
    return {"ok": True, "active": config.get_active_workspace()}


@app.post("/api/workspace/create")
async def api_workspace_create(request: Request):
    body = await request.json()
    name = (body.get("name") or body.get("workspace") or "").strip()
    if not name:
        return JSONResponse({"error": "workspace name required"}, status_code=400)
    err = config.validate_workspace_name(name)
    if err:
        return JSONResponse({"error": err}, status_code=400)
    created = config.register_workspace(name)
    return {
        "ok": True,
        "created": created,
        "active": config.get_active_workspace(),
        "workspaces": config.get_known_workspaces(),
    }


# ── Chat ───────────────────────────────────────────────────────

@app.get("/api/chat/status")
def api_chat_status():
    return chat_mod.status()


@app.get("/api/chat/sessions")
def api_chat_sessions():
    return {"sessions": chat_store.list_sessions()}


@app.post("/api/chat/sessions")
def api_chat_create_session():
    sid = chat_store.create_session()
    sess = chat_store.get_session(sid)
    return sess or {"id": sid}


@app.get("/api/chat/sessions/{sid}")
def api_chat_get_session(sid: str):
    sess = chat_store.get_session(unquote(sid))
    if not sess:
        return JSONResponse({"error": "not found"}, status_code=404)
    sess["messages"] = chat_store.get_messages(unquote(sid))
    return sess


@app.post("/api/chat/sessions/{sid}/rename")
async def api_chat_rename(sid: str, request: Request):
    body = await request.json()
    title = (body.get("title") or "").strip() or "New chat"
    ok = chat_store.rename_session(unquote(sid), title)
    return {"ok": ok}


@app.delete("/api/chat/sessions/{sid}")
def api_chat_delete(sid: str):
    ok = chat_store.delete_session(unquote(sid))
    return {"ok": ok}


@app.post("/api/chat")
async def api_chat(request: Request):
    body = await request.json()
    sid = body.get("session_id", "")
    user_msg = body.get("message", "")
    if not sid or not user_msg.strip():
        return JSONResponse({"error": "session_id and message required"}, status_code=400)

    sess = chat_store.get_session(sid)
    if not sess:
        return JSONResponse({"error": "session not found"}, status_code=404)

    prior = chat_store.get_messages(sid)
    chat_store.append_message(sid, role="user", content=user_msg)
    workspace = config.get_active_workspace()

    def stream():
        pending_tool = None
        try:
            for ev in chat_mod.stream_reply(prior, user_msg, workspace=workspace):
                etype = ev.get("type")
                if etype == "tool_call":
                    pending_tool = ev
                elif etype == "tool_result" and pending_tool:
                    chat_store.append_message(
                        sid, role="tool", content=ev.get("result", ""),
                        tool_name=pending_tool.get("name"),
                        tool_args=pending_tool.get("args"),
                    )
                    pending_tool = None
                elif etype == "assistant_message":
                    chat_store.append_message(
                        sid, role="assistant", content=ev.get("text", ""),
                    )
                yield f"data: {json.dumps(ev)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


# ── SSE live updates ───────────────────────────────────────────

_shutdown_event = threading.Event()


@app.get("/api/stream")
def api_stream():
    def event_stream():
        version = 0
        while not _shutdown_event.is_set():
            if check_for_changes():
                from .cli_viz import _data_version
                version = _data_version
                yield f"event: update\ndata: {json.dumps({'version': version})}\n\n"
            _shutdown_event.wait(2)  # interruptible sleep

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ── UI heartbeat / auto-shutdown ──────────────────────────────

_last_ui_ping: float = 0.0
_auto_shutdown = False
_SHUTDOWN_GRACE = 15  # seconds without ping before shutdown


@app.get("/api/ping")
def api_ping():
    global _last_ui_ping
    _last_ui_ping = time.time()
    return {"ok": True}


def _auto_shutdown_watcher():
    """Background thread: shuts down server when UI disconnects."""
    while not _shutdown_event.is_set():
        _shutdown_event.wait(5)
        if _last_ui_ping and time.time() - _last_ui_ping > _SHUTDOWN_GRACE:
            _shutdown_event.set()
            os.kill(os.getpid(), signal.SIGINT)
            break


# ── Jobs (ingestion progress + queue) ─────────────────────────

def _merge_progress(job: dict, progress: dict) -> dict:
    elapsed = time.time() - progress["started_at"]
    total = progress["total"]
    processed = progress["processed"]
    pct = processed / total if total > 0 else 0
    eta = None
    if 0 < pct < 1:
        eta = elapsed / pct * (1 - pct)
    return {
        **job,
        "phase": progress.get("phase"),
        "processed": processed,
        "total": total,
        "stored": progress.get("stored", 0),
        "skipped": progress.get("skipped", 0),
        "projects": progress.get("projects", []),
        "elapsed": round(elapsed, 1),
        "percent": round(pct * 100, 1),
        "eta_seconds": round(eta, 1) if eta is not None else None,
    }


def _attach_progress(job: dict | None) -> dict | None:
    """Enrich a running-job row with the live progress-file fields.

    The DB row stores the Go subprocess pid; the progress file is written
    by the Python grandchild, so the pids don't match. Because only one
    job runs at a time we just merge any live progress into the active
    row.
    """
    if job is None:
        return None
    from .progress import read_progress
    progress = read_progress()
    if progress is None:
        return job
    return _merge_progress(job, progress)


def _active_from_any_source() -> dict | None:
    """Return the currently running job — DB row or synthesized from the
    queue lock. CLI-launched jobs don't have a DB row (they only hold the
    flock), so we reconstruct one from lock metadata + progress file.

    The progress file's PID is the Python subprocess, while the lock
    holder's PID is the Go binary that spawned it — so we don't require
    them to match. If a progress file exists and its PID is alive, we
    merge it in.
    """
    db_active = job_queue.active_job()
    if db_active is not None:
        return _attach_progress(db_active)

    from . import queue_lock
    from .progress import read_progress
    holder = queue_lock.read_holder()
    progress = read_progress()
    if holder is None and progress is None:
        return None

    if holder is not None:
        synthetic = {
            "id": holder.get("job_id") or f"cli-{holder.get('pid')}",
            "command": holder.get("command", "unknown"),
            "body": {},
            "status": "running",
            "pid": holder.get("pid"),
            "pgid": None,
            "exit_code": None,
            "error": None,
            "created_at": holder.get("started_at"),
            "started_at": holder.get("started_at"),
            "ended_at": None,
            "source": "cli",
        }
    else:
        # Progress file says a job is live but the lock was already released
        # (e.g. the Go CLI exited but the Python progress writer lags by a
        # tick). Show the python process as active.
        synthetic = {
            "id": f"cli-{progress.get('pid')}",
            "command": progress.get("command", "unknown"),
            "body": {},
            "status": "running",
            "pid": progress.get("pid"),
            "pgid": None,
            "exit_code": None,
            "error": None,
            "created_at": progress.get("started_at"),
            "started_at": progress.get("started_at"),
            "ended_at": None,
            "source": "cli",
        }

    if progress is not None:
        synthetic = _merge_progress(synthetic, progress)
    return synthetic


@app.get("/api/jobs")
def api_jobs():
    """Back-compat endpoint — returns the currently running job (if any) in the
    legacy ``{"jobs": [...]}`` shape. New clients should prefer ``/api/queue``.
    """
    active = _active_from_any_source()
    return {"jobs": [active] if active else []}


@app.get("/api/queue")
def api_queue(recent_limit: int = 20):
    data = job_queue.list_queue(recent_limit=recent_limit)
    data["active"] = _active_from_any_source()
    return data


@app.get("/api/jobs/{job_id}")
def api_job_detail(job_id: str):
    job = job_queue.get_job(job_id)
    if not job:
        return JSONResponse({"error": "not found"}, status_code=404)
    if job.get("status") == "running":
        job = _attach_progress(job)
    return job


@app.get("/api/jobs/{job_id}/stream")
async def api_job_stream(job_id: str):
    job = job_queue.get_job(job_id)
    if not job:
        return JSONResponse({"error": "not found"}, status_code=404)

    async def gen():
        try:
            async for line in job_queue.tail_output(job_id):
                yield f"data: {json.dumps({'type': 'output', 'text': line})}\n\n"
            final = job_queue.get_job(job_id) or {}
            done_payload = {
                "type": "done",
                "exit_code": final.get("exit_code"),
                "status": final.get("status"),
            }
            yield f"data: {json.dumps(done_payload)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/jobs/{job_id}/cancel")
def api_job_cancel(job_id: str):
    # First try the SQLite queue (UI-launched jobs).
    result = job_queue.cancel(job_id)
    if result.get("ok"):
        return result
    # Fall back: maybe this is a CLI-launched job represented only by the
    # lock file + progress file.
    from . import queue_lock
    from .progress import read_progress

    holder = queue_lock.read_holder()
    progress = read_progress()
    if holder is None and progress is None:
        return result

    # Match the synthetic id the UI sent.
    candidate_ids = set()
    if holder:
        candidate_ids.add(holder.get("job_id") or f"cli-{holder.get('pid')}")
        candidate_ids.add(f"cli-{holder.get('pid')}")
    if progress:
        candidate_ids.add(f"cli-{progress.get('pid')}")
    if job_id not in candidate_ids:
        return result

    # Kill the Python subprocess (does the actual work) first — its in-process
    # httpx / llama-cpp threads die with it. If we only have the Go binary
    # PID (holder.pid), kill that too so its cmd.Wait() returns and it
    # releases the flock cleanly.
    targets: list[int] = []
    if progress and progress.get("pid"):
        targets.append(int(progress["pid"]))
    if holder and holder.get("pid") and holder["pid"] not in targets:
        targets.append(int(holder["pid"]))

    killed_any = False
    for pid in targets:
        try:
            os.kill(pid, signal.SIGTERM)
            killed_any = True
        except ProcessLookupError:
            pass

    if not killed_any:
        return {"ok": False, "error": "process already gone"}

    def _escalate(pids: list[int]):
        import time as _t
        _t.sleep(3.0)
        for p in pids:
            try:
                os.kill(p, 0)
            except ProcessLookupError:
                continue
            try:
                os.kill(p, signal.SIGKILL)
            except ProcessLookupError:
                pass

    threading.Thread(target=_escalate, args=(targets,), daemon=True).start()
    return {"ok": True, "was_running": True, "source": "cli", "pids": targets}


# ── Config ─────────────────────────────────────────────────────

@app.get("/api/config")
def api_config():
    from .config_schema import SETTINGS, resolve
    settings = []
    for s in SETTINGS:
        val, source = resolve(s.key)
        settings.append({
            "key": s.key,
            "value": val,
            "source": source,
            "default": s.default,
            "type": s.type.__name__,
            "env": s.env,
            "desc": s.desc,
        })
    return {"settings": settings}


@app.put("/api/config/{key:path}")
async def api_config_set(key: str, request: Request):
    body = await request.json()
    value = body.get("value")
    if value is None:
        return {"error": "value required"}
    from .config_schema import set_value
    try:
        val = set_value(key, str(value))
        return {"ok": True, "value": val}
    except Exception as e:
        return {"error": str(e)}


# ── CLI command execution ──────────────────────────────────────


def _reset_after_wipe():
    """Clear all in-memory caches after a wipe so the dashboard reflects empty state."""
    # Reset vectorstore client (Qdrant was restarted, old connection is stale)
    vs._client = None
    # Reset knowledge graph connections (stale after DB files deleted)
    for conn in kg._conns.values():
        try:
            conn.close()
        except Exception:
            pass
    kg._conns.clear()
    # Clear dashboard caches if cli_viz is loaded
    try:
        from . import cli_viz
        cli_viz._overview_cache = None
        cli_viz._project_cache.clear()
        cli_viz._cross_project_cache = None
        cli_viz._last_wal_size = 0
        cli_viz._last_row_count = -1
        cli_viz._data_version = 0
    except Exception:
        pass


# Allowed commands — just run the `imprint` binary directly.
_ALLOWED_COMMANDS = {
    "status", "ingest", "ingest-url", "refresh", "refresh-urls", "retag",
    "learn", "config", "wipe", "sync", "migrate", "workspace",
}


def _find_imprint_binary() -> str:
    """Find the imprint binary."""
    import shutil
    # Check PATH first
    found = shutil.which("imprint")
    if found:
        return found
    # Check bundled binaries
    import platform as plat
    system = plat.system().lower()
    machine = plat.machine().lower()
    arch = "amd64" if machine in ("x86_64", "amd64") else "arm64"
    bin_name = f"imprint-{system}-{arch}"
    bin_path = Path(__file__).parent.parent / "bin" / bin_name
    if bin_path.exists():
        return str(bin_path)
    return "imprint"


@app.post("/api/commands/{command}")
async def api_run_command(command: str, request: Request):
    """Enqueue a CLI command.

    Returns ``{job_id, position}`` immediately. Clients should open
    ``GET /api/jobs/{job_id}/stream`` for live output and call
    ``POST /api/jobs/{job_id}/cancel`` to abort.
    """
    body = {}
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            body = await request.json()
        except Exception:
            pass

    if command not in _ALLOWED_COMMANDS:
        return JSONResponse({"error": f"unknown command '{command}'"}, status_code=404)

    job_id = job_queue.enqueue(command, body)
    snapshot = job_queue.list_queue(recent_limit=0)
    position = 0
    if snapshot["active"] and snapshot["active"]["id"] != job_id:
        position = 1 + next(
            (i for i, j in enumerate(snapshot["queued"]) if j["id"] == job_id),
            len(snapshot["queued"]),
        )
    return {"job_id": job_id, "position": position}


# ── Sync ───────────────────────────────────────────────────────

@app.post("/api/sync/export")
async def api_sync_export(request: Request):
    body = await request.json() if request.headers.get("content-type") == "application/json" else {}
    from .cli_sync import export_snapshot
    workspace = body.get("workspace")
    output_dir = Path(body["output"]) if body.get("output") else None
    try:
        bundle = export_snapshot(output_dir=output_dir, workspace=workspace)
        return {"ok": True, "path": str(bundle)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/sync/import")
async def api_sync_import(request: Request):
    body = await request.json()
    bundle_path = body.get("path")
    if not bundle_path:
        return JSONResponse({"error": "path required"}, status_code=400)
    from .cli_sync import import_snapshot
    workspace = body.get("workspace")
    try:
        import_snapshot(bundle_path=Path(bundle_path), workspace=workspace)
        return {"ok": True}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Sync: file download / upload ──────────────────────────────

@app.get("/api/sync/export/download")
async def api_sync_export_download():
    """Create snapshot bundle, zip it, and stream as browser download."""
    from .cli_sync import export_snapshot

    try:
        bundle = export_snapshot()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    zip_path = tempfile.mktemp(suffix=".zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in bundle.rglob("*"):
            if f.is_file():
                zf.write(f, f.relative_to(bundle.parent))

    filename = bundle.name + ".zip"

    def _iter_zip():
        try:
            with open(zip_path, "rb") as f:
                while chunk := f.read(1024 * 1024):
                    yield chunk
        finally:
            try:
                os.unlink(zip_path)
            except OSError:
                pass

    return StreamingResponse(
        _iter_zip(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/sync/import/upload")
async def api_sync_import_upload(file: UploadFile = File(...)):
    """Accept a zip upload, extract, and restore the snapshot bundle."""
    tmp_zip = tempfile.mktemp(suffix=".zip")
    try:
        with open(tmp_zip, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                f.write(chunk)

        extract_dir = tempfile.mkdtemp()
        with zipfile.ZipFile(tmp_zip) as zf:
            zf.extractall(extract_dir)
        os.unlink(tmp_zip)
        tmp_zip = ""

        # Find the bundle dir (contains manifest.json)
        bundle = None
        for root, _dirs, files in os.walk(extract_dir):
            if "manifest.json" in files:
                bundle = Path(root)
                break
        if bundle is None:
            shutil.rmtree(extract_dir, ignore_errors=True)
            return JSONResponse({"error": "No manifest.json found in archive"}, status_code=400)

        from .cli_sync import import_snapshot
        import_snapshot(bundle_path=bundle)
        shutil.rmtree(extract_dir, ignore_errors=True)
        return {"ok": True}

    except Exception as e:
        if tmp_zip:
            try:
                os.unlink(tmp_zip)
            except OSError:
                pass
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Sync: live relay sessions (SSE) ──────────────────────────

@app.get("/api/sync/serve")
async def api_sync_serve():
    """SSE stream for provider (serve) mode. Connects to relay, yields events."""
    from .sync_ws import serve_session

    async def event_stream():
        async for event in serve_session():
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/sync/receive")
async def api_sync_receive(request: Request):
    """SSE stream for consumer (receive) mode. Needs room_id and pin."""
    body = await request.json()
    room_id = body.get("room_id", "").strip()
    pin = body.get("pin", "").strip()
    if not room_id or not pin:
        return JSONResponse({"error": "room_id and pin required"}, status_code=400)

    from .sync_ws import receive_session

    async def event_stream():
        async for event in receive_session(room_id, pin):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/sync/cancel")
async def api_sync_cancel(request: Request):
    """Cancel an active sync session by session_id."""
    body = await request.json()
    session_id = body.get("session_id", "")
    if not session_id:
        return JSONResponse({"error": "session_id required"}, status_code=400)

    from .sync_ws import cancel_session
    cancel_session(session_id)
    return {"ok": True}


@app.get("/api/desktop-learn/history")
def api_desktop_learn_history():
    """Return the tracker of previously-indexed desktop-export zips.

    Shape: ``{"seen": {<sha>: {path, origin, indexed_at, chunks}}, "count": N}``.
    Used by the dashboard's Sync page to show which Claude / ChatGPT
    Desktop exports have been ingested.
    """
    from .cli_desktop_learn import load_history
    return load_history()


@app.post("/api/desktop-learn/scan")
async def api_desktop_learn_scan(request: Request):
    """Run a one-shot Downloads scan for Claude / ChatGPT Desktop exports.

    Body (all optional): ``{"paths": ["/extra/dir", ...]}``. The scanner's
    default roots (``~/Downloads`` + WSL Windows-side Downloads) are
    always included.

    Returns the structured scan result:

        {
          "roots": [...],
          "scanned": int,
          "skipped_seen": int,
          "indexed_zips": int,
          "inserted_chunks": int,
          "indexed": [{path, origin, chunks, indexed_at}, ...]
        }

    Blocking — typical cost is a few ms when no new exports exist; one
    new export takes as long as chunking + embedding its
    ``conversations.json`` (seconds to minutes depending on size).
    """
    body: dict = {}
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            body = await request.json()
        except Exception:
            pass
    paths = body.get("paths") or []
    if not isinstance(paths, list):
        return JSONResponse({"error": "paths must be an array"}, status_code=400)

    from .cli_desktop_learn import scan_once_api
    try:
        result = await asyncio.to_thread(scan_once_api, [str(p) for p in paths])
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    return result


@app.post("/api/sync/approve")
async def api_sync_approve(request: Request):
    """Resolve a pending provider-side approval prompt.

    Mirrors the CLI's ``[y/n/t]`` prompt: ``decision`` must be one of
    ``accept`` (one-time), ``trust`` (accept + persist fingerprint), or
    ``reject``. The server streams an ``approval_required`` SSE event on
    ``/api/sync/serve`` when a non-trusted peer completes the PIN check;
    the UI then POSTs here with the session_id and the user's choice.
    """
    body = await request.json()
    session_id = body.get("session_id", "")
    decision = (body.get("decision") or "").lower()
    if not session_id:
        return JSONResponse({"error": "session_id required"}, status_code=400)
    if decision not in ("accept", "trust", "reject"):
        return JSONResponse(
            {"error": "decision must be 'accept', 'trust', or 'reject'"},
            status_code=400,
        )

    from .sync_ws import submit_approval
    if not submit_approval(session_id, decision):
        return JSONResponse(
            {"error": "no pending approval for this session (already decided, cancelled, or expired)"},
            status_code=404,
        )
    return {"ok": True, "decision": decision}


# ── Filesystem browser ────────────────────────────────────────

def _is_wsl() -> bool:
    try:
        with open("/proc/version") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


@app.get("/api/fs/roots")
def api_fs_roots():
    """Return starting points the server can see (server-side paths)."""
    import platform as _plat

    roots: list[dict[str, str]] = [
        {"label": "Home", "path": str(Path.home())},
        {"label": "Working dir", "path": os.getcwd()},
    ]
    system = _plat.system()
    if system == "Linux":
        if _is_wsl() and os.path.isdir("/mnt"):
            try:
                for name in sorted(os.listdir("/mnt")):
                    p = f"/mnt/{name}"
                    if os.path.isdir(p):
                        roots.append({"label": f"Windows {name.upper()}:", "path": p})
            except OSError:
                pass
        roots.append({"label": "/", "path": "/"})
    elif system == "Darwin":
        roots.append({"label": "/", "path": "/"})
        if os.path.isdir("/Volumes"):
            roots.append({"label": "/Volumes", "path": "/Volumes"})
    elif system == "Windows":
        import string
        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            if os.path.isdir(drive):
                roots.append({"label": f"{letter}:", "path": drive})
    return {"roots": roots, "platform": system, "wsl": system == "Linux" and _is_wsl()}


@app.get("/api/fs/list")
def api_fs_list(path: str = "", show_hidden: bool = False):
    """List directory entries with absolute paths (resolved server-side).

    Browser file inputs never expose absolute paths; this endpoint lets the UI
    render a server-side picker that returns real paths the server can read.
    """
    target = path.strip() or str(Path.home())
    try:
        p = Path(target).expanduser()
        # Only resolve if it exists — avoid resolving to cwd-relative garbage.
        if p.exists():
            p = p.resolve()
    except (OSError, RuntimeError) as e:
        return JSONResponse({"error": f"invalid path: {e}"}, status_code=400)

    if not p.exists():
        return JSONResponse({"error": f"path does not exist: {p}"}, status_code=404)

    parent = str(p.parent) if p.parent != p else None

    if p.is_file():
        return {
            "path": str(p),
            "parent": parent,
            "is_file": True,
            "entries": [],
        }

    entries: list[dict[str, Any]] = []
    try:
        children = list(p.iterdir())
    except PermissionError:
        return JSONResponse({"error": f"permission denied: {p}"}, status_code=403)
    except OSError as e:
        return JSONResponse({"error": f"read failed: {e}"}, status_code=400)

    for child in children:
        name = child.name
        if not show_hidden and name.startswith("."):
            continue
        try:
            is_dir = child.is_dir()
        except OSError:
            continue
        entries.append({
            "name": name,
            "is_dir": is_dir,
            "abs_path": str(child),
        })

    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))

    return {
        "path": str(p),
        "parent": parent,
        "is_file": False,
        "entries": entries,
    }


# ── Static file serving ───────────────────────────────────────

_UI_DIR = Path(__file__).parent / "ui" / "out"


def mount_static():
    """Mount Next.js static export if available."""
    if _UI_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(_UI_DIR), html=True), name="ui")


# ── Browser launcher ──────────────────────────────────────────

def _launch_browser(url: str):
    """Open browser in app/kiosk mode (Chrome preferred) or fallback."""
    import os
    import platform as plat
    import shutil
    import webbrowser

    chrome_flags = [
        f"--app={url}",
        "--window-size=1400,900",
        "--disable-extensions",
        "--disable-default-apps",
        "--no-first-run",
    ]

    candidates: list[str] = []
    system = plat.system()

    if system == "Linux":
        is_wsl = os.path.exists("/proc/version") and "microsoft" in open("/proc/version").read().lower()
        if is_wsl:
            for win_path in [
                "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe",
                "/mnt/c/Program Files (x86)/Google/Chrome/Application/chrome.exe",
            ]:
                if os.path.exists(win_path):
                    candidates.append(win_path)
        candidates.extend(["chromium", "chromium-browser", "google-chrome", "google-chrome-stable"])
    elif system == "Darwin":
        candidates.append("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        candidates.extend(["chromium", "google-chrome"])
    elif system == "Windows":
        candidates.extend([
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        ])

    for candidate in candidates:
        chrome = shutil.which(candidate) or (candidate if os.path.exists(candidate) else None)
        if chrome:
            try:
                subprocess.Popen(
                    [chrome] + chrome_flags,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                return
            except Exception:
                continue

    webbrowser.open(url)


# ── Entry point ───────────────────────────────────────────────

def main():
    global _auto_shutdown, _last_ui_ping

    import argparse
    parser = argparse.ArgumentParser(description="Imprint API server")
    parser.add_argument("--port", type=int, default=8420, help="Port to listen on")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--dev", action="store_true", help="Development mode (no static mount)")
    parser.add_argument("--no-browser", action="store_true", help="Don't open browser")
    parser.add_argument("--auto-shutdown", action="store_true",
                        help="Shut down when the UI disconnects (used by `imprint ui`)")
    args = parser.parse_args()

    if not args.dev:
        mount_static()

    url = f"http://{args.host}:{args.port}"
    print(f"\n  \033[0;33m\u2726 Imprint Dashboard at {url}\033[0m")
    print(f"  \033[2mPress Ctrl+C to stop\033[0m\n")

    if args.auto_shutdown:
        _auto_shutdown = True
        _last_ui_ping = time.time()  # grace period for initial page load
        t = threading.Thread(target=_auto_shutdown_watcher, daemon=True)
        t.start()

    # Set shutdown event on SIGINT/SIGTERM so SSE generators exit promptly.
    # Store original handlers — uvicorn installs its own, but ours fires first
    # to unblock any sleeping threads.
    _orig_sigint = signal.getsignal(signal.SIGINT)
    _orig_sigterm = signal.getsignal(signal.SIGTERM)

    def _on_shutdown(signum, frame):
        _shutdown_event.set()
        # Restore and re-raise so uvicorn's handler runs
        signal.signal(signal.SIGINT, _orig_sigint)
        signal.signal(signal.SIGTERM, _orig_sigterm)
        os.kill(os.getpid(), signum)

    signal.signal(signal.SIGINT, _on_shutdown)
    signal.signal(signal.SIGTERM, _on_shutdown)

    if not args.no_browser:
        threading.Timer(0.8, lambda: _launch_browser(url)).start()

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
