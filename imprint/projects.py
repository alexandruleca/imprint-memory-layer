"""Project detection — finds real project roots and extracts canonical names.

Instead of using top-level directory names, this scans for manifest files
(package.json, go.mod, pyproject.toml, etc.) to identify actual projects.
The canonical name is used for cross-machine identity — the same project
at different paths on different machines gets the same project name.
"""

import json
import os
import re
from pathlib import Path

# Manifest files that indicate a project root, in priority order
MANIFESTS = [
    "package.json",
    "go.mod",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "Cargo.toml",
    "composer.json",
    "pom.xml",
    "build.gradle",
    "Gemfile",
    "mix.exs",
    "requirements.txt",  # Python fallback — no name extraction, uses dir name
]

# Directories to skip entirely
SKIP_DIRS = {
    "node_modules", "__pycache__", ".venv", "dist", "build", ".git",
    ".next", ".nuxt", "vendor", "coverage", ".cache", ".turbo",
    ".idea", ".vscode", ".gitlab", "target", ".cargo",
}


def find_projects(root_dir: str, max_depth: int = 4) -> list[dict]:
    """Recursively find project roots under root_dir.

    Returns list of {name, path, manifest, type} dicts.
    Name is the canonical project identity (from manifest, not path).
    """
    root = Path(root_dir).resolve()
    projects = []
    seen_paths = set()

    def _walk(current: Path, depth: int):
        if depth > max_depth:
            return
        if not current.is_dir():
            return
        if current.name in SKIP_DIRS or current.name.startswith("."):
            return

        # Check for manifest files at this level
        for manifest in MANIFESTS:
            manifest_path = current / manifest
            if manifest_path.exists():
                name = _extract_name(manifest_path, manifest, current)
                real = str(current.resolve())
                if real not in seen_paths:
                    seen_paths.add(real)
                    projects.append({
                        "name": name,
                        "path": str(current),
                        "manifest": manifest,
                        "type": _project_type(manifest),
                    })
                return  # Don't recurse into sub-projects (e.g. monorepo packages)

        # No manifest here — recurse into subdirs
        try:
            for child in sorted(current.iterdir()):
                if child.is_dir():
                    _walk(child, depth + 1)
        except PermissionError:
            pass

    _walk(root, 0)

    # Always register the root as a fallback project when it isn't already
    # one (i.e. no manifest at root). This ensures manifest-less sibling
    # directories — a folder of HTML files, a docs-only dir, a data export —
    # still get indexed when ingesting a mixed parent directory. The root
    # fallback scan excludes paths already claimed by manifested sub-projects
    # via the `exclude_paths` field so files aren't double-indexed.
    root_is_project = any(Path(p["path"]).resolve() == root for p in projects)
    if not root_is_project:
        projects.append({
            "name": _sanitize_name(root.name) or "data",
            "path": str(root),
            "manifest": "",
            "type": "data",
            "exclude_paths": [p["path"] for p in projects],
        })

    return projects


def _sanitize_name(name: str) -> str:
    """Lowercase, strip leading dots, replace spaces with hyphens."""
    name = (name or "").strip().lstrip(".")
    return re.sub(r"\s+", "-", name)


def _extract_name(manifest_path: Path, manifest: str, project_dir: Path) -> str:
    """Extract canonical project name from a manifest file."""
    try:
        if manifest == "package.json":
            data = json.loads(manifest_path.read_text())
            name = data.get("name", "")
            # Strip npm scope prefix (@org/name → name)
            if "/" in name:
                name = name.split("/")[-1]
            return name or project_dir.name

        if manifest == "go.mod":
            content = manifest_path.read_text()
            match = re.search(r"^module\s+(.+)$", content, re.MULTILINE)
            if match:
                # Use last path segment: github.com/user/repo → repo
                return match.group(1).strip().rsplit("/", 1)[-1]

        if manifest == "pyproject.toml":
            content = manifest_path.read_text()
            match = re.search(r'name\s*=\s*"([^"]+)"', content)
            if match:
                return match.group(1)

        if manifest == "Cargo.toml":
            content = manifest_path.read_text()
            match = re.search(r'name\s*=\s*"([^"]+)"', content)
            if match:
                return match.group(1)

        if manifest == "composer.json":
            data = json.loads(manifest_path.read_text())
            name = data.get("name", "")
            if "/" in name:
                name = name.split("/")[-1]
            return name or project_dir.name

    except Exception:
        pass

    return project_dir.name


