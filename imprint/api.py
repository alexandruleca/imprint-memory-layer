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
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import config
from . import vectorstore as vs
from . import imprint_graph as kg
from . import chat as chat_mod
from . import chat_store

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
)


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
    return {"ok": True, "active": config.get_active_workspace()}


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

@app.get("/api/stream")
def api_stream():
    def event_stream():
        version = 0
        while True:
            if check_for_changes():
                from .cli_viz import _data_version
                version = _data_version
                yield f"event: update\ndata: {json.dumps({'version': version})}\n\n"
            time.sleep(2)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


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

# Allowed commands — just run the `imprint` binary directly.
_ALLOWED_COMMANDS = {
    "status", "ingest", "refresh", "refresh-urls", "retag",
    "config", "wipe", "sync",
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
    body = {}
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            body = await request.json()
        except Exception:
            pass

    if command not in _ALLOWED_COMMANDS:
        return JSONResponse({"error": f"unknown command '{command}'"}, status_code=404)

    # Build args: command + any flags from body
    imprint_bin = _find_imprint_binary()
    cmd_args = [imprint_bin, command]
    cmd_args.extend(_build_command_args(command, body))

    def stream():
        try:
            proc = subprocess.Popen(
                cmd_args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            for line in proc.stdout:
                yield f"data: {json.dumps({'type': 'output', 'text': line})}\n\n"
            proc.wait()
            yield f"data: {json.dumps({'type': 'done', 'exit_code': proc.returncode})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


def _build_command_args(command: str, body: dict) -> list[str]:
    """Build CLI args from request body."""
    args = []
    if command == "ingest":
        if body.get("dir"):
            args.append(body["dir"])
    elif command == "refresh":
        if body.get("dir"):
            args.append(body["dir"])
    elif command == "retag":
        if body.get("project"):
            args.extend(["--project", body["project"]])
        if body.get("dry_run"):
            args.append("--dry-run")
    elif command == "config":
        # Support `config`, `config get <key>`, `config set <key> <val>`
        action = body.get("action", "")
        if action:
            args.append(action)
        if body.get("key"):
            args.append(body["key"])
        if body.get("value") is not None:
            args.append(str(body["value"]))
    elif command == "wipe":
        if body.get("force"):
            args.append("--force")
        if body.get("all"):
            args.append("--all")
    elif command == "sync":
        action = body.get("action", "")
        if action:
            args.append(action)
    return args


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
    import argparse
    parser = argparse.ArgumentParser(description="Imprint API server")
    parser.add_argument("--port", type=int, default=8420, help="Port to listen on")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--dev", action="store_true", help="Development mode (no static mount)")
    parser.add_argument("--no-browser", action="store_true", help="Don't open browser")
    args = parser.parse_args()

    if not args.dev:
        mount_static()

    url = f"http://{args.host}:{args.port}"
    print(f"\n  \033[0;33m\u2726 Imprint Dashboard at {url}\033[0m")
    print(f"  \033[2mPress Ctrl+C to stop\033[0m\n")

    if not args.no_browser:
        threading.Timer(0.8, lambda: _launch_browser(url)).start()

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
