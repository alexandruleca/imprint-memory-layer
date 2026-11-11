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
from . import chat_replies, chat_store
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
    get_graph_scope,
    get_neighbors,
    get_memory,
    data_fingerprint,
    reset_cache,
    _get_overview,
    _get_project_detail,
    _get_project_list,
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
from .schedule_api import router as schedule_router
app.include_router(schedule_router)


@app.middleware("http")
async def invalidate_summaries_after_writes(request: Request, call_next):
    # A dashboard write (move, purge, organize, sync import, …) can change
    # counts without growing the WAL; the Home summary is rebuilt after it
    # instead of waiting for its maximum age (ADR 0152).
    response = await call_next(request)
    if (request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path.startswith("/api/")
            and response.status_code < 400):
        from . import dashboard_summary
        dashboard_summary.invalidate()
    return response


def _scheduled_prompt(entry: dict) -> dict:
    """A scheduled question (ADR 0158): a new chat in the active workspace
    whose reply runs like any other (ADR 0145); the alert links to it."""
    question = entry["payload"]["question"]
    workspace = config.get_active_workspace()
    sid = chat_store.create_session(title=entry["title"][:80] or "Scheduled question")
    chat_replies.start(sid, lambda cancel: _chat_events(sid, [], question, workspace, None, cancel),
                       prepare=lambda: chat_store.append_message(sid, role="user", content=question),
                       waiting=_WAITING)
    return {"chat": sid}


@app.on_event("startup")
async def _start_queue_dispatcher():
    job_queue.recover_on_startup()
    asyncio.create_task(job_queue.dispatcher_loop(_after_job))
    asyncio.create_task(job_queue.reaper_loop())
    from .sync_pairs import run
    app.state.sync_worker = asyncio.create_task(run())
    # Reminders, timers and scheduled jobs fire from this process (ADR 0158).
    from . import schedule
    schedule.start(run_prompt=_scheduled_prompt)
    # Skills and personas: written into the clients, edits there taken back (ADR 0169).
    from . import registry_files
    app.state.registry_pass = asyncio.create_task(registry_files.loop())
    # New releases install by themselves when nothing would be interrupted (ADR 0175).
    app.state.update_pass = asyncio.create_task(updates.auto_loop())


@app.on_event('shutdown')
async def _stop_sync_worker():
    from .voice_api import shutdown
    await shutdown()
    from . import schedule
    schedule.stop()
    chat_replies.stop_all()
    for name in ('sync_worker', 'registry_pass'):
        task = getattr(app.state, name, None)
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


@app.get("/api/projects")
def api_projects():
    """Every project of the active workspace with its memory count and colour,
    largest first, and the workspace's total. Cheaper than /api/overview,
    which also counts each project's types, domains and languages."""
    return _get_project_list()


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


@app.get("/api/dashboard")
def api_dashboard(fresh: bool = False):
    """The Home page's summary of the active workspace, from its snapshot
    (ADR 0152): answered at once, marked ``stale`` while a newer one is
    built. ``fresh`` waits for an up-to-date one."""
    from . import dashboard_summary
    return dashboard_summary.get(wait=None if fresh else dashboard_summary.WAIT)


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
    """Latest release, how this installation updates, update progress, and
    whether updates install by themselves (ADR 0175)."""
    try:
        report = updates.check(refresh)
    except Exception as error:
        return {**updates.status(), **updates.auto_status(), "error": str(error)}
    return {**report, **updates.status(), **updates.auto_status()}


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
    the first question as their title, and each source its snapshot's kind
    and time ("" and 0 without one)."""
    from . import source_snapshots
    sources = vs.list_sources(
        project=project, lang=lang, layer=layer, limit=limit,
    )
    titles = source_snapshots.conversation_titles()
    from .project_associations import accepted
    memberships = accepted()
    # One pass over the heads: the dashboard lists every source (ADR 0139).
    heads: dict[str, list[dict]] = {}
    for head in source_snapshots.list_heads():
        heads.setdefault(head['source'], []).append(head)
    def entry(source, count):
        owners = [head for head in heads.get(source, ()) if
                  not project or project == head['project'] or
                  project in memberships.get((head['origin'], source), [])]
        canonical = owners[0]['project'] if len(owners) == 1 else None
        return {'source': source, 'chunks': count, 'title': titles.get(source, ''),
                'project': canonical, 'linked': bool(project and canonical is not None and canonical != project),
                'kind': owners[0]['kind'] if owners else '',
                'updated': max((head['updated_at'] for head in owners), default=0)}
    return {
        "sources": [entry(s, c) for s, c in sources],
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
    q: str = Query("", max_length=200),
    sort: str = Query("count", pattern="^(count|name)$"),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    """One page of topics, searched by name and sorted (ADR 0150)."""
    filters = _parse_filters(type, lang, domain, layer)
    return build_topic_overview(filters, q=q, sort=sort, offset=offset, limit=limit)


@app.get("/api/topic/{name:path}")
def api_topic(name: str, project: str | None = None, offset: int = Query(0, ge=0),
              limit: int | None = Query(None, ge=1, le=200), q: str = Query("", max_length=200)):
    """A topic's memories by source; ``limit`` pages the sources (ADR 0150)."""
    return build_topic_detail(unquote(name), project, offset=offset, limit=limit, q=q)


