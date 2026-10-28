"""FastAPI backend for Imprint dashboard.

Exposes data endpoints for the Next.js UI.
Adds endpoints for CLI command execution and config management.

Usage:
    python -m imprint.api [--port 8420]
    # Or via uvicorn:
    uvicorn imprint.api:app --host 127.0.0.1 --port 8420 --no-proxy-headers
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

import uvicorn
from fastapi import FastAPI, Query, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import config
from . import vectorstore as vs
from . import imprint_graph as kg
from . import chat as chat_mod
from . import chat_store
from . import commands
from . import queue as job_queue
from . import updates
from .http_security import local_origin, local_host, loopback_address
from .processes import is_alive

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
    build_source_read,
    build_graph_scope,
    get_neighbors,
    get_memory,
    data_fingerprint,
    reset_cache,
    _get_overview,
    _get_project_detail,
    _build_global_filter,
)

app = FastAPI(title="Imprint API", version="1.0.0")
app.state.container_bind = False
app.state.server = None  # The DashboardServer serving this app, if any.


@app.exception_handler(vs.EmbeddingProfileError)
async def embedding_profile_conflict(_request: Request, error: vs.EmbeddingProfileError):
    return JSONResponse({
        "error": "embedding_profile_conflict",
        "detail": str(error),
        "collection": error.collection,
    }, status_code=409)


app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(?:127\.0\.0\.1|localhost|\[::1\])(?::[0-9]+)?",
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)


@app.middleware("http")
async def reject_remote_origin(request: Request, call_next):
    # Host and Origin are attacker-controlled HTTP headers, not proof that a
    # connection came from this machine. Container NAT is an explicit exception;
    # its host-side port publication must enforce the loopback boundary.
    if not app.state.container_bind and (
        request.client is None or not loopback_address(request.client.host)
    ):
        return JSONResponse({"detail": "Only loopback connections are allowed"}, status_code=403)
    # CORS alone only controls reading responses; it does not prevent a
    # malicious website from submitting requests to the local command API.
    if not local_host(request.headers.get("host", "")):
        return JSONResponse({"detail": "The dashboard requires a loopback host"}, status_code=403)
    if request.url.path.startswith('/api/browser/'):
        from .browser_api import boundary
        return await boundary(request, call_next)
    if request.headers.get('x-imprint-extension-id') is not None:
        return JSONResponse({'detail': 'Browser clients must use the capture API'}, status_code=403)
    origin = request.headers.get("origin")
    if origin is not None and not local_origin(origin):
        return JSONResponse({"detail": "Only local dashboard origins are allowed"}, status_code=403)
    return await call_next(request)


from .browser_api import router as browser_router
app.include_router(browser_router)
from .voice_api import router as voice_router
app.include_router(voice_router)


@app.on_event("startup")
async def _start_queue_dispatcher():
    job_queue.recover_on_startup()
    asyncio.create_task(job_queue.dispatcher_loop(_after_job))
    asyncio.create_task(job_queue.reaper_loop())
    from .sync_pairs import run
    app.state.sync_worker = asyncio.create_task(run())


@app.on_event('shutdown')
async def _stop_sync_worker():
    from .voice_api import shutdown
    await shutdown()
    task = getattr(app.state, 'sync_worker', None)
    if task:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


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
    limit: int = Query(30, ge=1, le=50),
    mode: str = Query("hybrid", pattern="^(hybrid|semantic)$"),
):
    if len(q) > 4000:
        return JSONResponse({"detail": "Search query exceeds 4000 characters"}, status_code=422)
    if not q.strip():
        return {"nodes": [], "total": 0}
    from .cli_viz import search_nodes
    return search_nodes(q, project=project, type_=type, domain=domain, limit=limit, mode=mode)


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
    """Return the version of this installation's imprint CLI.

    Shells out to ``imprint version`` and caches the answer for the process.
    A failed call reports ``"unknown"`` without caching it, so the next call
    tries again.
    """
    global _CACHED_VERSION
    if _CACHED_VERSION is None:
        try:
            out = subprocess.check_output(
                [job_queue.find_imprint_binary(), "version"],
                stderr=subprocess.STDOUT, timeout=5, text=True,
            ).strip()
        except Exception:
            return {"version": "unknown"}
        # Output is "imprint <version>"; strip the prefix if present.
        _CACHED_VERSION = out.split(" ", 1)[1] if out.startswith("imprint ") else out
    return {"version": _CACHED_VERSION}


_CACHED_VERSION: str | None = None


@app.get("/api/update")
def api_update(refresh: bool = False):
    """Latest release, how this installation updates, and update progress."""
    try:
        report = updates.check(refresh)
    except Exception as error:
        return {**updates.status(), "error": str(error)}
    return {**report, **updates.status()}


@app.post("/api/update")
def api_update_start():
    """Start updating to the latest release (ADR 0023)."""
    try:
        report = updates.check()
    except Exception as error:
        return JSONResponse({"error": f"Could not check for updates: {error}"}, status_code=502)
    if report.get("unsupported"):
        return JSONResponse({"error": report["unsupported"]}, status_code=400)
    if not report.get("update_available"):
        return JSONResponse({"error": f"Already on {report.get('current')}"}, status_code=400)
    try:
        updates.start()
    except updates.UpdateConflict as error:
        return JSONResponse({"error": str(error)}, status_code=409)
    return {"started": True, "target": report["latest"]}


@app.post("/api/restart")
def api_restart():
    """Restart this server so it serves the installed code (ADR 0050).

    It stops serving once in-flight requests finish and exits with
    ``RESTART_EXIT_CODE``; the desktop app or ``imprint ui`` that started it
    starts it again.
    """
    server = app.state.server
    if server is None:
        return JSONResponse({"error": "This server was not started by Imprint"}, status_code=409)
    server.restart()
    return {"restarting": True}


@app.get("/api/cross-project")
def api_cross_project():
    return build_cross_project_similarity()


@app.get("/api/memory/{mid:path}")
def api_memory(mid: str):
    data = get_memory(unquote(mid))
    return JSONResponse(data, status_code=404) if data.get("error") else data


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
    """List all indexed source files with chunk counts; conversations carry
    the first question as their title."""
    from . import source_snapshots
    sources = vs.list_sources(
        project=project, lang=lang, layer=layer, limit=limit,
    )
    titles = source_snapshots.conversation_titles()
    return {
        "sources": [{"source": s, "chunks": c, "title": titles.get(s, "")} for s, c in sources],
        "total": len(sources),
    }


@app.get("/api/sources/read")
def api_source_read(source: str, project: str | None = None, topic: str = ""):
    """A source's full redacted snapshot in readable blocks, marked where
    ``topic`` applies. A source in several projects needs ``project``."""
    try:
        data = build_source_read(source, project, topic)
    except ValueError as error:
        return JSONResponse({"error": str(error), "source": source}, status_code=409)
    return data if data["available"] else JSONResponse({
        **data, "error": "This source has no full snapshot. Learn the conversation again or refresh the file with --force to keep one.",
    }, status_code=404)


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
def api_topic(name: str, project: str | None = None):
    return build_topic_detail(unquote(name), project)


@app.get("/api/graph")
def api_graph(scope: str = "root", depth: int = 1):
    return build_graph_scope(unquote(scope), max(1, min(int(depth or 1), 3)))


# ── Workspaces ─────────────────────────────────────────────────

@app.get('/api/device')
def api_device():
    from .device import current
    return {'device': current(), 'working_directory': os.getcwd(), 'workspace': config.get_active_workspace()}

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
        return JSONResponse({"error": "workspace name required"}, status_code=400)
    err = config.validate_workspace_name(name)
    if err:
        return JSONResponse({"error": err}, status_code=400)
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


@app.get('/api/workspace/roots')
def api_workspace_roots(workspace: str = ''):
    from . import workspace_policy as policy
    try:
        ws = policy.workspace_name(workspace)
        return {'workspace': ws, 'roots': policy.roots(ws), 'exclusions': policy.exclusions(ws)}
    except ValueError as error:
        return JSONResponse({'error': str(error)}, status_code=400)


@app.post('/api/workspace/roots')
async def api_workspace_root_change(request: Request):
    from . import workspace_policy as policy
    body = await request.json()
    try:
        ws = policy.workspace_name(body.get('workspace'))
        action = body.get('action', 'add')
        if action == 'add':
            if not isinstance(body.get('allow_overlap', False), bool):
                raise ValueError('allow_overlap must be a boolean confirmation')
            return policy.add_root(body.get('path'), ws, allow_overlap=body.get('allow_overlap', False))
        if action == 'remove':
            return policy.remove_root(body.get('path'), ws)
        if action == 'restore':
            kind = body.get('kind', 'path')
            if kind not in ('path', 'source'):
                raise ValueError('kind must be path or source')
            policy.restore(body.get('path'), kind, ws)
            return {'workspace': ws, 'roots': policy.roots(ws), 'exclusions': policy.exclusions(ws)}
        raise ValueError('action must be add, remove or restore')
    except ValueError as error:
        return JSONResponse({'error': str(error)}, status_code=400)


@app.post('/api/sources/purge')
async def api_sources_purge(request: Request):
    from .purge import run_purge
    body = await request.json()
    try:
        if not isinstance(body.get('apply', False), bool) or not isinstance(body.get('recursive', False), bool):
            raise ValueError('apply and recursive must be booleans')
        if body.get('apply') and not body.get('workspace'):
            raise ValueError('Apply requires the explicit workspace returned by preview')
        result = await asyncio.to_thread(run_purge, body.get('target'), workspace=body.get('workspace'),
                                        kind=body.get('kind', 'path'), recursive=body.get('recursive', False),
                                        apply=body.get('apply', False))
        if result['applied']:
            _reset_after_wipe()
        return result
    except ValueError as error:
        return JSONResponse({'error': str(error)}, status_code=400)


@app.post('/api/projects/move')
async def api_projects_move(request: Request):
    """Preview, or apply, moving a project's memories to another project (ADR 0036).

    ``sources`` lists the source keys to move; without it the whole project
    moves (a merge when the target exists). Apply needs the workspace the
    preview returned, like purge.
    """
    from .projects import run_move
    body = await request.json()
    try:
        if not isinstance(body.get('apply', False), bool):
            raise ValueError('apply must be a boolean')
        if body.get('apply') and not body.get('workspace'):
            raise ValueError('Apply requires the explicit workspace returned by preview')
        sources = body.get('sources')
        if sources is not None and not isinstance(sources, list):
            raise ValueError('sources must be a list of source keys, or left out to move the whole project')
        return await asyncio.to_thread(run_move, body.get('project'), body.get('target'), sources,
                                       workspace=body.get('workspace'), apply=body.get('apply', False))
    except ValueError as error:
        return JSONResponse({'error': str(error)}, status_code=400)


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
    return StreamingResponse(_chat_events(sid, prior, user_msg, workspace),
                             media_type="text/event-stream")


def _chat_events(sid: str, prior: list[dict], user_msg: str, workspace: str):
    """Stream one reply as SSE and record it in the session."""
    pending_tool = None
    # The reply as streamed so far. A client that stops the reply closes
    # this generator; what it already showed stays in the conversation.
    partial, answered = [], False
    try:
        for ev in chat_mod.stream_reply(prior, user_msg, workspace=workspace):
            etype = ev.get("type")
            if etype == "token":
                partial.append(ev.get("text", ""))
            elif etype == "tool_call":
                partial.append("\n")
                pending_tool = ev
            elif etype == "tool_result" and pending_tool:
                chat_store.append_message(
                    sid, role="tool", content=ev.get("result", ""),
                    tool_name=pending_tool.get("name"),
                    tool_args=pending_tool.get("args"),
                )
                pending_tool = None
            elif etype == "assistant_message":
                answered = True
                chat_store.append_message(
                    sid, role="assistant", content=ev.get("text", ""),
                )
            yield f"data: {json.dumps(ev)}\n\n"
    except Exception as e:
        yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"
    finally:
        text = "".join(partial).strip()
        if not answered and text:
            chat_store.append_message(sid, role="assistant", content=text)


# ── SSE live updates ───────────────────────────────────────────

_shutdown_event = threading.Event()


@app.get("/api/stream")
async def api_stream():
    """An ``update`` event whenever the active workspace's data changes.

    Pages refetch on it; their reads are fresh either way (cli_viz checks
    the data fingerprint on every cached read). The heartbeat lets a closed
    connection end its stream instead of waiting for the next change.
    """
    async def event_stream():
        seen, version = await asyncio.to_thread(data_fingerprint), 0
        while not _shutdown_event.is_set():
            await asyncio.sleep(2)
            current = await asyncio.to_thread(data_fingerprint)
            if current != seen:
                seen, version = current, version + 1
                yield f"event: update\ndata: {json.dumps({'version': version})}\n\n"
            else:
                yield ": heartbeat\n\n"

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
        "unit": progress.get("unit", "files"),
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
    # The background job's progress belongs to its own row only.
    if (progress.get("command") == job_queue.BACKGROUND) != (job.get("command") == job_queue.BACKGROUND):
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
    # Background tagging is reported apart (`background` in /api/queue).
    if holder is not None and holder.get("preemptible"):
        holder = None
    if progress is not None and progress.get("command") == job_queue.BACKGROUND:
        progress = None
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


def _described(job: dict | None) -> dict | None:
    """A job with the names the dashboard shows for it (commands.describe)."""
    return job and {**job, **commands.describe(job.get("command", ""), job.get("body"))}


@app.get("/api/queue")
def api_queue(recent_limit: int = 20):
    data = job_queue.list_queue(recent_limit=recent_limit)
    background = _background_state()
    return {
        "active": _described(_active_from_any_source()),
        "queued": [_described(job) for job in data["queued"]],
        "recent": [_described(job) for job in data["recent"]],
        "background": {**background, "job": _described(background["job"])},
    }


def _background_state() -> dict:
    """Background tagging (ADR 0067): its state, the memories waiting for
    LLM tagging and its running or latest run, with live progress."""
    state = job_queue.background_state()
    if state["job"] and state["job"]["status"] == "running":
        state["job"] = _attach_progress(state["job"])
    return state


@app.post("/api/queue/background/resume")
def api_background_resume():
    """Start background tagging again after a cancel or a failure, as soon
    as no other job runs or waits."""
    job_queue.backlog_changed()
    return _background_state()


@app.get("/api/jobs/{job_id}")
def api_job_detail(job_id: str):
    job = job_queue.get_job(job_id)
    if not job:
        return JSONResponse({"error": "not found"}, status_code=404)
    if job.get("status") == "running":
        job = _attach_progress(job)
    return _described(job)


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
            if not is_alive(p):
                continue
            try:
                os.kill(p, getattr(signal, "SIGKILL", signal.SIGTERM))
            except ProcessLookupError:
                pass

    threading.Thread(target=_escalate, args=(targets,), daemon=True).start()
    return {"ok": True, "was_running": True, "source": "cli", "pids": targets}


# ── Config ─────────────────────────────────────────────────────

@app.get("/api/config")
def api_config():
    # Secret values never leave the process; display_value masks them.
    from dataclasses import asdict
    from .config_schema import SETTINGS, display_value, resolve
    settings = []
    for s in SETTINGS:
        val, source = resolve(s.key)
        settings.append({
            "key": s.key,
            "value": display_value(s, val),
            "source": source,
            "default": s.default,
            "type": s.type.__name__,
            "env": s.env,
            "desc": s.desc,
            "secret": s.secret,
            "choices": [asdict(c) for c in s.options()],
            "custom": s.custom,
        })
    return {"settings": settings}


@app.put("/api/config/{key:path}")
async def api_config_set(key: str, request: Request):
    body = await request.json()
    value = body.get("value")
    if value is None:
        return JSONResponse({"error": "value required"}, status_code=400)
    from .config_schema import SETTINGS_BY_KEY, display_value, set_value
    try:
        val = set_value(key, str(value))
    except KeyError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    _tagging_settings_changed(key)
    return {"ok": True, "value": display_value(SETTINGS_BY_KEY[key], val)}


@app.delete("/api/config/{key:path}")
def api_config_reset(key: str):
    """Remove the config.json override, like `imprint config reset <key>`."""
    from .config_schema import reset_value
    try:
        removed = reset_value(key)
    except KeyError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    _tagging_settings_changed(key)
    return {"ok": True, "removed": removed}


def _tagging_settings_changed(key: str) -> None:
    """Turning LLM tagging on, or fixing its provider, lets background
    tagging start or retry at once (ADR 0067)."""
    if key.startswith("tagger."):
        job_queue.backlog_changed()


# ── CLI command execution ──────────────────────────────────────


def _after_job(command: str, status: str) -> None:
    """Refresh this server after a queued job changed what it serves."""
    if status != "done":
        return
    if command in ("wipe", "migrate", "workspace", "retag", "scrub"):
        _reset_after_wipe()  # They mutate collections.


def _reset_after_wipe():
    """Clear all in-memory caches after a wipe so the dashboard reflects empty state."""
    # Reset vectorstore client (Qdrant was restarted, old connection is stale)
    vs._client = None
    # Reset knowledge graph connections (stale after DB files deleted)
    kg._close_all()
    # The wiped store restarts its WAL and point count, so forget cached views
    reset_cache()


@app.get("/api/commands")
def api_commands():
    """The Commands page's catalog and the clients its client fields offer.

    The clients come from ``imprint clients --json``; when the CLI cannot
    run, no queued command could either, so the failure is the answer.
    """
    try:
        return commands.catalog()
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as error:
        return JSONResponse({"error": f"Could not list clients: {error}"}, status_code=502)


@app.get("/api/wsl")
def api_wsl():
    """WSL distributions, their client profiles and which are enrolled.

    Probing can start a stopped distribution, so the dashboard asks only when
    its WSL section is opened or refreshed. Enroll and remove are queued
    ``wsl`` commands.
    """
    try:
        return commands.wsl_profiles()
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as error:
        return JSONResponse({"error": f"Could not list WSL profiles: {error}"}, status_code=502)


@app.post("/api/commands/{command}")
async def api_run_command(command: str, request: Request):
    """Enqueue a CLI command.

    Returns ``{job_id, position, merged}`` immediately. ``merged`` is true
    when an identical job was already waiting: its ID is returned and no
    second job is queued (ADR 0068). Clients should open
    ``GET /api/jobs/{job_id}/stream`` for live output and call
    ``POST /api/jobs/{job_id}/cancel`` to abort. An ingest or refresh whose
    ``dir`` lies outside the workspace's roots is refused with 409 and
    ``code: root_not_approved``, naming the root to approve.
    """
    body = {}
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            body = await request.json()
        except Exception:
            pass

    if command not in commands.ALLOWED:
        return JSONResponse({"error": f"unknown command '{command}'"}, status_code=404)

    try:
        submitted = job_queue.submit(command, body)
    except job_queue.RootNotApproved as error:
        # The dashboard asks to approve the root, then submits the job again.
        return JSONResponse({'error': str(error), 'code': 'root_not_approved', 'workspace': error.workspace,
                             'root': error.root, 'overlaps': error.overlaps}, status_code=409)
    except ValueError as error:
        return JSONResponse({'error': str(error)}, status_code=400)
    job_id = submitted["job_id"]
    snapshot = job_queue.list_queue(recent_limit=0)
    position = 0
    if snapshot["active"] and snapshot["active"]["id"] != job_id:
        position = 1 + next(
            (i for i, j in enumerate(snapshot["queued"]) if j["id"] == job_id),
            len(snapshot["queued"]),
        )
    return {"job_id": job_id, "position": position, "merged": submitted["merged"]}


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

@app.post("/api/sync/serve")
async def api_sync_serve(request: Request):
    """SSE stream for provider (serve) mode. Connects to relay, yields events.

    Body (all optional): ``workspace`` (an existing one; default: active,
    captured now), ``direction`` = ``both`` | ``send`` | ``receive`` and
    ``relay`` (default: the public relay). A POST with a JSON body, so a
    cross-site page cannot start a session.
    """
    from .sync_ws import SyncPolicy, parse_relay, serve_session, sync_workspace

    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        return JSONResponse({"error": "JSON object body required"}, status_code=400)
    try:
        policy = SyncPolicy.parse(body.get("direction", "both"))
        if type(body.get('remember', False)) is not bool:
            raise ValueError('remember must be a boolean')
        relay = body.get("relay") or ""
        if not isinstance(relay, str):
            raise ValueError("relay must be a string")
        endpoint = parse_relay(relay)
        ws, exists = sync_workspace(body.get("workspace"))
        if not exists:
            raise ValueError(f"workspace {ws!r} does not exist")
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    async def event_stream():
        async for event in serve_session(ws, policy, endpoint, remember=body.get('remember', False)):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/sync/receive")
async def api_sync_receive(request: Request):
    """SSE stream for consumer (receive) mode. Needs ``code``: what the
    serving side shows (``<room>-<pin>``, on its own or behind
    ``<host>/`` or ``wss://<host>/``).

    Optional ``workspace`` (default: active; a new name is created once the
    peer's data arrives) and ``direction`` = ``receive`` (default) |
    ``both`` | ``send``. Before a push the stream emits ``confirm_push``
    and waits for ``/api/sync/approve``.
    """
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict) or not isinstance(body.get("code"), str):
        return JSONResponse({"error": "code required"}, status_code=400)

    from .sync_ws import SyncPolicy, parse_code, receive_session, sync_workspace

    try:
        endpoint, room, pin = parse_code(body["code"])
        policy = SyncPolicy.parse(body.get("direction", "receive"))
        if type(body.get('remember', False)) is not bool:
            raise ValueError('remember must be a boolean')
        ws, exists = sync_workspace(body.get("workspace"))
        if not exists and not policy.receive:
            raise ValueError(f"workspace {ws!r} does not exist, so a send-only sync has nothing to send")
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    async def event_stream():
        async for event in receive_session(endpoint, room, pin, ws, policy, remember=body.get('remember', False)):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get('/api/sync/peers')
async def api_sync_peers():
    import sqlite3
    from .sync_pairs import pairs
    try:
        return {'peers': pairs(public=True)}
    except sqlite3.DatabaseError as error:
        return JSONResponse({'error': f'Could not read paired devices: {error}'}, status_code=500)


@app.post('/api/sync/peers/{pair_id}')
async def api_sync_peer_control(pair_id: str, request: Request):
    from .sync_pairs import forget, remove, set_enabled, pairs
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError('JSON object body required')
        if body.get('action') == 'forget':
            forget(pair_id, confirmed=body.get('confirm') is True)
        elif body.get('action') == 'remove':
            remove(pair_id, confirmed=body.get('confirm') is True)
        elif body.get('action') == 'enabled':
            set_enabled(pair_id, body.get('enabled'))
        else:
            raise ValueError('action must be enabled, forget or remove')
        return {'peers': pairs(public=True)}
    except ValueError as exc:
        return JSONResponse({'error': str(exc)}, status_code=400)


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
    """Resolve a pending sync decision.

    Serving: an ``approval_required`` event on ``/api/sync/serve`` (an
    unknown device passed the key exchange) takes ``accept`` (one-time),
    ``trust`` (accept + remember the fingerprint) or ``reject``. Joining: a
    ``confirm_push`` event on ``/api/sync/receive`` takes ``accept`` (send
    this workspace to the named device) or ``reject`` (skip the push).
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
        from .wsl_paths import distributions, linux_to_windows
        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            if os.path.isdir(drive):
                roots.append({"label": f"{letter}:", "path": drive})
        # \\wsl.localhost itself cannot be listed, so each distribution is a
        # root, in the namespace the WSL client bridge uses (ADR 0026).
        for name in distributions():
            roots.append({"label": f"WSL {name}", "path": linux_to_windows("/", name)})
    return {"roots": roots, "platform": system, "wsl": system == "Linux" and _is_wsl()}


def _crumbs(path: Path) -> list[dict[str, str]]:
    """Each step from the filesystem anchor (/, C:\\ or \\\\server\\share\\) down to ``path``."""
    return [{"label": step.name or step.anchor.rstrip("\\/") or step.anchor, "path": str(step)}
            for step in (*reversed(path.parents), path)]


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
            "crumbs": _crumbs(p),
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
        "crumbs": _crumbs(p),
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


# ── Serving (ADR 0050) ────────────────────────────────────────

# A server exits with this status when asked to restart, and whoever started
# it (the desktop app, `imprint ui`) starts it again with the installed code.
# EX_TEMPFAIL from sysexits.h: a temporary condition, try again.
RESTART_EXIT_CODE = 75

# Stopping waits this long for in-flight requests, then closes the rest. On
# Windows a client that resets its socket can make asyncio skip
# connection_lost, so uvicorn would wait for that connection forever.
GRACEFUL_SHUTDOWN_SECONDS = 5


def registration_path() -> Path:
    """``data/imprint_ui.json``: the process serving the dashboard and its port."""
    return config.get_data_dir() / "imprint_ui.json"


class DashboardServer(uvicorn.Server):
    """The dashboard's server, registered while it serves.

    Every launcher (the desktop app, ``imprint ui``) runs this, so the
    registration names the real server whoever started it, and the CLI and
    ``imprint update`` stop, report and restart that server.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 8420):
        super().__init__(uvicorn.Config(
            app, host=host, port=port, log_level="info", proxy_headers=False,
            ws_max_size=2_000_000, timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
        ))
        self.restart_requested = False
        app.state.server = self

    async def startup(self, sockets=None):
        await super().startup(sockets)  # Exits when the port cannot be bound.
        path = registration_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        staged = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        staged.write_text(json.dumps({"pid": os.getpid(), "port": self.config.port}), encoding="utf-8")
        os.replace(staged, path)

    async def shutdown(self, sockets=None):
        path = registration_path()
        try:
            if json.loads(path.read_text(encoding="utf-8")).get("pid") == os.getpid():
                path.unlink()
        except (OSError, ValueError):
            pass  # Already removed, or another server registered since.
        _shutdown_event.set()  # Unblock streaming responses.
        await super().shutdown(sockets)

    def restart(self):
        """Stop serving, then exit with ``RESTART_EXIT_CODE``."""
        self.restart_requested = True
        self.should_exit = True

    def run(self, sockets=None):
        super().run(sockets)
        if self.restart_requested:
            sys.exit(RESTART_EXIT_CODE)


# ── Entry point ───────────────────────────────────────────────

def main():
    global _auto_shutdown, _last_ui_ping

    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    import argparse
    parser = argparse.ArgumentParser(description="Imprint API server")
    parser.add_argument("--port", type=int, default=8420, help="Port to listen on")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--container-bind", action="store_true",
                        help="Allow 0.0.0.0 inside a container; publish its port on host loopback only")
    parser.add_argument("--dev", action="store_true", help="Development mode (no static mount)")
    parser.add_argument("--no-browser", action="store_true", help="Don't open browser")
    parser.add_argument("--auto-shutdown", action="store_true",
                        help="Shut down when the UI disconnects (used by `imprint ui`)")
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"} and not (args.container_bind and args.host == "0.0.0.0"):
        parser.error("The dashboard supports loopback access only; authenticated remote mode is not implemented")
    app.state.container_bind = args.container_bind and args.host == "0.0.0.0"

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

    if not args.no_browser:
        threading.Timer(0.8, lambda: _launch_browser(url)).start()

    DashboardServer(host=args.host, port=args.port).run()


if __name__ == "__main__":
    main()