def _project_type(manifest: str) -> str:
    """Infer project type from manifest."""
    return {
        "package.json": "node",
        "go.mod": "go",
        "pyproject.toml": "python",
        "setup.py": "python",
        "setup.cfg": "python",
        "Cargo.toml": "rust",
        "composer.json": "php",
        "pom.xml": "java",
        "build.gradle": "java",
        "Gemfile": "ruby",
        "mix.exs": "elixir",
        "requirements.txt": "python",
    }.get(manifest, "unknown")


# ── Moving memories between projects (ADR 0036) ─────────────────
#
# A writer names each memory's project: ingestion from the directory layout,
# learning from the conversation's working directory, MCP and URL ingestion
# from their argument, sync from the peer. That name is the memory's origin
# project (payload ``origin_project``). The workspace's placement rules map an
# origin project, either whole or narrowed to one source key, to the project
# the memory is placed in. vectorstore applies them on every write, as it
# applies purge exclusions, so a move outlives refresh, learning and sync.
# Rules are this device's policy for one workspace, like roots, exclusions
# and project mappings, and are never synced.

def _placement_rules(conn, workspace: str) -> dict[tuple[str, str], str]:
    return {(project, source): target for project, source, target in conn.execute(
        'SELECT project, source, target FROM placements WHERE workspace=?', (workspace,))}


def placement(origin: str, source: str, rules: dict[tuple[str, str], str]) -> str:
    """The project for memories that ``origin`` names with ``source``:
    a rule for that source, else one for the whole origin, else the origin."""
    return rules.get((origin, source)) or rules.get((origin, "")) or origin


def place(records: list[dict], workspace: str | None = None) -> list[dict]:
    """``records`` in the projects this workspace's rules place them in.

    Placement always starts from the origin, so placing a placed record again
    (a nested store, or a record coming back from a peer) changes nothing. A
    record that carries a logical ID (sync) gets the same kind of ID under
    its placed project.
    """
    from . import workspace_policy as policy
    from .device import origin_project
    ws = policy.workspace_name(workspace)
    with policy._db() as conn:
        rules = _placement_rules(conn, ws)
    placed = []
    for record in records:
        origin = origin_project(record)
        project = placement(origin, record.get("source", ""), rules)
        moved = {**record, "project": project, "origin_project": origin}
        if record.get("id") and project != record.get("project", ""):
            moved["id"] = relabeled_id(record["id"], record, project)
        placed.append(moved)
    return placed


def relabeled_id(memory_id: str, record: dict, project: str) -> str:
    """The logical ID ``memory_id`` takes when its memory moves to ``project``.

    IDs hash the project, source, content and position (vectorstore._make_id);
    a note stored without a position keeps that form.
    """
    from . import vectorstore as vs
    content, source = record.get("content", ""), record.get("source", "")
    if memory_id == vs._make_id(content, record.get("project", ""), source):
        return vs._make_id(content, project, source)
    return vs._make_id(content, project, source, record.get("chunk_index"))


def _moved_rules(rules: dict, project: str, target: str, pairs: set[tuple[str, str]],
                 whole: bool) -> dict:
    """Rules after moving ``pairs`` ((origin, source) of the moved memories) from
    ``project`` to ``target``. Moving the whole project also sends whatever was
    placed in it, and its own name, to ``target``. Rules that only restate the
    default are dropped, so moving something back leaves no rule behind."""
    new = dict(rules)
    if whole:
        new = {key: target if value == project else value for key, value in new.items()}
        new.setdefault((project, ""), target)
    for origin, source in pairs:
        if placement(origin, source, new) != target:
            new[(origin, source)] = target
    new = {key: value for key, value in new.items() if key[1] or value != key[0]}
    return {key: value for key, value in new.items()
            if not key[1] or value != new.get((key[0], ""), key[0])}


def project_name(value) -> str:
    if (not isinstance(value, str) or not value or value != value.strip() or len(value) > 256
            or not value.isprintable()):
        raise ValueError("A project name of 1-256 printable characters, without surrounding spaces, is required")
    return value