@app.get("/api/graph")
def api_graph(scope: str = "root", depth: int = 1):
    return get_graph_scope(unquote(scope), max(1, min(int(depth or 1), 3)))


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


@app.post('/api/migrate/preview')
async def api_migrate_preview(request: Request):
    """How many memories copying or moving projects, topics or sources to
    another workspace would take, and how many the target already holds.
    Changes nothing; the migrate command applies (ADR 0150)."""
    from .cli_migrate import preview
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError('A preview object is required')
        return await asyncio.to_thread(preview, body.get('from'), body.get('to'), body.get('mode'), body.get('values'))
    except ValueError as error:
        return JSONResponse({'error': str(error)}, status_code=400)


@app.get('/api/projects/associations')
def api_project_associations(source: str | None = None, project: str | None = None,
                             limit: int = Query(200, ge=1, le=1000)):
    from .project_associations import review
    return review(source=source, project=project, limit=limit)


@app.post('/api/projects/associations')
async def api_decide_project_association(request: Request):
    from .project_associations import decide
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError('A project-association object is required')
        if not body.get('workspace'):
            raise ValueError('An explicit workspace from the association review is required')
        if not isinstance(body.get('source'), str) or not isinstance(body.get('project'), str):
            raise ValueError('source and canonical project are required')
        result = await asyncio.to_thread(decide, body['source'], body['project'], body.get('target'),
                                         body.get('status'), body['workspace'])
        reset_cache()
        return result
    except ValueError as error:
        return JSONResponse({'error': str(error)}, status_code=400)


# ── Recordings (ADR 0131) ──────────────────────────────────────

def _transcribe_recording(recording_id: str) -> dict:
    """Queue a recording's transcription (the queue's lock keeps it to one heavy job)."""
    return job_queue.submit('transcribe-recording', {'id': recording_id})


@app.get('/api/recordings')
def api_recordings():
    from . import recordings
    return {'recordings': recordings.list_all(), 'default_project': recordings.DEFAULT_PROJECT}


@app.post('/api/recordings')
async def api_start_recording(request: Request):
    """Start a recording; its audio follows in pieces."""
    from . import recordings
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError('A recording object is required')
        return await asyncio.to_thread(recordings.start, body.get('title', ''), body.get('project') or recordings.DEFAULT_PROJECT,
                                       bool(body.get('others', True)), body.get('consent') is True)
    except ValueError as error:
        return JSONResponse({'error': str(error)}, status_code=400)


@app.post('/api/recordings/{recording_id}/audio')
async def api_recording_audio(recording_id: str, request: Request):
    """The next piece of a recording's audio, as raw bytes."""
    from . import recordings
    data = await request.body()
    try:
        return await asyncio.to_thread(recordings.append, recording_id, data)
    except KeyError:
        return JSONResponse({'error': 'recording not found'}, status_code=404)
    except ValueError as error:
        return JSONResponse({'error': str(error)}, status_code=409)


@app.post('/api/recordings/{recording_id}/stop')
async def api_stop_recording(recording_id: str, request: Request):
    """Stop a recording and queue its transcription."""
    from . import recordings
    body = {}
    if request.headers.get('content-type', '').startswith('application/json'):
        body = await request.json()
    try:
        duration = body.get('duration') if isinstance(body, dict) else None
        meta = await asyncio.to_thread(recordings.stop, recording_id, duration if isinstance(duration, (int, float)) else None)
        if not meta['audio_bytes']:
            return {**meta, 'job': None}
        return {**meta, 'job': _transcribe_recording(recording_id)}
    except KeyError:
        return JSONResponse({'error': 'recording not found'}, status_code=404)


