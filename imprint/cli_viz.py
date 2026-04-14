"""Obsidian-style force-directed graph visualization of the imprint memory.

Uses Sigma.js v3 (WebGL) + graphology + ForceAtlas2 for interactive 2D
force layout. Handles 100k+ nodes. Nodes = memories, edges = semantic
similarity (cosine KNN on embeddings). Same-project nodes cluster.

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

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from imprint import config, vectorstore as vs

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


def _extract_label(content: str, source: str, idx: int) -> str:
    for line in content.split("\n"):
        line = line.strip()
        if line and len(line) > 10 and not line.startswith(("[", "import ", "from ", "#", "---", "```")):
            return line[:80]
    return source or f"mem-{idx}"


def get_all_rows():
    """Pull every point from Qdrant with its vector. Streams via scroll so
    a 30k-row collection doesn't materialize all at once."""
    try:
        client = vs._ensure_collection()
    except Exception:
        return []

    try:
        info = client.get_collection(config.QDRANT_COLLECTION)
        if (info.points_count or 0) == 0:
            return []
    except Exception:
        return []

    from qdrant_client.http.exceptions import UnexpectedResponse  # noqa: F401

    rows: list[dict] = []
    offset = None
    try:
        while True:
            pts, offset = client.scroll(
                collection_name=config.QDRANT_COLLECTION,
                limit=2000,
                offset=offset,
                with_payload=True,
                with_vectors=True,
            )
            for p in pts:
                pl = p.payload or {}
                vecs = p.vector or {}
                vec = vecs.get(config.QDRANT_VECTOR_NAME) if isinstance(vecs, dict) else vecs
                rows.append({
                    "id": pl.get("_mid", ""),
                    "content": pl.get("content", ""),
                    "project": pl.get("project", ""),
                    "type": pl.get("type", ""),
                    "tags": pl.get("tags", {}),
                    "source": pl.get("source", ""),
                    "vector": vec,
                })
            if offset is None:
                break
    except Exception:
        pass
    return rows


def compute_edges(vectors, k=5):
    """Cosine-similarity KNN on embedding vectors — pure numpy, no scipy.
    Returns links with similarity scores for visual weight."""
    vecs = np.array(vectors, dtype=np.float32)
    n = len(vecs)
    if n < 2:
        return []
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    vecs = vecs / np.maximum(norms, 1e-8)
    k = min(k, n - 1)

    edges = {}  # (i,j) -> max similarity
    batch = 500
    for start in range(0, n, batch):
        end = min(start + batch, n)
        sims = vecs[start:end] @ vecs.T
        for bi in range(end - start):
            i = start + bi
            sims[bi, i] = -2.0
            top_k = np.argpartition(sims[bi], -k)[-k:]
            for j in top_k:
                key = (min(i, int(j)), max(i, int(j)))
                score = float(sims[bi, int(j)])
                if key not in edges or score > edges[key]:
                    edges[key] = score

    return [{"source": e[0], "target": e[1], "sim": round(s, 3)} for e, s in edges.items()]


def build_data():
    rows = get_all_rows()
    if not rows:
        return {"nodes": [], "links": [], "projects": [], "types": list(TYPE_COLORS.keys()),
                "projectColors": {}, "typeColors": TYPE_COLORS, "total": 0, "version": 0}

    MAX_NODES = 5000
    if len(rows) > MAX_NODES:
        rng = np.random.RandomState(42)
        indices = rng.choice(len(rows), MAX_NODES, replace=False)
        rows = [rows[i] for i in sorted(indices)]

    vectors = [r["vector"] for r in rows]

    projects = sorted(set(r.get("project", "") or "(none)" for r in rows))
    pc = {p: _project_color(p) for p in projects}

    nodes = []
    for i, r in enumerate(rows):
        project = r.get("project", "") or "(none)"
        mem_type = r.get("type", "finding")
        content = r.get("content", "")
        source = r.get("source", "")
        tags = r.get("tags", {})
        label = _extract_label(content, source, i)
        nid = hashlib.md5(f"{source}|{project}|{mem_type}|{content[:200]}".encode()).hexdigest()[:12]
        nodes.append({
            "id": nid,
            "idx": i,
            "project": project,
            "type": mem_type,
            "source": source,
            "label": label,
            "content": content[:500],
            "color": pc.get(project, "#ffa500"),
            "tags": tags,
        })

    links = compute_edges(vectors, k=5)

    conn_count = [0] * len(nodes)
    for link in links:
        conn_count[link["source"]] += 1
        conn_count[link["target"]] += 1
    for i, node in enumerate(nodes):
        node["val"] = 1 + conn_count[i]

    return {
        "nodes": nodes, "links": links, "projects": projects,
        "types": list(TYPE_COLORS.keys()),
        "projectColors": pc, "typeColors": TYPE_COLORS,
        "total": len(nodes), "version": int(time.time()),
    }


