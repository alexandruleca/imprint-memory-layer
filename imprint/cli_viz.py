"""Imprint Graph — hierarchical bubble visualization of memory.

Server-side aggregation via Qdrant facets. Frontend renders with G6 v5
(AntV) for Neo4j-style combo clustering with drill-down.

Zero build step. G6 loaded via CDN. Handles 100k+ memories by never
sending more than ~500 nodes to the browser at once.

Usage: python -m imprint.cli_viz [--port 8420]
"""

import hashlib
import http.server
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
import platform as plat
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from imprint import config, vectorstore as vs, imprint_graph as kg
from qdrant_client import models as qm

DEFAULT_PORT = 8420

PROJECT_COLORS = [
    "#ff6b6b", "#4ecdc4", "#60a5fa", "#a78bfa", "#fbbf24",
    "#f472b6", "#34d399", "#ff8c42", "#818cf8", "#fb923c",
    "#2dd4bf", "#e879f9", "#facc15", "#38bdf8", "#a3e635",
]

TYPE_COLORS = {
    "decision": "#ff6b6b", "pattern": "#4ecdc4", "bug": "#ff8c42",
    "preference": "#a78bfa", "architecture": "#60a5fa", "milestone": "#34d399",
    "finding": "#fbbf24", "conversation": "#f472b6",
}


def _project_color(name: str) -> str:
    h = int(hashlib.md5(name.encode()).hexdigest(), 16)
    return PROJECT_COLORS[h % len(PROJECT_COLORS)]


def _extract_label(content: str, source: str = "", fallback: str = "") -> str:
    for line in content.split("\n"):
        line = line.strip()
        if line and len(line) > 10 and not line.startswith(("[", "import ", "from ", "#", "---", "```")):
            return line[:80]
    return source or fallback or "memory"


# ── Backend API functions ──────────────────────────────────────


def _facet(key: str, limit: int = 100, filt: qm.Filter | None = None) -> list[tuple[str, int]]:
    """Facet counts with optional filter. Index-based, no scan."""
    client, coll = vs._ensure_collection()
    try:
        resp = client.facet(
            collection_name=coll,
            key=key,
            facet_filter=filt,
            limit=limit,
        )
        return [(hit.value, hit.count) for hit in resp.hits]
    except Exception:
        return []


def build_overview(filters: dict | None = None) -> dict:
    """Project-level aggregation. Returns bubble data for overview."""
    client, coll = vs._ensure_collection()
    try:
        info = client.get_collection(coll)
        total = info.points_count or 0
    except Exception:
        return {"projects": [], "total": 0, "version": int(time.time()), "facets": {}}

    if total == 0:
        return {"projects": [], "total": 0, "version": int(time.time()), "facets": {}}

    # Build global filter from query params
    global_filter = _build_global_filter(filters) if filters else None

    # Global facets for filter sidebar
    type_facets = _facet("type", 20, global_filter)
    lang_facets = _facet("tags.lang", 30, global_filter)
    domain_facets = _facet("tags.domain", 50, global_filter)
    layer_facets = _facet("tags.layer", 20, global_filter)

    # Project facets
    project_facets = _facet("project", 100, global_filter)

    projects = []
    for name, count in project_facets:
        if not name:
            continue
        proj_must = [qm.FieldCondition(key="project", match=qm.MatchValue(value=name))]
        if global_filter and global_filter.must:
            proj_must.extend(global_filter.must)
        proj_filter = qm.Filter(must=proj_must)

        type_in_proj = _facet("type", 20, proj_filter)
        domain_in_proj = _facet("tags.domain", 10, proj_filter)
        lang_in_proj = _facet("tags.lang", 5, proj_filter)

        projects.append({
            "id": f"proj_{name}",
            "name": name,
            "count": count,
            "types": {v: c for v, c in type_in_proj if v},
            "color": _project_color(name),
            "topDomains": [v for v, _ in domain_in_proj[:5] if v],
            "topLangs": [v for v, _ in lang_in_proj[:3] if v],
        })

    return {
        "projects": sorted(projects, key=lambda p: -p["count"]),
        "total": total,
        "version": int(time.time()),
        "facets": {
            "types": [(v, c) for v, c in type_facets if v],
            "langs": [(v, c) for v, c in lang_facets if v],
            "domains": [(v, c) for v, c in domain_facets if v],
            "layers": [(v, c) for v, c in layer_facets if v],
        },
    }


def _build_global_filter(filters: dict) -> qm.Filter | None:
    """Build Qdrant filter from query param dict."""
    must = []
    if filters.get("type"):
        vals = filters["type"] if isinstance(filters["type"], list) else [filters["type"]]
        must.append(qm.FieldCondition(key="type", match=qm.MatchAny(any=vals)))
    if filters.get("lang"):
        vals = filters["lang"] if isinstance(filters["lang"], list) else [filters["lang"]]
        must.append(qm.FieldCondition(key="tags.lang", match=qm.MatchAny(any=vals)))
    if filters.get("domain"):
        vals = filters["domain"] if isinstance(filters["domain"], list) else [filters["domain"]]
        must.append(qm.FieldCondition(key="tags.domain", match=qm.MatchAny(any=vals)))
    if filters.get("layer"):
        vals = filters["layer"] if isinstance(filters["layer"], list) else [filters["layer"]]
        must.append(qm.FieldCondition(key="tags.layer", match=qm.MatchAny(any=vals)))
    return qm.Filter(must=must) if must else None


def build_project_detail(project_name: str, filters: dict | None = None) -> dict:
    """Type/domain breakdown + sample nodes for one project."""
    client, coll = vs._ensure_collection()

    proj_must = [qm.FieldCondition(key="project", match=qm.MatchValue(value=project_name))]
    if filters:
        gf = _build_global_filter(filters)
        if gf and gf.must:
            proj_must.extend(gf.must)
    proj_filter = qm.Filter(must=proj_must)

    type_facets = _facet("type", 20, proj_filter)
    domain_facets = _facet("tags.domain", 30, proj_filter)
    lang_facets = _facet("tags.lang", 20, proj_filter)

    # Sample nodes
    try:
        sample_pts, _ = client.scroll(
            collection_name=coll,
            scroll_filter=proj_filter,
            limit=200,
            with_payload=["_mid", "content", "type", "source", "tags", "timestamp"],
            with_vectors=False,
        )
    except Exception:
        sample_pts = []

    sample_nodes = []
    for p in sample_pts:
        pl = p.payload or {}
        sample_nodes.append({
            "id": pl.get("_mid", ""),
            "label": _extract_label(pl.get("content", ""), pl.get("source", "")),
            "type": pl.get("type", ""),
            "source": pl.get("source", ""),
            "tags": pl.get("tags", {}),
            "content": (pl.get("content", "") or "")[:500],
        })

    total_count = sum(c for _, c in type_facets)

    return {
        "project": project_name,
        "count": total_count,
        "color": _project_color(project_name),
        "types": [{"name": v, "count": c} for v, c in type_facets if v],
        "domains": [{"name": v, "count": c} for v, c in domain_facets if v],
        "langs": [{"name": v, "count": c} for v, c in lang_facets if v],
        "sampleNodes": sample_nodes,
    }


def build_node_page(project: str = "", type_: str = "", domain: str = "",
                    lang: str = "", limit: int = 500, offset_id: str = "") -> dict:
    """Paginated leaf nodes with filters."""
    client, coll = vs._ensure_collection()

    must = []
    if project:
        must.append(qm.FieldCondition(key="project", match=qm.MatchValue(value=project)))
    if type_:
        must.append(qm.FieldCondition(key="type", match=qm.MatchValue(value=type_)))
    if domain:
        must.append(qm.FieldCondition(key="tags.domain", match=qm.MatchValue(value=domain)))
    if lang:
        must.append(qm.FieldCondition(key="tags.lang", match=qm.MatchValue(value=lang)))

    filt = qm.Filter(must=must) if must else None

    try:
        count_result = client.count(collection_name=coll, count_filter=filt, exact=False)
        total = count_result.count
    except Exception:
        total = 0

    try:
        pts, next_offset = client.scroll(
            collection_name=coll,
            scroll_filter=filt,
            limit=limit,
            offset=offset_id if offset_id else None,
            with_payload=["_mid", "content", "project", "type", "source", "tags", "timestamp"],
            with_vectors=False,
        )
    except Exception:
        pts = []
        next_offset = None

    nodes = []
    for p in pts:
        pl = p.payload or {}
        nodes.append({
            "id": pl.get("_mid", ""),
            "label": _extract_label(pl.get("content", ""), pl.get("source", "")),
            "content": (pl.get("content", "") or "")[:500],
            "type": pl.get("type", ""),
            "project": pl.get("project", ""),
            "source": pl.get("source", ""),
            "tags": pl.get("tags", {}),
        })

    return {
        "nodes": nodes,
        "total": total,
        "hasMore": next_offset is not None,
        "nextOffset": str(next_offset) if next_offset else None,
    }


def search_nodes(query: str, project: str = "", type_: str = "",
                 domain: str = "", limit: int = 20) -> dict:
    """Semantic search wrapper."""
    tag_filters = {}
    if domain:
        tag_filters["domain"] = [domain]

    results = vs.search(
        query=query,
        limit=limit,
        project=project,
        type=type_,
        tag_filters=tag_filters if tag_filters else None,
    )

    return {"nodes": results}


def get_memory(node_id: str) -> dict:
    """Get full memory content by ID (no truncation)."""
    client, coll = vs._ensure_collection()
    try:
        points = client.retrieve(coll, ids=[node_id], with_payload=True, with_vectors=False)
        if not points:
            return {"error": "not found"}
        p = points[0]
        pay = p.payload or {}
        tags = pay.get("tags", {})
        return {
            "id": node_id,
            "content": pay.get("content", ""),
            "project": pay.get("project", ""),
            "type": pay.get("type", ""),
            "source": pay.get("source", ""),
            "label": _extract_label(pay.get("content", ""), pay.get("source", "")),
            "tags": tags,
            "timestamp": pay.get("timestamp"),
        }
    except Exception:
        return {"error": "not found"}


def get_neighbors(node_id: str, k: int = 10) -> dict:
    """On-demand KNN for a single node."""
    client, coll = vs._ensure_collection()
    point_uuid = vs._point_uuid(node_id)

    try:
        pts = client.retrieve(
            collection_name=coll,
            ids=[point_uuid],
            with_vectors=True,
            with_payload=["_mid"],
        )
    except Exception:
        return {"source": node_id, "neighbors": []}

    if not pts:
        return {"source": node_id, "neighbors": []}

    vec = pts[0].vector
    if isinstance(vec, dict):
        vec = vec.get(config.QDRANT_VECTOR_NAME)

    try:
        hits = client.query_points(
            collection_name=coll,
            query=vec,
            using=config.QDRANT_VECTOR_NAME,
            limit=k + 1,
            with_payload=["_mid", "content", "project", "type", "source", "tags"],
        ).points
    except Exception:
        return {"source": node_id, "neighbors": []}

    neighbors = []
    for h in hits:
        pl = h.payload or {}
        mid = pl.get("_mid", "")
        if mid == node_id:
            continue
        neighbors.append({
            "id": mid,
            "label": _extract_label(pl.get("content", ""), pl.get("source", "")),
            "similarity": round(max(0.0, float(h.score)), 3),
            "project": pl.get("project", ""),
            "type": pl.get("type", ""),
            "source": pl.get("source", ""),
            "content": (pl.get("content", "") or "")[:300],
            "tags": pl.get("tags", {}),
        })

    return {"source": node_id, "neighbors": neighbors[:k]}