@app.post('/api/recordings/{recording_id}/transcribe')
def api_transcribe_recording(recording_id: str):
    from . import recordings
    try:
        meta = recordings.get(recording_id)
    except KeyError:
        return JSONResponse({'error': 'recording not found'}, status_code=404)
    if meta['status'] == 'recording' or not meta['audio_bytes']:
        return JSONResponse({'error': 'Stop the recording first; it needs audio to transcribe'}, status_code=409)
    return {'job': _transcribe_recording(recording_id)}


@app.get('/api/recordings/{recording_id}/transcript')
def api_recording_transcript(recording_id: str):
    from . import recordings
    try:
        path = recordings._folder(recording_id) / 'transcript.md'
        return {'text': path.read_text(encoding='utf-8')}
    except (KeyError, FileNotFoundError):
        return JSONResponse({'error': 'no transcript'}, status_code=404)


@app.get('/api/recordings/{recording_id}/audio')
def api_recording_file(recording_id: str, download: bool = False):
    """The recording's audio, to play or, with ``download``, to save as a
    file named after its title."""
    import re
    from fastapi.responses import FileResponse
    from . import recordings
    try:
        path = recordings._folder(recording_id) / 'audio.webm'
        title = recordings.get(recording_id)['title'] if download else ''
    except KeyError:
        return JSONResponse({'error': 'recording not found'}, status_code=404)
    if not path.exists():
        return JSONResponse({'error': 'no audio'}, status_code=404)
    if not download:
        return FileResponse(path, media_type='audio/webm')
    # Characters Windows does not allow in a file name, such as the title's "09:58".
    name = ' '.join(re.sub(r'[<>:"/\\|?*\x00-\x1f]', '-', title).split()).strip(' .') or 'recording'
    return FileResponse(path, media_type='audio/webm', filename=f'{name}.webm')


@app.delete('/api/recordings/{recording_id}/audio')
def api_recording_remove_audio(recording_id: str):
    from . import recordings
    try:
        return recordings.remove_audio(recording_id)
    except KeyError:
        return JSONResponse({'error': 'recording not found'}, status_code=404)
    except ValueError as error:
        return JSONResponse({'error': str(error)}, status_code=409)


@app.delete('/api/recordings/{recording_id}')
async def api_delete_recording(recording_id: str):
    from . import recordings
    try:
        await asyncio.to_thread(recordings.delete, recording_id)
        reset_cache()
        return {'deleted': True}
    except KeyError:
        return JSONResponse({'error': 'recording not found'}, status_code=404)


# ── Organize (ADR 0130) ────────────────────────────────────────

@app.get('/api/organize')
def api_organize(workspace: str | None = None):
    """Project profiles, placement proposals and the last organize run."""
    from . import organize
    return organize.review(workspace)


@app.post('/api/organize/decide')
async def api_organize_decide(request: Request):
    """Accept (move with a placement rule) or reject placement proposals."""
    from . import organize
    try:
        body = await request.json()
        if not isinstance(body, dict) or not isinstance(body.get('items'), list) or not isinstance(body.get('accept'), bool):
            raise ValueError('items (origin and source of each proposal) and accept are required')
        if not body.get('workspace'):
            raise ValueError('The workspace the review returned is required')
        result = await asyncio.to_thread(organize.decide, body['workspace'], body['items'], body['accept'])
        reset_cache()
        return result
    except ValueError as error:
        return JSONResponse({'error': str(error)}, status_code=400)


@app.post('/api/organize/undo')
async def api_organize_undo(request: Request):
    """Move sources Organize moved by itself back, and keep them there (ADR 0133)."""
    from . import organize
    try:
        body = await request.json()
        if not isinstance(body, dict) or not isinstance(body.get('items'), list) or not body.get('workspace'):
            raise ValueError('The workspace and items (origin and source of each move) are required')
        result = await asyncio.to_thread(organize.undo, body['workspace'], body['items'])
        reset_cache()
        return result
    except ValueError as error:
        return JSONResponse({'error': str(error)}, status_code=400)


