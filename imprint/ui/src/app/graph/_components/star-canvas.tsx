"use client";

import { useEffect, useImperativeHandle, useRef, useState } from "react";
import type { GraphNodeKind } from "@/lib/types";
import { EDGE_KINDS, nodeColor, shortLabel } from "./graph-style";
import { ForceModel, prefersReducedMotion, type GraphDebug, type GraphViewProps, type SimNode } from "./graph-model";
import { GraphTooltip, type TooltipHandle } from "./graph-tooltip";

type Three = typeof import("three");

/** Star colours: projects shine in their own colour, the rest in the
 * Echo hologram's blues and violets. */
const STAR: Record<Exclude<GraphNodeKind, "project">, string> = { topic: "#b9a8ff", source: "#8fd3ff", chunk: "#d9f5ff" };
/** How far apart kinds sit in depth, in layout units. */
const DEPTH: Record<GraphNodeKind, number> = { project: 60, topic: 170, source: 260, chunk: 200 };
/** Sprite size over the node's diameter: the rest is glow. */
const GLOW: Record<GraphNodeKind, number> = { project: 4.2, topic: 2.8, source: 3, chunk: 3.4 };
const IMPULSES = 160;
const TRAIL = 8;
const DUST = 1400;
const FOV = 45;
const VIEW_MS = 600;

function hash01(text: string, salt = 0): number {
  let h = 2166136261 ^ salt;
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 16777619);
  return ((h >>> 0) % 100000) / 100000;
}

const ease = (t: number) => (t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2);

const STAR_VERTEX = /* glsl */ `
attribute vec3 aColor; attribute float aSize; attribute float aPhase; attribute float aAlpha; attribute float aMark; attribute float aCore;
uniform float uScale; uniform float uTime; uniform float uTwinkle;
varying vec3 vColor; varying float vAlpha; varying float vMark; varying float vCore;
void main() {
  vCore = aCore;
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  float twinkle = 1.0 + uTwinkle * 0.16 * sin(uTime * 1.7 + aPhase * 6.2831);
  gl_PointSize = clamp(aSize * uScale / -mv.z * twinkle, 2.5, 240.0);
  vColor = aColor; vAlpha = aAlpha * (0.88 + 0.12 * twinkle); vMark = aMark;
  gl_Position = projectionMatrix * mv;
}`;

const STAR_FRAGMENT = /* glsl */ `
uniform float uFade;
varying vec3 vColor; varying float vAlpha; varying float vMark; varying float vCore;
void main() {
  vec2 p = gl_PointCoord * 2.0 - 1.0;
  float d = length(p);
  if (d > 1.0) discard;
  // A bright disc the node's size, a white-hot centre and a coloured corona
  // fading to the sprite's edge.
  float core = smoothstep(vCore, vCore * 0.7, d);
  float hot = smoothstep(vCore * 0.55, 0.0, d);
  float corona = exp(-d * d / (vCore * vCore) * 0.9) * 0.55 + exp(-d * d * 5.0) * 0.18;
  vec3 color = mix(vColor, vec3(1.0), hot * 0.75 + core * 0.15);
  float alpha = max(core, corona);
  if (vMark > 0.5) {
    // A ring: amber for a find match, white when selected, own colour on hover.
    float ring = smoothstep(0.06, 0.0, abs(d - min(0.92, vCore * 1.55)));
    vec3 ringColor = vMark > 2.5 ? vec3(0.98, 0.66, 0.1) : vMark > 1.5 ? vec3(1.0) : vColor;
    color = mix(color, ringColor, ring);
    alpha = max(alpha, ring);
  }
  gl_FragColor = vec4(color, alpha * vAlpha * uFade);
}`;

const LINE_VERTEX = /* glsl */ `
attribute vec3 aColor; attribute float aAlpha;
varying vec3 vColor; varying float vAlpha;
void main() {
  vColor = aColor; vAlpha = aAlpha;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}`;

const LINE_FRAGMENT = /* glsl */ `
uniform float uFade;
varying vec3 vColor; varying float vAlpha;
void main() { gl_FragColor = vec4(vColor, vAlpha * uFade); }`;

// An impulse is a head and a trail of points running along one connection:
// motion = [phase, cycles per second, lag behind the head, seed]. Like the
// Echo avatar's flow (exp(-fract(t - moment) / afterglow)), the light is
// brightest at the head and dies away behind it.
const IMPULSE_VERTEX = /* glsl */ `
attribute vec3 aFrom; attribute vec3 aTo; attribute vec4 aMotion;
uniform float uTime; uniform float uScale;
varying float vGlow;
void main() {
  float p = fract(uTime * aMotion.y + aMotion.x) * 1.35 - aMotion.z;
  if (p < 0.0 || p > 1.0) { gl_Position = vec4(2.0, 2.0, 2.0, 1.0); gl_PointSize = 0.0; vGlow = 0.0; return; }
  vec3 dir = aTo - aFrom;
  vec3 side = normalize(cross(dir, vec3(0.0, 0.0, 1.0)) + vec3(0.0001));
  float arc = sin(3.14159 * p);
  // A little crackle across the line, strongest mid-way.
  vec3 pos = mix(aFrom, aTo, p) + side * sin(p * 37.0 + aMotion.w * 19.0 + uTime * 11.0) * 2.4 * arc;
  vec4 mv = modelViewMatrix * vec4(pos, 1.0);
  float glow = exp(-aMotion.z / 0.035);
  vGlow = glow * (0.35 + 0.65 * arc);
  gl_PointSize = clamp((6.0 + 12.0 * glow) * uScale / -mv.z, 2.0, 30.0);
  gl_Position = projectionMatrix * mv;
}`;