_last_wal_size = 0
_last_row_count = -1
_data_version = 0


def check_for_changes():
    global _last_wal_size, _last_row_count, _data_version
    changed = False
    wal_path = config.get_data_dir() / "wal.jsonl"
    try:
        size = os.path.getsize(wal_path) if wal_path.exists() else 0
    except OSError:
        size = 0
    if size != _last_wal_size:
        _last_wal_size = size
        changed = True
    try:
        client = vs._ensure_collection()
        info = client.get_collection(config.QDRANT_COLLECTION)
        count = info.points_count or 0
        if count != _last_row_count:
            _last_row_count = count
            changed = True
    except Exception:
        pass
    if changed:
        _data_version += 1
    return changed


HTML_PAGE = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Imprint Graph</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: #1a1a2e; color: #dcddde; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; overflow: hidden; }

  #graph-container { width: 100vw; height: 100vh; }

  /* Info panel — top left */
  #info {
    position: fixed; top: 16px; left: 16px; z-index: 100;
    background: rgba(26, 26, 46, 0.85); border: 1px solid rgba(255,255,255,0.06);
    border-radius: 10px; padding: 12px 16px; backdrop-filter: blur(16px);
    min-width: 160px;
  }
  #info h1 { font-size: 13px; font-weight: 600; color: #e0e0e0; margin-bottom: 4px; }
  #info .stat { font-size: 11px; color: rgba(220,221,222,0.5); }
  #live-dot {
    display: inline-block; width: 5px; height: 5px; border-radius: 50%;
    background: #4ecdc4; margin-right: 5px;
    box-shadow: 0 0 6px rgba(78,205,196,0.6);
    animation: pulse 2.5s ease-in-out infinite;
  }
  @keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:0.3; } }

  /* Search — top center */
  #search {
    position: fixed; top: 16px; left: 50%; transform: translateX(-50%); z-index: 100;
  }
  #q {
    width: 280px; padding: 8px 14px; border-radius: 20px;
    background: rgba(26, 26, 46, 0.85); border: 1px solid rgba(255,255,255,0.08);
    color: #dcddde; font-size: 12px; outline: none; backdrop-filter: blur(16px);
    transition: border-color 0.2s;
  }
  #q:focus { border-color: rgba(78,205,196,0.4); }
  #q::placeholder { color: rgba(220,221,222,0.3); }

  /* Legend — right side */
  #legend {
    position: fixed; top: 16px; right: 16px; z-index: 100;
    background: rgba(26, 26, 46, 0.85); border: 1px solid rgba(255,255,255,0.06);
    border-radius: 10px; padding: 12px 14px; backdrop-filter: blur(16px);
    max-height: calc(100vh - 32px); overflow-y: auto; min-width: 150px;
  }
  #legend h2 { font-size: 10px; text-transform: uppercase; letter-spacing: 1.5px; color: rgba(220,221,222,0.4); margin-bottom: 8px; font-weight: 500; }
  .lg {
    display: flex; align-items: center; gap: 8px; padding: 3px 6px;
    border-radius: 4px; cursor: pointer; font-size: 11px; color: rgba(220,221,222,0.7);
    transition: background 0.15s;
  }
  .lg:hover { background: rgba(255,255,255,0.05); }
  .lg.active { background: rgba(255,255,255,0.08); color: #e0e0e0; }
  .lg .count { margin-left: auto; font-size: 10px; color: rgba(220,221,222,0.3); }
  .dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }

  /* Detail panel — right side drawer */
  #detail {
    display: none; position: fixed; top: 0; right: 0; bottom: 0; z-index: 200;
    width: 380px; background: rgba(22, 22, 40, 0.95);
    border-left: 1px solid rgba(255,255,255,0.06);
    backdrop-filter: blur(24px); overflow-y: auto;
    animation: slideIn 0.25s ease-out;
  }
  @keyframes slideIn { from { transform: translateX(100%); } to { transform: translateX(0); } }
  #detail .header {
    padding: 16px 20px; border-bottom: 1px solid rgba(255,255,255,0.05);
    display: flex; align-items: center; justify-content: space-between;
  }
  #detail .header h3 { font-size: 12px; font-weight: 600; color: #e0e0e0; }
  #detail .close-btn {
    cursor: pointer; color: rgba(220,221,222,0.4); font-size: 18px; line-height: 1;
    width: 24px; height: 24px; display: flex; align-items: center; justify-content: center;
    border-radius: 4px; transition: all 0.15s;
  }
  #detail .close-btn:hover { color: #dcddde; background: rgba(255,255,255,0.06); }

  /* Detail sections */
  .detail-section { padding: 12px 20px; border-bottom: 1px solid rgba(255,255,255,0.03); }
  .detail-section:last-child { border-bottom: none; }
  .detail-label { font-size: 9px; text-transform: uppercase; letter-spacing: 1.2px; color: rgba(220,221,222,0.35); margin-bottom: 6px; font-weight: 500; }
  .detail-value { font-size: 12px; color: rgba(220,221,222,0.8); line-height: 1.5; }
  .detail-value.mono { font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace; font-size: 11px; }

  /* Tag chips */
  .tag-chips { display: flex; flex-wrap: wrap; gap: 4px; }
  .tag-chip {
    display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 10px;
    background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.06);
    color: rgba(220,221,222,0.6);
  }
  .tag-chip.lang { border-color: rgba(96,165,250,0.3); color: rgba(96,165,250,0.8); }
  .tag-chip.layer { border-color: rgba(167,139,250,0.3); color: rgba(167,139,250,0.8); }
  .tag-chip.kind { border-color: rgba(52,211,153,0.3); color: rgba(52,211,153,0.8); }
  .tag-chip.domain { border-color: rgba(251,191,36,0.3); color: rgba(251,191,36,0.8); }
  .tag-chip.topic { border-color: rgba(244,114,182,0.3); color: rgba(244,114,182,0.8); }
  .tag-chip.type-tag { border-color: rgba(255,107,107,0.3); color: rgba(255,107,107,0.8); }

  /* Related nodes list */
  .related-node {
    display: flex; align-items: center; gap: 8px; padding: 6px 8px;
    border-radius: 6px; cursor: pointer; transition: background 0.15s;
    margin-bottom: 2px;
  }
  .related-node:hover { background: rgba(255,255,255,0.04); }
  .related-dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
  .related-label { font-size: 11px; color: rgba(220,221,222,0.7); flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .related-sim { font-size: 10px; color: rgba(78,205,196,0.5); flex-shrink: 0; font-family: 'SF Mono', monospace; }

  /* Content preview */
  .content-preview {
    font-size: 11px; line-height: 1.6; color: rgba(220,221,222,0.7);
    white-space: pre-wrap; font-family: 'SF Mono', 'Fira Code', monospace;
    max-height: 200px; overflow-y: auto;
    background: rgba(0,0,0,0.15); border-radius: 6px; padding: 10px 12px;
    margin-top: 4px;
  }

  /* Metadata grid */
  .meta-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .meta-item {}
  .meta-item .detail-label { margin-bottom: 3px; }
  .meta-item .detail-value { font-size: 11px; }

  /* Settling indicator */
  #settling {
    position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%); z-index: 100;
    background: rgba(26, 26, 46, 0.8); border: 1px solid rgba(78,205,196,0.15);
    border-radius: 16px; padding: 6px 14px; font-size: 11px; color: rgba(78,205,196,0.7);
    transition: opacity 0.5s; pointer-events: none;
  }

  /* Scrollbar */
  ::-webkit-scrollbar { width: 4px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.1); border-radius: 2px; }