@app.post('/api/organize/reopen')
async def api_organize_reopen(request: Request):
    """Return moved or kept proposals to review; a move is taken back first (ADR 0136)."""
    from . import organize
    try:
        body = await request.json()
        if not isinstance(body, dict) or not isinstance(body.get('items'), list) or not body.get('workspace'):
            raise ValueError('The workspace and items (origin and source of each proposal) are required')
        result = await asyncio.to_thread(organize.reopen, body['workspace'], body['items'])
        reset_cache()
        return result
    except ValueError as error:
        return JSONResponse({'error': str(error)}, status_code=400)


# ── Core memories (ADR 0129, ADR 0143) ─────────────────────────

@app.get('/api/core')
def api_core_memories():
    """Every core memory, guidelines and records, and how many of a
    session's estimated tokens the guidelines for every project take."""
    from . import core_memories
    return {'memories': core_memories.list_all(), 'budget': core_memories.SESSION_BUDGET,
            'used': core_memories.used_budget(), 'kinds': list(core_memories.KINDS),
            'max_text': core_memories.MAX_TEXT}


@app.post('/api/core')
async def api_add_core_memory(request: Request):
    """A guideline the user wrote, active at once; ``covers`` names the
    recorded core memories it was made from."""
    from . import core_memories
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError('A core memory object is required')
        covers = body.get('covers') or []
        if not isinstance(covers, list):
            raise ValueError('covers is a list of recorded core memory ids')
        return await asyncio.to_thread(core_memories.add, body.get('text'), body.get('kind', 'preference'),
                                       body.get('scope', core_memories.EVERYWHERE), covers)
    except (ValueError, RuntimeError) as error:
        return JSONResponse({'error': str(error)}, status_code=400)


@app.patch('/api/core/{memory_id}')
async def api_change_core_memory(memory_id: str, request: Request):
    """Edit, approve (state ``active``), retire or restore a core memory, or
    return a proposal to ``proposed`` (an undo, ADR 0136)."""
    from . import core_memories
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError('A change object is required')
        changes = {key: body[key] for key in ('text', 'kind', 'scope', 'state') if key in body}
        return await asyncio.to_thread(core_memories.update, memory_id, **changes)
    except KeyError:
        return JSONResponse({'error': 'core memory not found'}, status_code=404)
    except (ValueError, RuntimeError) as error:
        return JSONResponse({'error': str(error)}, status_code=400)


@app.post('/api/core/{memory_id}/resolve')
async def api_resolve_core_conflict(memory_id: str, request: Request):
    """Settle a newer recorded core memory's conflicts with older ones:
    ``{keep: newer | older | both}``, or ``open`` to undo (ADR 0143).
    Returns ``{changed: [...]}``, every core memory it changed."""
    from . import core_memories
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError('A resolution object is required')
        return {'changed': await asyncio.to_thread(core_memories.resolve, memory_id, body.get('keep'))}
    except KeyError:
        return JSONResponse({'error': 'core memory not found'}, status_code=404)
    except (ValueError, RuntimeError) as error:
        return JSONResponse({'error': str(error)}, status_code=400)


# ── Skills and personas (ADRs 0168, 0169) ──────────────────────

def _registry_limits() -> dict:
    from . import registry
    return {'kinds': list(registry.KINDS), 'main': registry.MAIN, 'max_name': registry.MAX_NAME,
            'max_description': registry.MAX_DESCRIPTION, 'max_file': registry.MAX_FILE,
            'max_total': registry.MAX_TOTAL}


def _registry_applied() -> dict:
    """Write every change into the clients' folders; a failure is reported
    with the change, which is kept either way."""
    from . import registry_files
    try:
        return registry_files.apply()
    except Exception as error:  # noqa: BLE001 - the change is stored; the page shows why it did not reach the clients
        return {'error': f'Saved, but not written to the clients: {error}'}


@app.get('/api/skills')
def api_skills():
    """Every skill and persona without their files, the clients' folders
    and what the last write into them reported."""
    from . import registry, registry_files
    return {'items': registry.list_all(), 'report': registry_files.last_report(), **_registry_limits()}


@app.post('/api/skills/apply')
async def api_apply_skills():
    """Write the registry into the clients' folders now, taking back edits
    made there, and list what the clients' folders hold that Imprint does
    not keep (``found``)."""
    from . import registry, registry_files

    def run():
        report = _registry_applied()
        try:
            found = registry_files.found()
        except Exception as error:  # noqa: BLE001 - shown on the page
            found, report = [], {**report, 'error': report.get('error') or f'Could not list the clients: {error}'}
        return {'items': registry.list_all(), 'report': report, 'found': found}

    return await asyncio.to_thread(run)