const IMPULSE_FRAGMENT = /* glsl */ `
uniform float uFade;
varying float vGlow;
void main() {
  float d = length(gl_PointCoord * 2.0 - 1.0);
  float a = (exp(-d * d * 4.0) * 0.8 + smoothstep(0.3, 0.0, d) * 0.6) * vGlow;
  vec3 color = mix(vec3(0.20, 0.69, 0.96), vec3(0.85, 0.98, 1.0), vGlow);
  gl_FragColor = vec4(color, a * uFade);
}`;

const DUST_VERTEX = /* glsl */ `
attribute float aPhase; attribute float aSize;
uniform float uTime; uniform float uTwinkle; uniform float uPixel;
varying float vAlpha;
void main() {
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  vAlpha = (0.25 + 0.35 * aPhase) * (1.0 + uTwinkle * 0.35 * sin(uTime * (0.6 + aPhase) + aPhase * 40.0));
  gl_PointSize = aSize * uPixel;
  gl_Position = projectionMatrix * mv;
}`;

const DUST_FRAGMENT = /* glsl */ `
varying float vAlpha;
void main() {
  float d = length(gl_PointCoord * 2.0 - 1.0);
  gl_FragColor = vec4(vec3(0.78, 0.88, 1.0), exp(-d * d * 4.0) * vAlpha);
}`;

interface Orbit { yaw: number; pitch: number; distance: number; target: [number, number, number] }

/**
 * The scope as a star system: memories are stars laid out by the same
 * force model as the graph and given depth by kind, connections are faint
 * filaments, and electric impulses run along them. Everything is three
 * draw calls of points and lines whose motion runs on the GPU; the CPU
 * touches buffers only while the layout moves or the states change.
 */
class StarView {
  readonly renderer: import("three").WebGLRenderer;
  readonly labels = document.createElement("canvas");
  private labelCtx: CanvasRenderingContext2D;
  private scene: import("three").Scene;
  private camera: import("three").PerspectiveCamera;
  private system: import("three").Group;
  private dust: import("three").Points;
  private stars: import("three").Points | null = null;
  private lines: import("three").LineSegments | null = null;
  private impulses: import("three").Points | null = null;
  private uniforms: Record<string, { value: number }>;
  private orbit: Orbit = { yaw: 0, pitch: -0.32, distance: 1200, target: [0, 0, 0] };
  private anim: { from: Orbit; to: Orbit; start: number } | null = null;
  private screen = new Float32Array(0);
  private raf = 0;
  private last = 0;
  private dirty = true;
  private interacting = 0;
  private settling = false;
  private settleStart = 0;
  private revealAt = 0;
  private pendingFrame: false | "instant" | "animate" = false;
  private impulseClock = 0;
  readonly reduced = prefersReducedMotion();
  private w = 0;
  private h = 0;
  private dpr = 1;
  private font: string;
  readonly frameRate: number;
  readonly renderer2: string;
  hover: SimNode | null = null;
  selectedId: string | null = null;
  matches: Set<string> | null = null;
  emphasis: Map<string, number> | null = null;
  /** Where a search's impulses start: the middle of its hits. */
  private origin: [number, number, number] = [0, 0, 0];
  private originStar: import("three").Points;
  showLabels = true;
  showEdges = true;
  insetBottom = 0;
  insetLeft = 0;
  labelsDrawn = 0;
  onFrame: () => void = () => {};

  /** Laid out, drawn and with the camera at rest. */
  get settled(): boolean {
    return !this.settling && !this.model.running && !this.anim && !this.pendingFrame;
  }