def move(project: str, target: str, sources: list[str] | None = None, *,
         workspace: str | None = None, apply: bool = False) -> dict:
    """Preview, or apply, moving a project's memories to ``target``.

    ``sources`` selects source keys; None moves the whole project, merging it
    into ``target`` when that project exists. Applying records the placement
    rules first, then rewrites each memory under ``target`` with its vector,
    source key and provenance unchanged, and removes the old copy. Graph
    facts reference source keys, which do not change. An interrupted apply
    leaves duplicates, never losses; applying again finishes it.
    """
    from qdrant_client import models as qm
    from . import config, vectorstore as vs, workspace_policy as policy
    from .device import origin_project
    ws = policy.workspace_name(workspace)
    project, target = project_name(project), project_name(target)
    if project == target:
        raise ValueError("Choose a different target project")
    if sources is not None:
        if not sources or not all(isinstance(source, str) and source for source in sources):
            raise ValueError("Select at least one source")
        sources = sorted(set(sources))
    must = [qm.FieldCondition(key="project", match=qm.MatchValue(value=project))]
    if sources is not None:
        must.append(qm.FieldCondition(key="source", match=qm.MatchAny(any=sources)))
    selection = qm.Filter(must=must)
    client, coll = vs._ensure_collection(ws)

    def scan(**options):
        offset = None
        while True:
            points, offset = client.scroll(collection_name=coll, scroll_filter=selection, limit=256,
                                           offset=offset, **options)
            yield points
            if offset is None:
                return

    pairs, moved_sources, chunks = set(), set(), 0
    for points in scan(with_payload=["project", "source", "origin_project"], with_vectors=False):
        for point in points:
            payload = point.payload or {}
            pairs.add((origin_project(payload), payload.get("source", "")))
            moved_sources.add(payload.get("source", ""))
            chunks += 1
    result = {"workspace": ws, "project": project, "target": target, "whole": sources is None,
              "chunks": chunks, "sources": len(moved_sources - {""}),
              "target_exists": bool(client.count(coll, count_filter=qm.Filter(must=[
                  qm.FieldCondition(key="project", match=qm.MatchValue(value=target))]), exact=True).count),
              "sample": sorted(moved_sources - {""})[:20], "applied": apply}
    if not apply:
        return result
    # Rules first, like purge's exclusion: a write that lands while points are
    # rewritten is already placed in the target.
    with policy._db() as conn:
        rules = _moved_rules(_placement_rules(conn, ws), project, target, pairs, sources is None)
        conn.execute("DELETE FROM placements WHERE workspace=?", (ws,))
        conn.executemany("INSERT INTO placements VALUES (?, ?, ?, ?)",
                         [(ws, origin, source, value) for (origin, source), value in rules.items()])
    for points in scan(with_payload=True, with_vectors=True):
        rewritten, retired = [], []
        for point in points:
            payload = point.payload or {}
            origin = origin_project(payload)
            placed = placement(origin, payload.get("source", ""), rules)
            memory_id = relabeled_id(payload.get("_mid") or "", payload, placed)
            vector = point.vector if isinstance(point.vector, dict) else {config.QDRANT_VECTOR_NAME: point.vector}
            rewritten.append(qm.PointStruct(id=vs._point_uuid(memory_id), vector=vector, payload={
                **payload, "_mid": memory_id, "project": placed, "origin_project": origin}))
            retired.append(point.id)
        if not rewritten:
            continue
        vs._persist_points(client, coll, rewritten, ws)
        kept = {point.id for point in rewritten}
        client.delete(collection_name=coll, wait=True, points_selector=qm.PointIdsList(
            points=[point_id for point_id in retired if point_id not in kept]))
    with vs._cache_lock:
        vs._id_caches.pop(coll, None)
    vs._wal_log("move", workspace=ws, project=project, target=target, chunks=chunks,
                sources=len(moved_sources))
    return result


def run_move(project: str, target: str, sources: list[str] | None = None, **options) -> dict:
    """API entry point; shares the ingestion lock like purge."""
    from . import queue_lock
    fd = queue_lock.acquire("move", "", block=False)
    if fd is None:
        raise ValueError("An ingestion or maintenance job is running; retry the move after it finishes")
    try:
        return move(project, target, sources, **options)
    finally:
        queue_lock.release(fd)


def main():
    """``imprint move``: the CLI holds the ingestion lock, as for purge."""
    import argparse
    import json
    parser = argparse.ArgumentParser(
        prog="imprint move",
        description="Move a project's memories, or some of its sources, to another project. "
                    "Previews unless --apply.")
    parser.add_argument("project", help="Project to move from")
    parser.add_argument("target", help="Project to move to; created if new, merged into if it exists")
    parser.add_argument("--source", action="append", dest="sources", metavar="KEY",
                        help="Move only this source key (repeatable); default: the whole project")
    parser.add_argument("--workspace")
    parser.add_argument("--apply", action="store_true", help="Move the memories and keep later writes there")
    args = parser.parse_args()
    try:
        result = move(args.project, args.target, args.sources, workspace=args.workspace, apply=args.apply)
    except ValueError as error:
        parser.exit(2, f"error: {error}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