@app.get('/api/skills/{item_id}')
def api_skill(item_id: str):
    """One item with its files and this device's history of it."""
    from . import registry
    item = registry.get(item_id)
    if item is None:
        return JSONResponse({'error': 'skill or persona not found'}, status_code=404)
    return {**item, 'history': [{k: v for k, v in version.items() if k != 'files'} | {'paths': list(version['files'])}
                                for version in registry.history(item_id)]}


@app.post('/api/skills')
async def api_add_skill(request: Request):
    """A new skill or persona: ``{kind, files}``, or ``{kind, name,
    description, instructions}`` from the dashboard's form."""
    from . import registry
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError('A skill or persona object is required')
        kind = body.get('kind')
        if kind not in registry.KINDS:
            raise ValueError(f'kind must be one of {", ".join(registry.KINDS)}')
        files = body.get('files')
        if files is None:
            files = registry.compose(kind, body.get('name', ''), body.get('description', ''), body.get('instructions', ''))
        item = await asyncio.to_thread(registry.create, kind, files)
        return {**item, 'report': await asyncio.to_thread(_registry_applied)}
    except ValueError as error:
        return JSONResponse({'error': str(error)}, status_code=400)


@app.patch('/api/skills/{item_id}')
async def api_change_skill(item_id: str, request: Request):
    """Edit an item's files (``files``), or enable, disable or remove it
    (``state``: active, disabled, retired); ``restore`` names a version from
    this device's history to make current."""
    from . import registry
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError('A change object is required')
        if body.get('restore'):
            item = await asyncio.to_thread(registry.restore, item_id, str(body['restore']))
        else:
            item = await asyncio.to_thread(registry.update, item_id, files=body.get('files'), state=body.get('state'))
        return {**item, 'report': await asyncio.to_thread(_registry_applied)}
    except KeyError:
        return JSONResponse({'error': 'skill, persona or version not found'}, status_code=404)
    except ValueError as error:
        return JSONResponse({'error': str(error)}, status_code=400)


@app.post('/api/skills/adopt')
async def api_adopt_skill(request: Request):
    """Keep a skill or persona found in a client's folder: ``{path, kind}``
    as ``POST /api/skills/apply`` listed it."""
    from . import registry_files
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError('An adopt object is required')
        return await asyncio.to_thread(registry_files.adopt, str(body.get('path') or ''), str(body.get('kind') or ''))
    except KeyError:
        return JSONResponse({'error': 'Not found in a connected client: refresh the page'}, status_code=404)
    except (ValueError, RuntimeError, OSError) as error:
        return JSONResponse({'error': str(error)}, status_code=400)


# ── Chat ───────────────────────────────────────────────────────

@app.get("/api/chat/status")
def api_chat_status():
    return chat_mod.status()


@app.get("/api/chat/sessions")
def api_chat_sessions():
    return {"sessions": [{**session, "replying": chat_replies.current(session["id"]) is not None}
                         for session in chat_store.list_sessions()]}


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
    sess["summary"] = chat_store.get_summary(unquote(sid))
    # A reply still being written is watched from /reply (ADR 0145), with
    # the question it waits on, if any (ADR 0159).
    reply = chat_replies.current(unquote(sid))
    sess["replying"] = reply is not None
    question = reply.pending() if reply is not None else None
    sess["question"] = question.event if question is not None else None
    return sess


@app.get("/api/chat/sessions/{sid}/reply")
async def api_chat_reply(sid: str):
    """The reply being written in this chat, from its first event: a page that
    comes back sees everything so far, then the rest as it comes (ADR 0145)."""
    reply = chat_replies.current(unquote(sid))
    if reply is None:
        return JSONResponse({"error": "no reply is being written"}, status_code=404)
    return StreamingResponse(_watch(reply), media_type="text/event-stream")


@app.post("/api/chat/sessions/{sid}/stop")
def api_chat_stop(sid: str):
    """The user's stop: the only thing that cancels a reply (ADR 0145)."""
    return {"ok": chat_replies.stop(unquote(sid))}


