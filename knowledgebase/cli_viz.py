"""3D brain cluster visualization of the knowledge base.

Serves an interactive Three.js visualization with real-time updates
via Server-Sent Events (SSE). Watches the WAL file for changes and
pushes updates to the frontend, which animates new nodes appearing.

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
    table = vs._get_table()
    count = table.count_rows()
    if count == 0:
        return []
    return table.search().limit(count).to_list()


_pca_basis = None  # stored on first computation for stable projections


def pca_3d(vectors):
    global _pca_basis
    X = np.array(vectors, dtype=np.float32)

    if _pca_basis is None:
        # First call: full PCA, store basis for subsequent projections
        mean = X.mean(axis=0)
        X_c = X - mean
        cov = np.cov(X_c, rowvar=False)
        eigenvalues, eigenvectors = np.linalg.eigh(cov)
        top3 = np.argsort(eigenvalues)[::-1][:3]
        eigvecs = eigenvectors[:, top3]
        projected = X_c @ eigvecs

        norms = []
        for i in range(3):
            col = projected[:, i]
            mn, rng = float(col.min()), float(col.max() - col.min())
            norms.append((mn, rng))
            if rng > 0:
                normalized = (col - mn) / rng * 2 - 1
                projected[:, i] = np.sign(normalized) * np.abs(normalized) ** 0.7 * 2.5
        _pca_basis = (mean, eigvecs, norms)
        return projected.tolist()
    else:
        # Subsequent calls: reuse stored basis so existing positions are stable
        mean, eigvecs, norms = _pca_basis
        X_c = X - mean
        projected = X_c @ eigvecs
        for i in range(3):
            col = projected[:, i]
            mn, rng = norms[i]
            if rng > 0:
                normalized = (col - mn) / rng * 2 - 1
                projected[:, i] = np.sign(normalized) * np.abs(normalized) ** 0.7 * 2.5
        return projected.tolist()


def build_data():
    rows = get_all_rows()
    if not rows:
        return {"nodes": [], "projects": [], "types": list(TYPE_COLORS.keys()),
                "projectColors": {}, "typeColors": TYPE_COLORS, "total": 0, "version": 0}

    vectors = [r["vector"] for r in rows]
    positions = pca_3d(vectors)

    projects = sorted(set(r.get("project", "") or "(none)" for r in rows))
    pc = {p: PROJECT_COLORS[i % len(PROJECT_COLORS)] for i, p in enumerate(projects)}

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
            "color": pc.get(project, "#888"),
        })

    return {
        "nodes": nodes, "projects": projects, "types": list(TYPE_COLORS.keys()),
        "projectColors": pc, "typeColors": TYPE_COLORS, "total": len(nodes),
        "version": int(time.time()),
    }


# WAL watcher — detects when knowledge base changes
_last_wal_size = 0
_data_version = 0


def check_wal_changed():
    global _last_wal_size, _data_version
    wal_path = config.get_data_dir() / "wal.jsonl"
    try:
        size = os.path.getsize(wal_path) if wal_path.exists() else 0
    except OSError:
        size = 0
    if size != _last_wal_size:
        _last_wal_size = size
        _data_version += 1
        return True
    return False


HTML_PAGE = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Knowledge Brain</title>
<style>
  *{margin:0;padding:0;box-sizing:border-box}
  body{background:#05050e;color:#d0d0d8;font-family:'Inter','SF Pro',-apple-system,sans-serif;overflow:hidden}

  #info{position:fixed;top:20px;left:20px;z-index:100;background:rgba(8,8,20,0.72);border:1px solid rgba(96,165,250,0.07);border-radius:14px;padding:14px 18px;backdrop-filter:blur(24px);-webkit-backdrop-filter:blur(24px)}
  #info h1{font-size:11px;color:rgba(96,165,250,0.85);margin-bottom:6px;letter-spacing:3px;font-weight:500}
  #info .stat{font-size:10px;color:rgba(160,160,180,0.45);font-weight:300}

  #live-dot{display:inline-block;width:4px;height:4px;border-radius:50%;background:#34d399;margin-right:6px;box-shadow:0 0 8px rgba(52,211,153,0.5);animation:breathe 3s ease-in-out infinite}
  @keyframes breathe{0%,100%{opacity:0.9;transform:scale(1)}50%{opacity:0.35;transform:scale(0.75)}}

  #legend{position:fixed;top:20px;right:20px;z-index:100;background:rgba(8,8,20,0.72);border:1px solid rgba(96,165,250,0.05);border-radius:14px;padding:14px;backdrop-filter:blur(24px);max-height:70vh;overflow-y:auto}
  #legend h2{font-size:8px;color:rgba(120,120,150,0.45);margin-bottom:8px;text-transform:uppercase;letter-spacing:3px;font-weight:400}
  .lg{font-size:10px;margin:4px 0;cursor:pointer;opacity:0.4;transition:all 0.4s ease;font-weight:300}
  .lg:hover{opacity:1}
  .lg .d{display:inline-block;width:5px;height:5px;border-radius:50%;margin-right:6px;vertical-align:middle}

  #search{position:fixed;top:20px;left:50%;transform:translateX(-50%);z-index:100}
  #search input{background:rgba(8,8,20,0.72);border:1px solid rgba(96,165,250,0.07);border-radius:20px;color:#c0c0d0;padding:8px 16px;width:240px;font-size:10px;font-family:inherit;font-weight:300;outline:none;backdrop-filter:blur(24px);transition:all 0.4s ease}
  #search input:focus{border-color:rgba(96,165,250,0.22);box-shadow:0 0 30px rgba(96,165,250,0.04);width:280px}
  #search input::placeholder{color:rgba(120,120,150,0.25)}

  #detail{position:fixed;bottom:20px;left:20px;right:20px;z-index:100;background:rgba(8,8,20,0.88);border:1px solid rgba(96,165,250,0.1);border-radius:14px;padding:16px 20px;max-height:200px;overflow-y:auto;display:none;backdrop-filter:blur(24px);animation:slideUp 0.35s ease}
  @keyframes slideUp{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:translateY(0)}}
  #detail .m{font-size:9px;color:rgba(96,165,250,0.65);margin-bottom:6px;letter-spacing:0.5px;font-weight:400}
  #detail .c{font-size:10px;white-space:pre-wrap;line-height:1.5;color:rgba(180,180,200,0.55);font-weight:300}
  #detail .x{position:absolute;top:8px;right:12px;cursor:pointer;color:rgba(120,120,150,0.35);font-size:16px;transition:color 0.3s}
  #detail .x:hover{color:rgba(200,200,220,0.7)}

  canvas{display:block}

  ::-webkit-scrollbar{width:3px}
  ::-webkit-scrollbar-track{background:transparent}
  ::-webkit-scrollbar-thumb{background:rgba(96,165,250,0.12);border-radius:2px}
</style>
</head>
<body>
<div id="info"><h1>KNOWLEDGE BRAIN</h1><div class="stat"><span id="live-dot"></span><span id="total">0</span> memories &middot; <span id="pcount">0</span> projects</div></div>
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
// ================================================================
//  Global state
// ================================================================
let DATA, scene, cam, ren, ctrl, composer;
let ray, mouse;
let spheres = [], edgeData = [], impulses = [], nodeMap = {};
let dustSystem, moteSystem;
let currentVersion = 0;

// ================================================================
//  Organic noise (multi-frequency sine — cheap, smooth, organic)
// ================================================================
function fbm(x, y, z, t) {
  let v = 0;
  v += Math.sin(x * 1.7 + t * 0.31) * Math.cos(y * 2.3 - t * 0.19) * 0.5;
  v += Math.sin(y * 3.1 - z * 1.7 + t * 0.47) * 0.25;
  v += Math.cos(z * 2.9 + x * 1.3 + t * 0.37) * 0.125;
  return v;
}

// ================================================================
//  Shaders
// ================================================================

// ── Node core: bioluminescent orb ──
const nodeVert = `
  varying vec3 vNormal;
  varying vec3 vViewDir;
  void main() {
    vNormal = normalize(normalMatrix * normal);
    vec4 mvPos = modelViewMatrix * vec4(position, 1.0);
    vViewDir = normalize(-mvPos.xyz);
    gl_Position = projectionMatrix * mvPos;
  }`;
const nodeFrag = `
  uniform vec3 uColor;
  uniform float uPulse;
  uniform float uFade;
  varying vec3 vNormal;
  varying vec3 vViewDir;
  void main() {
    float NdotV = max(dot(vNormal, vViewDir), 0.0);
    float core = pow(NdotV, 1.2);
    float rim  = pow(1.0 - NdotV, 3.0);
    vec3 col = uColor * (core * 1.8 + rim * 0.6);
    float alpha = (core * 0.95 + rim * 0.25) * (0.75 + 0.25 * uPulse) * uFade;
    gl_FragColor = vec4(col, alpha);
  }`;

// ── Halo (rendered BackSide) ──
const haloVert = `
  varying vec3 vNormal;
  varying vec3 vViewDir;
  void main() {
    vNormal = normalize(normalMatrix * normal);
    vec4 mvPos = modelViewMatrix * vec4(position, 1.0);
    vViewDir = normalize(-mvPos.xyz);
    gl_Position = projectionMatrix * mvPos;
  }`;
const haloFrag = `
  uniform vec3 uColor;
  uniform float uPulse;
  uniform float uFade;
  varying vec3 vNormal;
  varying vec3 vViewDir;
  void main() {
    float NdotV = abs(dot(vNormal, vViewDir));
    float rim = 1.0 - NdotV;
    float intensity = pow(rim, 1.8);
    vec3 col = uColor * 1.3;
    float alpha = intensity * (0.22 + 0.08 * uPulse) * uFade;
    gl_FragColor = vec4(col, alpha);
  }`;

// ── Atmospheric dust ──
const dustVert = `
  attribute float aSize;
  attribute float aPhase;
  uniform float uTime;
  varying float vAlpha;
  void main() {
    vec3 pos = position;
    pos.x += sin(uTime * 0.1 + aPhase * 6.28) * 0.02;
    pos.y += cos(uTime * 0.08 + aPhase * 4.0) * 0.015;
    pos.z += sin(uTime * 0.12 + aPhase * 5.0) * 0.02;
    vec4 mvPos = modelViewMatrix * vec4(pos, 1.0);
    gl_PointSize = aSize * (150.0 / -mvPos.z);
    gl_Position = projectionMatrix * mvPos;
    vAlpha = 0.1 + 0.06 * sin(uTime * 0.3 + aPhase * 10.0);
  }`;
const dustFrag = `
  varying float vAlpha;
  void main() {
    float d = length(gl_PointCoord - 0.5) * 2.0;
    if (d > 1.0) discard;
    float alpha = (1.0 - d * d) * vAlpha;
    gl_FragColor = vec4(0.3, 0.35, 0.6, alpha);
  }`;

// ── Luminous motes ──
const moteVert = `
  attribute float aSize;
  attribute float aPhase;
  attribute vec3 aColor;
  uniform float uTime;
  varying float vAlpha;
  varying vec3 vColor;
  void main() {
    vec3 pos = position;
    float t = uTime * 0.05;
    pos.x += sin(t + aPhase * 6.28) * 0.08;
    pos.y += cos(t * 1.3 + aPhase * 4.0) * 0.06;
    pos.z += sin(t * 0.9 + aPhase * 5.0) * 0.07;
    vec4 mvPos = modelViewMatrix * vec4(pos, 1.0);
    gl_PointSize = aSize * (200.0 / -mvPos.z);
    gl_Position = projectionMatrix * mvPos;
    vAlpha = 0.05 + 0.03 * sin(uTime * 0.2 + aPhase * 8.0);
    vColor = aColor;
  }`;
const moteFrag = `
  varying float vAlpha;
  varying vec3 vColor;
  void main() {
    float d = length(gl_PointCoord - 0.5) * 2.0;
    if (d > 1.0) discard;
    float alpha = pow(1.0 - d, 2.0) * vAlpha;
    gl_FragColor = vec4(vColor, alpha);
  }`;

// ── Vignette + chromatic aberration post-pass ──
const VignetteShader = {
  uniforms: {
    tDiffuse:    { value: null },
    uIntensity:  { value: 1.3 },
    uSoftness:   { value: 0.45 },
  },
  vertexShader: `
    varying vec2 vUv;
    void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }`,
  fragmentShader: `
    uniform sampler2D tDiffuse;
    uniform float uIntensity;
    uniform float uSoftness;
    varying vec2 vUv;
    void main() {
      vec4 color = texture2D(tDiffuse, vUv);
      float dist = distance(vUv, vec2(0.5));
      float vig  = smoothstep(0.8, uSoftness, dist * uIntensity);
      float ab   = dist * 0.0025;
      color.r = texture2D(tDiffuse, vUv + vec2(ab, 0.0)).r;
      color.b = texture2D(tDiffuse, vUv - vec2(ab, 0.0)).b;
      color.rgb *= vig;
      gl_FragColor = color;
    }`,
};

// ================================================================
//  Data loading
// ================================================================
async function load() {
  const r = await fetch('/api/data');
  DATA = await r.json();
  currentVersion = DATA.version;
  document.getElementById('total').textContent = DATA.total;
  document.getElementById('pcount').textContent = DATA.projects.length;
  buildLegend();
}

function buildLegend() {
  const el = document.getElementById('legend');
  el.innerHTML = '<h2>Projects</h2>';
  DATA.projects.forEach(p => {
    const d = document.createElement('div');
    d.className = 'lg';
    d.innerHTML = '<span class="d" style="background:'+DATA.projectColors[p]+';box-shadow:0 0 6px '+DATA.projectColors[p]+'40"></span>'+p;
    d.onclick = () => filterProject(p);
    el.appendChild(d);
  });
  const reset = document.createElement('div');
  reset.className = 'lg';
  reset.style.color = 'rgba(120,120,150,0.3)';
  reset.textContent = 'show all';
  reset.onclick = resetFilter;
  el.appendChild(reset);
}

// ================================================================
//  Scene initialisation
// ================================================================
async function init() {
  await load();

  scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x05050e, 0.12);

  cam = new THREE.PerspectiveCamera(50, innerWidth / innerHeight, 0.01, 200);
  cam.position.set(5.0, 3.5, 5.0);

  ren = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' });
  ren.setClearColor(0x05050e);
  ren.setSize(innerWidth, innerHeight);
  ren.setPixelRatio(Math.min(devicePixelRatio, 2));
  ren.toneMapping = THREE.ACESFilmicToneMapping;
  ren.toneMappingExposure = 1.2;
  document.body.appendChild(ren.domElement);

  // Post-processing pipeline: render ➜ bloom ➜ vignette
  composer = new THREE.EffectComposer(ren);
  composer.addPass(new THREE.RenderPass(scene, cam));
  composer.addPass(new THREE.UnrealBloomPass(
    new THREE.Vector2(innerWidth, innerHeight),
    1.6,   // strength
    0.7,   // radius
    0.12   // threshold
  ));
  composer.addPass(new THREE.ShaderPass(VignetteShader));

  ctrl = new THREE.OrbitControls(cam, ren.domElement);
  ctrl.enableDamping = true;
  ctrl.dampingFactor = 0.03;
  ctrl.autoRotate = true;
  ctrl.autoRotateSpeed = 0.15;
  ctrl.minDistance = 0.5;
  ctrl.maxDistance = 15;
  ctrl.enablePan = false;

  ray = new THREE.Raycaster();
  mouse = new THREE.Vector2();

  // Lighting
  scene.add(new THREE.AmbientLight(0x0a0a1a, 0.3));
  var keyLight = new THREE.PointLight(0x60a5fa, 0.3, 20);
  keyLight.position.set(0, 6, 0);
  scene.add(keyLight);
  var fillLight = new THREE.PointLight(0x4ecdc4, 0.15, 15);
  fillLight.position.set(-4, -2, 4);
  scene.add(fillLight);

  buildScene();

  ren.domElement.addEventListener('click', onClick);
  addEventListener('resize', onResize);
  document.getElementById('q').addEventListener('input', onSearch);
  connectSSE();
  animate();
}

// ================================================================
//  Scene construction
// ================================================================
function disposeObj(o) {
  if (o.geometry) o.geometry.dispose();
  if (o.material) {
    if (o.material.map) o.material.map.dispose();
    o.material.dispose();
  }
}

// ── Shared node creation ──
function createNodeMesh(n, birthTime) {
  var color = new THREE.Color(n.color);
  var size = n.type === 'decision' ? 0.024
           : n.type === 'architecture' ? 0.019
           : 0.015;
  var geo = new THREE.SphereGeometry(size, 24, 24);
  var mat = new THREE.ShaderMaterial({
    uniforms: { uColor: { value: color }, uPulse: { value: 0 }, uFade: { value: 0 } },
    vertexShader: nodeVert, fragmentShader: nodeFrag,
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
  });
  var mesh = new THREE.Mesh(geo, mat);
  mesh.position.set(n.x, n.y, n.z);
  mesh.userData = { id: n.id, nodeData: n, basePos: new THREE.Vector3(n.x, n.y, n.z),
                    phase: Math.random() * Math.PI * 2, birthTime: birthTime, size: size };
  var hGeo = new THREE.SphereGeometry(size * 3.5, 24, 24);
  var hMat = new THREE.ShaderMaterial({
    uniforms: { uColor: { value: color }, uPulse: { value: 0 }, uFade: { value: 0 } },
    vertexShader: haloVert, fragmentShader: haloFrag,
    transparent: true, side: THREE.BackSide, depthWrite: false, blending: THREE.AdditiveBlending,
  });
  mesh.add(new THREE.Mesh(hGeo, hMat));
  return mesh;
}

// ── Rebuild edges + impulses (cheap, called on every topology change) ──
function buildConnections() {
  edgeData.forEach(function(e) { disposeObj(e.line); scene.remove(e.line); });
  impulses.forEach(function(i) { disposeObj(i); scene.remove(i); });
  edgeData = []; impulses = [];
  if (spheres.length < 2) return;

  var K = 3;
  var positions = spheres.map(function(s) { return s.userData.basePos.clone(); });
  var edgeSet = new Set();

  positions.forEach(function(p, i) {
    var dists = positions.map(function(q, j) { return { j: j, d: p.distanceTo(q) }; })
      .filter(function(x) { return x.j !== i; })
      .sort(function(a, b) { return a.d - b.d; })
      .slice(0, K);

    dists.forEach(function(item) {
      var j = item.j, d = item.d;
      if (d > 1.2) return;
      var key = Math.min(i, j) + '_' + Math.max(i, j);
      if (edgeSet.has(key)) return;
      edgeSet.add(key);

      var start = positions[i].clone();
      var end   = positions[j].clone();
      var mid   = start.clone().add(end).multiplyScalar(0.5);
      var dir = end.clone().sub(start);
      var perp = new THREE.Vector3().crossVectors(dir, new THREE.Vector3(0, 1, 0));
      if (perp.lengthSq() < 0.0001) perp.crossVectors(dir, new THREE.Vector3(1, 0, 0));
      perp.normalize();
      var angle = Math.random() * Math.PI * 2;
      perp.applyAxisAngle(dir.clone().normalize(), angle);
      mid.add(perp.multiplyScalar(dir.length() * (0.1 + Math.random() * 0.2)));

      var curve = new THREE.QuadraticBezierCurve3(start, mid, end);
      var pts   = curve.getPoints(24);
      var geo   = new THREE.BufferGeometry().setFromPoints(pts);
      var ca = new THREE.Color(spheres[i].userData.nodeData.color);
      var cb = new THREE.Color(spheres[j].userData.nodeData.color);
      var cols = new Float32Array(pts.length * 3);
      for (var t = 0; t < pts.length; t++) {
        var frac = t / (pts.length - 1);
        var c = ca.clone().lerp(cb, frac);
        cols[t * 3] = c.r; cols[t * 3 + 1] = c.g; cols[t * 3 + 2] = c.b;
      }
      geo.setAttribute('color', new THREE.BufferAttribute(cols, 3));
      var mat = new THREE.LineBasicMaterial({
        vertexColors: true, transparent: true, opacity: 0.04,
        blending: THREE.AdditiveBlending, depthWrite: false,
      });
      var line = new THREE.Line(geo, mat);
      scene.add(line);
      edgeData.push({ line: line, curve: curve, nodeA: i, nodeB: j });
    });
  });

  var impulseCount = Math.min(edgeData.length, 80);
  for (var ii = 0; ii < impulseCount; ii++) {
    var edgeIdx = ii % edgeData.length;
    var edge = edgeData[edgeIdx];
    var nColor = new THREE.Color(spheres[edge.nodeA].userData.nodeData.color);
    var iGeo = new THREE.SphereGeometry(0.003, 8, 8);
    var iMat = new THREE.MeshBasicMaterial({
      color: nColor, transparent: true, opacity: 0,
      blending: THREE.AdditiveBlending, depthWrite: false,
    });
    var imp = new THREE.Mesh(iGeo, iMat);
    imp.userData = { edgeIndex: edgeIdx, t: Math.random(), speed: 0.12 + Math.random() * 0.25 };
    scene.add(imp);
    impulses.push(imp);
  }
}

// ── Full scene build (initial load only) ──
function buildScene() {
  spheres.forEach(function(s) { s.children.forEach(function(c) { disposeObj(c); }); disposeObj(s); scene.remove(s); });
  if (dustSystem) { disposeObj(dustSystem); scene.remove(dustSystem); }
  if (moteSystem) { disposeObj(moteSystem); scene.remove(moteSystem); }
  spheres = []; nodeMap = {};

  if (!DATA.nodes.length) return;
  var now = performance.now();

  DATA.nodes.forEach(function(n, i) {
    var mesh = createNodeMesh(n, now + i * 12);
    scene.add(mesh);
    spheres.push(mesh);
    nodeMap[n.id] = mesh;
  });

  buildConnections();

  // ────────────────────── Atmospheric dust ──────────────────────
  var dustCount = 2000;
  var dGeo = new THREE.BufferGeometry();
  var dPos = new Float32Array(dustCount * 3);
  var dSz  = new Float32Array(dustCount);
  var dPh  = new Float32Array(dustCount);
  for (var di = 0; di < dustCount; di++) {
    dPos[di*3]=(Math.random()-.5)*16; dPos[di*3+1]=(Math.random()-.5)*16; dPos[di*3+2]=(Math.random()-.5)*16;
    dSz[di] = 1.0 + Math.random() * 2.0;
    dPh[di] = Math.random();
  }
  dGeo.setAttribute('position', new THREE.BufferAttribute(dPos, 3));
  dGeo.setAttribute('aSize',    new THREE.BufferAttribute(dSz, 1));
  dGeo.setAttribute('aPhase',   new THREE.BufferAttribute(dPh, 1));
  dustSystem = new THREE.Points(dGeo, new THREE.ShaderMaterial({
    uniforms: { uTime: { value: 0 } },
    vertexShader: dustVert, fragmentShader: dustFrag,
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
  }));
  scene.add(dustSystem);

  // ────────────────────── Luminous motes ──────────────────────
  var moteCount = 120;
  var mGeo = new THREE.BufferGeometry();
  var mPos = new Float32Array(moteCount * 3);
  var mSz  = new Float32Array(moteCount);
  var mPh  = new Float32Array(moteCount);
  var mCol = new Float32Array(moteCount * 3);
  for (var mi = 0; mi < moteCount; mi++) {
    mPos[mi*3]=(Math.random()-.5)*10; mPos[mi*3+1]=(Math.random()-.5)*10; mPos[mi*3+2]=(Math.random()-.5)*10;
    mSz[mi] = 3 + Math.random() * 6;
    mPh[mi] = Math.random();
    var mc = new THREE.Color().setHSL(Math.random(), 0.3, 0.5);
    mCol[mi*3] = mc.r; mCol[mi*3+1] = mc.g; mCol[mi*3+2] = mc.b;
  }
  mGeo.setAttribute('position', new THREE.BufferAttribute(mPos, 3));
  mGeo.setAttribute('aSize',    new THREE.BufferAttribute(mSz, 1));
  mGeo.setAttribute('aPhase',   new THREE.BufferAttribute(mPh, 1));
  mGeo.setAttribute('aColor',   new THREE.BufferAttribute(mCol, 3));
  moteSystem = new THREE.Points(mGeo, new THREE.ShaderMaterial({
    uniforms: { uTime: { value: 0 } },
    vertexShader: moteVert, fragmentShader: moteFrag,
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
  }));
  scene.add(moteSystem);
}

// ── Incremental update (preserves existing nodes, adds new ones) ──
function updateScene() {
  var now = performance.now();
  var newDataById = {};
  DATA.nodes.forEach(function(n) { newDataById[n.id] = n; });
  var existingIds = new Set(Object.keys(nodeMap));

  // Add only new nodes
  var addedCount = 0;
  DATA.nodes.forEach(function(n) {
    if (!existingIds.has(n.id)) {
      var mesh = createNodeMesh(n, now + addedCount * 80);
      scene.add(mesh);
      spheres.push(mesh);
      nodeMap[n.id] = mesh;
      addedCount++;
    }
  });

  // Remove deleted nodes
  for (var si = spheres.length - 1; si >= 0; si--) {
    var s = spheres[si];
    if (!(s.userData.id in newDataById)) {
      s.children.forEach(function(c) { disposeObj(c); });
      disposeObj(s);
      scene.remove(s);
      delete nodeMap[s.userData.id];
      spheres.splice(si, 1);
    } else {
      // Keep nodeData reference fresh
      s.userData.nodeData = newDataById[s.userData.id];
    }
  }

  // Rebuild connections only if topology changed
  if (addedCount > 0) buildConnections();

  document.getElementById('total').textContent = spheres.length;
}

// ================================================================
//  Animation loop
// ================================================================
function animate() {
  requestAnimationFrame(animate);
  ctrl.update();

  var now = performance.now();
  var t   = now * 0.001;

  // Particle system time
  if (dustSystem) dustSystem.material.uniforms.uTime.value = t;
  if (moteSystem) moteSystem.material.uniforms.uTime.value = t;

  // Organic node motion
  for (var si = 0; si < spheres.length; si++) {
    var s  = spheres[si];
    var bp = s.userData.basePos;
    var ph = s.userData.phase;

    // Fade-in (smoothstep)
    var age = (now - s.userData.birthTime) / 1000;
    var f   = Math.min(Math.max(age / 0.8, 0), 1);
    var fade = f * f * (3 - 2 * f);
    s.material.uniforms.uFade.value = fade;
    s.children[0].material.uniforms.uFade.value = fade;

    // Organic breathing scale
    s.scale.setScalar(fade * (1 + 0.03 * Math.sin(t * 1.5 + ph)));

    // Multi-frequency organic displacement
    var amp = 0.006;
    s.position.x = bp.x + fbm(bp.x, bp.y, bp.z, t * 0.8 + ph) * amp;
    s.position.y = bp.y + fbm(bp.y, bp.z, bp.x, t * 0.7 + ph * 1.3) * amp;
    s.position.z = bp.z + fbm(bp.z, bp.x, bp.y, t * 0.9 + ph * 0.7) * amp;

    // Pulse
    var pulse = Math.sin(t * 0.8 + ph) * 0.5 + 0.5;
    s.material.uniforms.uPulse.value = pulse;
    s.children[0].material.uniforms.uPulse.value = pulse;
  }

  // Impulse particles flowing along curves
  for (var ii = 0; ii < impulses.length; ii++) {
    var imp  = impulses[ii];
    var edge = edgeData[imp.userData.edgeIndex];
    if (!edge) continue;

    imp.userData.t += imp.userData.speed * 0.012;
    if (imp.userData.t > 1) {
      imp.userData.t = 0;
      imp.userData.edgeIndex = Math.floor(Math.random() * edgeData.length);
      continue;
    }
    imp.position.copy(edge.curve.getPoint(imp.userData.t));
    var p = Math.sin(imp.userData.t * Math.PI);
    imp.material.opacity = p * 0.6;
    imp.scale.setScalar(0.5 + p);
  }

  // Edge breathing
  for (var ei = 0; ei < edgeData.length; ei++) {
    edgeData[ei].line.material.opacity = 0.025 + Math.sin(t * 0.3 + ei * 0.07) * 0.015;
  }

  composer.render();
}

// ================================================================
//  Interaction
// ================================================================
function onClick(e) {
  mouse.x =  (e.clientX / innerWidth)  * 2 - 1;
  mouse.y = -(e.clientY / innerHeight) * 2 + 1;
  ray.setFromCamera(mouse, cam);
  var hits = ray.intersectObjects(spheres);
  if (hits.length) {
    var n = hits[0].object.userData.nodeData;
    if (!n) return;
    document.getElementById('dm').textContent = n.project + '  \u00b7  ' + n.type + '  \u00b7  ' + n.source;
    document.getElementById('dc').textContent = n.content;
    document.getElementById('detail').style.display = 'block';
    ctrl.autoRotate = false;
    // Highlight pulse
    var mesh = hits[0].object;
    mesh.material.uniforms.uPulse.value = 2.0;
    mesh.children[0].material.uniforms.uPulse.value = 2.0;
    setTimeout(function() {
      mesh.material.uniforms.uPulse.value = 0.5;
      mesh.children[0].material.uniforms.uPulse.value = 0.5;
    }, 1500);
  }
}

function onResize() {
  cam.aspect = innerWidth / innerHeight;
  cam.updateProjectionMatrix();
  ren.setSize(innerWidth, innerHeight);
  composer.setSize(innerWidth, innerHeight);
}

function onSearch(e) {
  var q = e.target.value.toLowerCase();
  if (!q) { resetFilter(); return; }
  filterBy(function(n) {
    return n.content.toLowerCase().indexOf(q) >= 0
        || n.label.toLowerCase().indexOf(q) >= 0
        || n.project.toLowerCase().indexOf(q) >= 0;
  });
}

function filterProject(project) {
  filterBy(function(n) { return n.project === project; });
}

function filterBy(fn) {
  spheres.forEach(function(s) { s.visible = fn(s.userData.nodeData); });
  edgeData.forEach(function(e) { e.line.material.opacity = 0.015; });
}

function resetFilter() {
  spheres.forEach(function(s) { s.visible = true; });
}

// ================================================================
//  SSE live updates
// ================================================================
function connectSSE() {
  var es = new EventSource('/api/stream');
  es.addEventListener('update', function(e) {
    var info = JSON.parse(e.data);
    if (info.version !== currentVersion) {
      load().then(function() { updateScene(); });
    }
  });
  es.onerror = function() { setTimeout(connectSSE, 3000); es.close(); };
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
                    if check_wal_changed():
                        VizHandler.data_cache = build_data()
                        msg = json.dumps({"version": _data_version, "total": VizHandler.data_cache["total"]})
                        self.wfile.write(f"event: update\ndata: {msg}\n\n".encode())
                        self.wfile.flush()
                    else:
                        # Heartbeat to keep connection alive
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
    """Launch Chrome/Chromium in --app mode for a clean standalone window."""
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
    global _last_wal_size
    port = DEFAULT_PORT
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--port" and i + 1 < len(args):
            port = int(args[i + 1])

    print(f"\n  \033[0;36mBuilding visualization data...\033[0m")
    VizHandler.data_cache = build_data()
    total = VizHandler.data_cache["total"]
    projects = len(VizHandler.data_cache["projects"])

    # Initialize WAL watcher
    wal_path = config.get_data_dir() / "wal.jsonl"
    try:
        _last_wal_size = os.path.getsize(wal_path) if wal_path.exists() else 0
    except OSError:
        _last_wal_size = 0

    print(f"  {total} memories across {projects} projects")

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), VizHandler)
    url = f"http://127.0.0.1:{port}"
    print(f"\n  \033[0;32m✦ Knowledge Brain running at {url}\033[0m")
    print(f"  \033[2mLive updates enabled · Press Ctrl+C to stop\033[0m\n")

    threading.Timer(0.5, lambda: launch_app_window(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
        server.shutdown()


if __name__ == "__main__":
    main()
