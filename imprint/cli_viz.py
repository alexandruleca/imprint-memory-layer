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

from imprint import config, vectorstore as vs
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
<title>Imprint Graph</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
html, body { width: 100%; height: 100%; overflow: hidden; }
body { background: #1a1a2e; color: #dcddde; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; display: flex; }

/* Filter sidebar */
#sidebar {
  width: 250px; min-width: 250px; height: 100vh;
  background: #16213e; border-right: 1px solid #2a2a4a;
  overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 12px;
  z-index: 10;
}
#sidebar h2 { font-size: 14px; color: #4ecdc4; margin-bottom: 4px; text-transform: uppercase; letter-spacing: 1px; }
.filter-section { margin-bottom: 8px; }
.filter-section h3 { font-size: 12px; color: #8892b0; margin-bottom: 6px; cursor: pointer; user-select: none; }
.filter-section h3:hover { color: #dcddde; }
.filter-items { display: flex; flex-wrap: wrap; gap: 4px; }
.filter-chip {
  font-size: 11px; padding: 3px 8px; border-radius: 12px;
  background: #2a2a4a; color: #8892b0; cursor: pointer;
  border: 1px solid transparent; transition: all 0.15s; white-space: nowrap;
  user-select: none;
}
.filter-chip:hover { border-color: #4ecdc4; color: #dcddde; }
.filter-chip.active { background: #4ecdc422; border-color: #4ecdc4; color: #4ecdc4; }
.filter-chip .count { font-size: 10px; opacity: 0.6; margin-left: 3px; }

/* Main area */
#main { flex: 1; display: flex; flex-direction: column; position: relative; min-width: 0; }

/* Breadcrumb + search bar */
#topbar {
  display: flex; align-items: center; gap: 12px;
  padding: 10px 16px; background: #16213e; border-bottom: 1px solid #2a2a4a;
  z-index: 10;
}
#breadcrumb { display: flex; align-items: center; gap: 4px; font-size: 13px; flex-shrink: 0; }
.crumb { color: #8892b0; cursor: pointer; padding: 2px 6px; border-radius: 4px; }
.crumb:hover { color: #4ecdc4; background: #2a2a4a; }
.crumb.current { color: #dcddde; cursor: default; }
.crumb.current:hover { background: transparent; }
.crumb-sep { color: #4a4a6a; font-size: 11px; }
#search-box {
  flex: 1; max-width: 400px; margin-left: auto;
  background: #2a2a4a; border: 1px solid #3a3a5a; border-radius: 6px;
  padding: 6px 12px; color: #dcddde; font-size: 13px; outline: none;
}
#search-box:focus { border-color: #4ecdc4; }
#search-box::placeholder { color: #5a5a7a; }
#stats { font-size: 11px; color: #5a5a7a; margin-left: 8px; white-space: nowrap; }

/* Graph container */
#graph-container { flex: 1; position: relative; overflow: hidden; }

/* Loading overlay */
#loading {
  position: absolute; top: 0; left: 0; right: 0; bottom: 0;
  display: flex; align-items: center; justify-content: center;
  background: #1a1a2ecc; z-index: 50; font-size: 14px; color: #8892b0;
}
#loading.hidden { display: none; }
.spinner { width: 24px; height: 24px; border: 2px solid #4ecdc444; border-top-color: #4ecdc4; border-radius: 50%; animation: spin 0.8s linear infinite; margin-right: 10px; }
@keyframes spin { to { transform: rotate(360deg); } }

/* Detail panel */
#detail {
  position: absolute; top: 0; right: -400px; width: 400px; height: 100%;
  background: #16213e; border-left: 1px solid #2a2a4a;
  overflow-y: auto; padding: 16px; z-index: 20;
  transition: right 0.25s ease;
}
#detail.open { right: 0; }
#detail-close { position: absolute; top: 12px; right: 12px; cursor: pointer; color: #8892b0; font-size: 18px; background: none; border: none; }
#detail-close:hover { color: #dcddde; }
#detail h3 { font-size: 15px; color: #4ecdc4; margin-bottom: 12px; padding-right: 30px; }
.detail-field { margin-bottom: 10px; }
.detail-field label { font-size: 11px; color: #5a5a7a; text-transform: uppercase; display: block; margin-bottom: 2px; }
.detail-field .value { font-size: 13px; color: #dcddde; word-break: break-word; }
.detail-content { background: #1a1a2e; padding: 10px; border-radius: 6px; font-size: 12px; line-height: 1.5; white-space: pre-wrap; max-height: 300px; overflow-y: auto; color: #b0b0c0; }
.detail-tags { display: flex; flex-wrap: wrap; gap: 4px; }
.detail-tag { font-size: 10px; padding: 2px 6px; background: #2a2a4a; border-radius: 8px; color: #8892b0; }
.neighbor-item { padding: 6px 8px; margin: 4px 0; background: #1a1a2e; border-radius: 4px; cursor: pointer; font-size: 12px; border-left: 3px solid transparent; }
.neighbor-item:hover { background: #2a2a4a; border-left-color: #4ecdc4; }
.neighbor-sim { font-size: 10px; color: #4ecdc4; float: right; }

/* Reset button */
#reset-btn {
  position: absolute; top: 12px; left: 12px; z-index: 15;
  background: #4ecdc4; color: #1a1a2e; border: none; border-radius: 6px;
  padding: 6px 14px; font-size: 12px; font-weight: 600; cursor: pointer;
  display: none; transition: opacity 0.2s;
}
#reset-btn:hover { opacity: 0.85; }

/* Empty state */
#empty-state {
  display: none; position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);
  text-align: center; color: #5a5a7a; z-index: 5;
}
#empty-state h2 { font-size: 20px; margin-bottom: 8px; color: #8892b0; }
#empty-state p { font-size: 14px; }

/* Tooltip */
.g6-tooltip {
  background: #16213eee !important; border: 1px solid #4ecdc4 !important;
  border-radius: 6px !important; padding: 8px 12px !important;
  color: #dcddde !important; font-size: 12px !important;
  max-width: 300px !important; box-shadow: 0 4px 12px #00000066 !important;
}
</style>
</head>
<body>
<div id="sidebar">
  <h2>Imprint Graph</h2>
  <div id="filter-projects" class="filter-section"><h3>Projects</h3><div class="filter-items" id="fp-items"></div></div>
  <div id="filter-types" class="filter-section"><h3>Types</h3><div class="filter-items" id="ft-items"></div></div>
  <div id="filter-langs" class="filter-section"><h3>Languages</h3><div class="filter-items" id="fl-items"></div></div>
  <div id="filter-domains" class="filter-section"><h3>Domains</h3><div class="filter-items" id="fd-items"></div></div>
</div>

<div id="main">
  <div id="topbar">
    <div id="breadcrumb"><span class="crumb current" data-level="overview">Overview</span></div>
    <input id="search-box" type="text" placeholder="Search memories... (press /)" />
    <span id="stats"></span>
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

const TYPE_COLORS = {
  decision: '#ff6b6b', pattern: '#4ecdc4', bug: '#ff8c42',
  preference: '#a78bfa', architecture: '#60a5fa', milestone: '#34d399',
  finding: '#fbbf24', conversation: '#f472b6',
};

// ── Helpers ──
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
        labelFill: '#dcddde',
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
        stroke: d => d.data?.borderColor || '#2a2a4a',
      },
    },
    edge: {
      style: {
        stroke: '#4ecdc4',
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
        labelFill: '#8892b0',
        labelFontSize: 13,
        labelPlacement: 'top',
        cursor: 'pointer',
        collapsedSize: d => d.data?.collapsedSize || 40,
      },
    },
    plugins: [{
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
    background: '#1a1a2e',
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

  graph.setData({ nodes, edges: [], combos: [] });
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
        labelFill: '#8892b0',
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
    const moreId = 'load_more';
    nodes.push({
      id: moreId,
      data: { level: 'load-more', label: `+ ${data.total - data.nodes.length} more...` },
      style: {
        size: 30,
        fill: '#2a2a4a',
        stroke: '#4ecdc4',
        lineWidth: 1,
        labelText: `+${fmtNum(data.total - data.nodes.length)} more`,
        labelFontSize: 10,
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
    // Could load more nodes, for now drill into type view
  }
}

// ── Detail panel ──
async function showNodeDetail(d) {
  const body = document.getElementById('detail-body');
  const tags = d.tags || {};
  const domains = (tags.domain || []).map(t => `<span class="detail-tag">${t}</span>`).join('');
  const topics = (tags.topics || []).map(t => `<span class="detail-tag">${t}</span>`).join('');

  body.innerHTML = `
    <h3>${d.label || 'Memory'}</h3>
    <div class="detail-field"><label>Source</label><div class="value">${d.source || '—'}</div></div>
    <div class="detail-field"><label>Type</label><div class="value">${d.memType || '—'}</div></div>
    <div class="detail-field"><label>Project</label><div class="value">${d.project || '—'}</div></div>
    ${tags.lang ? `<div class="detail-field"><label>Language</label><div class="value">${tags.lang}</div></div>` : ''}
    ${tags.layer ? `<div class="detail-field"><label>Layer</label><div class="value">${tags.layer}</div></div>` : ''}
    ${domains ? `<div class="detail-field"><label>Domains</label><div class="detail-tags">${domains}</div></div>` : ''}
    ${topics ? `<div class="detail-field"><label>Topics</label><div class="detail-tags">${topics}</div></div>` : ''}
    <div class="detail-field"><label>Content</label><div class="detail-content">${escHtml(d.content || '')}</div></div>
    <div class="detail-field"><label>Neighbors</label><div id="neighbors-list"><i style="color:#5a5a7a;font-size:12px">Loading...</i></div></div>
  `;

  document.getElementById('detail').classList.add('open');
  detailOpen = true;

  // Load neighbors
  if (d.memId) {
    const nb = await api(`/api/neighbors?id=${encodeURIComponent(d.memId)}&k=8`);
    const list = document.getElementById('neighbors-list');
    if (nb.neighbors && nb.neighbors.length) {
      list.innerHTML = nb.neighbors.map(n => `
        <div class="neighbor-item" data-project="${escHtml(n.project)}" data-type="${escHtml(n.type)}" data-id="${escHtml(n.id)}">
          <span class="neighbor-sim">${(n.similarity * 100).toFixed(0)}%</span>
          <div style="font-size:12px;color:#dcddde">${escHtml(n.label)}</div>
          <div style="font-size:10px;color:#5a5a7a">${escHtml(n.project)} / ${escHtml(n.type)}</div>
        </div>
      `).join('');

      // Draw neighbor edges on graph
      clearNeighborEdges();
      const sourceNodeId = `leaf_${d.memId}`;
      for (const n of nb.neighbors) {
        const targetId = `leaf_${n.id}`;
        // Only draw edge if target node exists in current graph
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
                stroke: '#4ecdc4',
                lineWidth: Math.max(0.5, n.similarity * 2),
                opacity: Math.max(0.2, n.similarity * 0.8),
                lineDash: 0,
              },
            }]);
          }
        } catch (e) { /* target not in current graph */ }
      }
      if (neighborEdges.length) graph.draw();
    } else {
      list.innerHTML = '<i style="color:#5a5a7a;font-size:12px">No similar memories found</i>';
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
        labelFill: '#8892b0',
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
document.getElementById('search-box').addEventListener('input', (e) => {
  clearTimeout(searchTimeout);
  searchTimeout = setTimeout(() => doSearch(e.target.value.trim()), 300);
});

// Detail close button
document.getElementById('detail-close').addEventListener('click', closeDetail);

// Reset / back button
document.getElementById('reset-btn').addEventListener('click', () => {
  closeDetail();
  if (currentLevel === 'type' && currentProject) drillIntoProject(currentProject);
  else loadOverview();
});

// Resize handling
window.addEventListener('resize', () => {
  if (graph) {
    const c = document.getElementById('graph-container');
    graph.resize(c.clientWidth, c.clientHeight);
  }
});

// ── Boot ──
async function boot() {
  initGraph();
  await loadOverview();
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
