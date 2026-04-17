"""Sync export/import for Imprint memories.

Export creates a Qdrant collection snapshot + copies the SQLite graph DB.
Import restores both.  **No re-embedding needed on the receiving device.**

Usage:
    python -m imprint.cli_sync export [--output DIR] [--workspace NAME]
    python -m imprint.cli_sync import <snapshot_dir> [--workspace NAME]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

from . import config, vectorstore


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
    }
    (bundle / "manifest.json").write_text(json.dumps(meta, indent=2))

    size_mb = sum(f.stat().st_size for f in bundle.rglob("*") if f.is_file()) / (1024 * 1024)
    print(f"  Export complete: {bundle}", file=sys.stderr)
    print(f"  Size: {size_mb:.1f} MB  ({meta['point_count']} points)", file=sys.stderr)
    return bundle


def import_snapshot(
    bundle_path: Path,
    workspace: str | None = None,
) -> None:
    """Restore a snapshot bundle.  No re-embedding needed."""
    bundle = Path(bundle_path)
    if not bundle.is_dir():
        raise FileNotFoundError(f"Bundle not found: {bundle}")

    manifest_path = bundle / "manifest.json"
    if manifest_path.exists():
        meta = json.loads(manifest_path.read_text())
        print(
            f"  Importing: workspace={meta.get('workspace')}"
            f"  points={meta.get('point_count')}",
            file=sys.stderr,
        )

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

    # Restore SQLite graph DB
    graph_file = bundle / "imprint_graph.sqlite3"
    if graph_file.exists():
        dst = config.graph_db_path(workspace)
        print("  Restoring knowledge graph …", file=sys.stderr, flush=True)
        shutil.copy2(graph_file, dst)

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


def main():
    parser = argparse.ArgumentParser(description="Sync export/import for Imprint")
    sub = parser.add_subparsers(dest="action", required=True)

    exp = sub.add_parser("export", help="Export snapshot bundle")
    exp.add_argument("--output", type=Path, default=None, help="Output directory")
    exp.add_argument("--workspace", default=None, help="Target workspace")

    imp = sub.add_parser("import", help="Import snapshot bundle")
    imp.add_argument("bundle", type=Path, help="Path to export bundle directory")
    imp.add_argument("--workspace", default=None, help="Target workspace")

    args = parser.parse_args()

    if args.action == "export":
        export_snapshot(output_dir=args.output, workspace=args.workspace)
    elif args.action == "import":
        import_snapshot(bundle_path=args.bundle, workspace=args.workspace)


if __name__ == "__main__":
    main()