# ── Statistics ──────────────────────────────────────────────────

def build_stats() -> dict:
    """Build statistics overview."""
    client, coll = vs._ensure_collection()
    try:
        info = client.get_collection(coll)
        total = info.points_count or 0
    except Exception:
        total = 0

    stats = {"total": total, "types": [], "langs": [], "domains": [], "layers": [], "timeline": []}

    try:
        for key, target in [("project", None), ("type", "types"), ("tags.lang", "langs"),
                            ("tags.domain", "domains"), ("tags.layer", "layers")]:
            facet_result = client.facet(coll, key, limit=20)
            items = [(h.value, h.count) for h in facet_result.hits]
            if target:
                stats[target] = items
            else:
                stats["projects"] = items
    except Exception:
        pass

    # Timeline histogram — scroll recent 1000 by timestamp, bucket by month
    try:
        points, _ = client.scroll(coll, limit=1000, with_payload=["timestamp"],
                                  order_by=qm.OrderBy(key="timestamp", direction="desc"))
        import datetime
        buckets: dict[str, int] = {}
        for p in points:
            ts = (p.payload or {}).get("timestamp")
            if ts:
                dt = datetime.datetime.fromtimestamp(ts)
                key = dt.strftime("%Y-%m")
                buckets[key] = buckets.get(key, 0) + 1
        stats["timeline"] = sorted(buckets.items())[-12:]  # last 12 months
    except Exception:
        pass

    return stats


# ── Cross-project similarity ──────────────────────────────────────

_cross_project_cache: dict | None = None
_cross_project_version = -1


def build_cross_project_similarity(sample_per_project: int = 5, k: int = 5) -> dict:
    """Sample vectors from each project, find cross-project neighbors."""
    global _cross_project_cache, _cross_project_version
    if _cross_project_cache and _cross_project_version == _data_version:
        return _cross_project_cache

    client, coll = vs._ensure_collection()

    # Get projects via facet
    try:
        facet_result = client.facet(coll, "project", limit=50)
        projects = [(h.value, h.count) for h in facet_result.hits]
    except Exception:
        return {"edges": [], "pairs": {}}

    if len(projects) < 2:
        return {"edges": [], "pairs": {}}

    # Sample vectors from each project
    project_vectors: dict[str, list] = {}
    for proj_name, _ in projects:
        try:
            pts, _ = client.scroll(
                coll,
                scroll_filter=qm.Filter(must=[
                    qm.FieldCondition(key="project", match=qm.MatchValue(value=proj_name))
                ]),
                limit=sample_per_project,
                with_vectors=True,
                with_payload=True,
            )
            project_vectors[proj_name] = pts
        except Exception:
            continue

    # For each sample, find KNN and count cross-project hits
    edge_counts: dict[tuple, int] = {}
    edge_pairs: dict[str, list] = {}

    for proj_name, pts in project_vectors.items():
        for pt in pts:
            vec = pt.vector
            if isinstance(vec, dict):
                vec = vec.get(config.QDRANT_VECTOR_NAME, list(vec.values())[0] if vec else None)
            if not vec:
                continue
            try:
                results = client.query_points(
                    coll, query=vec, using=config.QDRANT_VECTOR_NAME,
                    limit=k, with_payload=["project", "_mid"],
                )
                for r in results.points:
                    other_proj = (r.payload or {}).get("project", "")
                    if other_proj and other_proj != proj_name:
                        key = tuple(sorted([proj_name, other_proj]))
                        edge_counts[key] = edge_counts.get(key, 0) + 1
                        pair_key = f"{key[0]}||{key[1]}"
                        if pair_key not in edge_pairs:
                            edge_pairs[pair_key] = []
                        if len(edge_pairs[pair_key]) < 3:
                            edge_pairs[pair_key].append({
                                "from_project": proj_name,
                                "to_project": other_proj,
                                "similarity": round(r.score, 3) if hasattr(r, 'score') else 0,
                            })
            except Exception:
                continue

    # Build edge list sorted by weight
    edges = sorted([
        {"source": k[0], "target": k[1], "weight": v}
        for k, v in edge_counts.items()
    ], key=lambda e: e["weight"], reverse=True)[:20]

    result = {"edges": edges, "pairs": edge_pairs}
    _cross_project_cache = result
    _cross_project_version = _data_version
    return result


# ── Timeline ──────────────────────────────────────────────────────

def build_timeline(project: str = "", limit: int = 500) -> dict:
    """Build timeline data — memories bucketed by time period."""
    client, coll = vs._ensure_collection()
    filters = []
    if project:
        filters.append(qm.FieldCondition(key="project", match=qm.MatchValue(value=project)))

    scroll_filter = qm.Filter(must=filters) if filters else None
    points, _next = client.scroll(
        coll,
        scroll_filter=scroll_filter,
        limit=limit,
        with_payload=True,
        order_by=qm.OrderBy(key="timestamp", direction="desc"),
    )

    items = []
    for p in points:
        pay = p.payload or {}
        tags = pay.get("tags", {})
        ts = pay.get("timestamp") or pay.get("source_mtime") or 0
        items.append({
            "id": pay.get("_mid", str(p.id)),
            "timestamp": ts,
            "project": pay.get("project", ""),
            "type": pay.get("type", ""),
            "source": pay.get("source", ""),
            "label": _extract_label(pay.get("content", ""), pay.get("source", "")),
            "content": (pay.get("content") or "")[:500],
            "tags": tags,
        })

    # Bucket by month
    buckets: dict[str, list] = {}
    for item in items:
        if item["timestamp"]:
            import datetime
            dt = datetime.datetime.fromtimestamp(item["timestamp"])
            key = dt.strftime("%Y-%m")
        else:
            key = "unknown"
        buckets.setdefault(key, []).append(item)

    return {"items": items, "buckets": {k: len(v) for k, v in buckets.items()}, "total": len(items)}


# ── Knowledge graph ──────────────────────────────────────────────

PREDICATE_COLORS = {
    "uses": "#60a5fa", "depends_on": "#60a5fa", "imports": "#60a5fa",
    "has": "#4ecdc4", "contains": "#4ecdc4", "defines": "#4ecdc4",
    "is": "#a78bfa", "is_a": "#a78bfa", "type_of": "#a78bfa",
    "created_by": "#f472b6", "authored_by": "#f472b6",
    "relates_to": "#fbbf24", "connected_to": "#fbbf24",
}

def _predicate_color(pred: str) -> str:
    p = pred.lower().replace(" ", "_")
    for k, v in PREDICATE_COLORS.items():
        if k in p:
            return v
    return "#8892b0"


def build_kg_data(subject: str = "", limit: int = 200) -> dict:
    """Build knowledge graph visualization data from facts."""
    facts = kg.query(subject=subject, active_only=False, limit=limit)
    if not facts:
        return {"nodes": [], "edges": [], "total": 0}

    entities: dict[str, dict] = {}
    edges = []

    for f in facts:
        # Add subject entity
        if f["subject"] not in entities:
            entities[f["subject"]] = {"id": f["subject"], "label": f["subject"], "connections": 0}
        entities[f["subject"]]["connections"] += 1

        # Add object entity
        if f["object"] not in entities:
            entities[f["object"]] = {"id": f["object"], "label": f["object"], "connections": 0}
        entities[f["object"]]["connections"] += 1

        edges.append({
            "id": f"kg_e_{f['id']}",
            "source": f["subject"],
            "target": f["object"],
            "label": f["predicate"],
            "color": _predicate_color(f["predicate"]),
            "ended": f["ended"] is not None,
            "timestamp": f["valid_from"],
            "source_ref": f["source"],
        })

    nodes = list(entities.values())
    return {"nodes": nodes, "edges": edges, "total": len(facts)}


# ── Change detection + caching ──────────────────────────────────

_last_wal_size = 0
_last_row_count = -1
_data_version = 0
_overview_cache = None
_overview_cache_version = -1
_project_cache: dict[str, tuple[int, dict]] = {}  # name -> (version, data)
_PROJECT_CACHE_MAX = 30


def check_for_changes():
    global _last_wal_size, _last_row_count, _data_version
    changed = False
    wp = config.wal_path()
    try:
        size = os.path.getsize(wp) if wp.exists() else 0
    except OSError:
        size = 0
    if size != _last_wal_size:
        _last_wal_size = size
        changed = True
    try:
        client, coll = vs._ensure_collection()
        info = client.get_collection(coll)
        count = info.points_count or 0
        if count != _last_row_count:
            _last_row_count = count
            changed = True
    except Exception:
        pass
    if changed:
        _data_version += 1
    return changed


def _get_overview(filters: dict | None = None) -> dict:
    global _overview_cache, _overview_cache_version
    # Filtered requests bypass cache
    if filters:
        return build_overview(filters)
    if _overview_cache and _overview_cache_version == _data_version:
        return _overview_cache
    _overview_cache = build_overview()
    _overview_cache_version = _data_version
    return _overview_cache


def _get_project_detail(name: str, filters: dict | None = None) -> dict:
    if filters:
        return build_project_detail(name, filters)
    cached = _project_cache.get(name)
    if cached and cached[0] == _data_version:
        return cached[1]
    data = build_project_detail(name)
    if len(_project_cache) >= _PROJECT_CACHE_MAX:
        oldest = next(iter(_project_cache))
        del _project_cache[oldest]
    _project_cache[name] = (_data_version, data)
    return data


# ── HTML page ──────────────────────────────────────────────────

HTML_PAGE = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Imprint Graph</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
:root {
  --bg-primary: #1a1a2e; --bg-secondary: #16213e; --bg-tertiary: #2a2a4a;
  --border: #2a2a4a; --border-focus: #3a3a5a; --separator: #4a4a6a;
  --text-muted: #5a5a7a; --text-secondary: #8892b0; --text-primary: #dcddde; --text-content: #b0b0c0;
  --accent: #4ecdc4; --accent-bg: #4ecdc422; --accent-semi: #4ecdc444;
  --shadow: #00000066;
}
[data-theme="light"] {
  --bg-primary: #f5f5f7; --bg-secondary: #ffffff; --bg-tertiary: #e8e8ed;
  --border: #d1d1d6; --border-focus: #b0b0b8; --separator: #c0c0c8;
  --text-muted: #8e8e93; --text-secondary: #636366; --text-primary: #1c1c1e; --text-content: #3a3a3c;
  --accent: #0a9b8e; --accent-bg: #0a9b8e18; --accent-semi: #0a9b8e30;
  --shadow: #00000022;
}
html, body { width: 100%; height: 100%; overflow: hidden; }
body { background: var(--bg-primary); color: var(--text-primary); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; display: flex; }