  constructor(private THREE: Three, private container: HTMLElement, readonly model: ForceModel) {
    const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true, powerPreference: "high-performance" });
    this.renderer = renderer;
    renderer.setClearColor(0x000000, 0);
    const gl = renderer.getContext();
    const info = gl.getExtension("WEBGL_debug_renderer_info");
    const device = String(gl.getParameter(info ? info.UNMASKED_RENDERER_WEBGL : gl.RENDERER));
    const software = /swiftshader|llvmpipe|softpipe|software|microsoft basic render/i.test(device);
    // Software WebGL can occupy every CPU (see the Echo avatar): a slow,
    // steady star field there; 30 fps at rest and 60 while it is handled.
    this.frameRate = software ? 10 : 30;
    this.renderer2 = software ? "webgl-software" : "webgl";
    renderer.domElement.className = "absolute inset-0 size-full touch-none select-none";
    renderer.domElement.setAttribute("aria-hidden", "true");
    container.appendChild(renderer.domElement);
    this.labels.className = "pointer-events-none absolute inset-0 size-full";
    this.labels.setAttribute("aria-hidden", "true");
    container.appendChild(this.labels);
    this.labelCtx = this.labels.getContext("2d")!;
    this.font = getComputedStyle(document.body).fontFamily;

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(FOV, 1, 1, 40000);
    this.system = new THREE.Group();
    this.scene.add(this.system);
    this.uniforms = { uTime: { value: 0 }, uScale: { value: 1 }, uFade: { value: 0 }, uTwinkle: { value: this.reduced ? 0 : 1 }, uPixel: { value: 1 } };

    // Distant dust, turning more slowly than the system for depth.
    const dust = new THREE.BufferGeometry();
    const pos = new Float32Array(DUST * 3), phase = new Float32Array(DUST), size = new Float32Array(DUST);
    for (let i = 0; i < DUST; i++) {
      const u = hash01(`d${i}`, 1) * 2 - 1, a = hash01(`d${i}`, 2) * Math.PI * 2, r = 5000 + hash01(`d${i}`, 3) * 9000;
      const s = Math.sqrt(1 - u * u);
      pos.set([r * s * Math.cos(a), r * u, r * s * Math.sin(a)], i * 3);
      phase[i] = hash01(`d${i}`, 4);
      size[i] = 0.8 + hash01(`d${i}`, 5) ** 3 * 2.4;
    }
    dust.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    dust.setAttribute("aPhase", new THREE.BufferAttribute(phase, 1));
    dust.setAttribute("aSize", new THREE.BufferAttribute(size, 1));
    this.dust = new THREE.Points(dust, this.material(DUST_VERTEX, DUST_FRAGMENT));
    this.scene.add(this.dust);

    // The query itself, a pale star the impulses leave from.
    const origin = new THREE.BufferGeometry();
    origin.setAttribute("position", new THREE.BufferAttribute(new Float32Array(3), 3));
    origin.setAttribute("aColor", new THREE.BufferAttribute(new Float32Array([0.78, 0.93, 1]), 3));
    origin.setAttribute("aSize", new THREE.BufferAttribute(new Float32Array([240]), 1));
    origin.setAttribute("aPhase", new THREE.BufferAttribute(new Float32Array([0.5]), 1));
    origin.setAttribute("aAlpha", new THREE.BufferAttribute(new Float32Array([0.9]), 1));
    origin.setAttribute("aMark", new THREE.BufferAttribute(new Float32Array([1]), 1));
    origin.setAttribute("aCore", new THREE.BufferAttribute(new Float32Array([0.2]), 1));
    this.originStar = new THREE.Points(origin, this.material(STAR_VERTEX, STAR_FRAGMENT));
    this.originStar.frustumCulled = false;
    this.originStar.visible = false;
    this.system.add(this.originStar);
    this.resize();
  }

  /** Light a search's hits, and send the impulses from the query to them. */
  setEmphasis(emphasis: Map<string, number> | null) {
    if (emphasis === this.emphasis) return;
    this.emphasis = emphasis;
    this.originStar.visible = !!emphasis?.size;
    this.syncPositions();
    for (let i = 0; i < this.impulseLinks.length; i++) this.aimImpulse(i, true);
    this.syncStates();
  }

  private material(vertexShader: string, fragmentShader: string) {
    const { THREE } = this;
    return new THREE.ShaderMaterial({
      vertexShader, fragmentShader, uniforms: this.uniforms,
      transparent: true, depthWrite: false, depthTest: false, blending: THREE.AdditiveBlending,
    });
  }

  destroy() {
    cancelAnimationFrame(this.raf);
    this.model.stop();
    for (const object of [this.dust, this.originStar, this.stars, this.lines, this.impulses]) {
      if (!object) continue;
      object.geometry.dispose();
      (object.material as import("three").Material).dispose();
    }
    this.renderer.dispose();
    this.renderer.domElement.remove();
    this.labels.remove();
  }

  resize() {
    const { clientWidth: w, clientHeight: h } = this.container;
    if (!w || !h) return;
    this.w = w; this.h = h;
    this.dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.renderer.setPixelRatio(this.dpr);
    this.renderer.setSize(w, h, false);
    this.labels.width = Math.round(w * this.dpr);
    this.labels.height = Math.round(h * this.dpr);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    // Point sizes are world sizes: px = size * scale / depth.
    this.uniforms.uScale.value = (h * this.dpr) / 2 / Math.tan((FOV * Math.PI) / 360);
    this.uniforms.uPixel.value = this.dpr;
    this.invalidate();
  }

  // ── Data ───────────────────────────────────────────────────────────

  setData(nodes: GraphViewProps["nodes"], edges: GraphViewProps["edges"]) {
    const { THREE } = this;
    const first = !this.model.nodes.length;
    this.model.setData(nodes, edges);
    this.hover = null;
    for (const object of [this.stars, this.lines, this.impulses]) {
      if (!object) continue;
      this.system.remove(object);
      object.geometry.dispose();
    }
    const n = this.model.nodes.length, l = this.model.links.length;
    const starGeometry = new THREE.BufferGeometry();
    const color = new Float32Array(n * 3), size = new Float32Array(n), phase = new Float32Array(n);
    const c = new THREE.Color();
    this.model.nodes.forEach((s, i) => {
      const kind = s.node.kind;
      c.set(kind === "project" ? nodeColor(s.node) : STAR[kind]);
      if (kind === "project") c.offsetHSL(0, 0.05, 0.08);
      color.set([c.r, c.g, c.b], i * 3);
      // The scope's own node is the system's sun.
      size[i] = s.r * 2 * GLOW[kind] * (s.node.focus ? 1.9 : 1);
      phase[i] = hash01(s.id, 7);
    });
    starGeometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(n * 3), 3));
    starGeometry.setAttribute("aColor", new THREE.BufferAttribute(color, 3));
    starGeometry.setAttribute("aSize", new THREE.BufferAttribute(size, 1));
    starGeometry.setAttribute("aPhase", new THREE.BufferAttribute(phase, 1));
    starGeometry.setAttribute("aCore", new THREE.BufferAttribute(new Float32Array(this.model.nodes.map((s) => 1 / GLOW[s.node.kind])), 1));
    starGeometry.setAttribute("aAlpha", new THREE.BufferAttribute(new Float32Array(n).fill(1), 1));
    starGeometry.setAttribute("aMark", new THREE.BufferAttribute(new Float32Array(n), 1));
    this.stars = new THREE.Points(starGeometry, (this.stars?.material as import("three").ShaderMaterial) ?? this.material(STAR_VERTEX, STAR_FRAGMENT));
    this.stars.frustumCulled = false;

    const lineGeometry = new THREE.BufferGeometry();
    const lineColor = new Float32Array(l * 6);
    this.model.links.forEach((link, i) => {
      const kindColor = EDGE_KINDS[link.edge.kind].color;
      for (const [j, end] of [link.source, link.target].entries()) {
        c.set(kindColor ?? (end.node.kind === "project" ? nodeColor(end.node) : "#7cc8ff"));
        lineColor.set([c.r, c.g, c.b], i * 6 + j * 3);
      }
    });
    lineGeometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(l * 6), 3));
    lineGeometry.setAttribute("aColor", new THREE.BufferAttribute(lineColor, 3));
    lineGeometry.setAttribute("aAlpha", new THREE.BufferAttribute(new Float32Array(l * 2), 1));
    this.lines = new THREE.LineSegments(lineGeometry, (this.lines?.material as import("three").ShaderMaterial) ?? this.material(LINE_VERTEX, LINE_FRAGMENT));
    this.lines.frustumCulled = false;

    // A fixed pool of impulses, each re-aimed at a connection now and then.
    const pool = l ? Math.min(IMPULSES, Math.max(24, l)) : 0;
    const impulseGeometry = new THREE.BufferGeometry();
    impulseGeometry.setAttribute("aFrom", new THREE.BufferAttribute(new Float32Array(pool * TRAIL * 3), 3));
    impulseGeometry.setAttribute("aTo", new THREE.BufferAttribute(new Float32Array(pool * TRAIL * 3), 3));
    impulseGeometry.setAttribute("aMotion", new THREE.BufferAttribute(new Float32Array(pool * TRAIL * 4), 4));
    // three.js needs a position attribute to know the vertex count.
    impulseGeometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(pool * TRAIL * 3), 3));
    this.impulses = new THREE.Points(impulseGeometry, (this.impulses?.material as import("three").ShaderMaterial) ?? this.material(IMPULSE_VERTEX, IMPULSE_FRAGMENT));
    this.impulses.frustumCulled = false;
    this.impulseLinks = new Int32Array(pool);
    this.impulseForward = new Uint8Array(pool);
    for (let i = 0; i < pool; i++) this.aimImpulse(i, true);

    this.system.add(this.lines, this.impulses, this.stars);
    this.screen = new Float32Array(n * 3);
    this.syncPositions();
    this.syncStates();
    this.settling = true;
    this.settleStart = performance.now();
    this.pendingFrame = first ? "instant" : "animate";
    this.onFrame();
    this.kick();
  }

  private impulseLinks = new Int32Array(0);
  private impulseForward = new Uint8Array(0);

  /** Point an impulse at a connection: heavier ones, and those of the hovered
   * or selected star, more often. */
  private aimImpulse(slot: number, initial = false) {
    const links = this.model.links;
    if (!links.length || !this.impulses) return;
    const hits = this.emphasis;
    if (hits?.size && Math.random() < 0.65) {
      // From the query to a hit: better-ranked hits draw more, and faster.
      const nodes = this.model.nodes;
      let index = -1, strength = 0;
      for (let tries = 0; tries < 12; tries++) {
        const i = Math.floor(Math.random() * nodes.length);
        const s = hits.get(nodes[i].id);
        if (s !== undefined && Math.random() < s) { index = i; strength = s; break; }
      }
      if (index >= 0) {
        this.impulseLinks[slot] = -1 - index;
        this.impulseForward[slot] = 1;
        const motion = this.impulses.geometry.getAttribute("aMotion") as import("three").BufferAttribute;
        const phase = Math.random(), speed = 0.3 + strength * 0.45, seed = Math.random();
        for (let t = 0; t < TRAIL; t++) motion.setXYZW(slot * TRAIL + t, initial ? phase : (phase + 0.5) % 1, speed, t * 0.012, seed);
        motion.needsUpdate = true;
        this.placeImpulse(slot);
        return;
      }
    }
    const focus = this.hover?.id ?? this.selectedId;
    let pick = Math.floor(Math.random() * links.length);
    for (let tries = 0; tries < 4; tries++) {
      const link = links[pick];
      const touches = focus && (link.source.id === focus || link.target.id === focus);
      if (touches || Math.random() < 0.35 + link.strength * 0.65) break;
      pick = Math.floor(Math.random() * links.length);
    }
    if (focus && Math.random() < 0.5) {
      const own = links.filter((link) => link.source.id === focus || link.target.id === focus);
      if (own.length) pick = links.indexOf(own[Math.floor(Math.random() * own.length)]);
    }
    this.impulseLinks[slot] = pick;
    this.impulseForward[slot] = Math.random() < 0.7 ? 1 : 0;
    const motion = this.impulses.geometry.getAttribute("aMotion") as import("three").BufferAttribute;
    const phase = Math.random(), speed = 0.28 + Math.random() * 0.3, seed = Math.random();
    for (let t = 0; t < TRAIL; t++) {
      motion.setXYZW(slot * TRAIL + t, initial ? phase : (phase + 0.5) % 1, speed, t * 0.012, seed);
    }
    motion.needsUpdate = true;
    this.placeImpulse(slot);
  }

  private placeImpulse(slot: number) {
    if (!this.impulses) return;
    const from = this.impulses.geometry.getAttribute("aFrom") as import("three").BufferAttribute;
    const to = this.impulses.geometry.getAttribute("aTo") as import("three").BufferAttribute;
    const target = this.impulseLinks[slot];
    if (target < 0) {
      const hit = this.model.nodes[-1 - target];
      if (!hit) return;
      for (let t = 0; t < TRAIL; t++) {
        from.setXYZ(slot * TRAIL + t, ...this.origin);
        to.setXYZ(slot * TRAIL + t, hit.x, -hit.y, this.zOf(hit));
      }
      from.needsUpdate = true;
      to.needsUpdate = true;
      return;
    }
    const link = this.model.links[target];
    if (!link) return;
    const [a, b] = this.impulseForward[slot] ? [link.source, link.target] : [link.target, link.source];
    for (let t = 0; t < TRAIL; t++) {
      const v = slot * TRAIL + t;
      from.setXYZ(v, a.x, -a.y, this.zOf(a));
      to.setXYZ(v, b.x, -b.y, this.zOf(b));
    }
    from.needsUpdate = true;
    to.needsUpdate = true;
  }

  private zOf(s: SimNode): number {
    return s.node.focus ? 0 : (hash01(s.id, 3) - 0.5) * DEPTH[s.node.kind];
  }

  private syncPositions() {
    if (!this.stars || !this.lines) return;
    if (this.emphasis?.size) {
      let x = 0, y = 0, count = 0;
      for (const s of this.model.nodes) if (this.emphasis.has(s.id)) { x += s.x; y += s.y; count += 1; }
      if (count) this.origin = [x / count, -y / count, 0];
      const at = this.originStar.geometry.getAttribute("position") as import("three").BufferAttribute;
      at.setXYZ(0, ...this.origin);
      at.needsUpdate = true;
    }
    const pos = this.stars.geometry.getAttribute("position") as import("three").BufferAttribute;
    this.model.nodes.forEach((s, i) => pos.setXYZ(i, s.x, -s.y, this.zOf(s)));
    pos.needsUpdate = true;
    const line = this.lines.geometry.getAttribute("position") as import("three").BufferAttribute;
    this.model.links.forEach((link, i) => {
      line.setXYZ(i * 2, link.source.x, -link.source.y, this.zOf(link.source));
      line.setXYZ(i * 2 + 1, link.target.x, -link.target.y, this.zOf(link.target));
    });
    line.needsUpdate = true;
    for (let i = 0; i < this.impulseLinks.length; i++) this.placeImpulse(i);
  }

  /** Hover, selection and find as per-star brightness and rings. */
  syncStates() {
    if (!this.stars || !this.lines) return;
    const { matches, selectedId } = this;
    const hoverId = this.hover?.id;
    const near = hoverId ? this.model.adjacency.get(hoverId) : null;
    const alpha = this.stars.geometry.getAttribute("aAlpha") as import("three").BufferAttribute;
    const mark = this.stars.geometry.getAttribute("aMark") as import("three").BufferAttribute;
    this.model.nodes.forEach((s, i) => {
      let a = matches ? (matches.has(s.id) ? 1 : 0.14) : this.emphasis && !this.emphasis.has(s.id) ? 0.3 : 1;
      if (hoverId && s.id !== hoverId && !near?.has(s.id)) a = Math.min(a, 0.35);
      alpha.setX(i, a);
      mark.setX(i, matches?.has(s.id) ? 3 : s.id === selectedId ? 2 : s.id === hoverId ? 1 : 0);
    });
    alpha.needsUpdate = true;
    mark.needsUpdate = true;
    const lineAlpha = this.lines.geometry.getAttribute("aAlpha") as import("three").BufferAttribute;
    this.model.links.forEach((link, i) => {
      const s = link.source.id, t = link.target.id;
      let a = 0.13 + link.strength * 0.2;
      if (matches && !(matches.has(s) && matches.has(t))) a = 0.025;
      else if (this.emphasis && !this.emphasis.has(s) && !this.emphasis.has(t)) a *= 0.45;
      else if (hoverId) a = s === hoverId || t === hoverId ? 0.65 : 0.04;
      if (selectedId && (s === selectedId || t === selectedId)) a = Math.max(a, 0.5);
      lineAlpha.setX(i * 2, a);
      lineAlpha.setX(i * 2 + 1, a);
    });
    lineAlpha.needsUpdate = true;
    this.lines.visible = this.showEdges;
    if (this.impulses) this.impulses.visible = this.showEdges && !this.reduced;
    this.invalidate();
  }

  // ── Camera ─────────────────────────────────────────────────────────

  private moveTo(to: Partial<Orbit>, animate: boolean) {
    const next = { ...(this.anim?.to ?? this.orbit), ...to };
    next.distance = Math.min(20000, Math.max(60, next.distance));
    next.pitch = Math.max(-1.3, Math.min(1.3, next.pitch));
    if (animate && !this.reduced) this.anim = { from: { ...this.orbit, target: [...this.orbit.target] }, to: next, start: performance.now() };
    else { this.anim = null; this.orbit = next; }
    this.invalidate();
  }

  /** The distance at which the whole system fits the view. */
  private framing(): Partial<Orbit> {
    const b = this.model.bounds();
    if (!b) return {};
    const cx = (b.minX + b.maxX) / 2, cy = (b.minY + b.maxY) / 2;
    let radius = 60;
    for (const s of this.model.nodes) radius = Math.max(radius, Math.hypot(s.x - cx, s.y - cy, this.zOf(s)) + s.r);
    const free = Math.max(1, this.w - Math.min(this.insetLeft, this.w * 0.6)) / Math.max(1, this.h);
    const vertical = (FOV * Math.PI) / 360, horizontal = Math.atan(Math.tan(vertical) * free);
    return { target: [cx, -cy, 0], distance: (radius / Math.sin(Math.min(vertical, horizontal))) * 0.92 };
  }

  frame(animate: boolean) { this.moveTo({ ...this.framing(), yaw: 0, pitch: -0.32 }, animate); }
  fit() { this.frame(true); }
  zoomBy(ratio: number) { this.moveTo({ distance: (this.anim?.to ?? this.orbit).distance / ratio }, true); }
  dolly(factor: number) { this.pendingFrame = false; this.moveTo({ distance: this.orbit.distance / factor }, false); }
  rotateBy(dx: number, dy: number) { this.pendingFrame = false; this.moveTo({ yaw: this.orbit.yaw + dx * 0.005, pitch: this.orbit.pitch + dy * 0.005 }, false); }

  focus(id: string) {
    const s = this.model.byId.get(id);
    if (!s) return;
    if (!this.settling) this.pendingFrame = false;
    const fit = this.framing().distance ?? this.orbit.distance;
    this.moveTo({ target: [s.x, -s.y, this.zOf(s)], distance: Math.min(this.orbit.distance, Math.max(260, fit * 0.45)) }, true);
  }

  relayout() {
    for (const s of this.model.nodes) { s.x += (Math.random() - 0.5) * 60; s.y += (Math.random() - 0.5) * 60; }
    this.model.reheat(1);
    this.pendingFrame = "animate";
    this.kick();
  }

  setForces(forces: GraphViewProps["forces"]) {
    this.model.setForces(forces);
    this.kick();
  }

  invalidate() {
    this.dirty = true;
    this.kick();
  }

  touch() {
    this.interacting = performance.now();
    this.kick();
  }

  private kick() {
    if (!this.raf) this.raf = requestAnimationFrame(this.loop);
  }

  // ── Frames ─────────────────────────────────────────────────────────

  private loop = (now: number) => {
    this.raf = 0;
    const ambient = !this.reduced;
    const needed = () => ambient || this.dirty || this.settling || this.model.running || !!this.anim || !!this.pendingFrame;
    if (!needed()) return;
    // A hidden page gets no frames at all; the next visible frame resumes.
    if (document.hidden) { this.kick(); return; }
    const handled = now - this.interacting < 800 || !!this.anim;
    const rate = this.frameRate < 30 ? this.frameRate : handled ? 60 : this.frameRate;
    if (now - this.last >= 1000 / rate - 2) {
      this.last = now;
      if (this.model.running) {
        this.model.step(this.settling ? 12 : 6);
        this.syncPositions();
        if (this.settling && (!this.model.running || now - this.settleStart > 1500)) this.reveal(now);
      } else if (this.settling) this.reveal(now);
      if (!this.model.running && this.pendingFrame && !this.settling) {
        this.frame(this.pendingFrame === "animate");
        this.pendingFrame = false;
      }
      this.dirty = false;
      this.render(now);
    }
    if (needed()) this.kick();
  };

  private reveal(now: number) {
    this.settling = false;
    if (this.pendingFrame) {
      this.frame(this.pendingFrame === "animate");
      // Shown before the layout settled: frame it again once it has,
      // unless the system is turned or zoomed by hand first.
      this.pendingFrame = this.model.running ? "animate" : false;
    }
    this.revealAt = now;
  }

  private render(now: number) {
    if (this.settling) return;
    const t = now / 1000;
    const u = this.uniforms;
    u.uTime.value = this.reduced ? 0 : t;
    u.uFade.value = this.reduced ? 1 : Math.min(1, (now - this.revealAt) / 500);
    if (this.anim) {
      const k = Math.min(1, (now - this.anim.start) / VIEW_MS), e = ease(k);
      const { from, to } = this.anim;
      this.orbit = {
        yaw: from.yaw + (to.yaw - from.yaw) * e,
        pitch: from.pitch + (to.pitch - from.pitch) * e,
        distance: from.distance + (to.distance - from.distance) * e,
        target: [0, 1, 2].map((i) => from.target[i] + (to.target[i] - from.target[i]) * e) as [number, number, number],
      };
      if (k >= 1) this.anim = null;
    }
    // A slow drift: the system sways about its middle, the dust turns behind.
    const drift = this.reduced ? 0 : Math.sin(t * 0.045) * 0.32;
    this.system.rotation.set(this.orbit.pitch + (this.reduced ? 0 : Math.sin(t * 0.031) * 0.04), this.orbit.yaw + drift, 0);
    this.system.updateMatrixWorld(true);
    this.dust.rotation.set(this.orbit.pitch * 0.4, this.orbit.yaw * 0.4 + t * (this.reduced ? 0 : 0.004), 0);
    const target = new this.THREE.Vector3(...this.orbit.target).applyMatrix4(this.system.matrixWorld);
    this.camera.position.set(target.x, target.y, target.z + this.orbit.distance);
    this.camera.lookAt(target);
    // Panels over the canvas move the picture's centre into the free part.
    const shiftX = -Math.min(this.insetLeft, this.w * 0.6) / 2, shiftY = (this.insetBottom * this.h) / 2;
    if (shiftX || shiftY) this.camera.setViewOffset(this.w, this.h, shiftX, shiftY, this.w, this.h);
    else if (this.camera.view?.enabled) this.camera.clearViewOffset();
    this.camera.updateMatrixWorld();

    // Re-aim a few impulses each second.
    if (!this.reduced && this.impulseLinks.length && now - this.impulseClock > 400) {
      this.impulseClock = now;
      for (let i = 0; i < Math.max(1, this.impulseLinks.length / 12); i++) this.aimImpulse(Math.floor(Math.random() * this.impulseLinks.length));
    }
    this.renderer.render(this.scene, this.camera);
    this.project();
    this.labelsDrawn = this.showLabels ? this.drawLabels() : this.clearLabels();
    this.onFrame();
  }

  /** Each star's place on screen (px) and pick radius, for labels and the pointer. */
  private project() {
    const v = new this.THREE.Vector3();
    const scale = this.uniforms.uScale.value / this.dpr;
    const camPos = this.camera.position;
    this.model.nodes.forEach((s, i) => {
      v.set(s.x, -s.y, this.zOf(s)).applyMatrix4(this.system.matrixWorld);
      const depth = v.distanceTo(camPos);
      v.project(this.camera);
      const behind = v.z > 1;
      this.screen[i * 3] = behind ? -1e5 : (v.x * 0.5 + 0.5) * this.w;
      this.screen[i * 3 + 1] = behind ? -1e5 : (-v.y * 0.5 + 0.5) * this.h;
      this.screen[i * 3 + 2] = Math.max(6, (s.r * scale) / Math.max(depth, 1));
    });
  }

  screenOf(id: string): [number, number] | null {
    const i = this.model.nodes.findIndex((s) => s.id === id);
    return i < 0 ? null : [this.screen[i * 3], this.screen[i * 3 + 1]];
  }

  nodeAt(px: number, py: number): SimNode | null {
    if (this.settling) return null;
    let best: SimNode | null = null, bestScore = Infinity;
    this.model.nodes.forEach((s, i) => {
      const dx = this.screen[i * 3] - px, dy = this.screen[i * 3 + 1] - py, r = this.screen[i * 3 + 2] + 3;
      const d2 = dx * dx + dy * dy;
      // Inside the star; nearer centres win, bigger stars on ties.
      if (d2 <= r * r && d2 / (r * r) < bestScore) { bestScore = d2 / (r * r); best = s; }
    });
    return best;
  }

  private clearLabels() {
    this.labelCtx.setTransform(1, 0, 0, 1, 0, 0);
    this.labelCtx.clearRect(0, 0, this.labels.width, this.labels.height);
    return 0;
  }

  private drawLabels(): number {
    const ctx = this.labelCtx;
    this.clearLabels();
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    const forced = (s: SimNode) => s.id === this.hover?.id || s.id === this.selectedId || !!s.node.focus || !!this.matches?.has(s.id);
    const order = this.model.nodes.map((s, i) => [s, i] as const)
      .filter(([s, i]) => forced(s) || s.node.kind === "project" || this.screen[i * 3 + 2] > 9)
      .sort((a, b) => Number(forced(b[0])) - Number(forced(a[0])) || b[0].priority - a[0].priority);
    const placed: Array<[number, number, number, number]> = [];
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    ctx.lineJoin = "round";
    ctx.lineWidth = 3;
    ctx.strokeStyle = "rgba(3, 6, 16, 0.85)";
    let count = 0;
    for (const [s, i] of order) {
      if (count >= 80) break;
      const x = this.screen[i * 3], y = this.screen[i * 3 + 1] + this.screen[i * 3 + 2] * 0.9 + 2;
      if (x < -100 || x > this.w + 100 || y < -20 || y > this.h + 20) continue;
      const strong = s.node.kind === "project" || !!s.node.focus;
      ctx.font = `${strong || s.id === this.selectedId ? 600 : 400} ${strong ? 12 : 10.5}px ${this.font}`;
      const text = shortLabel(s.node.label);
      const tw = ctx.measureText(text).width;
      const rect: [number, number, number, number] = [x - tw / 2 - 3, y, x + tw / 2 + 3, y + 15];
      if (!forced(s) && placed.some((r) => r[0] < rect[2] && r[2] > rect[0] && r[1] < rect[3] && r[3] > rect[1])) continue;
      placed.push(rect);
      const dim = this.matches ? (this.matches.has(s.id) ? 1 : 0.3) : this.hover && !forced(s) && !this.model.adjacency.get(this.hover.id)?.has(s.id) ? 0.35 : 1;
      ctx.globalAlpha = dim * (this.uniforms.uFade.value);
      ctx.fillStyle = s.id === this.selectedId ? "#ffffff" : strong ? "rgba(236, 246, 255, 0.95)" : "rgba(200, 225, 250, 0.82)";
      ctx.strokeText(text, x, y);
      ctx.fillText(text, x, y);
      count += 1;
    }
    ctx.globalAlpha = 1;
    return count;
  }
}