@app.post("/api/chat/sessions/{sid}/answer")
async def api_chat_answer(sid: str, request: Request):
    """The user's answer to the question the reply in this chat waits on:
    ``{id, answer}`` (ADR 0159). Any window may answer, not only the one
    that saw the question."""
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        return JSONResponse({"error": "id and answer required"}, status_code=400)
    try:
        chat_replies.answer(unquote(sid), body.get("id"), body.get("answer"))
    except chat_replies.Rejected as error:
        return JSONResponse({"error": str(error)}, status_code=error.status)
    return {"ok": True}


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

    if chat_replies.current(sid) is not None:
        return JSONResponse({"error": "A reply is still being written in this chat. Stop it or wait for it."}, status_code=409)
    prior = chat_store.get_messages(sid)
    summary = chat_store.get_summary(sid)
    workspace = config.get_active_workspace()
    try:
        # The reply runs on its own thread: the request only watches it, so a
        # closed tab or another chat never stops it (ADR 0145).
        # The reply can ask the user and wait for the answer (ADR 0159); it is
        # registered under its chat until it ends, so the lookup finds it.
        reply = chat_replies.start(sid, lambda cancel: _chat_events(sid, prior, user_msg, workspace, summary, cancel,
                                                                    ask=lambda event: chat_replies.current(sid).ask(event)),
                                   prepare=lambda: chat_store.append_message(sid, role="user", content=user_msg),
                                   waiting=_WAITING)
    except chat_replies.Busy:
        return JSONResponse({"error": "A reply is still being written in this chat. Stop it or wait for it."}, status_code=409)
    return StreamingResponse(_watch(reply), media_type="text/event-stream")


_WAITING = f"data: {json.dumps({'type': 'analysis', 'text': 'Waiting for the reply in another chat to finish…'})}\n\n"


async def _watch(reply: chat_replies.Reply):
    """Every event of a reply from the first, as SSE, until it ends. Leaving
    stops only the watching; a keep-alive lets a closed connection notice."""
    seen = 0
    while True:
        batch, finished = await asyncio.to_thread(reply.wait, seen, 1.0)
        seen += len(batch)
        for event in batch:
            yield event
        if finished and seen >= len(reply.events):
            return
        if not batch:
            yield ": keep-alive\n\n"


def _chat_events(sid: str, prior: list[dict], user_msg: str, workspace: str, summary: dict | None = None,
                 cancel: threading.Event | None = None, ask=None):
    """Stream one reply as SSE and record it in the session, with the
    running summary it updates (ADR 0141). A question the reply asks and its
    answer are recorded as its ask_user tool exchange (ADR 0159)."""
    pending_tool = None
    # The reply as streamed so far. When the user stops the reply (or this
    # generator is closed), what was already shown stays in the conversation.
    partial, answered = [], False
    options = {"cancel_event": cancel} if cancel is not None else {}
    if ask is not None:
        options["ask"] = ask
    try:
        for ev in chat_mod.stream_reply(prior, user_msg, workspace=workspace, summary=summary, **options):
            etype = ev.get("type")
            if etype == "summary":
                chat_store.set_summary(sid, ev.get("text", ""), ev.get("covers", 0), ev.get("retry_after", 0))
            elif etype == "token":
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


@app.get("/api/queue/history")
def api_queue_history(limit: int = 25, before: str | None = None, bounds: str = "", status: str | None = None):
    """Jobs that ended, newest first, a page at a time: ``next`` is the
    ``before`` of the following page. ``bounds``, comma-separated period
    starts in epoch seconds, newest first, adds ``counts`` per period and
    for everything older (the Queue page's Today, Yesterday and so on).
    ``status`` (done, failed or cancelled) keeps the jobs that ended so."""
    try:
        starts = [float(value) for value in bounds.split(",") if value.strip()]
        history = job_queue.list_history(limit=limit, before=before, bounds=starts, status=status)
    except ValueError as error:
        return JSONResponse({"error": f"Invalid history request: {error}"}, status_code=400)
    return {**history, "jobs": [_described(job) for job in history["jobs"]]}


@app.get("/api/agents")
def api_agents(limit: int = 50, before: str | None = None, client: str | None = None, q: str | None = None):
    """The agents that used this Imprint's MCP server (ADR 0117): the
    connected sessions (``running``, first page only), one page of the
    others by last activity (``sessions``, ``next`` cursor for ``before``,
    ``total``; ADR 0151), optionally of one ``client`` or matching ``q``,
    and every session totalled by client (``clients``)."""
    from . import agent_activity
    try:
        page = agent_activity.page_sessions(limit, before, client=client, query=q)
    except ValueError:
        return JSONResponse({"error": "Invalid cursor"}, status_code=400)
    return {**page, "clients": agent_activity.client_totals()}


