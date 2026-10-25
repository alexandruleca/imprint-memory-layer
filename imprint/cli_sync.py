"""``imprint sync``: live peer sync and snapshot bundles.

Live sync (``serve`` / ``join``) is the terminal front-end for the sessions
in :mod:`imprint.sync_ws`, the same ones the dashboard streams over SSE.

Export creates a Qdrant collection snapshot + copies the SQLite graph DB.
Import restores both, then scrubs them: a bundle is a raw copy of storage and
may predate redaction (ADR 0060). Only memories that held secrets are
embedded again.

Usage:
    python -m imprint.cli_sync serve [--relay HOST] [--workspace NAME] [--send-only | --receive-only]
    python -m imprint.cli_sync join CODE [--workspace NAME] [--two-way | --send-only] [--fresh]
    python -m imprint.cli_sync export [--output DIR] [--workspace NAME]
    python -m imprint.cli_sync import <snapshot_dir> [--workspace NAME]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

from . import config, sync_ws, vectorstore


def _snapshots_dir() -> Path:
    return config.get_data_dir() / "qdrant_snapshots"


def export_snapshot(
    output_dir: Path | None = None,
    workspace: str | None = None,
) -> Path:
    """Create a portable snapshot bundle (Qdrant snapshot + KG + metadata).

    Returns the path to the created bundle directory.
    """
    client, coll = vectorstore._ensure_collection(workspace)

    # Qdrant snapshot
    print("  Creating Qdrant collection snapshot …", file=sys.stderr, flush=True)
    snap_info = client.create_snapshot(collection_name=coll, wait=True)
    if not snap_info:
        raise RuntimeError("Qdrant snapshot creation returned None")

    snap_name = snap_info.name
    snap_source = _snapshots_dir() / coll / snap_name

    # Bundle directory
    ts = time.strftime("%Y%m%d-%H%M%S")
    ws_label = workspace or config.get_active_workspace()
    bundle_name = f"imprint-export-{ws_label}-{ts}"
    if output_dir is None:
        output_dir = config.get_data_dir() / "exports"
    bundle = output_dir / bundle_name
    bundle.mkdir(parents=True, exist_ok=True)

    # Copy Qdrant snapshot into bundle
    qdrant_dst = bundle / "qdrant.snapshot"
    if snap_source.exists():
        shutil.copy2(snap_source, qdrant_dst)
    else:
        # Some Qdrant versions put snapshots at a different path —
        # try downloading via HTTP API as fallback.
        _download_snapshot_http(coll, snap_name, qdrant_dst)

    # Copy SQLite graph DB
    graph_src = config.graph_db_path(workspace)
    if graph_src.exists():
        shutil.copy2(graph_src, bundle / "imprint_graph.sqlite3")

    # Metadata
    meta = {
        "workspace": ws_label,
        "collection": coll,
        "snapshot": snap_name,
        "timestamp": time.time(),
        "point_count": _count_points(client, coll),
        "embedding_identity": config.embedding_identity(),
    }
    from .device import current
    meta['exported_from_device'] = current()
    (bundle / "manifest.json").write_text(json.dumps(meta, indent=2))

    size_mb = sum(f.stat().st_size for f in bundle.rglob("*") if f.is_file()) / (1024 * 1024)
    print(f"  Export complete: {bundle}", file=sys.stderr)
    print(f"  Size: {size_mb:.1f} MB  ({meta['point_count']} points)", file=sys.stderr)
    return bundle


def import_snapshot(
    bundle_path: Path,
    workspace: str | None = None,
) -> None:
    """Restore a snapshot bundle, then redact any secrets it brought."""
    bundle = Path(bundle_path)
    if not bundle.is_dir():
        raise FileNotFoundError(f"Bundle not found: {bundle}")

    manifest_path = bundle / "manifest.json"
    meta = {}
    if manifest_path.exists():
        meta = json.loads(manifest_path.read_text())
        print(
            f"  Importing: workspace={meta.get('workspace')}"
            f"  points={meta.get('point_count')}",
            file=sys.stderr,
        )

    if meta.get("embedding_identity") != config.embedding_identity():
        raise ValueError("Snapshot embedding profile mismatch or missing identity; reindex/export with the same model settings before importing")
    from .workspace_policy import exclusions
    if exclusions(workspace):
        raise ValueError('Snapshot restore would bypass purge exclusions. Import into a fresh workspace or explicitly restore its exclusions first.')
    client, coll = vectorstore._ensure_collection(workspace)

    # Restore Qdrant snapshot
    snap_file = bundle / "qdrant.snapshot"
    if not snap_file.exists():
        raise FileNotFoundError(f"No qdrant.snapshot in {bundle}")

    print("  Restoring Qdrant collection …", file=sys.stderr, flush=True)
    # recover_snapshot expects a file:// URI or HTTP URL for local files
    client.recover_snapshot(
        collection_name=coll,
        location=f"file://{snap_file.resolve()}",
    )
    from qdrant_client import models as qm
    client.set_payload(collection_name=coll, payload={'source_path_local': False}, points=qm.Filter(), wait=True)

    # Restore SQLite graph DB
    graph_file = bundle / "imprint_graph.sqlite3"
    if graph_file.exists():
        dst = config.graph_db_path(workspace)
        print("  Restoring knowledge graph …", file=sys.stderr, flush=True)
        shutil.copy2(graph_file, dst)

    from .cli_scrub import scrub, summary
    print(summary(scrub(workspace, apply=True)), file=sys.stderr)
    print("  Import complete.", file=sys.stderr)


def _count_points(client, coll: str) -> int:
    try:
        info = client.get_collection(coll)
        return info.points_count or 0
    except Exception:
        return 0


def _download_snapshot_http(coll: str, snap_name: str, dest: Path) -> None:
    """Fallback: download snapshot via Qdrant HTTP API."""
    from .config_schema import resolve
    host = resolve("qdrant.host")[0]
    port = resolve("qdrant.port")[0]
    url = f"http://{host}:{port}/collections/{coll}/snapshots/{snap_name}"
    try:
        import httpx
        with httpx.stream("GET", url, timeout=300.0) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in resp.iter_bytes(1024 * 1024):
                    f.write(chunk)
    except Exception as e:
        raise RuntimeError(f"Failed to download snapshot: {e}") from e


# ── Live sync front-end ───────────────────────────────────────────

_COLOR = os.environ.get("NO_COLOR", "") == ""
C_RESET = "\033[0m"
C_RED = "\033[0;31m"
C_GREEN = "\033[0;32m"
C_YELLOW = "\033[1;33m"
C_CYAN = "\033[0;36m"


def _paint(color: str, text: str) -> str:
    return f"{color}{text}{C_RESET}" if _COLOR else text


def _info(msg: str) -> None:
    print(f"{_paint(C_CYAN, '[*]')} {msg}", flush=True)


def _ok(msg: str) -> None:
    print(f"{_paint(C_GREEN, '[+]')} {msg}", flush=True)


def _warn(msg: str) -> None:
    print(f"{_paint(C_YELLOW, '[!]')} {msg}", flush=True)


def _fail(msg: str) -> None:
    print(f"{_paint(C_RED, '[x]')} {msg}", flush=True)


def _header(msg: str) -> None:
    print(f"\n{_paint(C_GREEN, msg)}", flush=True)


def _duration(seconds: float) -> str:
    total = int(seconds)
    h, m, s = total // 3600, total % 3600 // 60, total % 60
    if h:
        return f"{h}h{m}m"
    return f"{m}m{s}s" if m else f"{s}s"


def _size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return ""


def _stats(stats: dict | None) -> str:
    memories = (stats or {}).get("memories", {})
    facts = (stats or {}).get("facts", {})
    present = memories.get("skipped", 0) + facts.get("skipped", 0)
    return (f"{memories.get('inserted', 0)} new memories, {memories.get('updated', 0)} retagged,"
            f" {facts.get('inserted', 0)} new facts ({present} already present)")


def _plan(event: dict) -> str:
    phases = ("pull, then push" if event["pull"] and event["push"]
              else "pull only" if event["pull"] else "push only")
    return " — ".join([phases, *event["skipped"]])


class _Progress:
    """Per-phase progress: a counter updated in place on a terminal (at most
    once a second otherwise) and a summary line when the phase ends."""

    LABELS = {"send": "Sending", "push": "Sending", "receive": "Receiving", "pull": "Receiving", "store": "Storing"}

    def __init__(self) -> None:
        self.tty = sys.stdout.isatty()
        self.phase: str | None = None

    def update(self, event: dict) -> None:
        if event["phase"] != self.phase:
            self.finish()
            self.phase, self.start, self.shown, self.counts = event["phase"], time.monotonic(), 0.0, {}
        self.counts[event["dataset"]] = (event["done"], event["total"])
        now = time.monotonic()
        done, total = event["done"], event["total"]
        if not self.tty and now - self.shown < 1 and done < total:
            return
        self.shown = now
        elapsed = now - self.start
        suffix = f" [{_duration(elapsed)}]"
        if 0 < done < total and elapsed > 0:
            suffix = f" [{_duration(elapsed)} elapsed, ~{_duration((total - done) * elapsed / done)} left]"
        line = f"  {self.LABELS.get(self.phase, self.phase)} {event['dataset']}: {done}/{total}{suffix}"
        print(f"\r\033[K{line}" if self.tty else line, end="" if self.tty else "\n", flush=True)

    def finish(self) -> None:
        if self.phase is None:
            return
        if self.tty:
            print("\r\033[K", end="", flush=True)
        parts = [f"{done}/{total} {ds}" for ds, (done, total) in self.counts.items() if total]
        label = self.LABELS.get(self.phase, self.phase)
        summary = f": {', '.join(parts)}" if parts else ""
        _ok(f"{label} complete{summary} ({_duration(time.monotonic() - self.start)})")
        self.phase = None


def _ask(prompt: str) -> asyncio.Future:
    """Read a line on a daemon thread, so the event loop keeps the relay
    connection alive while the user decides and Ctrl+C is not held up."""
    loop = asyncio.get_running_loop()
    answer = loop.create_future()

    def read() -> None:
        try:
            line = input(prompt)
        except (EOFError, OSError):
            line = ""
        loop.call_soon_threadsafe(lambda: answer.done() or answer.set_result(line))

    threading.Thread(target=read, daemon=True).start()
    return answer


async def _approve(peer: dict) -> str:
    _header("═══ Incoming Sync Request ═══")
    print(f"  Device:   {peer.get('hostname', '')}")
    print(f"  User:     {peer.get('user', '')}")
    print(f"  OS:       {peer.get('os', '')}")
    print(f"  ID:       {peer.get('fingerprint', '')}")
    print()
    reply = (await _ask("  Accept? [y]es / [n]o / [t]rust & accept: ")).strip().lower()
    return {"y": "accept", "yes": "accept", "t": "trust", "trust": "trust"}.get(reply, "reject")


async def _render(session, on_event) -> int:
    """Print a session's events; ``on_event`` handles the role-specific ones
    and returns False for those it does not know. Returns the exit code."""
    progress = _Progress()
    code = 0
    async for event in session:
        kind = event["type"]
        if kind == "progress":
            progress.update(event)
            continue
        progress.finish()
        if await on_event(event):
            continue
        if kind == "workspace_created":
            _ok(f"Created workspace: {event['workspace']}")
        elif kind == "warning":
            _warn(event["message"])
        elif kind == "error":
            _fail(event["message"])
            code = 1
        elif kind == "cancelled":
            print("\n  Disconnecting...")
            code = 130
    return code


async def _serve(args) -> int:
    workspace, exists = sync_ws.sync_workspace(args.workspace)
    if not exists:
        _fail(f"workspace {workspace!r} does not exist (see `imprint workspace`)")
        return 1
    relay = sync_ws.parse_relay(args.relay)
    policy = _policy(args)
    _header("═══ Imprint Sync Server ═══")
    _info(f"Connecting to relay: {relay}")
    room: dict = {}

    async def on_event(event: dict) -> bool:
        kind, status = event["type"], event.get("status")
        if kind == "room":
            room.update(event)
        elif status == "waiting":
            device = sync_ws.get_device_identity()
            _ok("Connected! Share this with the other machine:")
            print(f"\n  imprint sync {room['target']}\n")
            print(f"  This device: {device['hostname']} (id: {device['fingerprint']})")
            print(f"  Workspace:   {workspace}")
            print(f"  Mode:        {policy.mode()}\n")
            _info("Waiting for peer... (Ctrl+C to stop)")
        elif kind == "approval_required":
            sync_ws.submit_approval(room["session_id"], await _approve(event))
        elif kind == "auto_accepted":
            _ok(f"Trusted device connected: {event['hostname']} ({event['fingerprint']})")
        elif kind == "trusted":
            _ok("Device added to trusted list")
        elif kind == "peer_connected":
            _ok(f"Peer connected: {event['hostname']} ({event['fingerprint']})")
        elif status == "sending":
            _info("Peer requesting pull...")
        elif status == "receiving":
            _info("Peer pushing data...")
        elif kind == "refused":
            _warn(f"Refused {event['request']}: {event['message']}")
        elif kind == "push_complete":
            _ok("Merged peer data: " + _stats(event["stats"]))
        elif kind == "paired":
            _ok("Paired for automatic sync. Keep the dashboard or imprint sync watch running on both devices.")
        elif kind == "done":
            print("  Connection closed.")
        else:
            return kind == "status"
        return True

    if getattr(args, 'remember', False):
        _info('Automatic pairing enabled for this workspace and direction; the other device must also opt in.')
    return await _render(sync_ws.serve_session(workspace, policy, relay, remember=getattr(args, 'remember', False)), on_event)


async def _join(args) -> int:
    try:
        relay, room_id, pin = sync_ws.parse_code(args.code)
    except ValueError as e:
        _fail(str(e))
        return 1
    policy = _policy(args)
    workspace, exists = sync_ws.sync_workspace(args.workspace)
    if not exists and not policy.receive:
        _fail(f"workspace {workspace!r} does not exist, so a --send-only sync has nothing to send (see `imprint workspace`)")
        return 1

    _header("═══ Imprint Sync ═══")
    _info(f"Workspace: {workspace}" + ("" if exists else " (new — created when data arrives)"))

    session: dict = {}

    async def on_event(event: dict) -> bool:
        kind, status = event["type"], event.get("status")
        if kind == "session":
            session.update(event)
        elif status == "connected":
            _ok("Connected to relay")
        elif status == "handshake_sent":
            _info("Handshake sent — waiting for the other machine to accept this device...")
        elif status == "handshake_ok":
            _ok("Handshake accepted")
        elif kind == "peer_connected":
            _ok(f"Connected to {_device(event)}")
        elif kind == "confirm_push":
            reply = (await _ask(f"  Send your workspace '{event['workspace']}' to {_device(event)}? [y/N]: ")).strip()
            sync_ws.submit_approval(session["session_id"], "accept" if reply.lower() in ("y", "yes") else "reject")
        elif kind == "plan":
            _info("Plan: " + _plan(event))
            if args.direction == "receive":
                _info("Joining only receives; add --two-way to also send this workspace (you confirm first).")
        elif status == "pulling":
            _info("Pulling remote data...")
        elif status == "pushing":
            _info("Pushing local data...")
        elif kind == "pull_complete":
            _ok("Merged remote → local: " + _stats(event["stats"]))
        elif kind == "push_complete":
            _ok("Pushed local → remote: " + _stats(event["stats"]))
        elif kind == "paired":
            _ok("Paired for automatic sync. Keep the dashboard or imprint sync watch running on both devices.")
        elif kind == "done":
            _header("═══ Sync Complete ═══")
        else:
            return kind == "status"
        return True

    if not policy.receive:
        return await _connect_and_sync(relay, room_id, pin, workspace, policy, None, on_event,
                                       remember=getattr(args, 'remember', False))
    return await _receive(args, relay, room_id, pin, workspace, policy, on_event)


async def _receive(args, relay, room_id, pin, workspace, policy, on_event) -> int:
    """A joining run that may import. A completed download that failed to
    store is finished locally first: the peer's room is gone by now."""
    buffer = sync_ws.pending_buffer_path()
    if args.fresh:
        removed = sync_ws.discard_pending(buffer)
        _info("--fresh: " + (f"cleared {', '.join(map(str, removed))}" if removed
                             else "no buffer to clear, will download fresh"))
    try:
        pending = sync_ws.pending_download(buffer)
    except ValueError as e:
        _fail(str(e))
        return 1
    if pending is None:
        return await _connect_and_sync(relay, room_id, pin, workspace, policy, buffer, on_event,
                                       remember=getattr(args, 'remember', False))
    if pending.workspace != workspace:
        _fail(f"An unfinished download into workspace {pending.workspace!r} is waiting to be stored. "
              f"Finish it with --workspace {pending.workspace}, or discard it with --fresh.")
        return 1
    _info(f"Found local buffer: {pending.memories} memories, {pending.facts} facts "
          f"(downloaded {_duration(time.time() - pending.modified)} ago, {_size(pending.size)})")
    _info("Resuming local processing — peer not needed (run with --fresh to redownload)")
    code = await _render(sync_ws.resume_session(pending), on_event)
    if code == 0:
        _info("To also send this workspace, run `imprint sync <code> --two-way` while the other machine serves.")
    return code