export function StarCanvas({
  nodes, edges, showLabels, showEdges, forces, selectedId, matches, emphasis, insetBottom, insetLeft, onSelect, onOpen, onUnavailable, ref,
}: GraphViewProps & { onUnavailable: (reason: string) => void }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<StarView | null>(null);
  const tooltip = useRef<TooltipHandle>(null);
  const [ready, setReady] = useState(false);
  const latest = useRef({ nodes, edges, forces, onSelect, onOpen, onUnavailable });
  useEffect(() => { latest.current = { nodes, edges, forces, onSelect, onOpen, onUnavailable }; });

  useImperativeHandle(ref, () => ({
    zoomBy: (ratio) => viewRef.current?.zoomBy(ratio),
    fit: () => viewRef.current?.fit(),
    relayout: () => viewRef.current?.relayout(),
    focus: (id) => viewRef.current?.focus(id),
  }), []);

  useEffect(() => {
    const container = containerRef.current!;
    let cancelled = false;
    let release: (() => void) | undefined;
    (async () => {
      // three.js loads only when the star view is on.
      const THREE = await import("three");
      if (cancelled) return;
      let view: StarView;
      try {
        view = new StarView(THREE, container, new ForceModel(latest.current.forces));
      } catch (error) {
        latest.current.onUnavailable(error instanceof Error ? error.message : String(error));
        return;
      }
      viewRef.current = view;
      const canvas = view.renderer.domElement;
      const debug: GraphDebug = { screenOf: (id) => view.screenOf(id), labels: 0, renderer: view.renderer2 };
      (container as HTMLElement & { graphDebug?: GraphDebug }).graphDebug = debug;
      container.dataset.renderer = view.renderer2;
      container.dataset.frameRateLimit = String(view.frameRate);
      // Reduced motion: a still star field, drawn only when something changes.
      container.dataset.motion = view.reduced ? "reduced" : "full";
      view.onFrame = () => {
        debug.labels = view.labelsDrawn;
        container.dataset.ready = view.settled ? "true" : "settling";
        container.dataset.nodes = String(view.model.nodes.length);
      };
      const onLost = (event: Event) => { event.preventDefault(); latest.current.onUnavailable("The WebGL context was lost"); };
      canvas.addEventListener("webglcontextlost", onLost);

      const pointers = new Map<number, { x: number; y: number }>();
      let press: { id: number; x: number; y: number; moved: boolean } | null = null;
      let pinch = 0;
      const local = (e: { clientX: number; clientY: number }) => {
        const rect = canvas.getBoundingClientRect();
        return { x: e.clientX - rect.left, y: e.clientY - rect.top };
      };
      const setHover = (node: SimNode | null, x: number, y: number) => {
        if (view.hover === node) { if (node) tooltip.current?.move(x, y); return; }
        view.hover = node;
        canvas.style.cursor = node ? "pointer" : "";
        tooltip.current?.show(node?.node ?? null, x, y);
        view.syncStates();
      };
      const onDown = (e: PointerEvent) => {
        const p = local(e);
        pointers.set(e.pointerId, p);
        canvas.setPointerCapture(e.pointerId);
        if (pointers.size === 2) {
          const [a, b] = [...pointers.values()];
          pinch = Math.hypot(a.x - b.x, a.y - b.y);
          press = null;
          return;
        }
        press = { id: e.pointerId, ...p, moved: false };
      };
      const onMove = (e: PointerEvent) => {
        const p = local(e);
        const prev = pointers.get(e.pointerId);
        if (prev) pointers.set(e.pointerId, p);
        if (pointers.size === 2 && prev) {
          const [a, b] = [...pointers.values()];
          const d = Math.hypot(a.x - b.x, a.y - b.y);
          if (pinch) view.dolly(d / pinch);
          pinch = d;
          view.touch();
          return;
        }
        if (press && press.id === e.pointerId && prev) {
          if (!press.moved && Math.hypot(p.x - press.x, p.y - press.y) > 3) {
            press.moved = true;
            setHover(null, p.x, p.y);
          }
          // Dragging turns the system.
          if (press.moved) { view.rotateBy(p.x - prev.x, p.y - prev.y); view.touch(); }
          return;
        }
        if (e.pointerType === "mouse" || e.pointerType === "pen") setHover(view.nodeAt(p.x, p.y), p.x, p.y);
      };
      const onUp = (e: PointerEvent) => {
        pointers.delete(e.pointerId);
        if (pointers.size < 2) pinch = 0;
        if (!press || press.id !== e.pointerId) return;
        const { moved, x, y } = press;
        press = null;
        if (!moved) latest.current.onSelect(view.nodeAt(x, y)?.id ?? null);
      };
      const onCancel = (e: PointerEvent) => { pointers.delete(e.pointerId); press = null; };
      const onLeave = () => { if (!press) setHover(null, 0, 0); };
      const onDouble = (e: MouseEvent) => {
        const p = local(e);
        const node = view.nodeAt(p.x, p.y);
        if (node) latest.current.onOpen(node.id);
      };
      const onWheel = (e: WheelEvent) => {
        e.preventDefault();
        const delta = e.deltaY * (e.deltaMode === 1 ? 33 : e.deltaMode === 2 ? 400 : 1);
        view.dolly(Math.exp(-delta * (e.ctrlKey ? 0.01 : 0.0015)));
        view.touch();
        setHover(null, 0, 0);
      };
      canvas.addEventListener("pointerdown", onDown);
      canvas.addEventListener("pointermove", onMove);
      canvas.addEventListener("pointerup", onUp);
      canvas.addEventListener("pointercancel", onCancel);
      canvas.addEventListener("pointerleave", onLeave);
      canvas.addEventListener("dblclick", onDouble);
      canvas.addEventListener("wheel", onWheel, { passive: false });
      const resize = new ResizeObserver(() => view.resize());
      resize.observe(container);
      release = () => {
        resize.disconnect();
        canvas.removeEventListener("webglcontextlost", onLost);
        view.destroy();
        viewRef.current = null;
        delete (container as HTMLElement & { graphDebug?: GraphDebug }).graphDebug;
      };
      setReady(true);
    })().catch((error) => { if (!cancelled) latest.current.onUnavailable(error instanceof Error ? error.message : String(error)); });
    return () => { cancelled = true; release?.(); };
  }, []);

  useEffect(() => { if (ready) viewRef.current?.setData(nodes, edges); }, [ready, nodes, edges]);
  useEffect(() => { if (ready) viewRef.current?.setForces(forces); }, [ready, forces]);
  useEffect(() => {
    const view = viewRef.current;
    if (!ready || !view) return;
    Object.assign(view, { showLabels, showEdges, selectedId, matches, insetBottom, insetLeft });
    view.syncStates();
  }, [ready, showLabels, showEdges, selectedId, matches, insetBottom, insetLeft]);
  useEffect(() => { if (ready) viewRef.current?.setEmphasis(emphasis); }, [ready, emphasis, nodes]);

  return (
    <div className="absolute inset-0 overflow-hidden bg-[radial-gradient(ellipse_at_50%_42%,#10213f_0%,#081022_45%,#03060f_100%)]">
      <div ref={containerRef} className="absolute inset-0" data-graph-view="stars" />
      {!ready && <span className="sr-only" role="status">Loading the star view…</span>}
      <GraphTooltip ref={tooltip} />
    </div>
  );
}