/* Filter sidebar */
#sidebar {
  width: 250px; min-width: 250px; height: 100vh;
  background: var(--bg-secondary); border-right: 1px solid var(--border);
  overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 12px;
  z-index: 10;
}
#sidebar h2 { font-size: 14px; color: var(--accent); margin-bottom: 4px; text-transform: uppercase; letter-spacing: 1px; }
#workspace-select {
  width: 100%; padding: 5px 8px; font-size: 12px; border-radius: 6px;
  background: var(--bg-tertiary); color: var(--text-primary); border: 1px solid var(--border);
  cursor: pointer; outline: none; margin-bottom: 4px;
}
#workspace-select:focus { border-color: var(--accent); }
.view-btn {
  flex: 1; padding: 5px 4px; font-size: 11px; font-weight: 600; border-radius: 6px;
  background: var(--bg-tertiary); color: var(--text-secondary); border: 1px solid var(--border);
  cursor: pointer; transition: all 0.15s; text-align: center;
}
.view-btn:hover { border-color: var(--accent); color: var(--text-primary); }
.view-btn.active { background: var(--accent-bg); border-color: var(--accent); color: var(--accent); }
.filter-section { margin-bottom: 8px; }
.filter-section h3 { font-size: 12px; color: var(--text-secondary); margin-bottom: 6px; cursor: pointer; user-select: none; }
.filter-section h3:hover { color: var(--text-primary); }
.filter-items { display: flex; flex-wrap: wrap; gap: 4px; }
.filter-chip {
  font-size: 11px; padding: 3px 8px; border-radius: 12px;
  background: var(--bg-tertiary); color: var(--text-secondary); cursor: pointer;
  border: 1px solid transparent; transition: all 0.15s; white-space: nowrap;
  user-select: none;
}
.filter-chip:hover { border-color: var(--accent); color: var(--text-primary); }
.filter-chip.active { background: var(--accent-bg); border-color: var(--accent); color: var(--accent); }
.filter-chip .count { font-size: 10px; opacity: 0.6; margin-left: 3px; }

/* Main area */
#main { flex: 1; display: flex; flex-direction: column; position: relative; min-width: 0; }

/* Breadcrumb + search bar */
#topbar {
  display: flex; align-items: center; gap: 12px; position: relative;
  padding: 10px 16px; background: var(--bg-secondary); border-bottom: 1px solid var(--border);
  z-index: 10;
}
#breadcrumb { display: flex; align-items: center; gap: 4px; font-size: 13px; flex-shrink: 0; }
.crumb { color: var(--text-secondary); cursor: pointer; padding: 2px 6px; border-radius: 4px; }
.crumb:hover { color: var(--accent); background: var(--bg-tertiary); }
.crumb.current { color: var(--text-primary); cursor: default; }
.crumb.current:hover { background: transparent; }
.crumb-sep { color: var(--separator); font-size: 11px; }
#search-box {
  flex: 1; max-width: 400px; margin-left: auto;
  background: var(--bg-tertiary); border: 1px solid var(--border-focus); border-radius: 6px;
  padding: 6px 12px; color: var(--text-primary); font-size: 13px; outline: none;
}
#search-box:focus { border-color: var(--accent); }
#search-box::placeholder { color: var(--text-muted); }
#search-history {
  position: absolute; top: 100%; right: 0; width: 400px; max-height: 200px;
  background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 6px;
  overflow-y: auto; z-index: 30; display: none; margin-top: 2px;
  box-shadow: 0 4px 12px var(--shadow);
}
#search-history.visible { display: block; }
.search-history-item {
  padding: 6px 12px; font-size: 12px; color: var(--text-secondary); cursor: pointer;
  border-bottom: 1px solid var(--border);
}
.search-history-item:last-child { border-bottom: none; }
.search-history-item:hover { background: var(--bg-tertiary); color: var(--text-primary); }
.search-history-item .sh-time { font-size: 10px; color: var(--text-muted); float: right; }
#stats { font-size: 11px; color: var(--text-muted); margin-left: 8px; white-space: nowrap; }

/* Graph container */
#graph-container { flex: 1; position: relative; overflow: hidden; }

/* Loading overlay */
#loading {
  position: absolute; top: 0; left: 0; right: 0; bottom: 0;
  display: flex; align-items: center; justify-content: center;
  background: color-mix(in srgb, var(--bg-primary) 80%, transparent); z-index: 50; font-size: 14px; color: var(--text-secondary);
}
#loading.hidden { display: none; }
.spinner { width: 24px; height: 24px; border: 2px solid var(--accent-semi); border-top-color: var(--accent); border-radius: 50%; animation: spin 0.8s linear infinite; margin-right: 10px; }
@keyframes spin { to { transform: rotate(360deg); } }

/* Detail panel */
#detail {
  position: absolute; top: 0; right: -400px; width: 400px; height: 100%;
  background: var(--bg-secondary); border-left: 1px solid var(--border);
  overflow-y: auto; padding: 16px; z-index: 20;
  transition: right 0.25s ease;
}
#detail.open { right: 0; }
#detail-close { position: absolute; top: 12px; right: 12px; cursor: pointer; color: var(--text-secondary); font-size: 18px; background: none; border: none; }
#detail-close:hover { color: var(--text-primary); }
#detail h3 { font-size: 15px; color: var(--accent); margin-bottom: 12px; padding-right: 30px; }
.detail-field { margin-bottom: 10px; }
.detail-field label { font-size: 11px; color: var(--text-muted); text-transform: uppercase; display: block; margin-bottom: 2px; }
.detail-field .value { font-size: 13px; color: var(--text-primary); word-break: break-word; }
.detail-content { background: var(--bg-primary); padding: 10px; border-radius: 6px; font-size: 12px; line-height: 1.5; white-space: pre-wrap; max-height: 300px; overflow-y: auto; color: var(--text-content); }
.detail-content pre[class*="language-"] { background: transparent !important; margin: 0 !important; padding: 0 !important; }
.detail-content code[class*="language-"] { font-size: 12px !important; font-family: 'SF Mono', 'Fira Code', monospace; }
.detail-tags { display: flex; flex-wrap: wrap; gap: 4px; }
.detail-tag { font-size: 10px; padding: 2px 6px; background: var(--bg-tertiary); border-radius: 8px; color: var(--text-secondary); }
.neighbor-item { padding: 6px 8px; margin: 4px 0; background: var(--bg-primary); border-radius: 4px; cursor: pointer; font-size: 12px; border-left: 3px solid transparent; }
.neighbor-item:hover { background: var(--bg-tertiary); border-left-color: var(--accent); }
.neighbor-sim { font-size: 10px; color: var(--accent); float: right; }

/* Reset button */
#reset-btn {
  position: absolute; top: 12px; left: 12px; z-index: 15;
  background: var(--accent); color: var(--bg-primary); border: none; border-radius: 6px;
  padding: 6px 14px; font-size: 12px; font-weight: 600; cursor: pointer;
  display: none; transition: opacity 0.2s;
}
#reset-btn:hover { opacity: 0.85; }

/* Empty state */
#empty-state {
  display: none; position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);
  text-align: center; color: var(--text-muted); z-index: 5;
}
#empty-state h2 { font-size: 20px; margin-bottom: 8px; color: var(--text-secondary); }
#empty-state p { font-size: 14px; }

/* Minimap */
.g6-minimap { position: absolute !important; bottom: 12px; right: 12px; z-index: 15; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; background: color-mix(in srgb, var(--bg-secondary) 80%, transparent); }
.g6-minimap canvas { border-radius: 6px; }

/* Stats bars */
.stat-row { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; font-size: 11px; }
.stat-label { min-width: 70px; color: var(--text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.stat-bar { flex: 1; height: 12px; background: var(--bg-tertiary); border-radius: 3px; overflow: hidden; }
.stat-bar-fill { height: 100%; border-radius: 3px; transition: width 0.3s; }
.stat-count { min-width: 35px; text-align: right; color: var(--text-muted); font-size: 10px; }
.stat-subtitle { font-size: 11px; color: var(--text-muted); margin: 8px 0 4px; text-transform: uppercase; letter-spacing: 0.5px; }

/* Layout picker */
.layout-btn {
  padding: 3px 8px; font-size: 10px; border-radius: 4px;
  background: var(--bg-tertiary); color: var(--text-muted); border: 1px solid var(--border);
  cursor: pointer; transition: all 0.15s;
}
.layout-btn:hover { border-color: var(--accent); color: var(--text-secondary); }
.layout-btn.active { background: var(--accent-bg); border-color: var(--accent); color: var(--accent); }

/* Theme toggle */
#theme-toggle {
  background: var(--bg-tertiary); border: 1px solid var(--border); border-radius: 6px;
  color: var(--text-secondary); cursor: pointer; padding: 4px 10px; font-size: 14px;
  line-height: 1; transition: all 0.15s;
}
#theme-toggle:hover { border-color: var(--accent); color: var(--text-primary); }

/* Tooltip */
.g6-tooltip {
  background: color-mix(in srgb, var(--bg-secondary) 93%, transparent) !important; border: 1px solid var(--accent) !important;
  border-radius: 6px !important; padding: 8px 12px !important;
  color: var(--text-primary) !important; font-size: 12px !important;
  max-width: 300px !important; box-shadow: 0 4px 12px var(--shadow) !important;
}

/* Sidebar toggle (mobile) */
#sidebar-toggle {
  display: none; position: absolute; top: 10px; left: 10px; z-index: 25;
  background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 6px;
  color: var(--text-primary); cursor: pointer; padding: 6px 10px; font-size: 16px;
}
#sidebar-toggle:hover { border-color: var(--accent); }

/* Responsive */
@media (max-width: 768px) {
  #sidebar {
    position: absolute; left: -260px; z-index: 30; height: 100vh;
    transition: left 0.25s ease; box-shadow: 4px 0 12px var(--shadow);
  }
  #sidebar.open { left: 0; }
  #sidebar-toggle { display: block; }
  #detail { width: 100%; right: -100%; }
  #detail.open { right: 0; }
  #topbar { flex-wrap: wrap; }
  #search-box { max-width: 100%; order: 10; flex-basis: 100%; margin: 6px 0 0; }
  #layout-picker { display: none; }
}
</style>
</head>
<body>
<button id="sidebar-toggle">&#9776;</button>
<div id="sidebar">
  <h2>Imprint Graph</h2>
  <select id="workspace-select" title="Switch workspace"></select>
  <div style="display:flex;gap:6px;margin-bottom:4px">
    <button class="view-btn active" id="btn-memories" title="Memory graph">Memories</button>
    <button class="view-btn" id="btn-kg" title="Knowledge graph">KG</button>
    <button class="view-btn" id="btn-timeline" title="Timeline view">Timeline</button>
  </div>
  <div id="filter-projects" class="filter-section"><h3>Projects</h3><div class="filter-items" id="fp-items"></div></div>
  <div id="filter-types" class="filter-section"><h3>Types</h3><div class="filter-items" id="ft-items"></div></div>
  <div id="filter-langs" class="filter-section"><h3>Languages</h3><div class="filter-items" id="fl-items"></div></div>
  <div id="filter-domains" class="filter-section"><h3>Domains</h3><div class="filter-items" id="fd-items"></div></div>
  <div id="stats-section" style="display:none;border-top:1px solid var(--border);padding-top:8px">
    <h3 style="font-size:12px;color:var(--text-secondary);margin-bottom:8px;cursor:pointer" id="stats-toggle">Statistics</h3>
    <div id="stats-body"></div>
  </div>