async def _connect_and_sync(relay, room_id, pin, workspace, policy, buffer, on_event, *, remember=False) -> int:
    _info(f"Connecting to {relay}...")
    return await _render(sync_ws.receive_session(relay, room_id, pin, workspace, policy, buffer, remember=remember), on_event)


def _device(peer: dict) -> str:
    return f"{peer['hostname']} ({peer['user']}, {peer['os']}, id {peer['fingerprint']})"


def _policy(args) -> "sync_ws.SyncPolicy":
    return sync_ws.SyncPolicy.parse(args.direction)


def _live(coro) -> int:
    try:
        return asyncio.run(coro)
    except KeyboardInterrupt:
        print("\n  Disconnecting...")
        return 130
    except ValueError as e:
        _fail(str(e))
        return 1


def _direction_flags(parser: argparse.ArgumentParser, *flags: tuple[str, str, str], default: str) -> None:
    """--workspace plus mutually exclusive direction flags, each storing a
    SyncPolicy direction; serving and joining default differently."""
    parser.add_argument("--workspace", default=None,
                        help="Sync this workspace instead of the active one (the active workspace is not changed)")
    direction = parser.add_mutually_exclusive_group()
    for flag, value, text in flags:
        direction.add_argument(flag, dest="direction", action="store_const", const=value, help=text)
    parser.set_defaults(direction=default)


