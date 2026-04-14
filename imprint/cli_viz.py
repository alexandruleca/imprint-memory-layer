"""2D scatter visualization of imprint memory.

Pre-computes layout server-side (t-SNE or PCA). Renders with Canvas 2D.
Zero JS dependencies. Handles 50k+ nodes at 60fps.

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
    """Pull every point from Qdrant with its vector. Streams via scroll."""
    try:
        client, coll = vs._ensure_collection()
    except Exception:
        return []

    try:
        info = client.get_collection(coll)
        if (info.points_count or 0) == 0:
            return []
    except Exception:
        return []

    rows: list[dict] = []
    offset = None
    try:
        while True:
            pts, offset = client.scroll(
                collection_name=coll,
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


def compute_layout_2d(vectors):
    """Reduce embeddings to 2D positions via t-SNE (sklearn) or PCA fallback."""
    vecs = np.array(vectors, dtype=np.float32)
    n = len(vecs)
    if n < 2:
        return np.zeros((n, 2), dtype=np.float32)

    try:
        from sklearn.decomposition import PCA
        from sklearn.manifold import TSNE
        n_pca = min(50, vecs.shape[1], n - 1)
        reduced = PCA(n_components=n_pca).fit_transform(vecs)
        perp = min(30, max(5, n // 5))
        pos = TSNE(
            n_components=2, perplexity=perp, init="pca",
            random_state=42, max_iter=500, learning_rate="auto",
        ).fit_transform(reduced)
    except ImportError:
        centered = vecs - vecs.mean(axis=0)
        _, _, Vt = np.linalg.svd(centered, full_matrices=False)
        pos = centered @ Vt[:2].T

    mins = pos.min(axis=0)
    maxs = pos.max(axis=0)
    span = np.maximum(maxs - mins, 1e-8)
    pos = (pos - mins) / span * 1000 - 500
    return pos


def compute_neighbors(vectors, k=5):
    """Cosine KNN — returns per-node neighbor lists."""
    vecs = np.array(vectors, dtype=np.float32)
    n = len(vecs)
    if n < 2:
        return [[] for _ in range(n)]

    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    vecs = vecs / np.maximum(norms, 1e-8)
    k = min(k, n - 1)

    neighbors: list[list[dict]] = [[] for _ in range(n)]
    batch = 2000
    for start in range(0, n, batch):
        end = min(start + batch, n)
        sims = vecs[start:end] @ vecs.T
        for bi in range(end - start):
            i = start + bi
            sims[bi, i] = -2.0
            top_k = np.argpartition(sims[bi], -k)[-k:]
            for j in top_k:
                neighbors[i].append({"idx": int(j), "sim": round(float(sims[bi, int(j)]), 3)})

    return neighbors


_layout_cache: dict[str, tuple[float, float]] = {}


def build_data():
    global _layout_cache
    rows = get_all_rows()
    if not rows:
        return {"nodes": [], "projects": [], "types": list(TYPE_COLORS.keys()),
                "projectColors": {}, "typeColors": TYPE_COLORS, "total": 0, "version": 0}

    vectors = [r["vector"] for r in rows]

    # Generate stable node IDs
    nids = []
    for i, r in enumerate(rows):
        nid = r.get("id") or hashlib.md5(
            f"{i}|{r.get('source', '')}|{r.get('project', '')}|{r.get('type', '')}|{r.get('content', '')[:200]}".encode()
        ).hexdigest()[:12]
        nids.append(nid)

    # Layout: full t-SNE or incremental
    cached = sum(1 for nid in nids if nid in _layout_cache)
    if cached < len(rows) * 0.8 or not _layout_cache:
        print(f"  \033[2mComputing 2D layout for {len(rows)} memories...\033[0m")
        positions = compute_layout_2d(vectors)
        _layout_cache.clear()
        for i in range(len(rows)):
            _layout_cache[nids[i]] = (round(float(positions[i, 0]), 2), round(float(positions[i, 1]), 2))
    else:
        positions = np.zeros((len(rows), 2), dtype=np.float32)
        new_indices = []
        for i, nid in enumerate(nids):
            if nid in _layout_cache:
                positions[i] = _layout_cache[nid]
            else:
                new_indices.append(i)
        if new_indices:
            vecs = np.array(vectors, dtype=np.float32)
            norms_all = np.linalg.norm(vecs, axis=1, keepdims=True)
            normed = vecs / np.maximum(norms_all, 1e-8)
            new_set = set(new_indices)
            for i in new_indices:
                sims = normed[i] @ normed.T
                sims[i] = -2.0
                top5 = np.argpartition(sims, -5)[-5:]
                placed = [int(j) for j in top5 if int(j) not in new_set]
                if placed:
                    positions[i, 0] = np.mean(positions[placed, 0]) + np.random.randn() * 3
                    positions[i, 1] = np.mean(positions[placed, 1]) + np.random.randn() * 3
                else:
                    positions[i] = np.random.randn(2) * 50
                _layout_cache[nids[i]] = (round(float(positions[i, 0]), 2), round(float(positions[i, 1]), 2))

    print(f"  \033[2mComputing neighbors...\033[0m")
    all_neighbors = compute_neighbors(vectors, k=5)

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
        nb = all_neighbors[i]
        nodes.append({
            "id": nids[i],
            "idx": i,
            "x": round(float(positions[i, 0]), 2) if isinstance(positions, np.ndarray) else _layout_cache[nids[i]][0],
            "y": round(float(positions[i, 1]), 2) if isinstance(positions, np.ndarray) else _layout_cache[nids[i]][1],
            "project": project,
            "type": mem_type,
            "source": source,
            "label": label,
            "content": content[:500],
            "color": pc.get(project, "#ffa500"),
            "tags": tags,
            "val": 1 + len(nb),
            "neighbors": nb,
        })

    return {
        "nodes": nodes, "projects": projects,
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


HTML_PAGE = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Imprint Graph</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: #1a1a2e; color: #dcddde; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; overflow: hidden; }

  #canvas { display: block; width: 100vw; height: 100vh; }

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

  .detail-section { padding: 12px 20px; border-bottom: 1px solid rgba(255,255,255,0.03); }
  .detail-section:last-child { border-bottom: none; }
  .detail-label { font-size: 9px; text-transform: uppercase; letter-spacing: 1.2px; color: rgba(220,221,222,0.35); margin-bottom: 6px; font-weight: 500; }
  .detail-value { font-size: 12px; color: rgba(220,221,222,0.8); line-height: 1.5; }
  .detail-value.mono { font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace; font-size: 11px; }

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

  .related-node {
    display: flex; align-items: center; gap: 8px; padding: 6px 8px;
    border-radius: 6px; cursor: pointer; transition: background 0.15s;
    margin-bottom: 2px;
  }
  .related-node:hover { background: rgba(255,255,255,0.04); }
  .related-dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
  .related-label { font-size: 11px; color: rgba(220,221,222,0.7); flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .related-sim { font-size: 10px; color: rgba(78,205,196,0.5); flex-shrink: 0; font-family: 'SF Mono', monospace; }

  .content-preview {
    font-size: 11px; line-height: 1.6; color: rgba(220,221,222,0.7);
    white-space: pre-wrap; font-family: 'SF Mono', 'Fira Code', monospace;
    max-height: 200px; overflow-y: auto;
    background: rgba(0,0,0,0.15); border-radius: 6px; padding: 10px 12px;
    margin-top: 4px;
  }

  .meta-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }

  #loading {
    position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%); z-index: 100;
    background: rgba(26, 26, 46, 0.8); border: 1px solid rgba(78,205,196,0.15);
    border-radius: 16px; padding: 6px 14px; font-size: 11px; color: rgba(78,205,196,0.7);
    transition: opacity 0.5s; pointer-events: none;
  }

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

<div id="loading">Loading...</div>

<canvas id="canvas"></canvas>

<script>
(function() {
  'use strict';

  var DATA = null;
  var currentVersion = 0;
  var camera = { x: 0, y: 0, zoom: 1 };
  var displayW = 0, displayH = 0;
  var canvas, ctx;
  var qtree = null;
  var hoveredNode = null;
  var selectedNode = null;
  var activeProjectFilter = null;
  var matchSet = null;
  var dirty = true;
  var dragging = false;
  var dragLast = null;
  var didDrag = false;
  var animTarget = null;
  var nbSet = null;

  // ── Helpers ──────────────────────────────────────────────────
  function hexA(hex, a) {
    return 'rgba(' + parseInt(hex.slice(1,3),16) + ',' + parseInt(hex.slice(3,5),16) + ',' + parseInt(hex.slice(5,7),16) + ',' + a + ')';
  }
  function esc(s) { return s ? s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;') : ''; }
  function s2w(sx, sy) { return { x: (sx - displayW/2) / camera.zoom + camera.x, y: (sy - displayH/2) / camera.zoom + camera.y }; }

  // ── Quadtree ─────────────────────────────────────────────────
  function QT(x, y, w, h) { this.x=x; this.y=y; this.w=w; this.h=h; this.pts=[]; this.ch=null; }
  QT.prototype.insert = function(px, py, d) {
    if (px < this.x || px >= this.x+this.w || py < this.y || py >= this.y+this.h) return;
    if (!this.ch && this.pts.length < 8) { this.pts.push({x:px,y:py,d:d}); return; }
    if (!this.ch) {
      var hw=this.w/2, hh=this.h/2;
      this.ch=[new QT(this.x,this.y,hw,hh),new QT(this.x+hw,this.y,hw,hh),
               new QT(this.x,this.y+hh,hw,hh),new QT(this.x+hw,this.y+hh,hw,hh)];
      for (var i=0;i<this.pts.length;i++)
        for (var j=0;j<4;j++) this.ch[j].insert(this.pts[i].x,this.pts[i].y,this.pts[i].d);
      this.pts=[];
    }
    for (var i=0;i<4;i++) this.ch[i].insert(px,py,d);
  };
  QT.prototype.nearest = function(qx,qy,r) {
    var best={d:null,dist:r*r}; this._f(qx,qy,best); return best.d;
  };
  QT.prototype._f = function(qx,qy,best) {
    var cx=Math.max(this.x,Math.min(qx,this.x+this.w));
    var cy=Math.max(this.y,Math.min(qy,this.y+this.h));
    if ((cx-qx)*(cx-qx)+(cy-qy)*(cy-qy) > best.dist) return;
    for (var i=0;i<this.pts.length;i++) {
      var dx=this.pts[i].x-qx, dy=this.pts[i].y-qy, d2=dx*dx+dy*dy;
      if (d2 < best.dist) { best.dist=d2; best.d=this.pts[i].d; }
    }
    if (this.ch) for (var i=0;i<4;i++) this.ch[i]._f(qx,qy,best);
  };

  // ── Quadtree build ──────────────────────────────────────────
  function buildQT() {
    if (!DATA || !DATA.nodes.length) { qtree = null; return; }
    var x1=Infinity,x2=-Infinity,y1=Infinity,y2=-Infinity;
    for (var i=0;i<DATA.nodes.length;i++) {
      var n=DATA.nodes[i];
      if (n.x<x1) x1=n.x; if (n.x>x2) x2=n.x;
      if (n.y<y1) y1=n.y; if (n.y>y2) y2=n.y;
    }
    var pad=10;
    qtree = new QT(x1-pad, y1-pad, (x2-x1)+pad*2, (y2-y1)+pad*2);
    for (var i=0;i<DATA.nodes.length;i++) {
      var n=DATA.nodes[i];
      qtree.insert(n.x, n.y, n);
    }
  }

  // ── Camera animation ────────────────────────────────────────
  function animateCam(target, dur) {
    animTarget = {
      fx: camera.x, fy: camera.y, fz: camera.zoom,
      tx: target.x, ty: target.y, tz: target.zoom,
      t0: performance.now(), dur: dur || 600
    };
    markDirty();
  }
  function tickAnim() {
    if (!animTarget) return false;
    var t = (performance.now() - animTarget.t0) / animTarget.dur;
    if (t >= 1) {
      camera.x=animTarget.tx; camera.y=animTarget.ty; camera.zoom=animTarget.tz;
      animTarget=null; return true;
    }
    t = 1 - Math.pow(1-t, 3);
    camera.x = animTarget.fx + (animTarget.tx - animTarget.fx) * t;
    camera.y = animTarget.fy + (animTarget.ty - animTarget.fy) * t;
    camera.zoom = animTarget.fz + (animTarget.tz - animTarget.fz) * t;
    return true;
  }

  // ── Neighbor set for active node ────────────────────────────
  function updateNbSet() {
    var active = selectedNode || hoveredNode;
    if (!active) { nbSet = null; return; }
    nbSet = new Set();
    var nb = active.neighbors || [];
    for (var i=0;i<nb.length;i++) nbSet.add(nb[i].idx);
  }

  // ── Dirty-flag rendering ────────────────────────────────────
  function markDirty() { if (!dirty) { dirty=true; requestAnimationFrame(render); } }

  function render() {
    dirty = false;
    var anim = tickAnim();

    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = '#1a1a2e';
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    if (!DATA || !DATA.nodes.length) { if (anim) markDirty(); return; }

    var hw = displayW/2, hh = displayH/2;
    var z = camera.zoom, cx = camera.x, cy = camera.y;
    var active = selectedNode || hoveredNode;
    var hasFilter = !!(activeProjectFilter || matchSet);

    // ── Edges (only for active node) ──
    if (active) {
      var nb = active.neighbors || [];
      var ax = hw + (active.x - cx) * z, ay = hh + (active.y - cy) * z;
      ctx.lineWidth = 1;
      for (var i=0;i<nb.length;i++) {
        var tgt = DATA.nodes[nb[i].idx];
        if (!tgt) continue;
        ctx.strokeStyle = 'rgba(78,205,196,' + (0.15 + nb[i].sim * 0.45).toFixed(3) + ')';
        ctx.beginPath();
        ctx.moveTo(ax, ay);
        ctx.lineTo(hw + (tgt.x - cx) * z, hh + (tgt.y - cy) * z);
        ctx.stroke();
      }
    }

    // ── Nodes ──
    for (var i=0;i<DATA.nodes.length;i++) {
      var n = DATA.nodes[i];
      var sx = hw + (n.x - cx) * z, sy = hh + (n.y - cy) * z;
      var r = Math.max(1.5, Math.sqrt(n.val) * Math.min(z, 4) * 0.9);

      if (sx + r < 0 || sx - r > displayW || sy + r < 0 || sy - r > displayH) continue;

      var a = 0.85;
      if (hasFilter) {
        if (activeProjectFilter && n.project !== activeProjectFilter) a = 0.06;
        if (matchSet && !matchSet.has(n)) a = 0.06;
      }
      if (active && a > 0.06) {
        if (n === active) a = 1.0;
        else if (nbSet && nbSet.has(n.idx)) a = 0.9;
        else if (!hasFilter) a = 0.1;
      }

      ctx.beginPath();
      ctx.arc(sx, sy, r, 0, 6.2832);
      ctx.fillStyle = hexA(n.color, a);
      ctx.fill();
    }

    // ── Rings ──
    if (hoveredNode) {
      var hsx = hw + (hoveredNode.x - cx)*z, hsy = hh + (hoveredNode.y - cy)*z;
      var hr = Math.max(1.5, Math.sqrt(hoveredNode.val)*Math.min(z,4)*0.9) + 3;
      ctx.beginPath(); ctx.arc(hsx, hsy, hr, 0, 6.2832);
      ctx.strokeStyle = '#fff'; ctx.lineWidth = 2; ctx.stroke();
    }
    if (selectedNode && selectedNode !== hoveredNode) {
      var ssx = hw + (selectedNode.x - cx)*z, ssy = hh + (selectedNode.y - cy)*z;
      var sr = Math.max(1.5, Math.sqrt(selectedNode.val)*Math.min(z,4)*0.9) + 3;
      ctx.beginPath(); ctx.arc(ssx, ssy, sr, 0, 6.2832);
      ctx.strokeStyle = '#4ecdc4'; ctx.lineWidth = 2; ctx.stroke();
    }

    // ── Labels (zoom-dependent) ──
    if (z > 2.5) {
      ctx.font = '10px -apple-system,BlinkMacSystemFont,sans-serif';
      ctx.fillStyle = 'rgba(220,221,222,0.7)';
      ctx.textBaseline = 'middle';
      for (var i=0;i<DATA.nodes.length;i++) {
        var n = DATA.nodes[i];
        var sx = hw + (n.x - cx)*z, sy = hh + (n.y - cy)*z;
        if (sx < -50 || sx > displayW+50 || sy < -20 || sy > displayH+20) continue;
        if (activeProjectFilter && n.project !== activeProjectFilter) continue;
        if (matchSet && !matchSet.has(n)) continue;
        var r = Math.max(1.5, Math.sqrt(n.val)*Math.min(z,4)*0.9);
        if (r > 3 || z > 5) ctx.fillText(n.label.substring(0,40), sx + r + 4, sy);
      }
    }

    // ── Active label (always shown) ──
    if (active) {
      var alx = hw + (active.x - cx)*z, aly = hh + (active.y - cy)*z;
      var alr = Math.max(1.5, Math.sqrt(active.val)*Math.min(z,4)*0.9);
      ctx.font = 'bold 12px -apple-system,BlinkMacSystemFont,sans-serif';
      ctx.fillStyle = '#e0e0e0';
      ctx.textBaseline = 'middle';
      ctx.fillText(active.label, alx + alr + 5, aly);
    }

    if (anim) markDirty();
  }

  // ── Fit camera to data ──────────────────────────────────────
  function fitAll(dur) {
    if (!DATA || !DATA.nodes.length) return;
    var x1=Infinity,x2=-Infinity,y1=Infinity,y2=-Infinity;
    for (var i=0;i<DATA.nodes.length;i++) {
      var n=DATA.nodes[i];
      if (n.x<x1) x1=n.x; if (n.x>x2) x2=n.x;
      if (n.y<y1) y1=n.y; if (n.y>y2) y2=n.y;
    }
    var span = Math.max(x2-x1, y2-y1, 1);
    var z = Math.min(displayW, displayH) / (span * 1.15);
    animateCam({x:(x1+x2)/2, y:(y1+y2)/2, zoom:z}, dur||400);
  }

  // ── Detail panel ─────────────────────────────────────────────
  function showDetail(n) {
    if (!n) return;
    var tags = n.tags || {};
    var connections = (n.neighbors || []).slice().sort(function(a,b) { return b.sim - a.sim; });

    var html = '';
    html += '<div class="detail-section">';
    html += '<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">';
    html += '<span style="width:10px;height:10px;border-radius:50%;background:' + n.color + '"></span>';
    html += '<span style="font-size:13px;font-weight:600;color:' + n.color + '">' + esc(n.project) + '</span>';
    html += '</div>';
    html += '<div class="detail-label">Label</div>';
    html += '<div class="detail-value">' + esc(n.label) + '</div>';
    html += '</div>';

    html += '<div class="detail-section"><div class="meta-grid">';
    html += '<div><div class="detail-label">Type</div><div class="detail-value"><span class="tag-chip type-tag">' + esc(n.type) + '</span></div></div>';
    html += '<div><div class="detail-label">Connections</div><div class="detail-value">' + connections.length + ' nodes</div></div>';
    if (n.source) {
      html += '<div style="grid-column:span 2"><div class="detail-label">Source</div><div class="detail-value mono">' + esc(n.source) + '</div></div>';
    }
    html += '</div></div>';

    var hasTag = tags.lang || tags.layer || tags.kind || (tags.domain && tags.domain.length) || (tags.topics && tags.topics.length);
    if (hasTag) {
      html += '<div class="detail-section"><div class="detail-label">Tags</div><div class="tag-chips">';
      if (tags.lang) html += '<span class="tag-chip lang">' + esc(tags.lang) + '</span>';
      if (tags.layer) html += '<span class="tag-chip layer">' + esc(tags.layer) + '</span>';
      if (tags.kind) html += '<span class="tag-chip kind">' + esc(tags.kind) + '</span>';
      if (tags.domain) for (var di=0;di<tags.domain.length;di++) html += '<span class="tag-chip domain">' + esc(tags.domain[di]) + '</span>';
      if (tags.topics) for (var ti=0;ti<tags.topics.length;ti++) html += '<span class="tag-chip topic">' + esc(tags.topics[ti]) + '</span>';
      html += '</div></div>';
    }

    if (connections.length > 0) {
      html += '<div class="detail-section"><div class="detail-label">Related (' + connections.length + ')</div>';
      var mx = Math.min(connections.length, 20);
      for (var ri=0;ri<mx;ri++) {
        var rel = connections[ri];
        var rn = DATA.nodes[rel.idx];
        if (!rn) continue;
        html += '<div class="related-node" data-idx="' + rel.idx + '">';
        html += '<span class="related-dot" style="background:' + rn.color + '"></span>';
        html += '<span class="related-label">' + esc(rn.label) + '</span>';
        html += '<span class="related-sim">' + (rel.sim * 100).toFixed(0) + '%</span>';
        html += '</div>';
      }
      html += '</div>';
    }

    html += '<div class="detail-section"><div class="detail-label">Content</div>';
    html += '<div class="content-preview">' + esc(n.content) + '</div></div>';

    document.getElementById('detail-title').textContent = 'Memory Details';
    document.getElementById('detail-body').innerHTML = html;
    document.getElementById('detail').style.display = 'block';

    document.querySelectorAll('.related-node[data-idx]').forEach(function(el) {
      el.addEventListener('click', function() {
        var idx = parseInt(this.getAttribute('data-idx'));
        var target = DATA.nodes[idx];
        if (!target) return;
        selectedNode = target;
        updateNbSet();
        showDetail(target);
        animateCam({x: target.x, y: target.y, zoom: Math.max(camera.zoom, 4)}, 600);
      });
    });
  }

  function closeDetail() {
    document.getElementById('detail').style.display = 'none';
    if (selectedNode) { selectedNode = null; updateNbSet(); markDirty(); }
  }

  // ── Search ───────────────────────────────────────────────────
  var searchTimer;
  document.getElementById('q').addEventListener('input', function(e) {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(function() {
      var q = e.target.value.toLowerCase().trim();
      if (!q) { matchSet = null; markDirty(); return; }
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
      markDirty();
    }, 150);
  });

  // ── Legend ────────────────────────────────────────────────────
  function buildLegend() {
    if (!DATA) return;
    var el = document.getElementById('legend');
    el.innerHTML = '<h2>Projects</h2>';
    var projCounts = {};
    DATA.nodes.forEach(function(n) { projCounts[n.project] = (projCounts[n.project]||0) + 1; });
    DATA.projects.forEach(function(p) {
      var d = document.createElement('div');
      d.className = 'lg' + (activeProjectFilter === p ? ' active' : '');
      d.innerHTML = '<span class="dot" style="background:' + DATA.projectColors[p] + '"></span>'
        + esc(p) + '<span class="count">' + (projCounts[p]||0) + '</span>';
      d.onclick = function() {
        activeProjectFilter = (activeProjectFilter === p) ? null : p;
        buildLegend();
        markDirty();
      };
      el.appendChild(d);
    });
    var sep = document.createElement('h2');
    sep.style.marginTop = '14px';
    sep.textContent = 'Types';
    el.appendChild(sep);
    var typeCounts = {};
    DATA.nodes.forEach(function(n) { typeCounts[n.type] = (typeCounts[n.type]||0) + 1; });
    DATA.types.forEach(function(t) {
      if (!typeCounts[t]) return;
      var d = document.createElement('div');
      d.className = 'lg'; d.style.cursor = 'default';
      d.innerHTML = '<span class="dot" style="background:' + (DATA.typeColors[t]||'#888') + '"></span>'
        + esc(t) + '<span class="count">' + (typeCounts[t]||0) + '</span>';
      el.appendChild(d);
    });
  }

  function updateStats() {
    if (!DATA) return;
    document.getElementById('stats').textContent =
      DATA.total + ' memories \u00b7 ' + DATA.projects.length + ' projects';
  }

  // ── Interaction ──────────────────────────────────────────────
  function initInteraction() {
    canvas.addEventListener('mousedown', function(e) {
      if (e.button === 0) { dragging = true; didDrag = false; dragLast = {x:e.offsetX, y:e.offsetY}; }
    });
    canvas.addEventListener('mouseup', function() { dragging = false; });
    canvas.addEventListener('mouseleave', function() { dragging = false; });

    canvas.addEventListener('mousemove', function(e) {
      if (dragging) {
        var dx = e.offsetX - dragLast.x, dy = e.offsetY - dragLast.y;
        if (Math.abs(dx) > 2 || Math.abs(dy) > 2) didDrag = true;
        camera.x -= dx / camera.zoom;
        camera.y -= dy / camera.zoom;
        dragLast = {x:e.offsetX, y:e.offsetY};
        markDirty();
        return;
      }
      var w = s2w(e.offsetX, e.offsetY);
      var hit = qtree ? qtree.nearest(w.x, w.y, 15/camera.zoom) : null;
      if (hit !== hoveredNode) {
        hoveredNode = hit;
        updateNbSet();
        canvas.style.cursor = hit ? 'pointer' : 'grab';
        markDirty();
      }
    });

    canvas.addEventListener('click', function(e) {
      if (didDrag) return;
      if (hoveredNode) {
        selectedNode = hoveredNode;
        updateNbSet();
        showDetail(selectedNode);
        markDirty();
      } else {
        selectedNode = null;
        updateNbSet();
        closeDetail();
        markDirty();
      }
    });

    canvas.addEventListener('dblclick', function(e) {
      var w = s2w(e.offsetX, e.offsetY);
      animateCam({x:w.x, y:w.y, zoom: camera.zoom * 2.5}, 400);
    });

    canvas.addEventListener('wheel', function(e) {
      e.preventDefault();
      var factor = e.deltaY > 0 ? 0.88 : 1.14;
      var before = s2w(e.offsetX, e.offsetY);
      camera.zoom = Math.max(0.05, Math.min(camera.zoom * factor, 100));
      var after = s2w(e.offsetX, e.offsetY);
      camera.x += before.x - after.x;
      camera.y += before.y - after.y;
      markDirty();
    }, {passive: false});
  }

  // ── Keyboard ─────────────────────────────────────────────────
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
      closeDetail();
      if (activeProjectFilter) { activeProjectFilter = null; buildLegend(); markDirty(); }
      if (matchSet) { matchSet = null; document.getElementById('q').value = ''; markDirty(); }
    }
    if (e.key === '/' && document.activeElement.tagName !== 'INPUT') {
      e.preventDefault();
      document.getElementById('q').focus();
    }
  });

  // ── Resize ───────────────────────────────────────────────────
  function resizeCanvas() {
    displayW = window.innerWidth;
    displayH = window.innerHeight;
    canvas.width = displayW;
    canvas.height = displayH;
    markDirty();
  }
  window.addEventListener('resize', resizeCanvas);

  // ── Load data ────────────────────────────────────────────────
  function loadData() {
    fetch('/api/data')
      .then(function(r) { return r.json(); })
      .then(function(data) {
        currentVersion = data.version;
        DATA = data;
        buildQT();
        buildLegend();
        updateStats();
        fitAll(600);
        var el = document.getElementById('loading');
        if (el) { el.style.opacity = '0'; setTimeout(function() { el.style.display = 'none'; }, 500); }
      })
      .catch(function(err) { console.error('Failed to load data:', err); });
  }

  // ── SSE ──────────────────────────────────────────────────────
  function connectSSE() {
    var es = new EventSource('/api/stream');
    es.addEventListener('update', function(e) {
      var info = JSON.parse(e.data);
      if (info.version !== currentVersion) {
        fetch('/api/data')
          .then(function(r) { return r.json(); })
          .then(function(data) {
            currentVersion = data.version;
            DATA = data;
            buildQT();
            buildLegend();
            updateStats();
            markDirty();
          });
      }
    });
    es.onerror = function() { es.close(); setTimeout(connectSSE, 3000); };
  }

  // ── Init ─────────────────────────────────────────────────────
  canvas = document.getElementById('canvas');
  ctx = canvas.getContext('2d');
  resizeCanvas();
  initInteraction();
  loadData();
  connectSSE();
  canvas.style.cursor = 'grab';

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
                        self.wfile.write(": heartbeat\n\n".encode())
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

    wp = config.wal_path()
    try:
        _last_wal_size = os.path.getsize(wp) if wp.exists() else 0
    except OSError:
        _last_wal_size = 0
    _last_row_count = total

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