</div>

<div id="main">
  <div id="topbar">
    <div id="breadcrumb"><span class="crumb current" data-level="overview">Overview</span></div>
    <input id="search-box" type="text" placeholder="Search memories... (press /)" autocomplete="off" />
    <div id="search-history"></div>
    <span id="stats"></span>
    <span id="layout-picker" style="display:flex;gap:2px">
      <button class="layout-btn active" data-layout="force" title="Force layout">Force</button>
      <button class="layout-btn" data-layout="radial" title="Radial layout">Radial</button>
      <button class="layout-btn" data-layout="grid" title="Grid layout">Grid</button>
    </span>
    <button id="theme-toggle" title="Toggle theme">&#9790;</button>
  </div>
  <div id="graph-container">
    <button id="reset-btn">&larr; Back to Overview</button>
    <div id="loading"><div class="spinner"></div>Loading graph...</div>
    <div id="empty-state"><h2>No memories yet</h2><p>Ingest some files to get started:<br><code>imprint ingest &lt;dir&gt;</code></p></div>
    <div id="detail">
      <button id="detail-close">&times;</button>
      <div id="detail-body"></div>
    </div>
  </div>
</div>

<script src="https://unpkg.com/@antv/g6@5/dist/g6.min.js"></script>
<link rel="stylesheet" href="https://unpkg.com/prismjs@1/themes/prism-tomorrow.min.css" />
<script src="https://unpkg.com/prismjs@1/prism.min.js"></script>
<script src="https://unpkg.com/prismjs@1/plugins/autoloader/prism-autoloader.min.js"></script>
<script>
(function() {
'use strict';

// ── State ──
let DATA = null;         // current overview data
let graph = null;
let currentLevel = 'overview';   // overview | project | type
let currentProject = null;
let currentType = null;
let activeFilters = {};  // {type: [...], lang: [...], domain: [...]}
let searchTimeout = null;
let detailOpen = false;
let neighborEdges = [];
let detailStack = []; // navigation stack for detail panel

const TYPE_COLORS = {
  decision: '#ff6b6b', pattern: '#4ecdc4', bug: '#ff8c42',
  preference: '#a78bfa', architecture: '#60a5fa', milestone: '#34d399',
  finding: '#fbbf24', conversation: '#f472b6',
};

// ── Helpers ──
function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
function show(el) { el.classList.remove('hidden'); el.style.display = ''; }
function hide(el) { el.classList.add('hidden'); el.style.display = 'none'; }

function filterParams() {
  const p = new URLSearchParams();
  for (const [k, vals] of Object.entries(activeFilters)) {
    if (vals && vals.length) vals.forEach(v => p.append(k, v));
  }
  return p.toString();
}

async function api(path) {
  const sep = path.includes('?') ? '&' : '?';
  const fp = filterParams();
  const url = fp ? path + sep + fp : path;
  const r = await fetch(url);
  return r.json();
}

// ── Graph init ──
function initGraph() {
  const container = document.getElementById('graph-container');
  const w = container.clientWidth;
  const h = container.clientHeight;

  graph = new G6.Graph({
    container: 'graph-container',
    width: w,
    height: h,
    autoFit: 'view',
    padding: [60, 60, 60, 60],
    animation: true,
    behaviors: [
      'drag-canvas',
      'zoom-canvas',
      'drag-element',
    ],
    node: {
      style: {
        labelText: d => d.data?.label || d.id,
        labelFill: d => cssVar('--text-primary'),
        labelFontSize: d => {
          if (d.data?.level === 'leaf') return 9;
          const sz = d.style?.size || 40;
          return Math.max(9, Math.min(14, sz / 6));
        },
        labelPlacement: 'center',
        labelMaxWidth: d => (d.style?.size || 40) * 1.4,
        labelWordWrap: true,
        labelMaxLines: 3,
        labelTextOverflow: 'ellipsis',
        cursor: 'pointer',
        lineWidth: 2,
        stroke: d => d.data?.borderColor || cssVar('--border'),
      },
    },
    edge: {
      style: {
        stroke: d => cssVar('--accent'),
        lineWidth: 1,
        lineDash: [4, 4],
        endArrow: false,
      },
    },
    combo: {
      type: 'circle',
      style: {
        fillOpacity: 0.06,
        stroke: d => d.data?.color || '#4ecdc444',
        lineWidth: 1,
        labelText: d => d.data?.label || '',
        labelFill: d => cssVar('--text-secondary'),
        labelFontSize: 13,
        labelPlacement: 'top',
        cursor: 'pointer',
        collapsedSize: d => d.data?.collapsedSize || 40,
      },
    },
    plugins: [{
      type: 'minimap',
      key: 'minimap',
      size: [160, 110],
      className: 'g6-minimap',
    }, {
      type: 'tooltip',
      getContent: (e, items) => {
        if (!items || !items.length) return '';
        const d = items[0];
        if (!d || !d.data) return '';
        const dd = d.data;
        if (dd.level === 'project') {
          const types = dd.types || {};
          const typesStr = Object.entries(types).map(([t, c]) => `${t}: ${c}`).join(', ');
          return `<div class="g6-tooltip"><b>${dd.name}</b><br>${dd.count} memories<br>${typesStr || ''}</div>`;
        }
        if (dd.level === 'type-cluster') {
          return `<div class="g6-tooltip"><b>${dd.typeName}</b><br>${dd.count} memories in ${dd.project}</div>`;
        }
        if (dd.level === 'leaf') {
          return `<div class="g6-tooltip"><b>${dd.source || dd.label}</b><br>Type: ${dd.memType || ''}<br>${(dd.content || '').substring(0, 150)}...</div>`;
        }
        return '';
      },
    }],
    background: getComputedStyle(document.documentElement).getPropertyValue('--bg-primary').trim(),
  });

  graph.on('node:click', onNodeClick);

  return graph;
}

// ── Overview rendering ──
async function loadOverview() {
  showLoading();
  currentLevel = 'overview';
  currentProject = null;
  currentType = null;
  updateBreadcrumb();

  DATA = await api('/api/overview');

  if (!DATA.projects || DATA.projects.length === 0) {
    hideLoading();
    document.getElementById('empty-state').style.display = 'block';
    if (graph) graph.clear();
    return;
  }
  document.getElementById('empty-state').style.display = 'none';

  buildFilterSidebar(DATA.facets, DATA.projects);
  updateStats(DATA.total, DATA.projects.length);

  const nodes = DATA.projects.map(p => ({
    id: p.id,
    data: {
      level: 'project',
      name: p.name,
      count: p.count,
      types: p.types,
      color: p.color,
      topDomains: p.topDomains,
      topLangs: p.topLangs,
      label: `${p.name}\n(${fmtNum(p.count)})`,
      borderColor: p.color,
    },
    style: {
      size: clampSize(p.count, 40, 120),
      fill: p.color + '33',
      stroke: p.color,
      lineWidth: 2,
      labelText: `${p.name}\n(${fmtNum(p.count)})`,
    },
  }));

  // Fetch cross-project similarity edges (async, non-blocking render)
  const edges = [];
  graph.setData({ nodes, edges, combos: [] });
  graph.setLayout({
    type: 'force',
    preventOverlap: true,
    nodeSize: d => clampSize(d.data?.count || 1, 40, 120) + 80,
    nodeSpacing: 80,
    linkDistance: 350,
    nodeStrength: -3000,
    animated: true,
    maxSpeed: 200,
  });
  await graph.render();
  hideLoading();
  pushState();

  // Load cross-project edges after initial render (don't block)
  if (DATA.projects.length > 1) {
    fetch('/api/cross-project').then(r => r.json()).then(cp => {
      if (cp.edges && cp.edges.length && currentLevel === 'overview') {
        const projIds = new Set(DATA.projects.map(p => p.id));
        const newEdges = [];
        for (const e of cp.edges) {
          const srcId = DATA.projects.find(p => p.name === e.source)?.id;
          const tgtId = DATA.projects.find(p => p.name === e.target)?.id;
          if (srcId && tgtId && projIds.has(srcId) && projIds.has(tgtId)) {
            const opacity = Math.min(0.6, 0.1 + e.weight * 0.05);
            const width = Math.min(3, 0.5 + e.weight * 0.3);
            newEdges.push({
              id: `cp_${e.source}_${e.target}`,
              source: srcId, target: tgtId,
              data: { level: 'cross-project', weight: e.weight },
              style: {
                stroke: cssVar('--accent'),
                lineWidth: width,
                opacity: opacity,
                lineDash: [6, 4],
              },
            });
          }
        }
        if (newEdges.length) {
          try { graph.addEdgeData(newEdges); graph.draw(); } catch (e) {}
        }
      }
    }).catch(() => {});
  }
}

// ── Project drill-in ──
async function drillIntoProject(projectName) {
  showLoading();
  currentLevel = 'project';
  currentProject = projectName;
  currentType = null;
  updateBreadcrumb();

  const detail = await api(`/api/project/${encodeURIComponent(projectName)}`);

  const nodes = [];
  const combos = [];
  const comboId = `combo_${projectName}`;

  // Project combo
  combos.push({
    id: comboId,
    data: { label: projectName, color: detail.color },
  });

  // Type cluster nodes only — no peripheral projects
  for (const t of detail.types) {
    if (!t.name) continue;
    const nodeId = `type_${projectName}_${t.name}`;
    const sz = clampSize(t.count, 40, 90);
    nodes.push({
      id: nodeId,
      combo: comboId,
      data: {
        level: 'type-cluster',
        typeName: t.name,
        project: projectName,
        count: t.count,
        color: TYPE_COLORS[t.name] || '#60a5fa',
        label: `${t.name}\n(${fmtNum(t.count)})`,
        borderColor: TYPE_COLORS[t.name] || '#60a5fa',
      },
      style: {
        size: sz,
        fill: (TYPE_COLORS[t.name] || '#60a5fa') + '33',
        stroke: TYPE_COLORS[t.name] || '#60a5fa',
        lineWidth: 2,
        labelText: `${t.name}\n(${fmtNum(t.count)})`,
      },
    });
  }

  graph.setData({ nodes, edges: [], combos });
  graph.setLayout({
    type: 'force',
    preventOverlap: true,
    nodeSize: d => clampSize(d.data?.count || 1, 40, 90) + 60,
    nodeSpacing: 60,
    linkDistance: 250,
    nodeStrength: -1500,
    animated: true,
  });
  await graph.render();
  hideLoading();
  pushState();
}

// ── Type drill-in (show leaf nodes) ──
async function drillIntoType(projectName, typeName) {
  showLoading();
  currentLevel = 'type';
  currentProject = projectName;
  currentType = typeName;
  updateBreadcrumb();

  const data = await api(`/api/nodes?project=${encodeURIComponent(projectName)}&type=${encodeURIComponent(typeName)}&limit=300`);

  const nodes = [];
  const edges = [];
  const typeColor = TYPE_COLORS[typeName] || '#60a5fa';

  // Central type hub node
  const hubId = `hub_${projectName}_${typeName}`;
  nodes.push({
    id: hubId,
    data: {
      level: 'hub',
      label: `${typeName} (${fmtNum(data.total)})`,
      color: typeColor,
    },
    style: {
      size: 50,
      fill: typeColor + '33',
      stroke: typeColor,
      lineWidth: 3,
      labelText: `${typeName}\n${fmtNum(data.total)} memories`,
      labelFontSize: 13,
    },
  });

  for (const n of data.nodes) {
    const nid = `leaf_${n.id}`;
    nodes.push({
      id: nid,
      data: {
        level: 'leaf',
        memId: n.id,
        memType: n.type,
        project: projectName,
        source: n.source,
        content: n.content,
        tags: n.tags,
        label: n.label,
        borderColor: typeColor + '88',
      },
      style: {
        size: 12,
        fill: typeColor + '44',
        stroke: typeColor + '66',
        lineWidth: 1,
        labelText: n.label,
        labelFontSize: 9,
        labelFill: cssVar('--text-secondary'),
      },
    });

    edges.push({
      id: `e_${hubId}_${nid}`,
      source: hubId,
      target: nid,
      style: { stroke: typeColor + '22', lineWidth: 0.5, lineDash: 0 },
    });
  }

  if (data.hasMore) {
    const remaining = data.total - data.nodes.length;
    const moreId = 'load_more';
    nodes.push({
      id: moreId,
      data: {
        level: 'load-more',
        label: `+ ${remaining} more...`,
        nextOffset: data.nextOffset,
        project: projectName,
        typeName: typeName,
        hubId: hubId,
        loaded: data.nodes.length,
        total: data.total,
      },
      style: {
        size: 30,
        fill: '#2a2a4a',
        stroke: '#4ecdc4',
        lineWidth: 1,
        labelText: `+${fmtNum(remaining)} more`,
        labelFontSize: 10,
        cursor: 'pointer',
      },
    });
    edges.push({
      id: `e_${hubId}_${moreId}`,
      source: hubId, target: moreId,
      style: { stroke: '#4ecdc422', lineWidth: 0.5, lineDash: [4, 4] },
    });
  }

  graph.setData({ nodes, edges, combos: [] });
  graph.setLayout({
    type: 'force',
    preventOverlap: true,
    nodeSize: d => (d.id === hubId ? 70 : 30),
    nodeSpacing: 40,
    linkDistance: d => d.source === hubId || d.target === hubId ? 180 : 100,
    nodeStrength: -600,
    animated: true,
    maxSpeed: 200,
  });
  await graph.render();
  hideLoading();
  pushState();
}

// ── Load more nodes (pagination) ──
async function loadMoreNodes(d) {
  const typeColor = TYPE_COLORS[d.typeName] || '#60a5fa';
  const url = `/api/nodes?project=${encodeURIComponent(d.project)}&type=${encodeURIComponent(d.typeName)}&limit=300&offset=${encodeURIComponent(d.nextOffset)}`;
  const data = await api(url);

  // Remove the load-more node and its edge
  try { graph.removeEdgeData([`e_${d.hubId}_load_more`]); } catch (e) {}
  try { graph.removeNodeData(['load_more']); } catch (e) {}

  // Add new leaf nodes
  const newNodes = [];
  const newEdges = [];
  for (const n of data.nodes) {
    const nid = `leaf_${n.id}`;
    newNodes.push({
      id: nid,
      data: {
        level: 'leaf',
        memId: n.id,
        memType: n.type,
        project: d.project,
        source: n.source,
        content: n.content,
        tags: n.tags,
        label: n.label,
        borderColor: typeColor + '88',
      },
      style: {
        size: 12,
        fill: typeColor + '44',
        stroke: typeColor + '66',
        lineWidth: 1,
        labelText: n.label,
        labelFontSize: 9,
        labelFill: cssVar('--text-secondary'),
      },
    });
    newEdges.push({
      id: `e_${d.hubId}_${nid}`,
      source: d.hubId,
      target: nid,
      style: { stroke: typeColor + '22', lineWidth: 0.5, lineDash: 0 },
    });
  }

  graph.addNodeData(newNodes);
  graph.addEdgeData(newEdges);

  // Add new load-more node if still more
  const loaded = d.loaded + data.nodes.length;
  if (data.hasMore) {
    const remaining = d.total - loaded;
    graph.addNodeData([{
      id: 'load_more',
      data: {
        level: 'load-more',
        label: `+ ${remaining} more...`,
        nextOffset: data.nextOffset,
        project: d.project,
        typeName: d.typeName,
        hubId: d.hubId,
        loaded: loaded,
        total: d.total,
      },
      style: {
        size: 30, fill: '#2a2a4a', stroke: '#4ecdc4', lineWidth: 1,
        labelText: `+${fmtNum(remaining)} more`, labelFontSize: 10, cursor: 'pointer',
      },
    }]);
    graph.addEdgeData([{
      id: `e_${d.hubId}_load_more`,
      source: d.hubId, target: 'load_more',
      style: { stroke: '#4ecdc422', lineWidth: 0.5, lineDash: [4, 4] },
    }]);
  }

  graph.draw();
}

// ── Node click handler ──
async function onNodeClick(e) {
  const nodeId = e.target?.id;
  if (!nodeId) return;
  const nodeData = graph.getNodeData(nodeId);
  if (!nodeData || !nodeData.data) return;
  const d = nodeData.data;

  if (d.level === 'project') {
    closeDetail();
    await drillIntoProject(d.name);
  } else if (d.level === 'type-cluster') {
    closeDetail();
    await drillIntoType(d.project, d.typeName);
  } else if (d.level === 'leaf') {
    await showNodeDetail(d);
  } else if (d.level === 'load-more') {
    await loadMoreNodes(d);
  } else if (d.level === 'kg-entity') {
    await loadKnowledgeGraph(d.label);
  }
}

// ── Detail panel ──
async function showNodeDetail(d, pushToStack) {
  if (pushToStack !== false && d.memId) detailStack.push(d);
  const body = document.getElementById('detail-body');
  const tags = d.tags || {};

  // Load full content if we have a memId
  let content = d.content || '';
  if (d.memId) {
    try {
      const full = await fetch(`/api/memory/${encodeURIComponent(d.memId)}`).then(r => r.json());
      if (full && !full.error) content = full.content || content;
    } catch (e) {}
  }

  const domains = (tags.domain || []).map(t => `<span class="detail-tag">${t}</span>`).join('');
  const topics = (tags.topics || []).map(t => `<span class="detail-tag">${t}</span>`).join('');
  const hasBack = detailStack.length > 1;
  const nodeInGraph = d.memId && (() => { try { return !!graph.getNodeData(`leaf_${d.memId}`); } catch { return false; } })();

  body.innerHTML = `
    <div style="display:flex;align-items:center;gap:6px;margin-bottom:8px">
      ${hasBack ? `<button id="detail-back" style="background:var(--bg-tertiary);border:1px solid var(--border);border-radius:4px;color:var(--text-secondary);cursor:pointer;padding:2px 8px;font-size:11px">&larr; Back</button>` : ''}
      ${nodeInGraph ? `<button id="detail-find" style="background:var(--bg-tertiary);border:1px solid var(--border);border-radius:4px;color:var(--text-secondary);cursor:pointer;padding:2px 8px;font-size:11px">Find in graph</button>` : ''}
    </div>
    <h3>${escHtml(d.label || 'Memory')}</h3>
    <div class="detail-field"><label>Source</label><div class="value">${escHtml(d.source || '—')}</div></div>
    <div class="detail-field"><label>Type</label><div class="value">${escHtml(d.memType || '—')}</div></div>
    <div class="detail-field"><label>Project</label><div class="value">${escHtml(d.project || '—')}</div></div>
    ${tags.lang ? `<div class="detail-field"><label>Language</label><div class="value">${escHtml(tags.lang)}</div></div>` : ''}
    ${tags.layer ? `<div class="detail-field"><label>Layer</label><div class="value">${escHtml(tags.layer)}</div></div>` : ''}
    ${domains ? `<div class="detail-field"><label>Domains</label><div class="detail-tags">${domains}</div></div>` : ''}
    ${topics ? `<div class="detail-field"><label>Topics</label><div class="detail-tags">${topics}</div></div>` : ''}
    <div class="detail-field"><label>Content</label><div class="detail-content">${tags.lang ? `<pre style="margin:0;background:transparent"><code class="language-${escHtml(tags.lang)}">${escHtml(content)}</code></pre>` : escHtml(content)}</div></div>
    <div class="detail-field"><label>Neighbors</label><div id="neighbors-list"><i style="color:var(--text-muted);font-size:12px">Loading...</i></div></div>
  `;

  // Wire back button
  const backBtn = document.getElementById('detail-back');
  if (backBtn) backBtn.addEventListener('click', () => {
    detailStack.pop(); // remove current
    const prev = detailStack.pop(); // get previous (will be re-pushed by showNodeDetail)
    if (prev) showNodeDetail(prev);
  });

  // Wire find-in-graph button
  const findBtn = document.getElementById('detail-find');
  if (findBtn && d.memId) findBtn.addEventListener('click', () => {
    try { graph.focusElement(`leaf_${d.memId}`); } catch (e) {}
  });

  document.getElementById('detail').classList.add('open');
  detailOpen = true;

  // Syntax highlight code content
  if (tags.lang && typeof Prism !== 'undefined') {
    const codeEl = body.querySelector('code[class*="language-"]');
    if (codeEl) Prism.highlightElement(codeEl);
  }

  // Load neighbors
  if (d.memId) {
    const nb = await api(`/api/neighbors?id=${encodeURIComponent(d.memId)}&k=8`);
    const list = document.getElementById('neighbors-list');
    if (nb.neighbors && nb.neighbors.length) {
      list.innerHTML = nb.neighbors.map(n => `
        <div class="neighbor-item" data-project="${escHtml(n.project)}" data-type="${escHtml(n.type)}" data-id="${escHtml(n.id)}">
          <span class="neighbor-sim">${(n.similarity * 100).toFixed(0)}%</span>
          <div style="font-size:12px;color:var(--text-primary)">${escHtml(n.label)}</div>
          <div style="font-size:10px;color:var(--text-muted)">${escHtml(n.project)} / ${escHtml(n.type)}</div>
        </div>
      `).join('');

      // Make neighbors clickable — navigate to their detail
      list.querySelectorAll('.neighbor-item').forEach(item => {
        item.addEventListener('click', async () => {
          const nId = item.dataset.id;
          try {
            const full = await fetch(`/api/memory/${encodeURIComponent(nId)}`).then(r => r.json());
            if (full && !full.error) {
              showNodeDetail({
                memId: nId,
                memType: full.type,
                project: full.project,
                source: full.source,
                content: full.content,
                tags: full.tags,
                label: full.label,
              });
            }
          } catch (e) {}
        });
      });

      // Draw neighbor edges on graph
      clearNeighborEdges();
      const sourceNodeId = `leaf_${d.memId}`;
      for (const n of nb.neighbors) {
        const targetId = `leaf_${n.id}`;
        try {
          const targetNode = graph.getNodeData(targetId);
          if (targetNode) {
            const edgeId = `nb_${d.memId}_${n.id}`;
            neighborEdges.push(edgeId);
            graph.addEdgeData([{
              id: edgeId,
              source: sourceNodeId,
              target: targetId,
              style: {
                stroke: cssVar('--accent'),
                lineWidth: Math.max(0.5, n.similarity * 2),
                opacity: Math.max(0.2, n.similarity * 0.8),
                lineDash: 0,
              },
            }]);
          }
        } catch (e) {}
      }
      if (neighborEdges.length) graph.draw();
    } else {
      list.innerHTML = '<i style="color:var(--text-muted);font-size:12px">No similar memories found</i>';
    }
  }
}

function clearNeighborEdges() {
  if (neighborEdges.length) {
    try { graph.removeEdgeData(neighborEdges); } catch (e) {}
    neighborEdges = [];
  }
}

function closeDetail() {
  document.getElementById('detail').classList.remove('open');
  detailOpen = false;
  detailStack = [];
  clearNeighborEdges();
}

// ── Search ──
async function doSearch(query) {
  if (!query || query.length < 2) {
    // Reload current view
    if (currentLevel === 'overview') await loadOverview();
    else if (currentLevel === 'project' && currentProject) await drillIntoProject(currentProject);
    return;
  }

  addSearchHistory(query);
  showLoading();
  const results = await api(`/api/search?q=${encodeURIComponent(query)}&limit=30`);
  hideLoading();

  if (!results.nodes || results.nodes.length === 0) return;

  // Show search results as a star layout
  currentLevel = 'search';
  updateBreadcrumb();

  const nodes = [];
  const edges = [];
  const hubId = 'search_hub';

  nodes.push({
    id: hubId,
    data: { level: 'hub', label: `"${query}"`, color: '#4ecdc4' },
    style: {
      size: 40, fill: '#4ecdc433', stroke: '#4ecdc4', lineWidth: 2,
      labelText: `"${query}"\n${results.nodes.length} results`,
      labelFontSize: 12,
    },
  });

  for (const n of results.nodes) {
    const nid = `search_${n.id}`;
    const typeColor = TYPE_COLORS[n.type] || '#60a5fa';
    nodes.push({
      id: nid,
      data: {
        level: 'leaf',
        memId: n.id,
        memType: n.type,
        project: n.project,
        source: n.source,
        content: n.content,
        tags: n.tags,
        label: n.label || _extract(n.content),
        borderColor: typeColor,
      },
      style: {
        size: 10 + (n.similarity || 0) * 15,
        fill: typeColor + '44',
        stroke: typeColor,
        lineWidth: 1,
        labelText: n.label || _extract(n.content),
        labelFontSize: 9,
        labelFill: cssVar('--text-secondary'),
      },
    });
    edges.push({
      id: `se_${hubId}_${nid}`,
      source: hubId, target: nid,
      style: {
        stroke: '#4ecdc4' + Math.round((n.similarity || 0.5) * 99).toString(16).padStart(2, '0'),
        lineWidth: Math.max(0.5, (n.similarity || 0.5) * 2),
        lineDash: 0,
      },
    });
  }

  graph.setData({ nodes, edges, combos: [] });
  graph.setLayout({
    type: 'force',
    preventOverlap: true,
    nodeSize: 40,
    nodeSpacing: 40,
    linkDistance: 200,
    nodeStrength: -800,
    animated: true,
  });
  await graph.render();
  pushState();
}

function _extract(content) {
  if (!content) return 'memory';
  const lines = content.split('\n');
  for (const l of lines) {
    const t = l.trim();
    if (t.length > 10) return t.substring(0, 80);
  }
  return content.substring(0, 80);
}

// ── Search history ──
function getSearchHistory() {
  try { return JSON.parse(localStorage.getItem('imprint_search_history') || '[]'); } catch { return []; }
}
function addSearchHistory(query) {
  if (!query || query.length < 2) return;
  let history = getSearchHistory().filter(h => h.q !== query);
  history.unshift({ q: query, t: Date.now() });
  if (history.length > 10) history = history.slice(0, 10);
  localStorage.setItem('imprint_search_history', JSON.stringify(history));
}
function showSearchHistory() {
  const el = document.getElementById('search-history');
  const history = getSearchHistory();
  if (!history.length) { el.classList.remove('visible'); return; }
  const now = Date.now();
  el.innerHTML = history.map(h => {
    const ago = Math.round((now - h.t) / 60000);
    const timeStr = ago < 60 ? `${ago}m ago` : ago < 1440 ? `${Math.round(ago / 60)}h ago` : `${Math.round(ago / 1440)}d ago`;
    return `<div class="search-history-item" data-query="${escHtml(h.q)}"><span class="sh-time">${timeStr}</span>${escHtml(h.q)}</div>`;
  }).join('');
  el.classList.add('visible');
  el.querySelectorAll('.search-history-item').forEach(item => {
    item.addEventListener('mousedown', (e) => {
      e.preventDefault();
      const q = item.dataset.query;
      document.getElementById('search-box').value = q;
      el.classList.remove('visible');
      doSearch(q);
    });
  });
}
function hideSearchHistory() { document.getElementById('search-history').classList.remove('visible'); }

// ── Knowledge graph view ──
async function loadKnowledgeGraph(subject) {
  showLoading();
  currentLevel = 'kg';
  updateBreadcrumb();

  const url = subject ? `/api/kg/entity/${encodeURIComponent(subject)}` : '/api/kg';
  const data = await fetch(url).then(r => r.json());
  hideLoading();

  if (!data.nodes || !data.nodes.length) {
    if (graph) graph.clear();
    document.getElementById('empty-state').style.display = 'block';
    document.getElementById('empty-state').querySelector('h2').textContent = 'No knowledge graph facts yet';
    document.getElementById('empty-state').querySelector('p').innerHTML = 'Add facts via <code>mcp__imprint__kg_add</code>';
    return;
  }
  document.getElementById('empty-state').style.display = 'none';
  updateStats(data.total, data.nodes.length + ' entities');

  const nodes = data.nodes.map(n => ({
    id: n.id,
    data: {
      level: 'kg-entity',
      label: n.label,
      connections: n.connections,
    },
    style: {
      size: Math.max(20, Math.min(60, 15 + n.connections * 5)),
      fill: cssVar('--accent') + '33',
      stroke: cssVar('--accent'),
      lineWidth: 2,
      labelText: n.label.length > 30 ? n.label.substring(0, 27) + '...' : n.label,
      labelFontSize: 11,
    },
  }));

  const edges = data.edges.map(e => ({
    id: e.id,
    source: e.source,
    target: e.target,
    data: { label: e.label, ended: e.ended },
    style: {
      stroke: e.ended ? (e.color + '44') : e.color,
      lineWidth: e.ended ? 0.5 : 1.5,
      lineDash: e.ended ? [4, 4] : 0,
      endArrow: true,
      endArrowSize: 6,
      labelText: e.label,
      labelFontSize: 9,
      labelFill: cssVar('--text-muted'),
      labelBackground: true,
      labelBackgroundFill: cssVar('--bg-primary'),
      labelBackgroundRadius: 3,
      labelBackgroundPadding: [2, 4, 2, 4],
    },
  }));

  graph.setData({ nodes, edges, combos: [] });
  graph.setLayout({
    type: 'force',
    preventOverlap: true,
    nodeSize: 40,
    nodeSpacing: 60,
    linkDistance: 200,
    nodeStrength: -1500,
    animated: true,
  });
  await graph.render();
  pushState();
}

// ── Timeline view ──
async function loadTimeline(project) {
  showLoading();
  currentLevel = 'timeline';
  updateBreadcrumb();

  const url = project ? `/api/timeline?project=${encodeURIComponent(project)}&limit=300` : '/api/timeline?limit=300';
  const data = await fetch(url).then(r => r.json());
  hideLoading();

  if (!data.items || !data.items.length) {
    if (graph) graph.clear();
    return;
  }
  updateStats(data.total, Object.keys(data.buckets).length + ' months');

  // Group items by month, position chronologically
  const months = Object.keys(data.buckets).sort();
  const projectSet = [...new Set(data.items.map(i => i.project))];
  const projIndex = {};
  projectSet.forEach((p, i) => projIndex[p] = i);

  const nodes = [];
  const edges = [];

  // Month header nodes
  months.forEach((m, i) => {
    nodes.push({
      id: `month_${m}`,
      data: { level: 'timeline-month', label: m },
      style: {
        x: 150 + i * 200,
        y: 30,
        size: 10,
        fill: cssVar('--accent') + '44',
        stroke: cssVar('--accent'),
        lineWidth: 1,
        labelText: m,
        labelFontSize: 11,
        labelFill: cssVar('--text-secondary'),
        labelPlacement: 'top',
      },
    });
  });

  // Memory nodes positioned by month (x) and project (y)
  const monthItems = {};
  data.items.forEach(item => {
    let mKey = 'unknown';
    if (item.timestamp) {
      const d = new Date(item.timestamp * 1000);
      mKey = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0');
    }
    if (!monthItems[mKey]) monthItems[mKey] = [];
    monthItems[mKey].push(item);
  });

  const typeColors = TYPE_COLORS;
  let nodeCount = 0;
  months.forEach((m, mi) => {
    const items = monthItems[m] || [];
    items.slice(0, 50).forEach((item, ii) => {
      const nid = `tl_${item.id}`;
      const tc = typeColors[item.type] || '#60a5fa';
      const yBase = 80 + (projIndex[item.project] || 0) * 60;
      const yOffset = (ii % 5) * 12;
      nodes.push({
        id: nid,
        data: {
          level: 'leaf',
          memId: item.id,
          memType: item.type,
          project: item.project,
          source: item.source,
          content: item.content,
          tags: item.tags,
          label: item.label,
          borderColor: tc,
        },
        style: {
          x: 150 + mi * 200 + (ii % 10) * 15 - 60,
          y: yBase + yOffset,
          size: 10,
          fill: tc + '44',
          stroke: tc + '88',
          lineWidth: 1,
          labelText: '',
        },
      });
      nodeCount++;
      if (nodeCount > 500) return;
    });
  });

  // Project legend nodes on the left
  projectSet.forEach((p, i) => {
    nodes.push({
      id: `proj_label_${i}`,
      data: { level: 'label' },
      style: {
        x: 50,
        y: 80 + i * 60,
        size: 8,
        fill: _project_color_js(p) + '44',
        stroke: _project_color_js(p),
        lineWidth: 1,
        labelText: p,
        labelFontSize: 10,
        labelFill: cssVar('--text-secondary'),
        labelPlacement: 'right',
      },
    });
  });

  graph.setData({ nodes, edges: [], combos: [] });
  // Use fixed positions, no force layout
  graph.setLayout({ type: 'preset' });
  await graph.render();
  pushState();
}

function _project_color_js(name) {
  const PROJECT_COLORS = ['#ff6b6b','#4ecdc4','#60a5fa','#a78bfa','#fbbf24','#f472b6','#34d399','#ff8c42','#818cf8','#fb923c','#2dd4bf','#e879f9','#facc15','#38bdf8','#a3e635'];
  let h = 0;
  for (let i = 0; i < name.length; i++) h = ((h << 5) - h + name.charCodeAt(i)) | 0;
  return PROJECT_COLORS[Math.abs(h) % PROJECT_COLORS.length];
}

// ── View switching ──
let currentView = 'memories';
function setView(view) {
  currentView = view;
  document.querySelectorAll('.view-btn').forEach(b => b.classList.remove('active'));
  document.getElementById(`btn-${view}`).classList.add('active');
  // Show/hide filter sidebar sections based on view
  const filterSections = document.querySelectorAll('.filter-section');
  filterSections.forEach(s => s.style.display = view === 'memories' ? '' : 'none');
}
document.getElementById('btn-memories').addEventListener('click', () => { setView('memories'); loadOverview(); });
document.getElementById('btn-kg').addEventListener('click', () => { setView('kg'); loadKnowledgeGraph(); });
document.getElementById('btn-timeline').addEventListener('click', () => { setView('timeline'); loadTimeline(); });

// ── Filter sidebar ──
function buildFilterSidebar(facets, projects) {
  // Projects
  renderChips('fp-items', projects.map(p => ({ key: p.name, label: p.name, count: p.count, filterKey: 'project' })));
  // Types
  renderChips('ft-items', (facets.types || []).map(([v, c]) => ({ key: v, label: v, count: c, filterKey: 'type' })));
  // Languages
  renderChips('fl-items', (facets.langs || []).map(([v, c]) => ({ key: v, label: v, count: c, filterKey: 'lang' })));
  // Domains
  renderChips('fd-items', (facets.domains || []).map(([v, c]) => ({ key: v, label: v, count: c, filterKey: 'domain' })));
}

function renderChips(containerId, items) {
  const el = document.getElementById(containerId);
  if (!el) return;
  el.innerHTML = items.map(item => {
    const active = (activeFilters[item.filterKey] || []).includes(item.key);
    return `<span class="filter-chip ${active ? 'active' : ''}" data-key="${escHtml(item.filterKey)}" data-value="${escHtml(item.key)}">${escHtml(item.label)}<span class="count">${fmtNum(item.count)}</span></span>`;
  }).join('');

  el.querySelectorAll('.filter-chip').forEach(chip => {
    chip.addEventListener('click', () => {
      const key = chip.dataset.key;
      const val = chip.dataset.value;
      if (!activeFilters[key]) activeFilters[key] = [];
      const idx = activeFilters[key].indexOf(val);
      if (idx >= 0) {
        activeFilters[key].splice(idx, 1);
        if (!activeFilters[key].length) delete activeFilters[key];
      } else {
        activeFilters[key].push(val);
      }
      chip.classList.toggle('active');
      loadOverview(); // Re-fetch with filters
    });
  });
}

// ── Breadcrumb ──
function updateResetBtn() {
  const btn = document.getElementById('reset-btn');
  if (currentLevel === 'overview') {
    btn.style.display = 'none';
  } else {
    btn.style.display = 'block';
    btn.textContent = currentLevel === 'type' ? '\u2190 Back to ' + currentProject : '\u2190 Back to Overview';
  }
}

function updateBreadcrumb() {
  const bc = document.getElementById('breadcrumb');
  let html = '';

  if (currentLevel === 'overview') {
    html = '<span class="crumb current">Overview</span>';
  } else if (currentLevel === 'project') {
    html = '<span class="crumb" data-action="overview">Overview</span><span class="crumb-sep">&rsaquo;</span>';
    html += `<span class="crumb current">${escHtml(currentProject)}</span>`;
  } else if (currentLevel === 'type') {
    html = '<span class="crumb" data-action="overview">Overview</span><span class="crumb-sep">&rsaquo;</span>';
    html += `<span class="crumb" data-action="project" data-name="${escHtml(currentProject)}">${escHtml(currentProject)}</span><span class="crumb-sep">&rsaquo;</span>`;
    html += `<span class="crumb current">${escHtml(currentType)}</span>`;
  } else if (currentLevel === 'search') {
    html = '<span class="crumb" data-action="overview">Overview</span><span class="crumb-sep">&rsaquo;</span>';
    html += '<span class="crumb current">Search Results</span>';
  } else if (currentLevel === 'kg') {
    html = '<span class="crumb current">Knowledge Graph</span>';
  } else if (currentLevel === 'timeline') {
    html = '<span class="crumb current">Timeline</span>';
  }

  bc.innerHTML = html;
  bc.querySelectorAll('.crumb[data-action]').forEach(c => {
    c.addEventListener('click', () => {
      closeDetail();
      if (c.dataset.action === 'overview') loadOverview();
      else if (c.dataset.action === 'project') drillIntoProject(c.dataset.name);
    });
  });
  updateResetBtn();
}

// ── UI helpers ──
function showLoading() { document.getElementById('loading').classList.remove('hidden'); document.getElementById('loading').style.display = 'flex'; }
function hideLoading() { document.getElementById('loading').classList.add('hidden'); document.getElementById('loading').style.display = 'none'; }
function updateStats(total, projects) { document.getElementById('stats').textContent = `${fmtNum(total)} memories \u00b7 ${projects} projects`; }

function fmtNum(n) {
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
  if (n >= 1000) return (n / 1000).toFixed(1) + 'k';
  return String(n);
}

function clampSize(count, min, max) {
  const s = Math.sqrt(count) * 3;
  return Math.max(min, Math.min(max, s));
}

function escHtml(s) {
  if (!s) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ── SSE live updates ──
function connectSSE() {
  const es = new EventSource('/api/stream');
  es.addEventListener('update', async (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (currentLevel === 'overview') {
        DATA = await api('/api/overview');
        if (DATA.projects && DATA.projects.length) {
          buildFilterSidebar(DATA.facets, DATA.projects);
          updateStats(DATA.total, DATA.projects.length);
          // Soft update: just resize nodes
          for (const p of DATA.projects) {
            try {
              graph.updateNodeData([{
                id: p.id,
                data: { count: p.count, label: `${p.name}\n(${fmtNum(p.count)})` },
                style: { size: clampSize(p.count, 40, 120), labelText: `${p.name}\n(${fmtNum(p.count)})` },
              }]);
            } catch (e) {}
          }
          graph.draw();
        }
      }
    } catch (err) {}
  });
  es.onerror = () => { es.close(); setTimeout(connectSSE, 3000); };
}

// ── Keyboard shortcuts ──
document.addEventListener('keydown', (e) => {
  if (e.key === '/' && document.activeElement !== document.getElementById('search-box')) {
    e.preventDefault();
    document.getElementById('search-box').focus();
  }
  if (e.key === 'Escape') {
    if (detailOpen) { closeDetail(); return; }
    const sb = document.getElementById('search-box');
    if (document.activeElement === sb) { sb.blur(); sb.value = ''; loadOverview(); return; }
    if (currentLevel === 'type' && currentProject) { drillIntoProject(currentProject); return; }
    if (currentLevel === 'project' || currentLevel === 'search') { loadOverview(); return; }
  }
});

// Search input
const searchBox = document.getElementById('search-box');
searchBox.addEventListener('input', (e) => {
  clearTimeout(searchTimeout);
  hideSearchHistory();
  searchTimeout = setTimeout(() => doSearch(e.target.value.trim()), 300);
});
searchBox.addEventListener('focus', () => { if (!searchBox.value.trim()) showSearchHistory(); });
searchBox.addEventListener('blur', () => { setTimeout(hideSearchHistory, 150); });

// Detail close button
document.getElementById('detail-close').addEventListener('click', closeDetail);

// Reset / back button
document.getElementById('reset-btn').addEventListener('click', () => {
  closeDetail();
  if (currentLevel === 'type' && currentProject) drillIntoProject(currentProject);
  else loadOverview();
});

// Sidebar toggle (mobile)
document.getElementById('sidebar-toggle').addEventListener('click', () => {
  document.getElementById('sidebar').classList.toggle('open');
});

// Resize handling
window.addEventListener('resize', () => {
  if (graph) {
    const c = document.getElementById('graph-container');
    graph.resize(c.clientWidth, c.clientHeight);
  }
});

// ── Layout picker ──
let currentLayoutType = 'force';
function getLayoutConfig(type, context) {
  if (type === 'radial') {
    return {
      type: 'radial',
      preventOverlap: true,
      nodeSize: 50,
      nodeSpacing: 30,
      unitRadius: context === 'overview' ? 200 : 150,
      animated: true,
    };
  }
  if (type === 'grid') {
    return {
      type: 'grid',
      preventOverlap: true,
      nodeSize: 50,
      sortBy: 'data.count',
      animated: true,
    };
  }
  // Default: force (return null to use view-specific force config)
  return null;
}
document.getElementById('layout-picker').addEventListener('click', async (e) => {
  const btn = e.target.closest('.layout-btn');
  if (!btn) return;
  currentLayoutType = btn.dataset.layout;
  document.querySelectorAll('.layout-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  // Re-layout without re-fetching data
  const cfg = getLayoutConfig(currentLayoutType, currentLevel);
  if (cfg) {
    graph.setLayout(cfg);
    await graph.layout();
  } else {
    // Re-render current view to get its native force config
    if (currentLevel === 'overview') await loadOverview();
    else if (currentLevel === 'project' && currentProject) await drillIntoProject(currentProject);
    else if (currentLevel === 'type' && currentProject && currentType) await drillIntoType(currentProject, currentType);
  }
});

// ── URL state ──
function pushState() {
  let hash = '#/';
  if (currentLevel === 'project' && currentProject) hash = `#/project/${encodeURIComponent(currentProject)}`;
  else if (currentLevel === 'type' && currentProject && currentType) hash = `#/project/${encodeURIComponent(currentProject)}/type/${encodeURIComponent(currentType)}`;
  else if (currentLevel === 'search') {
    const q = document.getElementById('search-box').value.trim();
    if (q) hash = `#/search/${encodeURIComponent(q)}`;
  }
  const fp = filterParams();
  if (fp) hash += (hash.includes('?') ? '&' : '?') + fp;
  if (location.hash !== hash) history.pushState(null, '', hash);
}
async function restoreFromHash() {
  const hash = decodeURIComponent(location.hash || '');
  if (!hash || hash === '#/') { await loadOverview(); return; }
  const m = hash.match(/^#\/project\/([^/]+)\/type\/([^?]+)/);
  if (m) { await loadOverview(); await drillIntoProject(decodeURIComponent(m[1])); await drillIntoType(decodeURIComponent(m[1]), decodeURIComponent(m[2])); return; }
  const mp = hash.match(/^#\/project\/([^/?]+)/);
  if (mp) { await loadOverview(); await drillIntoProject(decodeURIComponent(mp[1])); return; }
  const ms = hash.match(/^#\/search\/([^?]+)/);
  if (ms) { const q = decodeURIComponent(ms[1]); document.getElementById('search-box').value = q; await loadOverview(); await doSearch(q); return; }
  await loadOverview();
}
window.addEventListener('popstate', () => restoreFromHash());

// ── Theme toggle ──
function initTheme() {
  const saved = localStorage.getItem('imprint_theme');
  if (saved) document.documentElement.dataset.theme = saved;
  updateThemeIcon();
}
function toggleTheme() {
  const isDark = document.documentElement.dataset.theme !== 'light';
  document.documentElement.dataset.theme = isDark ? 'light' : '';
  localStorage.setItem('imprint_theme', isDark ? 'light' : 'dark');
  updateThemeIcon();
  // Update G6 graph background
  if (graph) {
    const canvas = document.querySelector('#graph-container canvas');
    if (canvas) canvas.style.background = getComputedStyle(document.documentElement).getPropertyValue('--bg-primary').trim();
  }
}
function updateThemeIcon() {
  const btn = document.getElementById('theme-toggle');
  if (btn) btn.innerHTML = document.documentElement.dataset.theme === 'light' ? '&#9728;' : '&#9790;';
}
document.getElementById('theme-toggle').addEventListener('click', toggleTheme);

// ── Statistics panel ──
async function loadStats() {
  const section = document.getElementById('stats-section');
  section.style.display = 'block';
  const body = document.getElementById('stats-body');
  body.innerHTML = '<i style="color:var(--text-muted);font-size:11px">Loading...</i>';

  const data = await fetch('/api/stats').then(r => r.json());
  function bars(items, color) {
    if (!items || !items.length) return '<i style="color:var(--text-muted);font-size:10px">None</i>';
    const max = Math.max(...items.map(([,c]) => c));
    return items.slice(0, 8).map(([name, count]) =>
      `<div class="stat-row"><span class="stat-label" title="${escHtml(name)}">${escHtml(name)}</span><div class="stat-bar"><div class="stat-bar-fill" style="width:${(count/max*100).toFixed(0)}%;background:${color}"></div></div><span class="stat-count">${fmtNum(count)}</span></div>`
    ).join('');
  }

  let html = `<div style="font-size:18px;color:var(--text-primary);margin-bottom:8px">${fmtNum(data.total)} <span style="font-size:11px;color:var(--text-muted)">memories</span></div>`;
  html += `<div class="stat-subtitle">By Type</div>${bars(data.types, '#4ecdc4')}`;
  html += `<div class="stat-subtitle">By Language</div>${bars(data.langs, '#60a5fa')}`;
  html += `<div class="stat-subtitle">By Domain</div>${bars(data.domains, '#fbbf24')}`;

  if (data.timeline && data.timeline.length) {
    const maxT = Math.max(...data.timeline.map(([,c]) => c));
    html += '<div class="stat-subtitle">Activity (monthly)</div>';
    html += '<div style="display:flex;align-items:end;gap:2px;height:50px">';
    for (const [month, count] of data.timeline) {
      const h = Math.max(2, (count / maxT) * 48);
      html += `<div title="${month}: ${count}" style="flex:1;height:${h}px;background:var(--accent);border-radius:2px 2px 0 0;opacity:0.7"></div>`;
    }
    html += '</div>';
    const first = data.timeline[0][0];
    const last = data.timeline[data.timeline.length - 1][0];
    html += `<div style="display:flex;justify-content:space-between;font-size:9px;color:var(--text-muted)"><span>${first}</span><span>${last}</span></div>`;
  }

  body.innerHTML = html;
}

// ── Workspace switcher ──
async function loadWorkspaces() {
  const data = await fetch('/api/workspaces').then(r => r.json());
  const sel = document.getElementById('workspace-select');
  sel.innerHTML = data.workspaces.map(w =>
    `<option value="${escHtml(w)}" ${w === data.active ? 'selected' : ''}>${escHtml(w)}</option>`
  ).join('');
}
document.getElementById('workspace-select').addEventListener('change', async (e) => {
  const ws = e.target.value;
  await fetch('/api/workspace/switch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ workspace: ws }),
  });
  activeFilters = {};
  await loadOverview();
});

// ── Boot ──
async function boot() {
  initTheme();
  initGraph();
  loadWorkspaces();
  loadStats();
  await restoreFromHash();
  connectSSE();
}

boot();

})();
</script>
</body>
</html>"""


# ── HTTP handler ──────────────────────────────────────────────

class VizHandler(http.server.BaseHTTPRequestHandler):

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        qs = parse_qs(parsed.query, keep_blank_values=True)

        # Flatten single-value params, keep multi-value as lists
        def param(key, default=""):
            vals = qs.get(key, [])
            return vals[0] if len(vals) == 1 else (vals if vals else default)

        def param_list(key):
            return qs.get(key, [])

        def filters_from_qs():
            f = {}
            for k in ("type", "lang", "domain", "layer"):
                v = param_list(k)
                if v:
                    f[k] = v
            return f if f else None

        if path == "/api/overview":
            data = _get_overview(filters_from_qs())
            self._json(data)

        elif path.startswith("/api/project/"):
            name = path[len("/api/project/"):]
            from urllib.parse import unquote
            name = unquote(name)
            data = _get_project_detail(name, filters_from_qs())
            self._json(data)

        elif path == "/api/nodes":
            data = build_node_page(
                project=param("project"),
                type_=param("type"),
                domain=param("domain"),
                lang=param("lang"),
                limit=int(param("limit") or "500"),
                offset_id=param("offset"),
            )
            self._json(data)

        elif path == "/api/search":
            q = param("q")
            if not q:
                self._json({"nodes": []})
            else:
                data = search_nodes(
                    query=q,
                    project=param("project"),
                    type_=param("type"),
                    domain=param("domain"),
                    limit=int(param("limit") or "20"),
                )
                self._json(data)

        elif path == "/api/neighbors":
            nid = param("id")
            k = int(param("k") or "10")
            if not nid:
                self._json({"source": "", "neighbors": []})
            else:
                data = get_neighbors(nid, k)
                self._json(data)

        elif path == "/api/stats":
            data = build_stats()
            self._json(data)

        elif path == "/api/cross-project":
            data = build_cross_project_similarity()
            self._json(data)

        elif path.startswith("/api/memory/"):
            mid = path[len("/api/memory/"):]
            from urllib.parse import unquote
            mid = unquote(mid)
            data = get_memory(mid)
            self._json(data)

        elif path == "/api/timeline":
            project = param("project", "")
            limit = int(param("limit") or "500")
            data = build_timeline(project=project, limit=limit)
            self._json(data)

        elif path == "/api/kg":
            subject = param("subject", "")
            limit = int(param("limit") or "200")
            data = build_kg_data(subject=subject, limit=limit)
            self._json(data)

        elif path.startswith("/api/kg/entity/"):
            entity = path[len("/api/kg/entity/"):]
            from urllib.parse import unquote
            entity = unquote(entity)
            data = build_kg_data(subject=entity, limit=50)
            self._json(data)

        elif path == "/api/workspaces":
            self._json({
                "active": config.get_active_workspace(),
                "workspaces": config.get_known_workspaces(),
            })

        elif path == "/api/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                while True:
                    if check_for_changes():
                        global _overview_cache
                        _overview_cache = None
                        _project_cache.clear()
                        msg = json.dumps({"version": _data_version, "total": _last_row_count})
                        self.wfile.write(f"event: update\ndata: {msg}\n\n".encode())
                        self.wfile.flush()
                    else:
                        self.wfile.write(": heartbeat\n\n".encode())
                        self.wfile.flush()
                    time.sleep(2)
            except (BrokenPipeError, ConnectionResetError):
                pass

        else:
            # Serve HTML page
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode())

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        if path == "/api/workspace/switch":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            name = body.get("workspace", "")
            if not name:
                self._json({"error": "workspace name required"})
                return
            err = config.validate_workspace_name(name)
            if err:
                self._json({"error": err})
                return
            config.switch_workspace(name)
            # Reset Qdrant client to pick up new collection
            vs._client = None
            global _overview_cache, _last_wal_size, _last_row_count
            _overview_cache = None
            _project_cache.clear()
            _last_wal_size = 0
            _last_row_count = 0
            self._json({"ok": True, "active": config.get_active_workspace()})
        else:
            self.send_response(404)
            self.end_headers()

    def _json(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


# ── Chrome launcher ──────────────────────────────────────────

def launch_app_window(url: str):
    chrome_flags = [
        f"--app={url}",
        "--window-size=1200,800",
        "--disable-extensions",
        "--disable-default-apps",
        "--no-first-run",
    ]

    candidates = []
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
                subprocess.Popen([chrome] + chrome_flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            except Exception:
                continue

    webbrowser.open(url)


# ── Main ──────────────────────────────────────────────────────

def main():
    global _last_wal_size, _last_row_count
    port = DEFAULT_PORT
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--port" and i + 1 < len(args):
            port = int(args[i + 1])

    print(f"\n  \033[0;36mStarting Imprint Graph...\033[0m")

    # Warm up Qdrant connection + initial counts
    try:
        client, coll = vs._ensure_collection()
        info = client.get_collection(coll)
        total = info.points_count or 0
    except Exception:
        total = 0

    wp = config.wal_path()
    try:
        _last_wal_size = os.path.getsize(wp) if wp.exists() else 0
    except OSError:
        _last_wal_size = 0
    _last_row_count = total

    # Pre-warm overview cache
    _get_overview()

    projects = len((_overview_cache or {}).get("projects", []))
    print(f"  {total} memories, {projects} projects")

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), VizHandler)
    url = f"http://127.0.0.1:{port}"
    print(f"\n  \033[0;33m\u2726 Imprint Graph running at {url}\033[0m")
    print(f"  \033[2mLive updates enabled \u00b7 Press Ctrl+C to stop\033[0m\n")

    threading.Timer(0.5, lambda: launch_app_window(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
        server.shutdown()


if __name__ == "__main__":
    main()