@app.get("/api/agents/{session_id}/calls")
def api_agent_calls(session_id: str, limit: int = 200, before: int | None = None):
    """One session's tool calls, newest first; ``before`` pages back."""
    from . import agent_activity
    session = agent_activity.get_session(session_id)
    if session is None:
        return JSONResponse({"error": "No such agent session"}, status_code=404)
    return {"session": session, "calls": agent_activity.session_calls(session_id, limit, before)}


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


@app.post("/api/jobs/{job_id}/retry")
def api_job_retry(job_id: str):
    """Queue a finished job again with the same command, arguments and
    workspace. Replies like ``POST /api/commands/{command}`` plus
    ``retry_of``; an identical waiting job absorbs it (ADR 0068). 404 for an
    unknown job, 409 for one still waiting or running, or whose folder is no
    longer an approved root (``code: root_not_approved``)."""
    try:
        submitted = job_queue.resubmit(job_id)
    except LookupError:
        return JSONResponse({"error": "not found"}, status_code=404)
    except job_queue.NotRetryable as error:
        return JSONResponse({"error": str(error)}, status_code=409)
    except job_queue.RootNotApproved as error:
        return JSONResponse({'error': str(error), 'code': 'root_not_approved', 'workspace': error.workspace,
                             'root': error.root, 'overlaps': error.overlaps}, status_code=409)
    except ValueError as error:
        return JSONResponse({"error": str(error)}, status_code=400)
    return _queued(submitted)


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
    from .config_help import HELP
    settings = []
    for s in SETTINGS:
        val, source = resolve(s.key)
        explained = HELP.get(s.key, {})
        settings.append({
            "key": s.key,
            "value": display_value(s, val),
            "source": source,
            "default": s.default,
            "type": "int" if isinstance(s.default, int) and not isinstance(s.default, bool) else s.type.__name__,
            "env": s.env,
            "desc": s.desc,
            "help": explained.get("help", ""),
            "example": explained.get("example", ""),
            "secret": s.secret,
            "choices": [asdict(c) for c in s.options()],
            "custom": s.custom,
        })
    return {"settings": settings}


@app.get("/api/config/{key:path}")
def api_config_get(key: str):
    """One setting's value and where it comes from; the desktop app reads
    `desktop.floating_orb` this way (ADR 0174). Secrets stay masked."""
    from .config_schema import SETTINGS_BY_KEY, display_value, resolve
    setting = SETTINGS_BY_KEY.get(key)
    if setting is None:
        return JSONResponse({"error": f"unknown setting: {key}"}, status_code=404)
    value, source = resolve(key)
    return {"key": key, "value": display_value(setting, value), "source": source}


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
    _optional_tool_changed(key, val)
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
    """Turning LLM tagging or background Organize on, or fixing the
    provider, lets the background job start or retry at once (ADR 0067,
    0132)."""
    if key.startswith(("tagger.", "organize.")):
        job_queue.backlog_changed()


def _optional_tool_changed(key: str, value) -> None:
    """Turning on kreuzberg or obscura installs it in the background; the
    setting's On choice reports progress and failure (ADR 0121)."""
    from . import optional_tools
    for tool, setting in optional_tools.SETTINGS.items():
        if key == setting and value is True:
            optional_tools.start_background(tool)


# ── CLI command execution ──────────────────────────────────────


def _after_job(command: str, status: str) -> None:
    """Refresh this server after a queued job changed what it serves."""
    from . import dashboard_summary
    dashboard_summary.invalidate()  # Even a failed job may have stored some.
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
    from . import dashboard_summary
    dashboard_summary.reset()


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
    return _queued(submitted)


def _queued(submitted: dict) -> dict:
    """A submission's reply: its job, its place in the queue (0 when it
    runs next or now) and whether it joined an identical waiting job."""
    job_id = submitted["job_id"]
    snapshot = job_queue.list_queue(recent_limit=0)
    position = 0
    if snapshot["active"] and snapshot["active"]["id"] != job_id:
        position = 1 + next(
            (i for i, j in enumerate(snapshot["queued"]) if j["id"] == job_id),
            len(snapshot["queued"]),
        )
    return {**submitted, "position": position}


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
