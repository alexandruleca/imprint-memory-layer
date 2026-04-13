"""3D brain cluster visualization of the knowledge base — Jarvis-style holographic sphere.

GPU-instanced points rendering for 2500+ nodes at 60fps.
Edges precomputed in Python/numpy. All animation in vertex shaders.

Usage: python -m knowledgebase.cli_viz [--port 8420]
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

from knowledgebase import config, vectorstore as vs

DEFAULT_PORT = 8420
SPHERE_RADIUS = 2.5

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
                # Qdrant returns dict of named vectors
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


_pca_basis = None


def pca_3d(vectors):
    global _pca_basis
    X = np.array(vectors, dtype=np.float32)
    n = len(X)
    # Sphere grows with node count so nodes stay spaced out
    R = SPHERE_RADIUS * max(1.0, (n / 500) ** 0.4)

    if _pca_basis is None:
        mean = X.mean(axis=0)
        X_c = X - mean
        cov = np.cov(X_c, rowvar=False)
        eigenvalues, eigenvectors = np.linalg.eigh(cov)
        top3 = np.argsort(eigenvalues)[::-1][:3]
        eigvecs = eigenvectors[:, top3]
        projected = X_c @ eigvecs
        norms = np.linalg.norm(projected, axis=1)
        max_norm = float(norms.max()) if norms.max() > 0 else 1.0
        _pca_basis = (mean, eigvecs, max_norm)
    else:
        mean, eigvecs, max_norm = _pca_basis
        projected = (X - mean) @ eigvecs

    norms = np.linalg.norm(projected, axis=1, keepdims=True)
    directions = projected / np.maximum(norms, 1e-8)
    norm_ratio = np.minimum(norms / max_norm, 1.5)
    radial_scale = 0.65 + np.power(norm_ratio, 0.6) * 0.35
    positions = directions * R * radial_scale
    return positions.tolist(), float(R)


def compute_edges(positions, k=3, max_dist=1.8):
    """KNN edges computed in numpy — O(n²) but vectorized and fast."""
    n = len(positions)
    if n < 2:
        return []
    pos = np.array(positions, dtype=np.float32)
    k = min(k, n - 1)

    # Pairwise squared distances: ||a-b||^2 = ||a||^2 + ||b||^2 - 2a·b
    norms_sq = (pos ** 2).sum(axis=1)
    dist_sq = norms_sq[:, None] + norms_sq[None, :] - 2.0 * (pos @ pos.T)
    np.fill_diagonal(dist_sq, np.inf)
    max_dist_sq = max_dist * max_dist

    edges = set()
    nearest_k = np.argpartition(dist_sq, k, axis=1)[:, :k]  # (n, k)
    for i in range(n):
        for j_idx in range(k):
            j = int(nearest_k[i, j_idx])
            if dist_sq[i, j] > max_dist_sq:
                continue
            edges.add((min(i, j), max(i, j)))

    return [list(e) for e in edges]


def build_data():
    rows = get_all_rows()
    if not rows:
        return {"nodes": [], "edges": [], "projects": [], "types": list(TYPE_COLORS.keys()),
                "projectColors": {}, "typeColors": TYPE_COLORS, "total": 0, "version": 0}

    vectors = [r["vector"] for r in rows]
    positions, radius = pca_3d(vectors)

    projects = sorted(set(r.get("project", "") or "(none)" for r in rows))
    # Hash project name for a deterministic, stable color index — so colors don't
    # shift when projects are added/removed and the sort order changes.
    def _project_color(name: str) -> str:
        h = int(hashlib.md5(name.encode()).hexdigest(), 16)
        return PROJECT_COLORS[h % len(PROJECT_COLORS)]
    pc = {p: _project_color(p) for p in projects}

    nodes = []
    for i, r in enumerate(rows):
        project = r.get("project", "") or "(none)"
        mem_type = r.get("type", "finding")
        content = r.get("content", "")
        source = r.get("source", "")
        label = ""
        for line in content.split("\n"):
            line = line.strip()
            if line and len(line) > 10 and not line.startswith(("[", "import ", "from ", "#", "---", "```")):
                label = line[:80]
                break
        nid = hashlib.md5(f"{source}|{project}|{mem_type}|{content[:200]}".encode()).hexdigest()[:12]
        nodes.append({
            "id": nid,
            "x": positions[i][0], "y": positions[i][1], "z": positions[i][2],
            "project": project, "type": mem_type, "source": source,
            "label": label or source or f"mem-{i}",
            "content": content[:500],
            "color": pc.get(project, "#ffa500"),
        })

    # Scale edge distance with sphere radius; use avg NN distance heuristic
    avg_nn = radius * np.sqrt(4 * np.pi / max(len(nodes), 1))
    edges = compute_edges(positions, k=3, max_dist=max(avg_nn * 4, 1.0))

    return {
        "nodes": nodes, "edges": edges, "radius": radius, "projects": projects,
        "types": list(TYPE_COLORS.keys()),
        "projectColors": pc, "typeColors": TYPE_COLORS, "total": len(nodes),
        "version": int(time.time()),
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
<title>Knowledge Core</title>
<style>
  *{margin:0;padding:0;box-sizing:border-box}
  body{background:#020205;color:#d0c8a0;font-family:'Inter','SF Pro',-apple-system,sans-serif;overflow:hidden}
  #info{position:fixed;top:20px;left:20px;z-index:100;background:rgba(10,8,4,0.7);border:1px solid rgba(255,180,40,0.08);border-radius:14px;padding:14px 18px;backdrop-filter:blur(24px)}
  #info h1{font-size:11px;color:rgba(255,200,60,0.8);margin-bottom:6px;letter-spacing:3px;font-weight:500}
  #info .stat{font-size:10px;color:rgba(200,180,120,0.4);font-weight:300}
  #live-dot{display:inline-block;width:4px;height:4px;border-radius:50%;background:#ffc800;margin-right:6px;box-shadow:0 0 8px rgba(255,200,0,0.5);animation:breathe 3s ease-in-out infinite}
  @keyframes breathe{0%,100%{opacity:0.9;transform:scale(1)}50%{opacity:0.35;transform:scale(0.75)}}
  #legend{position:fixed;top:20px;right:20px;z-index:100;background:rgba(10,8,4,0.7);border:1px solid rgba(255,180,40,0.06);border-radius:14px;padding:14px;backdrop-filter:blur(24px);max-height:70vh;overflow-y:auto}
  #legend h2{font-size:8px;color:rgba(200,170,80,0.4);margin-bottom:8px;text-transform:uppercase;letter-spacing:3px;font-weight:400}
  .lg{font-size:10px;margin:4px 0;cursor:pointer;opacity:0.4;transition:all 0.4s ease;font-weight:300}
  .lg:hover{opacity:1}
  .lg .d{display:inline-block;width:5px;height:5px;border-radius:50%;margin-right:6px;vertical-align:middle}
  #search{position:fixed;top:20px;left:50%;transform:translateX(-50%);z-index:100}
  #search input{background:rgba(10,8,4,0.7);border:1px solid rgba(255,180,40,0.08);border-radius:20px;color:#d0c8a0;padding:8px 16px;width:240px;font-size:10px;font-family:inherit;font-weight:300;outline:none;backdrop-filter:blur(24px);transition:all 0.4s ease}
  #search input:focus{border-color:rgba(255,180,40,0.25);box-shadow:0 0 30px rgba(255,180,40,0.04);width:280px}
  #search input::placeholder{color:rgba(200,170,80,0.25)}
  #detail{position:fixed;bottom:20px;left:20px;right:20px;z-index:100;background:rgba(10,8,4,0.88);border:1px solid rgba(255,180,40,0.1);border-radius:14px;padding:16px 20px;max-height:200px;overflow-y:auto;display:none;backdrop-filter:blur(24px);animation:slideUp 0.35s ease}
  @keyframes slideUp{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:translateY(0)}}
  #detail .m{font-size:9px;color:rgba(255,200,60,0.6);margin-bottom:6px;letter-spacing:0.5px;font-weight:400}
  #detail .c{font-size:10px;white-space:pre-wrap;line-height:1.5;color:rgba(200,180,120,0.5);font-weight:300}
  #detail .x{position:absolute;top:8px;right:12px;cursor:pointer;color:rgba(200,170,80,0.3);font-size:16px;transition:color 0.3s}
  #detail .x:hover{color:rgba(255,220,100,0.7)}
  canvas{display:block}
  ::-webkit-scrollbar{width:3px}
  ::-webkit-scrollbar-track{background:transparent}
  ::-webkit-scrollbar-thumb{background:rgba(255,180,40,0.1);border-radius:2px}
</style>
</head>
<body>
<div id="info"><h1>KNOWLEDGE CORE</h1><div class="stat"><span id="live-dot"></span><span id="total">0</span> memories &middot; <span id="pcount">0</span> projects</div></div>
<div id="search"><input type="text" placeholder="Filter..." id="q"></div>
<div id="legend"></div>
<div id="detail"><span class="x" onclick="this.parentElement.style.display='none'">&times;</span><div class="m" id="dm"></div><div class="c" id="dc"></div></div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/shaders/CopyShader.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/shaders/LuminosityHighPassShader.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/postprocessing/EffectComposer.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/postprocessing/RenderPass.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/postprocessing/UnrealBloomPass.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/postprocessing/ShaderPass.js"></script>
<script>
var SPHERE_R = 2.5;

// ================================================================
//  State — all rendering uses shared typed arrays, not individual meshes
// ================================================================
var DATA, scene, cam, ren, ctrl, composer, ray, mouse;
var worldGroup = null;
var nodeSystem = null, edgeMesh = null, impulseSystem = null;
var moteSystem = null;
var orbitalRings = [], shellMesh = null;
var nodeDataArray = [];  // parallel to Points geometry
var oldNodeInfo = {};    // id → {phase, birthTime} for incremental persistence
var activeFilterFn = null; // persists across data reloads
var edgeFadeStart = 0;   // time when edges should begin fading in
var currentVersion = 0;

// ================================================================
//  Shaders — all node animation runs on the GPU
// ================================================================
var nodePointVert = [
  'attribute vec3 aColor;',
  'attribute float aSize;',
  'attribute float aPhase;',
  'attribute float aBirthTime;',
  'attribute float aVisible;',
  'uniform float uTime;',
  'uniform float uNow;',
  'varying vec3 vColor;',
  'varying float vAlpha;',
  'varying float vVisible;',
  '',
  'void main() {',
  '  float age = uNow - aBirthTime;',
  '  float f = clamp(age / 0.8, 0.0, 1.0);',
  '  float fade = f * f * (3.0 - 2.0 * f);',
  '',
  '  float ph = aPhase;',
  '  float t = uTime;',
  '  float amp = 0.005;',
  '  vec3 pos = position;',
  '  pos.x += (sin(pos.x*1.7+t*0.31+ph)*cos(pos.y*2.3-t*0.19)*0.5',
  '           + sin(pos.y*3.1-pos.z*1.7+t*0.47)*0.25) * amp;',
  '  pos.y += (cos(pos.z*2.9+pos.x*1.3+t*0.37+ph*1.3)*0.125',
  '           + sin(pos.x*1.9+t*0.28+ph*0.7)*0.25) * amp;',
  '  pos.z += (sin(pos.z*2.1+pos.y*1.5+t*0.33+ph*0.5)*0.25',
  '           + cos(pos.x*2.7-t*0.22)*0.125) * amp;',
  '',
  '  float scale = fade * (1.0 + 0.03 * sin(t * 1.5 + ph));',
  '  vec4 mvPos = modelViewMatrix * vec4(pos, 1.0);',
  '  gl_PointSize = max(aSize * scale * (50.0 / -mvPos.z), 1.0);',
  '  gl_Position = projectionMatrix * mvPos;',
  '',
  '  float pulse = sin(t * 0.8 + ph) * 0.5 + 0.5;',
  '  vColor = aColor;',
  '  vVisible = aVisible;',
  '  vAlpha = fade * mix(0.15, 1.0, aVisible) * (0.8 + 0.2 * pulse);',
  '}'
].join('\n');

var nodePointFrag = [
  'varying vec3 vColor;',
  'varying float vAlpha;',
  'varying float vVisible;',
  'void main() {',
  '  float d = length(gl_PointCoord - 0.5) * 2.0;',
  '  if (d > 1.0) discard;',
  '  float core = smoothstep(0.3, 0.0, d);',
  '  float glow = pow(max(1.0 - d, 0.0), 3.0) * 0.4;',
  '  vec3 warm = vec3(1.0, 0.95, 0.85);',
  '  vec3 col = mix(vColor, warm, core * 0.35);',
  '  float gray = dot(col, vec3(0.299, 0.587, 0.114));',
  '  col = mix(vec3(gray * 0.4), col, vVisible);',
  '  float brightness = core * 1.0 + glow;',
  '  float alpha = brightness * vAlpha;',
  '  if (alpha < 0.003) discard;',
  '  gl_FragColor = vec4(col * brightness, alpha);',
  '}'
].join('\n');

var moteVert = [
  'attribute float aSize;',
  'attribute float aPhase;',
  'attribute vec3 aColor;',
  'uniform float uTime;',
  'varying float vAlpha;',
  'varying vec3 vColor;',
  'void main() {',
  '  vec3 pos = position;',
  '  float t = uTime*0.04;',
  '  pos.x += sin(t+aPhase*6.28)*0.1;',
  '  pos.y += cos(t*1.3+aPhase*4.0)*0.08;',
  '  pos.z += sin(t*0.9+aPhase*5.0)*0.09;',
  '  vec4 mvPos = modelViewMatrix * vec4(pos, 1.0);',
  '  gl_PointSize = aSize * (150.0 / -mvPos.z);',
  '  gl_Position = projectionMatrix * mvPos;',
  '  vAlpha = 0.03 + 0.02*sin(uTime*0.2+aPhase*8.0);',
  '  vColor = aColor;',
  '}'
].join('\n');

var moteFrag = [
  'varying float vAlpha;',
  'varying vec3 vColor;',
  'void main() {',
  '  float d = length(gl_PointCoord-0.5)*2.0;',
  '  if(d>1.0) discard;',
  '  gl_FragColor = vec4(vColor, pow(1.0-d,2.0)*vAlpha);',
  '}'
].join('\n');

var VignetteShader = {
  uniforms: { tDiffuse:{value:null}, uIntensity:{value:1.2}, uSoftness:{value:0.5} },
  vertexShader: 'varying vec2 vUv; void main(){vUv=uv;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0);}',
  fragmentShader: [
    'uniform sampler2D tDiffuse; uniform float uIntensity; uniform float uSoftness; varying vec2 vUv;',
    'void main(){',
    '  vec4 c=texture2D(tDiffuse,vUv);',
    '  float dist=distance(vUv,vec2(0.5));',
    '  float vig=smoothstep(0.8,uSoftness,dist*uIntensity);',
    '  float ab=dist*0.0015;',
    '  c.r=texture2D(tDiffuse,vUv+vec2(ab,0.0)).r;',
    '  c.b=texture2D(tDiffuse,vUv-vec2(ab,0.0)).b;',
    '  c.rgb*=vig;',
    '  c.rgb=mix(c.rgb,c.rgb*vec3(1.06,0.98,0.88),0.15);',
    '  gl_FragColor=c;',
    '}'
  ].join('\n'),
};

// ── Electrical impulses along edges ──
var impulseVert = [
  'attribute vec3 aStart;',
  'attribute vec3 aEnd;',
  'attribute float aPhase;',
  'attribute float aSpeed;',
  'attribute vec3 aImpColor;',
  'uniform float uTime;',
  'varying float vAlpha;',
  'varying vec3 vColor;',
  'void main() {',
  // Fast, sharp traversal — each spark races across the edge then resets
  '  float raw = fract(uTime * aSpeed + aPhase);',
  // Sharpen the leading edge with a power curve
  '  float t = pow(raw, 0.6);',
  '  vec3 pos = mix(aStart, aEnd, t);',
  // Flicker: high-frequency noise makes it look electrical
  '  float flicker = 0.6 + 0.4 * sin(uTime * 47.0 + aPhase * 91.0);',
  // Sharp brightness spike at the leading front
  '  float spike = pow(raw, 8.0) * 4.0 + pow(1.0 - raw, 3.0) * 0.2;',
  '  spike = min(spike, 3.0) * flicker;',
  '  vec4 mvPos = modelViewMatrix * vec4(pos, 1.0);',
  '  gl_PointSize = max((0.8 + spike * 1.2) * (30.0 / -mvPos.z), 1.0);',
  '  gl_Position = projectionMatrix * mvPos;',
  '  vAlpha = spike;',
  '  vColor = aImpColor;',
  '}'
].join('\n');

var impulseFrag = [
  'varying float vAlpha;',
  'varying vec3 vColor;',
  'void main() {',
  '  float d = length(gl_PointCoord - 0.5) * 2.0;',
  '  if (d > 1.0) discard;',
  // Tight bright core — looks like a spark, not a soft blob
  '  float core = pow(max(1.0 - d, 0.0), 4.0);',
  '  vec3 hot = mix(vColor, vec3(1.0, 0.98, 0.9), core * 0.7);',
  '  gl_FragColor = vec4(hot * core * 2.5, core * vAlpha);',
  '}'
].join('\n');

// ================================================================
//  Node material (shared, created once)
// ================================================================
var nodeMaterial = null;
function getNodeMaterial() {
  if (!nodeMaterial) {
    nodeMaterial = new THREE.ShaderMaterial({
      uniforms: { uTime: {value:0}, uNow: {value:0} },
      vertexShader: nodePointVert,
      fragmentShader: nodePointFrag,
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    });
  }
  return nodeMaterial;
}

// ================================================================
//  Data loading
// ================================================================
function load() {
  return fetch('/api/data').then(function(r){return r.json();}).then(function(d) {
    DATA = d;
    currentVersion = DATA.version;
    document.getElementById('total').textContent = DATA.total;
    document.getElementById('pcount').textContent = DATA.projects.length;
    buildLegend();
  });
}

function buildLegend() {
  var el = document.getElementById('legend');
  el.innerHTML = '<h2>Projects</h2>';
  DATA.projects.forEach(function(p) {
    var d = document.createElement('div');
    d.className = 'lg';
    d.innerHTML = '<span class="d" style="background:'+DATA.projectColors[p]+';box-shadow:0 0 6px '+DATA.projectColors[p]+'40"></span>'+p;
    d.onclick = function(){filterProject(p);};
    el.appendChild(d);
  });
  var reset = document.createElement('div');
  reset.className = 'lg';
  reset.style.color = 'rgba(200,170,80,0.25)';
  reset.textContent = 'show all';
  reset.onclick = resetFilter;
  el.appendChild(reset);
}

// ================================================================
//  Scene init
// ================================================================
function init() {
  load().then(function() {
    SPHERE_R = DATA.radius || 2.5;
    scene = new THREE.Scene();
    worldGroup = new THREE.Group();
    scene.add(worldGroup);

    var camDist = SPHERE_R * 2.2;
    cam = new THREE.PerspectiveCamera(55, innerWidth/innerHeight, 0.01, 200);
    cam.position.set(camDist * 0.65, camDist * 0.45, camDist * 0.65);

    ren = new THREE.WebGLRenderer({antialias:true, alpha:true, powerPreference:'high-performance'});
    ren.setClearColor(0x020205);
    ren.setSize(innerWidth, innerHeight);
    ren.setPixelRatio(Math.min(devicePixelRatio, 2));
    ren.toneMapping = THREE.ACESFilmicToneMapping;
    ren.toneMappingExposure = 1.0;
    document.body.appendChild(ren.domElement);

    composer = new THREE.EffectComposer(ren);
    composer.addPass(new THREE.RenderPass(scene, cam));
    composer.addPass(new THREE.UnrealBloomPass(
      new THREE.Vector2(innerWidth, innerHeight),
      0.7, 0.4, 0.4
    ));
    composer.addPass(new THREE.ShaderPass(VignetteShader));

    ctrl = new THREE.OrbitControls(cam, ren.domElement);
    ctrl.enableDamping = true;
    ctrl.dampingFactor = 0.03;
    ctrl.autoRotate = false;
    ctrl.minDistance = Math.max(0.3, SPHERE_R * 0.3);
    ctrl.maxDistance = SPHERE_R * 6;
    ctrl.enablePan = false;

    ray = new THREE.Raycaster();
    ray.params.Points.threshold = 0.06;
    mouse = new THREE.Vector2();

    scene.add(new THREE.AmbientLight(0x1a1408, 0.3));
    var kl = new THREE.PointLight(0xffa500, 0.2, 20);
    kl.position.set(0, 5, 0);
    scene.add(kl);

    buildScene();

    ren.domElement.addEventListener('click', onClick);
    addEventListener('resize', onResize);
    document.getElementById('q').addEventListener('input', onSearch);
    connectSSE();
    animate();
  });
}

// ================================================================
//  Build nodes (single Points geometry — 1 draw call for all nodes)
// ================================================================
function rebuildNodes() {
  if (nodeSystem)    { nodeSystem.geometry.dispose(); worldGroup.remove(nodeSystem); }
  if (edgeMesh)      { edgeMesh.geometry.dispose(); worldGroup.remove(edgeMesh); }
  if (impulseSystem) { impulseSystem.geometry.dispose(); worldGroup.remove(impulseSystem); }
  nodeSystem = null; edgeMesh = null; impulseSystem = null;

  var N = DATA.nodes.length;
  if (N === 0) return;
  nodeDataArray = DATA.nodes;

  var positions = new Float32Array(N * 3);
  var colors    = new Float32Array(N * 3);
  var sizes     = new Float32Array(N);
  var phases    = new Float32Array(N);
  var births    = new Float32Array(N);
  var visible   = new Float32Array(N);

  var now = performance.now() / 1000;
  var addedCount = 0;

  for (var i = 0; i < N; i++) {
    var n = DATA.nodes[i];
    positions[i*3]=n.x; positions[i*3+1]=n.y; positions[i*3+2]=n.z;
    var c = new THREE.Color(n.color);
    colors[i*3]=c.r; colors[i*3+1]=c.g; colors[i*3+2]=c.b;
    sizes[i] = n.type==='decision' ? 5.0 : n.type==='architecture' ? 4.0 : 3.0;

    var old = oldNodeInfo[n.id];
    if (old) {
      phases[i] = old.phase;
      births[i] = old.birthTime;
    } else {
      phases[i] = Math.random() * Math.PI * 2;
      births[i] = now + addedCount * 0.06;
      addedCount++;
    }
    visible[i] = 1.0;
  }

  // Save for next incremental update
  oldNodeInfo = {};
  for (var i = 0; i < N; i++) {
    oldNodeInfo[DATA.nodes[i].id] = { phase: phases[i], birthTime: births[i] };
  }

  // Edges fade in after ~80% of new nodes have materialized
  edgeFadeStart = now + addedCount * 0.06 * 0.8;

  var geo = new THREE.BufferGeometry();
  geo.setAttribute('position',   new THREE.BufferAttribute(positions, 3));
  geo.setAttribute('aColor',     new THREE.BufferAttribute(colors, 3));
  geo.setAttribute('aSize',      new THREE.BufferAttribute(sizes, 1));
  geo.setAttribute('aPhase',     new THREE.BufferAttribute(phases, 1));
  geo.setAttribute('aBirthTime', new THREE.BufferAttribute(births, 1));
  geo.setAttribute('aVisible',   new THREE.BufferAttribute(visible, 1));

  nodeSystem = new THREE.Points(geo, getNodeMaterial());
  worldGroup.add(nodeSystem);

  // ── Edges: single LineSegments (1 draw call) ──
  if (DATA.edges && DATA.edges.length > 0) {
    var ePos = new Float32Array(DATA.edges.length * 6);
    var eCol = new Float32Array(DATA.edges.length * 6);
    for (var ei = 0; ei < DATA.edges.length; ei++) {
      var a = DATA.nodes[DATA.edges[ei][0]];
      var b = DATA.nodes[DATA.edges[ei][1]];
      var o = ei * 6;
      ePos[o]=a.x; ePos[o+1]=a.y; ePos[o+2]=a.z;
      ePos[o+3]=b.x; ePos[o+4]=b.y; ePos[o+5]=b.z;
      var ca = new THREE.Color(a.color);
      var cb = new THREE.Color(b.color);
      eCol[o]=ca.r; eCol[o+1]=ca.g; eCol[o+2]=ca.b;
      eCol[o+3]=cb.r; eCol[o+4]=cb.g; eCol[o+5]=cb.b;
    }
    var eGeo = new THREE.BufferGeometry();
    eGeo.setAttribute('position', new THREE.BufferAttribute(ePos, 3));
    eGeo.setAttribute('color',    new THREE.BufferAttribute(eCol, 3));
    // Store original colors for filter restore, and edge node indices for filter logic
    eGeo.userData = { origColors: new Float32Array(eCol), edgeIndices: DATA.edges };
    edgeMesh = new THREE.LineSegments(eGeo, new THREE.LineBasicMaterial({
      vertexColors: true, transparent: true, opacity: 0.09,
      blending: THREE.AdditiveBlending, depthWrite: false,
    }));
    worldGroup.add(edgeMesh);

    // ── Electrical impulses along edges ──
    var impN = Math.min(Math.floor(DATA.edges.length * 0.4), 400);
    if (impN > 0) {
      var iG = new THREE.BufferGeometry();
      var iPos=new Float32Array(impN*3), iSt=new Float32Array(impN*3), iEn=new Float32Array(impN*3);
      var iPh=new Float32Array(impN), iSp=new Float32Array(impN), iCo=new Float32Array(impN*3);
      for (var ii=0; ii<impN; ii++) {
        var ei = ii % DATA.edges.length;
        var ea = DATA.nodes[DATA.edges[ei][0]];
        var eb = DATA.nodes[DATA.edges[ei][1]];
        iPos[ii*3]=(ea.x+eb.x)*0.5; iPos[ii*3+1]=(ea.y+eb.y)*0.5; iPos[ii*3+2]=(ea.z+eb.z)*0.5;
        iSt[ii*3]=ea.x; iSt[ii*3+1]=ea.y; iSt[ii*3+2]=ea.z;
        iEn[ii*3]=eb.x; iEn[ii*3+1]=eb.y; iEn[ii*3+2]=eb.z;
        iPh[ii] = Math.random();
        iSp[ii] = 0.3 + Math.random() * 0.7;
        var ic1=new THREE.Color(ea.color), ic2=new THREE.Color(eb.color);
        iCo[ii*3]=(ic1.r+ic2.r)*0.5; iCo[ii*3+1]=(ic1.g+ic2.g)*0.5; iCo[ii*3+2]=(ic1.b+ic2.b)*0.5;
      }
      iG.setAttribute('position', new THREE.BufferAttribute(iPos, 3));
      iG.setAttribute('aStart',   new THREE.BufferAttribute(iSt, 3));
      iG.setAttribute('aEnd',     new THREE.BufferAttribute(iEn, 3));
      iG.setAttribute('aPhase',   new THREE.BufferAttribute(iPh, 1));
      iG.setAttribute('aSpeed',   new THREE.BufferAttribute(iSp, 1));
      iG.setAttribute('aImpColor',new THREE.BufferAttribute(iCo, 3));
      impulseSystem = new THREE.Points(iG, new THREE.ShaderMaterial({
        uniforms: { uTime: {value:0} },
        vertexShader: impulseVert, fragmentShader: impulseFrag,
        transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
      }));
      worldGroup.add(impulseSystem);
    }
  }
}

// ================================================================
//  Full scene build
// ================================================================
function disposeObj(o) {
  if (o.geometry) o.geometry.dispose();
  if (o.material) o.material.dispose();
}

function buildScene() {
  orbitalRings.forEach(function(r){disposeObj(r);worldGroup.remove(r);});
  if (shellMesh)  {disposeObj(shellMesh);worldGroup.remove(shellMesh);}
  if (moteSystem) {disposeObj(moteSystem);worldGroup.remove(moteSystem);}
  orbitalRings=[]; shellMesh=null;

  rebuildNodes();

  // ── Orbital rings ──
  var ringCfg = [
    {r:SPHERE_R*1.08,rx:0,ry:0,op:0.06},
    {r:SPHERE_R*1.18,rx:0.35,ry:0.2,op:0.04},
    {r:SPHERE_R*1.04,rx:0.65,ry:0.8,op:0.05},
    {r:SPHERE_R*1.14,rx:0.2,ry:0.55,op:0.03},
    {r:SPHERE_R*1.10,rx:0.85,ry:0.35,op:0.04},
  ];
  ringCfg.forEach(function(cfg) {
    var pts = [];
    for (var i=0;i<=128;i++){var a=(i/128)*Math.PI*2; pts.push(new THREE.Vector3(Math.cos(a)*cfg.r,0,Math.sin(a)*cfg.r));}
    var g=new THREE.BufferGeometry().setFromPoints(pts);
    var m=new THREE.LineBasicMaterial({color:0xffa500,transparent:true,opacity:cfg.op,blending:THREE.AdditiveBlending,depthWrite:false});
    var ring=new THREE.Line(g,m);
    ring.rotation.x=cfg.rx*Math.PI; ring.rotation.y=cfg.ry*Math.PI;
    ring.userData.speed=0.00015+Math.random()*0.00025;
    worldGroup.add(ring); orbitalRings.push(ring);
  });

  // ── Shell ──
  var sG=new THREE.IcosahedronGeometry(SPHERE_R*1.02,2);
  shellMesh=new THREE.Mesh(sG,new THREE.MeshBasicMaterial({color:0xffa500,transparent:true,opacity:0.015,wireframe:true,blending:THREE.AdditiveBlending,depthWrite:false}));
  worldGroup.add(shellMesh);

  // ── Motes ──
  var mN=100, mG=new THREE.BufferGeometry();
  var mP=new Float32Array(mN*3),mS=new Float32Array(mN),mH=new Float32Array(mN),mC=new Float32Array(mN*3);
  for(var i=0;i<mN;i++){
    var a1=Math.random()*Math.PI*2, a2=Math.acos(2*Math.random()-1), mr=SPHERE_R*(0.3+Math.random()*1.4);
    mP[i*3]=Math.sin(a2)*Math.cos(a1)*mr; mP[i*3+1]=Math.sin(a2)*Math.sin(a1)*mr; mP[i*3+2]=Math.cos(a2)*mr;
    mS[i]=2+Math.random()*4; mH[i]=Math.random();
    var mc=new THREE.Color().setHSL(0.1+Math.random()*0.07,0.7,0.45+Math.random()*0.2);
    mC[i*3]=mc.r; mC[i*3+1]=mc.g; mC[i*3+2]=mc.b;
  }
  mG.setAttribute('position',new THREE.BufferAttribute(mP,3));
  mG.setAttribute('aSize',new THREE.BufferAttribute(mS,1));
  mG.setAttribute('aPhase',new THREE.BufferAttribute(mH,1));
  mG.setAttribute('aColor',new THREE.BufferAttribute(mC,3));
  moteSystem=new THREE.Points(mG,new THREE.ShaderMaterial({uniforms:{uTime:{value:0}},vertexShader:moteVert,fragmentShader:moteFrag,transparent:true,depthWrite:false,blending:THREE.AdditiveBlending}));
  worldGroup.add(moteSystem);
}

// ================================================================
//  Incremental update
// ================================================================
function updateScene() {
  var prevR = SPHERE_R;
  SPHERE_R = DATA.radius || SPHERE_R;
  // Radius grew/shrank → scaffold (rings, shell, motes) must rescale with it.
  if (Math.abs(SPHERE_R - prevR) > 1e-4) {
    buildScene();
    if (ctrl) {
      ctrl.minDistance = Math.max(0.3, SPHERE_R * 0.3);
      ctrl.maxDistance = SPHERE_R * 6;
    }
  } else {
    rebuildNodes();
  }
  if (activeFilterFn) applyFilter();
  document.getElementById('total').textContent = DATA.nodes.length;
}

// ================================================================
//  Animation — just 3 uniform updates, no per-node loops
// ================================================================
function animate() {
  requestAnimationFrame(animate);
  ctrl.update();
  var t = performance.now() * 0.001;

  // Node animation — all on GPU
  var mat = getNodeMaterial();
  mat.uniforms.uTime.value = t;
  mat.uniforms.uNow.value = t;

  if (moteSystem)    moteSystem.material.uniforms.uTime.value = t;
  if (impulseSystem) impulseSystem.material.uniforms.uTime.value = t;

  // Earth-like rotation + subtle jiggle (3 float ops, negligible cost)
  if (worldGroup) {
    worldGroup.rotation.y += 0.0004;
    worldGroup.rotation.x = Math.sin(t * 0.13) * 0.012;
    worldGroup.rotation.z = Math.cos(t * 0.11) * 0.008;
  }

  for (var i=0;i<orbitalRings.length;i++) orbitalRings[i].rotation.y += orbitalRings[i].userData.speed;

  // Edges + impulses fade in after nodes materialize
  var edgeAge = t - edgeFadeStart;
  var edgeFade = edgeAge > 0 ? Math.min(edgeAge / 1.5, 1.0) : 0;
  if (edgeMesh) edgeMesh.material.opacity = (0.06 + Math.sin(t*0.3)*0.03) * edgeFade;
  if (impulseSystem) impulseSystem.visible = (edgeFade > 0.5) && !activeFilterFn;

  composer.render();
}

// ================================================================
//  Interaction
// ================================================================
function onClick(e) {
  mouse.x=(e.clientX/innerWidth)*2-1;
  mouse.y=-(e.clientY/innerHeight)*2+1;
  ray.setFromCamera(mouse, cam);
  if (!nodeSystem) return;
  var hits = ray.intersectObject(nodeSystem);
  if (hits.length) {
    var idx = hits[0].index;
    var n = nodeDataArray[idx];
    if (!n) return;
    document.getElementById('dm').textContent = n.project+'  \u00b7  '+n.type+'  \u00b7  '+n.source;
    document.getElementById('dc').textContent = n.content;
    document.getElementById('detail').style.display = 'block';
    ctrl.autoRotate = false;
  }
}

function onResize() {
  cam.aspect=innerWidth/innerHeight;
  cam.updateProjectionMatrix();
  ren.setSize(innerWidth,innerHeight);
  composer.setSize(innerWidth,innerHeight);
}

function onSearch(e) {
  var q = e.target.value.toLowerCase();
  if (!q) { resetFilter(); return; }
  filterBy(function(n){
    return n.content.toLowerCase().indexOf(q)>=0
        || n.label.toLowerCase().indexOf(q)>=0
        || n.project.toLowerCase().indexOf(q)>=0
        || n.type.toLowerCase().indexOf(q)>=0
        || n.source.toLowerCase().indexOf(q)>=0;
  });
}

function filterProject(project) {
  filterBy(function(n){return n.project===project;});
}

function filterBy(fn) {
  activeFilterFn = fn;
  applyFilter();
}

function resetFilter() {
  activeFilterFn = null;
  applyFilter();
}

function applyFilter() {
  if (!nodeSystem) return;
  var vis = nodeSystem.geometry.getAttribute('aVisible').array;
  if (activeFilterFn) {
    // Build per-node match set
    var matched = new Uint8Array(nodeDataArray.length);
    for (var i=0;i<nodeDataArray.length;i++) {
      matched[i] = activeFilterFn(nodeDataArray[i]) ? 1 : 0;
      vis[i] = matched[i] ? 1.0 : 0.0;
    }
    nodeSystem.geometry.getAttribute('aVisible').needsUpdate = true;

    // Fade edges: dim connections where neither endpoint matches
    if (edgeMesh && edgeMesh.geometry.userData.origColors) {
      var oc = edgeMesh.geometry.userData.origColors;
      var ec = edgeMesh.geometry.getAttribute('color').array;
      var edges = edgeMesh.geometry.userData.edgeIndices;
      for (var ei=0; ei<edges.length; ei++) {
        var aMatch = matched[edges[ei][0]], bMatch = matched[edges[ei][1]];
        // Both match: full color. One matches: dim. Neither: very dim.
        var f = (aMatch && bMatch) ? 1.0 : (aMatch || bMatch) ? 0.2 : 0.04;
        var o = ei * 6;
        for (var ci=0; ci<6; ci++) ec[o+ci] = oc[o+ci] * f;
      }
      edgeMesh.geometry.getAttribute('color').needsUpdate = true;
      edgeMesh.material.opacity = 0.09;
    }
    if (impulseSystem) impulseSystem.visible = false;
  } else {
    for (var i=0;i<vis.length;i++) vis[i]=1.0;
    nodeSystem.geometry.getAttribute('aVisible').needsUpdate = true;
    // Restore original edge colors
    if (edgeMesh && edgeMesh.geometry.userData.origColors) {
      var oc = edgeMesh.geometry.userData.origColors;
      var ec = edgeMesh.geometry.getAttribute('color').array;
      for (var ci=0; ci<oc.length; ci++) ec[ci] = oc[ci];
      edgeMesh.geometry.getAttribute('color').needsUpdate = true;
      edgeMesh.material.opacity = 0.09;
    }
    if (impulseSystem) impulseSystem.visible = true;
  }
}

// ================================================================
//  SSE
// ================================================================
function connectSSE() {
  var es = new EventSource('/api/stream');
  es.addEventListener('update', function(e) {
    var info = JSON.parse(e.data);
    if (info.version !== currentVersion) {
      load().then(function(){ updateScene(); });
    }
  });
  es.onerror = function(){ setTimeout(connectSSE,3000); es.close(); };
}

init();
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
    edges = len(VizHandler.data_cache.get("edges", []))

    wal_path = config.get_data_dir() / "wal.jsonl"
    try:
        _last_wal_size = os.path.getsize(wal_path) if wal_path.exists() else 0
    except OSError:
        _last_wal_size = 0
    _last_row_count = total

    print(f"  {total} memories, {edges} edges, {projects} projects")

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), VizHandler)
    url = f"http://127.0.0.1:{port}"
    print(f"\n  \033[0;33m✦ Knowledge Core running at {url}\033[0m")
    print(f"  \033[2mLive updates enabled · Press Ctrl+C to stop\033[0m\n")

    threading.Timer(0.5, lambda: launch_app_window(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
        server.shutdown()


if __name__ == "__main__":
    main()
