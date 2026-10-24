#!/usr/bin/env python3
"""Native Echo graphical check using only a disposable API/profile and fake mic."""
import argparse
import contextlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from unittest.mock import patch


def serve(port: int):
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles
    from imprint import voice_api as voice
    import uvicorn
    app = FastAPI()
    app.include_router(voice.router)

    @app.get("/api/ping")
    def ping(): return {"ok": True}

    @app.get("/api/version")
    def version(): return {"version": "native-voice-smoke"}

    @app.get("/api/workspaces")
    def workspaces(): return {"active": "default", "known": ["default"]}

    @app.get("/api/{rest:path}")
    def unused(rest: str): return {"jobs": [], "requests": [], "roots": []}

    @app.on_event("shutdown")
    async def shutdown(): await voice.shutdown()

    app.mount("/", StaticFiles(directory="imprint/ui/out", html=True), name="static")
    with contextlib.ExitStack() as stack:
        if os.environ.get("IMPRINT_VOICE_SMOKE_DIAGNOSTICS") == "1":
            # Disposable fixture only: never enable native prompt logs in the API.
            log = stack.enter_context((Path(os.environ["IMPRINT_DATA_DIR"]) / "native.log").open("wb"))
            original_popen, original_verify = subprocess.Popen, voice.voice_runtime.verify
            def verify(root):
                began = time.monotonic()
                log.write(b"Verifying voice assets\n"); log.flush()
                result = original_verify(root)
                log.write(f"Assets verified after {time.monotonic() - began:.2f}s\n".encode()); log.flush()
                return result
            def launch(*arguments, **kwargs):
                command = arguments[0] if arguments else kwargs.get("args", [])
                if isinstance(command, (list, tuple)) and command and Path(command[0]).name in {
                    "llama-liquid-audio-server", "llama-liquid-audio-server.exe",
                }:
                    log.write(b"Launching owned native worker\n"); log.flush()
                    kwargs.update(stdout=log, stderr=subprocess.STDOUT)
                return original_popen(*arguments, **kwargs)
            stack.enter_context(patch.object(voice.voice_runtime, "verify", side_effect=verify))
            stack.enter_context(patch.object(subprocess, "Popen", side_effect=launch))
        uvicorn.run(app, host="127.0.0.1", port=port, proxy_headers=False, log_level="error")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--serve", type=int)
    parser.add_argument("--bundled-setup", action="store_true", help="Install the shipped engine with PATH empty; seed only verified model weights")
    parser.add_argument("--native-logs", action="store_true", help="Retain startup diagnostics from disposable fixtures only")
    args = parser.parse_args()
    if args.serve:
        serve(args.serve)
        return
    if not args.runtime_dir or not args.output_dir:
        parser.error("Supply --runtime-dir and --output-dir")
    node = shutil.which("node")
    if not node:
        parser.error("Node is required")
    with tempfile.TemporaryDirectory(prefix="imprint-voice-desktop-") as directory:
        env = dict(os.environ, IMPRINT_DATA_DIR=directory, IMPRINT_WORKSPACE="",
                   IMPRINT_VOICE_RUNTIME_DIR=str(args.runtime_dir.resolve()), IMPRINT_CHAT_PROVIDER="local")
        if args.bundled_setup:
            from imprint import voice_bundle, voice_runtime, lfm_audio
            voice_runtime.verify(args.runtime_dir.resolve())
            if not voice_bundle.available():
                raise RuntimeError("Bundle the native engine before --bundled-setup")
            private_cache = Path(directory) / "voice-cache"
            private_cache.mkdir()
            for name in lfm_audio.ASSETS:
                if name == lfm_audio.ARCHIVE: continue
                shutil.copyfile(args.runtime_dir / name, private_cache / name)
            env["IMPRINT_VOICE_RUNTIME_DIR"] = str(private_cache)
            env["PATH"] = ""  # neither CMake nor a compiler can be discovered
        if args.native_logs:
            env["IMPRINT_VOICE_SMOKE_DIAGNOSTICS"] = "1"
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        from imprint.voice_runtime import child_options
        with (Path(directory) / "api.log").open("wb") as log:
            api = subprocess.Popen([sys.executable, __file__, "--serve", str(port)], env=env,
                                   stdout=log, stderr=log, stdin=subprocess.DEVNULL, **child_options())
            setup_state = None
            try:
                for _ in range(300):
                    if api.poll() is not None:
                        raise RuntimeError("Disposable GUI API failed to start")
                    try:
                        # Explicitly ignore any machine HTTP proxy for loopback.
                        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                        with opener.open(f"http://127.0.0.1:{port}/api/ping", timeout=.5): break
                    except OSError:
                        time.sleep(.1)
                else:
                    raise RuntimeError("Disposable GUI API timed out")
                # Exercise the real managed setup child against the already
                # verified native cache. It must reuse it without build tools or
                # new downloads, and must not start a microphone/session.
                address = f"http://127.0.0.1:{port}"
                request = urllib.request.Request(address + "/api/voice/setup", data=b"", method="POST",
                                                 headers={"Origin": address})
                with opener.open(request, timeout=5) as response:
                    setup_state = json.load(response)
                deadline = time.monotonic() + 120
                while setup_state["busy"] and time.monotonic() < deadline:
                    time.sleep(.2)
                    with opener.open(address + "/api/voice/setup", timeout=5) as response:
                        setup_state = json.load(response)
                if setup_state["stage"] != "ready" or setup_state["busy"] or not setup_state["installed"]:
                    raise RuntimeError(f"Managed cached setup failed: {setup_state}")
                if args.bundled_setup and not setup_state.get("bundled_engine"):
                    raise RuntimeError("Setup did not use the bundled native engine")
                subprocess.run([node, "ui-electron/smoke-voice.mjs", f"http://127.0.0.1:{port}",
                                str(args.output_dir.resolve())], env=env, check=True, timeout=210)
                (args.output_dir / "setup.json").write_text(json.dumps(setup_state, indent=2) + "\n", encoding="utf-8")
                from PIL import Image
                with Image.open(args.output_dir / "companion.png") as image:
                    if image.mode != "RGBA" or image.getpixel((0, 0))[3] != 0:
                        raise RuntimeError("Companion screenshot must have transparent background pixels")
            except BaseException:
                args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
                log.flush()
                shutil.copy2(Path(directory) / "api.log", args.output_dir / "api.log")
                raise
            finally:
                api.terminate()
                try: api.wait(timeout=5)
                except subprocess.TimeoutExpired: api.kill(); api.wait()
                if setup_state is not None:
                    args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
                    (args.output_dir / "setup.json").write_text(json.dumps(setup_state, indent=2) + "\n", encoding="utf-8")
                native_log = Path(directory) / "native.log"
                if native_log.exists():
                    args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
                    shutil.copy2(native_log, args.output_dir / "native.log")


if __name__ == "__main__":
    main()
