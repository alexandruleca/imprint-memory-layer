"""Private launcher in the operator's isolated PersonaPlex Python environment.

No model stack is imported until source/session validation succeeds. The pinned
upstream server starts on import, so remove only its exact final startup block,
install the local boundary/context adapter, then call its original main().
"""
from __future__ import annotations

import argparse
import ast
import hmac
import ipaddress
from pathlib import Path
import secrets
import sys
import types

# The external interpreter is invoked with -I; use only this explicit Imprint
# source location and the verified external runtime location.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from imprint.personaplex import MODEL_REPO, MODEL_REVISION, VOICES, load_session, validate_source


def load_server(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    expected = ast.parse("with torch.no_grad():\n    main()\n").body[0]
    if not tree.body or ast.dump(tree.body[-1]) != ast.dump(expected):
        raise RuntimeError("Unsupported PersonaPlex startup block")
    tree.body.pop()
    module = types.ModuleType("moshi.server")
    module.__file__, module.__package__ = str(path), "moshi"
    sys.modules["moshi.server"] = module
    exec(compile(tree, str(path), "exec"), module.__dict__)
    return module


def install_adapter(server, session: dict, *, port: int, tls: bool) -> None:
    from aiohttp import web

    scheme = "https" if tls else "http"
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    origins = {f"{scheme}://{host}" for host in hosts}
    capability = secrets.token_urlsafe(32)
    cookie_name = "imprint_personaplex"

    @web.middleware
    async def boundary(request, handler):
        try:
            local = ipaddress.ip_address(request.remote or "").is_loopback
        except ValueError:
            local = False
        origin = request.headers.get("Origin")
        if not local or request.host not in hosts or (origin is not None and origin not in origins):
            raise web.HTTPForbidden(text="Only the local Imprint voice page is allowed")
        if request.path == "/api/chat" and (
            origin not in origins or not hmac.compare_digest(request.cookies.get(cookie_name, ""), capability)
        ):
            raise web.HTTPForbidden(text="Open the local voice page before connecting")
        response = await handler(request)
        if request.path != "/api/chat":
            response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
            response.headers["X-Frame-Options"] = "DENY"
        if request.path == "/" and request.method == "GET":
            response.set_cookie(cookie_name, capability, httponly=True, samesite="Strict", secure=tls)
            response.headers["Cache-Control"] = "no-store"
        return response

    original_chat = server.ServerState.handle_chat

    async def handle_chat(state, request):
        # Browser parameters and URL logs never contain the memory snapshot.
        query = request.rel_url.query.copy()
        if query.get("voice_prompt", "") not in VOICES:
            raise web.HTTPBadRequest(text="Choose a supported PersonaPlex voice")
        query["text_prompt"] = session["text_prompt"]
        # This upstream revision reads request['seed'] instead of its query;
        # the stock UI uses text_seed/audio_seed. Do not admit the broken key.
        query.pop("seed", None)
        return await original_chat(state, request.clone(rel_url=request.rel_url.with_query(query)))

    server.ServerState.handle_chat = handle_chat
    original_log = server.ColorizedLog.log

    def log(logger, level, message):
        if message.startswith("text prompt:"):
            message = "text prompt: [Imprint startup memory snapshot]"
        return original_log(logger, level, message)

    server.ColorizedLog.log = log
    original_download = server.hf_hub_download

    def download(repo_id, filename, **kwargs):
        if repo_id != MODEL_REPO:
            raise RuntimeError("Unexpected model repository")
        kwargs.update(revision=MODEL_REVISION, local_files_only=True)
        return original_download(repo_id, filename, **kwargs)

    server.hf_hub_download = download
    original_run = server.web.run_app

    def run_app(app, *args, **kwargs):
        app.middlewares.insert(0, boundary)
        # The pinned server accepts --host but fails to pass it to run_app.
        kwargs["host"] = "127.0.0.1"
        kwargs["access_log"] = None
        return original_run(app, *args, **kwargs)

    server.web.run_app = run_app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--cpu-offload", action="store_true")
    parser.add_argument("--ssl", type=Path)
    args = parser.parse_args()
    session = load_session(args.session)
    validate_source(args.source)
    sys.path.insert(0, str(args.source / "moshi"))
    server = load_server(args.source / "moshi" / "moshi" / "server.py")
    install_adapter(server, session, port=args.port, tls=bool(args.ssl))
    sys.argv = ["moshi.server", "--host", "127.0.0.1", "--port", str(args.port), "--hf-repo", MODEL_REPO]
    if args.cpu_offload:
        sys.argv.append("--cpu-offload")
    if args.ssl:
        sys.argv.extend(["--ssl", str(args.ssl)])
    with server.torch.no_grad():
        server.main()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Avoid a traceback exposing a query containing the injected prompt.
        print(f"PersonaPlex runtime failed ({type(error).__name__}). Check the pinned environment and model cache.", file=sys.stderr)
        raise SystemExit(1)