def main():
    parser = argparse.ArgumentParser(prog="imprint sync", description="Live peer sync and snapshot bundles")
    sub = parser.add_subparsers(dest="action", required=True)

    serve = sub.add_parser("serve", help="Expose a workspace via the relay; prints a one-time share code")
    serve.add_argument("--relay", default=None, help="Relay host (default: the public relay)")
    serve.add_argument('--remember', action='store_true', help='Offer automatic sync for this workspace/direction; both devices must opt in')
    _direction_flags(serve,
                     ("--send-only", "send", "Peers can pull this workspace; pushes are refused"),
                     ("--receive-only", "receive", "Peers can push into this workspace; nothing is sent"),
                     default="both")

    join = sub.add_parser("join", help="Sync with a machine that is serving (receives only unless told otherwise)")
    join.add_argument("code", help="The code the serving machine shows, e.g. 7f3a9c21-Ab3xY9Kq or wss://<host>/<code>")
    join.add_argument("--fresh", action="store_true", help="Discard any saved download and re-download")
    join.add_argument('--remember', action='store_true', help='Pair for automatic sync with the approved workspace/direction')
    _direction_flags(join,
                     ("--two-way", "both", "Also send this workspace (you see who receives it and confirm first)"),
                     ("--send-only", "send", "Only send this workspace (confirmed first); import nothing"),
                     default="receive")

    exp = sub.add_parser("export", help="Export snapshot bundle")
    exp.add_argument("--output", type=Path, default=None, help="Output directory")
    exp.add_argument("--workspace", default=None, help="Target workspace")

    imp = sub.add_parser("import", help="Import snapshot bundle")
    imp.add_argument("bundle", type=Path, help="Path to export bundle directory")
    imp.add_argument("--workspace", default=None, help="Target workspace")

    sub.add_parser('watch', help='Automatically sync paired devices while this process runs')
    sub.add_parser('peers', help='List paired devices and pending forget acknowledgements')
    for name in ('enable', 'disable', 'forget'):
        control = sub.add_parser(name, help=f'{name.title()} a paired device')
        control.add_argument('pair_id')

    args = parser.parse_args()

    if args.action == "serve":
        sys.exit(_live(_serve(args)))
    elif args.action == "join":
        if args.fresh and args.direction == "send":
            join.error("--fresh re-downloads a pull, and a --send-only sync downloads nothing")
        sys.exit(_live(_join(args)))
    elif args.action == "export":
        export_snapshot(output_dir=args.output, workspace=args.workspace)
    elif args.action == "import":
        import_snapshot(bundle_path=args.bundle, workspace=args.workspace)
    elif args.action == 'watch':
        from .sync_pairs import run
        _info('Automatic sync is running. Ctrl+C stops it; the dashboard also runs this worker.')
        sys.exit(_live(run()))
    elif args.action == 'peers':
        from .sync_pairs import pairs
        print(json.dumps(pairs(public=True), indent=2))
    elif args.action in ('enable', 'disable', 'forget'):
        from .sync_pairs import get, forget, set_enabled
        try:
            if args.action == 'forget':
                peer = get(args.pair_id)['peer']
                answer = input(f"Forget {_device(peer)} on BOTH devices, for all paired workspaces? "
                               'Existing memories remain. An offline peer is notified when both reconnect. [y/N]: ')
                if answer.strip().lower() not in ('y', 'yes'):
                    return
                forget(args.pair_id, confirmed=True)
                _ok('Revoked locally. Remote removal is pending until the devices reconnect. Run imprint sync watch or keep the dashboard open.')
            else:
                set_enabled(args.pair_id, args.action == 'enable')
        except ValueError as exc:
            _fail(str(exc))
            sys.exit(1)


if __name__ == "__main__":
    main()