</style>
</head>
<body>

<div id="info">
  <h1>Imprint Graph</h1>
  <div class="stat"><span id="live-dot" class="live-dot"></span><span id="stats"></span></div>
</div>

<div id="search"><input id="q" type="text" placeholder="Search memories...  (press /)" spellcheck="false"></div>

<div id="legend"></div>

<div id="detail">
  <div class="header">
    <h3 id="detail-title">Node Details</h3>
    <span class="close-btn" onclick="closeDetail()">&times;</span>
  </div>
  <div id="detail-body"></div>
</div>

<div id="settling">Settling graph...</div>

<div id="graph-container"></div>

<script src="https://cdn.jsdelivr.net/npm/graphology@0.25.4/dist/graphology.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/graphology-layout-forceatlas2@0.10.1/dist/graphology-layout-forceatlas2.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/sigma@3/build/sigma.min.js"></script>
<script>
(function() {
  'use strict';

  var DATA = null;
  var currentVersion = 0;
  var adjacencyMap = new Map();
  var linksByNode = new Map();
  var nodeById = {};
  var hoveredNode = null;
  var selectedNode = null;
  var activeProjectFilter = null;
  var matchSet = null;
  var graph = null;
  var renderer = null;
  var fa2 = null;

  // ── Helpers ──────────────────────────────────────────────────
  function escHtml(s) {
    if (!s) return '';
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  function hexToRgba(hex, alpha) {
    var r = parseInt(hex.slice(1,3),16), g = parseInt(hex.slice(3,5),16), b = parseInt(hex.slice(5,7),16);
    return 'rgba(' + r + ',' + g + ',' + b + ',' + alpha + ')';
  }

  // ── Build graph from API data ────────────────────────────────
  function buildGraph(data) {
    DATA = data;
    nodeById = {};

    graph = new graphology.Graph();

    // Add nodes with random initial positions
    data.nodes.forEach(function(n) {
      nodeById[n.id] = n;
      graph.addNode(n.id, {
        label: n.label,
        x: Math.random() * 1000 - 500,
        y: Math.random() * 1000 - 500,
        size: Math.max(2, Math.sqrt(n.val) * 2),
        color: n.color,
        // Stash metadata on node attributes
        _project: n.project,
        _type: n.type,
        _source: n.source,
        _content: n.content,
        _tags: n.tags,
        _val: n.val,
        _origColor: n.color,
      });
    });

    // Add edges
    data.links.forEach(function(link, i) {
      var srcId = data.nodes[link.source].id;
      var tgtId = data.nodes[link.target].id;
      if (graph.hasNode(srcId) && graph.hasNode(tgtId) && !graph.hasEdge('e' + i)) {
        var sim = link.sim || 0.5;
        var srcNode = data.nodes[link.source];
        var tgtNode = data.nodes[link.target];
        var sameProject = srcNode.project === tgtNode.project;
        graph.addEdge(srcId, tgtId, {
          key: 'e' + i,
          size: 0.3,
          color: sameProject ? hexToRgba(srcNode.color, 0.06) : 'rgba(255,255,255,0.02)',
          _sim: sim,
          _origColor: sameProject ? hexToRgba(srcNode.color, 0.06) : 'rgba(255,255,255,0.02)',
        });
      }
    });

    rebuildAdjacency(data);
    return graph;
  }

  // ── Adjacency + relations map ────────────────────────────────
  function rebuildAdjacency(data) {
    adjacencyMap = new Map();
    linksByNode = new Map();

    data.links.forEach(function(link) {
      var srcId = data.nodes[link.source].id;
      var tgtId = data.nodes[link.target].id;
      var sim = link.sim || 0.5;

      if (!adjacencyMap.has(srcId)) adjacencyMap.set(srcId, new Set());
      if (!adjacencyMap.has(tgtId)) adjacencyMap.set(tgtId, new Set());
      adjacencyMap.get(srcId).add(tgtId);
      adjacencyMap.get(tgtId).add(srcId);

      if (!linksByNode.has(srcId)) linksByNode.set(srcId, []);
      if (!linksByNode.has(tgtId)) linksByNode.set(tgtId, []);
      linksByNode.get(srcId).push({ nodeId: tgtId, sim: sim });
      linksByNode.get(tgtId).push({ nodeId: srcId, sim: sim });
    });
  }

  // ── Sigma renderer with reducers ─────────────────────────────
  function createRenderer(graph) {
    var container = document.getElementById('graph-container');

    renderer = new Sigma(graph, container, {
      allowInvalidContainer: true,
      renderLabels: true,
      labelDensity: 0.07,
      labelGridCellSize: 100,
      labelRenderedSizeThreshold: 6,
      labelFont: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
      labelColor: { color: '#dcddde' },
      labelSize: 10,
      defaultEdgeType: 'line',
      stagePadding: 40,

      nodeReducer: function(node, data) {
        var res = Object.assign({}, data);
        var neighbors = adjacencyMap.get(node);

        // Hover highlight
        if (hoveredNode) {
          if (node === hoveredNode) {
            res.highlighted = true;
            res.zIndex = 2;
          } else if (neighbors && neighbors.has(hoveredNode)) {
            res.zIndex = 1;
          } else {
            res.color = '#222230';
            res.label = '';
            res.zIndex = 0;
          }
        }
        // Selected highlight
        else if (selectedNode) {
          if (node === selectedNode) {
            res.highlighted = true;
            res.zIndex = 2;
          } else if (neighbors && neighbors.has(selectedNode)) {
            res.zIndex = 1;
          } else {
            res.color = '#2a2a3a';
            res.label = '';
            res.zIndex = 0;
          }
        }
        // Search filter
        else if (matchSet) {
          var nodeData = nodeById[node];
          if (!nodeData || !matchSet.has(nodeData)) {
            res.color = '#222230';
            res.label = '';
          }
        }
        // Project filter
        else if (activeProjectFilter) {
          if (data._project !== activeProjectFilter) {
            res.color = '#222230';
            res.label = '';
          }
        }

        return res;
      },

      edgeReducer: function(edge, data) {
        var res = Object.assign({}, data);
        var src = graph.source(edge);
        var tgt = graph.target(edge);

        if (hoveredNode) {
          if (src === hoveredNode || tgt === hoveredNode) {
            var sim = data._sim || 0.5;
            res.color = 'rgba(255,255,255,' + (0.15 + sim * 0.35).toFixed(3) + ')';
            res.size = Math.max(0.8, sim * 2.5);
            res.zIndex = 1;
          } else {
            res.color = 'rgba(255,255,255,0.003)';
          }
        } else if (selectedNode) {
          if (src === selectedNode || tgt === selectedNode) {
            var sim = data._sim || 0.5;
            res.color = 'rgba(78,205,196,' + (0.1 + sim * 0.3).toFixed(3) + ')';
            res.size = Math.max(0.6, sim * 2);
            res.zIndex = 1;
          } else {
            res.color = 'rgba(255,255,255,0.003)';
          }
        } else if (matchSet) {
          var srcData = nodeById[src], tgtData = nodeById[tgt];
          if (!srcData || !tgtData || !matchSet.has(srcData) || !matchSet.has(tgtData)) {
            res.color = 'rgba(255,255,255,0.003)';
          }
        } else if (activeProjectFilter) {
          var sa = graph.getNodeAttribute(src, '_project');
          var ta = graph.getNodeAttribute(tgt, '_project');
          if (sa !== activeProjectFilter || ta !== activeProjectFilter) {
            res.color = 'rgba(255,255,255,0.003)';
          }
        }

        return res;
      }
    });

    // ── Hover events ─────────────────────────────────────────
    renderer.on('enterNode', function(e) {
      hoveredNode = e.node;
      container.style.cursor = 'pointer';
      renderer.refresh();
    });

    renderer.on('leaveNode', function() {
      hoveredNode = null;
      container.style.cursor = 'default';
      renderer.refresh();
    });

    // ── Click events ─────────────────────────────────────────
    renderer.on('clickNode', function(e) {
      var node = e.node;
      selectedNode = node;
      renderer.refresh();
      showDetail(node);
    });

    renderer.on('clickStage', function() {
      selectedNode = null;
      hoveredNode = null;
      closeDetail();
      renderer.refresh();
    });

    return renderer;
  }

  // ── ForceAtlas2 layout ───────────────────────────────────────
  function startLayout(graph) {
    var n = graph.order;
    var settings = {
      scalingRatio: n > 2000 ? 20 : n > 500 ? 10 : 5,
      gravity: n > 2000 ? 0.5 : 1,
      strongGravityMode: false,
      barnesHutOptimize: n > 1000,
      barnesHutTheta: 0.5,
      slowDown: 5,
      adjustSizes: false,
    };

    fa2 = new FA2Layout(graph, {
      settings: settings,
    });
    fa2.start();

    // Stop after convergence or timeout
    setTimeout(function() {
      if (fa2 && fa2.isRunning()) fa2.stop();
      var el = document.getElementById('settling');
      if (el) { el.style.opacity = '0'; setTimeout(function() { if (el) el.style.display = 'none'; }, 500); }
    }, n > 2000 ? 12000 : 6000);
  }

  // ── Detail panel ─────────────────────────────────────────────
  function showDetail(nodeId) {
    var n = nodeById[nodeId];
    if (!n) return;
    var attrs = graph.getNodeAttributes(nodeId);
    var tags = attrs._tags || {};
    var connections = (linksByNode.get(nodeId) || []).slice().sort(function(a,b) { return b.sim - a.sim; });

    var html = '';

    // Project + Label
    html += '<div class="detail-section">';
    html += '<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">';
    html += '<span style="width:10px;height:10px;border-radius:50%;background:' + n.color + '"></span>';
    html += '<span style="font-size:13px;font-weight:600;color:' + n.color + '">' + escHtml(n.project) + '</span>';
    html += '</div>';
    html += '<div class="detail-label">Label</div>';
    html += '<div class="detail-value">' + escHtml(n.label) + '</div>';
    html += '</div>';

    // Metadata grid
    html += '<div class="detail-section"><div class="meta-grid">';
    html += '<div class="meta-item"><div class="detail-label">Type</div><div class="detail-value"><span class="tag-chip type-tag">' + escHtml(n.type) + '</span></div></div>';
    html += '<div class="meta-item"><div class="detail-label">Connections</div><div class="detail-value">' + (n.val - 1) + ' nodes</div></div>';
    if (n.source) {
      html += '<div class="meta-item" style="grid-column:span 2"><div class="detail-label">Source</div><div class="detail-value mono">' + escHtml(n.source) + '</div></div>';
    }
    html += '</div></div>';

    // Tags
    var hasAnyTag = tags.lang || tags.layer || tags.kind || (tags.domain && tags.domain.length) || (tags.topics && tags.topics.length);
    if (hasAnyTag) {
      html += '<div class="detail-section">';
      html += '<div class="detail-label">Tags</div>';
      html += '<div class="tag-chips">';
      if (tags.lang) html += '<span class="tag-chip lang">' + escHtml(tags.lang) + '</span>';
      if (tags.layer) html += '<span class="tag-chip layer">' + escHtml(tags.layer) + '</span>';
      if (tags.kind) html += '<span class="tag-chip kind">' + escHtml(tags.kind) + '</span>';
      if (tags.domain) for (var di = 0; di < tags.domain.length; di++) html += '<span class="tag-chip domain">' + escHtml(tags.domain[di]) + '</span>';
      if (tags.topics) for (var ti = 0; ti < tags.topics.length; ti++) html += '<span class="tag-chip topic">' + escHtml(tags.topics[ti]) + '</span>';
      html += '</div></div>';
    }

    // Related nodes
    if (connections.length > 0) {
      html += '<div class="detail-section">';
      html += '<div class="detail-label">Related (' + connections.length + ')</div>';
      var maxShow = Math.min(connections.length, 20);
      for (var ri = 0; ri < maxShow; ri++) {
        var rel = connections[ri];
        var relNode = nodeById[rel.nodeId];
        if (!relNode) continue;
        html += '<div class="related-node" data-id="' + escHtml(rel.nodeId) + '">';
        html += '<span class="related-dot" style="background:' + relNode.color + '"></span>';
        html += '<span class="related-label">' + escHtml(relNode.label) + '</span>';
        html += '<span class="related-sim">' + (rel.sim * 100).toFixed(0) + '%</span>';
        html += '</div>';
      }
      html += '</div>';
    }

    // Content
    html += '<div class="detail-section">';
    html += '<div class="detail-label">Content</div>';
    html += '<div class="content-preview">' + escHtml(n.content) + '</div>';
    html += '</div>';

    document.getElementById('detail-title').textContent = 'Memory Details';
    document.getElementById('detail-body').innerHTML = html;
    document.getElementById('detail').style.display = 'block';

    // Click handlers for related nodes
    document.querySelectorAll('.related-node[data-id]').forEach(function(el) {
      el.addEventListener('click', function() {
        var id = this.getAttribute('data-id');
        selectedNode = id;
        renderer.refresh();
        showDetail(id);
        // Animate camera to node
        var pos = renderer.getNodeDisplayData(id);
        if (pos) renderer.getCamera().animate({ x: pos.x, y: pos.y, ratio: 0.3 }, { duration: 600 });
      });
    });
  }

  function closeDetail() {
    document.getElementById('detail').style.display = 'none';
    if (selectedNode) { selectedNode = null; if (renderer) renderer.refresh(); }
  }

  // ── Search ───────────────────────────────────────────────────
  var searchTimer;
  document.getElementById('q').addEventListener('input', function(e) {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(function() {
      var q = e.target.value.toLowerCase().trim();
      if (!q) { matchSet = null; if (renderer) renderer.refresh(); return; }
      matchSet = new Set();
      if (DATA) DATA.nodes.forEach(function(n) {
        if ((n.content && n.content.toLowerCase().indexOf(q) !== -1) ||
            (n.label && n.label.toLowerCase().indexOf(q) !== -1) ||
            (n.project && n.project.toLowerCase().indexOf(q) !== -1) ||
            (n.type && n.type.toLowerCase().indexOf(q) !== -1) ||
            (n.source && n.source.toLowerCase().indexOf(q) !== -1)) {
          matchSet.add(n);
        }
      });
      if (renderer) renderer.refresh();
    }, 150);
  });

  // ── Legend ────────────────────────────────────────────────────
  function buildLegend() {
    if (!DATA) return;
    var el = document.getElementById('legend');
    el.innerHTML = '<h2>Projects</h2>';

    var projCounts = {};
    DATA.nodes.forEach(function(n) { projCounts[n.project] = (projCounts[n.project] || 0) + 1; });

    DATA.projects.forEach(function(p) {
      var d = document.createElement('div');
      d.className = 'lg' + (activeProjectFilter === p ? ' active' : '');
      d.innerHTML = '<span class="dot" style="background:' + DATA.projectColors[p] + '"></span>'
        + escHtml(p) + '<span class="count">' + (projCounts[p] || 0) + '</span>';
      d.onclick = function() {
        activeProjectFilter = (activeProjectFilter === p) ? null : p;
        buildLegend();
        if (renderer) renderer.refresh();
      };
      el.appendChild(d);
    });

    var sep = document.createElement('h2');
    sep.style.marginTop = '14px';
    sep.textContent = 'Types';
    el.appendChild(sep);

    var typeCounts = {};
    DATA.nodes.forEach(function(n) { typeCounts[n.type] = (typeCounts[n.type] || 0) + 1; });

    DATA.types.forEach(function(t) {
      if (!typeCounts[t]) return;
      var d = document.createElement('div');
      d.className = 'lg';
      d.style.cursor = 'default';
      d.innerHTML = '<span class="dot" style="background:' + (DATA.typeColors[t] || '#888') + '"></span>'
        + escHtml(t) + '<span class="count">' + (typeCounts[t] || 0) + '</span>';
      el.appendChild(d);
    });
  }

  // ── Stats ────────────────────────────────────────────────────
  function updateStats() {
    if (!DATA) return;
    document.getElementById('stats').textContent =
      DATA.total + ' memories \u00b7 ' + DATA.links.length + ' links \u00b7 ' + DATA.projects.length + ' projects';
  }

  // ── Load data ────────────────────────────────────────────────
  function loadData() {
    fetch('/api/data')
      .then(function(r) { return r.json(); })
      .then(function(data) {
        currentVersion = data.version;
        var g = buildGraph(data);
        createRenderer(g);
        startLayout(g);
        buildLegend();
        updateStats();
      })
      .catch(function(err) { console.error('Failed to load data:', err); });
  }

  // ── SSE live updates ─────────────────────────────────────────
  function connectSSE() {
    var es = new EventSource('/api/stream');
    es.addEventListener('update', function(e) {
      var info = JSON.parse(e.data);
      if (info.version !== currentVersion) {
        fetch('/api/data')
          .then(function(r) { return r.json(); })
          .then(function(data) {
            // Save existing positions
            var oldPos = {};
            if (graph) {
              graph.forEachNode(function(node) {
                oldPos[node] = { x: graph.getNodeAttribute(node, 'x'), y: graph.getNodeAttribute(node, 'y') };
              });
            }

            // Tear down old
            if (fa2 && fa2.isRunning()) fa2.stop();
            if (renderer) renderer.kill();

            currentVersion = data.version;
            var g = buildGraph(data);

            // Restore positions for existing nodes
            g.forEachNode(function(node) {
              if (oldPos[node]) {
                g.setNodeAttribute(node, 'x', oldPos[node].x);
                g.setNodeAttribute(node, 'y', oldPos[node].y);
              }
            });

            createRenderer(g);
            startLayout(g);
            buildLegend();
            updateStats();
          });
      }
    });
    es.onerror = function() { es.close(); setTimeout(connectSSE, 3000); };
  }

  // ── Keyboard shortcuts ───────────────────────────────────────
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
      closeDetail();
      if (activeProjectFilter) { activeProjectFilter = null; buildLegend(); if (renderer) renderer.refresh(); }
      if (matchSet) { matchSet = null; document.getElementById('q').value = ''; if (renderer) renderer.refresh(); }
    }
    if (e.key === '/' && document.activeElement.tagName !== 'INPUT') {
      e.preventDefault();
      document.getElementById('q').focus();
    }
  });

  // ── Init ─────────────────────────────────────────────────────
  loadData();
  connectSSE();

  window.closeDetail = closeDetail;

})();
</script>
</body>
</html>"""


class VizHandler(http.server.BaseHTTPRequestHandler):
    data_cache = None

    def do_GET(self):
        if self.path == "/api/data":
            if VizHandler.data_cache is None:
                VizHandler.data_cache = build_data()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(VizHandler.data_cache).encode())

        elif self.path == "/api/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                while True:
                    if check_for_changes():
                        VizHandler.data_cache = build_data()
                        msg = json.dumps({"version": _data_version, "total": VizHandler.data_cache["total"]})
                        self.wfile.write(f"event: update\ndata: {msg}\n\n".encode())
                        self.wfile.flush()
                    else:
                        self.wfile.write(f": heartbeat\n\n".encode())
                        self.wfile.flush()
                    time.sleep(2)
            except (BrokenPipeError, ConnectionResetError):
                pass

        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode())

    def log_message(self, format, *args):
        pass


def launch_app_window(url: str):
    chrome_flags = [
        f"--app={url}",
        "--window-size=800,600",
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


def main():
    global _last_wal_size, _last_row_count
    port = DEFAULT_PORT
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--port" and i + 1 < len(args):
            port = int(args[i + 1])

    print(f"\n  \033[0;36mBuilding visualization data...\033[0m")
    VizHandler.data_cache = build_data()
    total = VizHandler.data_cache["total"]
    projects = len(VizHandler.data_cache["projects"])
    links = len(VizHandler.data_cache.get("links", []))

    wal_path = config.get_data_dir() / "wal.jsonl"
    try:
        _last_wal_size = os.path.getsize(wal_path) if wal_path.exists() else 0
    except OSError:
        _last_wal_size = 0
    _last_row_count = total

    print(f"  {total} memories, {links} links, {projects} projects")

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), VizHandler)
    url = f"http://127.0.0.1:{port}"
    print(f"\n  \033[0;33m✦ Imprint Graph running at {url}\033[0m")
    print(f"  \033[2mLive updates enabled · Press Ctrl+C to stop\033[0m\n")

    threading.Timer(0.5, lambda: launch_app_window(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
        server.shutdown()


if __name__ == "__main__":
    main()
