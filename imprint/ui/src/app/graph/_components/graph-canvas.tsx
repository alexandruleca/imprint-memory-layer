"use client";

import { useEffect, useImperativeHandle, useRef } from "react";
import { EDGE_KINDS, nodeColor, readGraphTheme, shortLabel, type GraphTheme } from "./graph-style";
import { ForceModel, prefersReducedMotion, type GraphDebug, type GraphViewProps, type SimNode } from "./graph-model";
import { GraphTooltip, type TooltipHandle } from "./graph-tooltip";

export type { GraphCanvasHandle, GraphForces } from "./graph-model";

const PADDING = 48;
const ZOOM_MIN = 0.15, ZOOM_MAX = 4;
const VIEW_MS = 350;
const FADE_MS = 220;
/** Labels beyond this many are dropped, lowest priority first. */
const LABEL_CAP = 400;
const MATCH = "#f59e0b";
/** Past this many nodes the canvas trades detail for frame time. */
const LARGE = 800;

interface Camera { k: number; x: number; y: number }

const ease = (t: number) => (t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2);

/** Paths grouped by style, drawn with one stroke or fill per group. */
class Batches {
  private groups = new Map<string, { path: Path2D; color: string; alpha: number; width: number; dash?: number[] }>();

  path(color: string, alpha: number, width = 0, dash?: number[]): Path2D {
    const a = Math.round(alpha * 50) / 50;
    const key = `${color}|${a}|${width}|${dash?.join(",") ?? ""}`;
    let group = this.groups.get(key);
    if (!group) this.groups.set(key, group = { path: new Path2D(), color, alpha: a, width, dash });
    return group.path;
  }

  stroke(ctx: CanvasRenderingContext2D) {
    for (const g of this.groups.values()) {
      if (g.alpha <= 0) continue;
      ctx.globalAlpha = g.alpha;
      ctx.strokeStyle = g.color;
      ctx.lineWidth = g.width;
      ctx.setLineDash(g.dash ?? []);
      ctx.stroke(g.path);
    }
    ctx.setLineDash([]);
    this.groups.clear();
  }

  fill(ctx: CanvasRenderingContext2D) {
    for (const g of this.groups.values()) {
      if (g.alpha <= 0) continue;
      ctx.globalAlpha = g.alpha;
      ctx.fillStyle = g.color;
      ctx.fill(g.path);
    }
    this.groups.clear();
  }
}

/**
 * The graph drawn on one 2D canvas. Nothing is retained per element: each
 * frame strokes the edges, fills the nodes and places the labels that fit,
 * and frames are drawn only while something changes (the layout settling,
 * the camera moving, a hover or a state). The layout is stepped a few
 * milliseconds per frame, so it never blocks input.
 */
class GraphView {
  readonly canvas = document.createElement("canvas");
  private ctx: CanvasRenderingContext2D;
  model: ForceModel;
  private theme: GraphTheme;
  private font: string;
  private cam: Camera = { k: 1, x: 0, y: 0 };
  private anim: { from: Camera; to: Camera; start: number } | null = null;
  private w = 0;
  private h = 0;
  private dpr = 1;
  private raf = 0;
  private dirty = true;
  /** The first layout of new data runs before anything is drawn. */
  private settling = false;
  private settleStart = 0;
  private revealAt = 0;
  /** Frame the camera once the layout settles: false, "instant" or "animate". */
  private pendingFrame: false | "instant" | "animate" = false;
  private hasDrawn = false;
  private widths = new Map<string, number>();
  hover: SimNode | null = null;
  selectedId: string | null = null;
  matches: Set<string> | null = null;
  emphasis: Map<string, number> | null = null;
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

  constructor(private container: HTMLElement, model: ForceModel) {
    this.model = model;
    this.ctx = this.canvas.getContext("2d")!;
    this.canvas.className = "absolute inset-0 size-full touch-none select-none";
    this.canvas.setAttribute("aria-hidden", "true");
    container.appendChild(this.canvas);
    this.theme = readGraphTheme(container);
    this.font = getComputedStyle(document.body).fontFamily;
    this.resize();
  }

  destroy() {
    cancelAnimationFrame(this.raf);
    clearTimeout(this.labelTimer);
    this.model.stop();
    this.canvas.remove();
  }

  resize() {
    const { clientWidth: w, clientHeight: h } = this.container;
    if (!w || !h) return;
    const first = !this.w;
    // A large graph draws to a coarser backing store: fill rate, not
    // geometry, is what a full-screen canvas at 2x pays for.
    this.dpr = Math.min(window.devicePixelRatio || 1, this.model.nodes.length > LARGE ? 1.25 : 2);
    this.canvas.width = Math.round(w * this.dpr);
    this.canvas.height = Math.round(h * this.dpr);
    // Keep the middle of the view where it was.
    if (!first) { this.cam.x += (w - this.w) / 2; this.cam.y += (h - this.h) / 2; }
    else this.cam = { k: 1, x: w / 2, y: h / 2 };
    this.w = w; this.h = h;
    this.invalidate();
  }

  retheme() {
    this.theme = readGraphTheme(this.container);
    this.invalidate();
  }

  get dark(): boolean {
    return this.theme.dark;
  }

  setData(nodes: GraphViewProps["nodes"], edges: GraphViewProps["edges"]) {
    const animate = this.hasDrawn;
    const wasLarge = this.model.nodes.length > LARGE;
    this.model.setData(nodes, edges);
    this.hover = null;
    if (wasLarge !== this.model.nodes.length > LARGE) this.resize();
    this.beginSettle(animate ? "animate" : "instant");
    // Report the new state now: nothing is drawn until the layout settles.
    this.onFrame();
  }

  /** Lay out without drawing, then show the result framed. */
  private beginSettle(frame: "instant" | "animate") {
    this.settling = true;
    this.settleStart = performance.now();
    this.pendingFrame = frame;
    this.kick();
  }

  relayout() {
    // A fresh arrangement: scatter around the current middle and settle.
    for (const n of this.model.nodes) {
      n.x += (Math.random() - 0.5) * 60;
      n.y += (Math.random() - 0.5) * 60;
    }
    this.model.reheat(1);
    if (prefersReducedMotion()) this.beginSettle("animate");
    else { this.pendingFrame = "animate"; this.kick(); }
  }

  setForces(forces: GraphViewProps["forces"]) {
    this.model.setForces(forces);
    if (prefersReducedMotion()) this.beginSettle("animate");
    else this.kick();
  }

  invalidate() {
    this.dirty = true;
    this.kick();
  }

  private kick() {
    if (!this.raf) this.raf = requestAnimationFrame(this.loop);
  }

  private loop = (now: number) => {
    this.raf = 0;
    let busy = false;
    if (this.model.running) {
      if (this.settling) {
        // Up to ~12 ms of layout a frame keeps the page responsive; a graph
        // still moving after 1.5 s (3 s when large) is shown moving.
        const large = this.model.nodes.length > LARGE;
        this.model.step(large ? 20 : 12);
        if (!this.model.running || now - this.settleStart > (large ? 3000 : 1500)) this.reveal(now);
      } else {
        this.model.step(6);
        this.dirty = true;
      }
      busy = true;
    } else if (this.settling) {
      this.reveal(now);
    }
    if (!this.model.running && this.pendingFrame && !this.settling) {
      this.frame(this.pendingFrame === "animate");
      this.pendingFrame = false;
    }
    if (this.anim) {
      const t = Math.min(1, (now - this.anim.start) / VIEW_MS), e = ease(t);
      const { from, to } = this.anim;
      this.cam = { k: from.k + (to.k - from.k) * e, x: from.x + (to.x - from.x) * e, y: from.y + (to.y - from.y) * e };
      if (t >= 1) this.anim = null;
      this.dirty = true;
      busy = true;
    }
    if (this.revealAt) {
      // The frame after the fade ends draws at full opacity.
      this.dirty = true;
      if (now - this.revealAt >= FADE_MS) this.revealAt = 0;
      else busy = true;
    }
    if (this.dirty) {
      this.dirty = false;
      this.draw(now);
    }
    if (busy) this.kick();
  };

  private reveal(now: number) {
    this.settling = false;
    // The first graph appears in place; later ones are framed, then shown.
    if (this.pendingFrame) {
      this.frame(this.pendingFrame === "animate" && this.hasDrawn);
      // Shown before the layout settled: frame it again once it has,
      // unless the camera is moved by hand first.
      this.pendingFrame = this.model.running ? "animate" : false;
    }
    this.revealAt = prefersReducedMotion() ? 0 : now;
    this.dirty = true;
  }

  // ── Camera ─────────────────────────────────────────────────────────

  private moveTo(to: Camera, animate: boolean) {
    to.k = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, to.k));
    if (animate && !prefersReducedMotion()) this.anim = { from: { ...this.cam }, to, start: performance.now() };
    else { this.anim = null; this.cam = to; }
    this.invalidate();
  }

  /** Show the graph at its natural size, zooming out only when it does not
   * fit, so a small scope is not blown up to fill the screen. */
  frame(animate: boolean) {
    const b = this.model.bounds();
    if (!b || !this.w) return;
    const bw = b.maxX - b.minX, bh = b.maxY - b.minY;
    const [left, width] = this.free();
    const k = Math.min(1, (width - 2 * PADDING) / Math.max(bw, 1), (this.h - 2 * PADDING) / Math.max(bh, 1));
    const cx = (b.minX + b.maxX) / 2, cy = (b.minY + b.maxY) / 2;
    const kk = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, k));
    this.moveTo({ k: kk, x: left + width / 2 - cx * kk, y: this.h / 2 - cy * kk }, animate);
  }

  /** The part of the canvas no panel covers: [left, width] in px. */
  private free(): [number, number] {
    const left = Math.min(this.insetLeft, this.w * 0.6);
    return [left, this.w - left];
  }

  fit() {
    const b = this.model.bounds();
    if (!b || !this.w) return;
    const [left, width] = this.free();
    const k = Math.min((width - 2 * PADDING) / Math.max(b.maxX - b.minX, 1), (this.h - 2 * PADDING) / Math.max(b.maxY - b.minY, 1));
    const kk = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, k));
    const cx = (b.minX + b.maxX) / 2, cy = (b.minY + b.maxY) / 2;
    this.moveTo({ k: kk, x: left + width / 2 - cx * kk, y: this.h / 2 - cy * kk }, true);
  }

  zoomAt(px: number, py: number, factor: number, animate = false) {
    this.movedAt = performance.now();
    if (!this.settling) this.pendingFrame = false;
    const base = this.anim?.to ?? this.cam;
    const k = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, base.k * factor));
    const f = k / base.k;
    this.moveTo({ k, x: px - (px - base.x) * f, y: py - (py - base.y) * f }, animate);
  }

  zoomBy(ratio: number) {
    this.zoomAt(this.w / 2, this.h / 2, ratio, true);
  }

  /** When the camera last moved by hand. */
  private movedAt = 0;
  private labelTimer = 0;

  panBy(dx: number, dy: number) {
    this.movedAt = performance.now();
    this.pendingFrame = false;
    this.anim = null;
    this.cam = { ...this.cam, x: this.cam.x + dx, y: this.cam.y + dy };
    this.invalidate();
  }

  focus(id: string) {
    const n = this.model.byId.get(id);
    if (!n) return;
    if (!this.settling) this.pendingFrame = false;
    const k = Math.max(this.cam.k, 1);
    const inset = this.insetBottom * this.h;
    const [left, width] = this.free();
    this.moveTo({ k, x: left + width / 2 - n.x * k, y: (this.h - inset) / 2 - n.y * k }, true);
  }

  toWorld(px: number, py: number): [number, number] {
    return [(px - this.cam.x) / this.cam.k, (py - this.cam.y) / this.cam.k];
  }

  screenOf(id: string): [number, number] | null {
    const n = this.model.byId.get(id);
    return n ? [n.x * this.cam.k + this.cam.x, n.y * this.cam.k + this.cam.y] : null;
  }

  /** The topmost node under a point (px), with a few px of slack. */
  nodeAt(px: number, py: number): SimNode | null {
    if (this.settling) return null;
    const [wx, wy] = this.toWorld(px, py);
    const slack = 4 / this.cam.k;
    const nodes = this.model.nodes;
    for (let i = nodes.length - 1; i >= 0; i--) {
      const n = nodes[i];
      const r = n.r + slack, dx = n.x - wx, dy = n.y - wy;
      if (dx * dx + dy * dy <= r * r) return n;
    }
    return null;
  }

  // ── Drawing ────────────────────────────────────────────────────────

  private textWidth(text: string, weight: number, size: number): number {
    const key = `${weight}|${text}`;
    let w = this.widths.get(key);
    if (w === undefined) {
      this.ctx.font = `${weight} 100px ${this.font}`;
      w = this.ctx.measureText(text).width;
      if (this.widths.size > 20000) this.widths.clear();
      this.widths.set(key, w);
    }
    return (w * size) / 100;
  }

  private draw(now: number) {
    const { ctx, dpr, w, h, cam, theme, model } = this;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    // While the first layout of new data runs, the previous picture stays.
    if (this.settling) return;
    ctx.clearRect(0, 0, w, h);
    if (model.nodes.length) this.hasDrawn = true;
    const fade = this.revealAt ? Math.min(1, (now - this.revealAt) / FADE_MS) : 1;
    const hoverSet = this.hover ? model.adjacency.get(this.hover.id) : null;
    const hoverId = this.hover?.id;
    const { matches, selectedId } = this;
    const { emphasis } = this;
    const alphaOf = (id: string) => {
      let a = matches ? (matches.has(id) ? 1 : 0.12) : emphasis && !emphasis.has(id) ? 0.5 : 1;
      if (hoverId && id !== hoverId && !hoverSet?.has(id)) a = Math.min(a, 0.4);
      return a * fade;
    };
    // The visible world, with a margin for halos and labels.
    const m = 40 / cam.k;
    const [x0, y0] = this.toWorld(0, 0), [x1, y1] = this.toWorld(w, h);
    const outside = (x: number, y: number, r: number) => x + r < x0 - m || x - r > x1 + m || y + r < y0 - m || y - r > y1 + m;

    ctx.setTransform(dpr * cam.k, 0, 0, dpr * cam.k, dpr * cam.x, dpr * cam.y);
    ctx.lineCap = "round";

    // Strokes and fills are batched by style: a frame is a few paths, not
    // one canvas state change per element.
    const strokes = new Batches(), fills = new Batches();
    if (this.showEdges) {
      for (const l of model.links) {
        const s = l.source, t = l.target;
        if ((s.x < x0 - m && t.x < x0 - m) || (s.x > x1 + m && t.x > x1 + m) || (s.y < y0 - m && t.y < y0 - m) || (s.y > y1 + m && t.y > y1 + m)) continue;
        const kind = EDGE_KINDS[l.edge.kind];
        const fromProject = s.node.kind === "project";
        let a = kind.color ? 0.7 : fromProject ? 0.45 : 0.3;
        if (matches && !(matches.has(s.id) && matches.has(t.id))) a = 0.03;
        else if (hoverId) a = s.id === hoverId || t.id === hoverId ? 0.85 : 0.12;
        if (selectedId && (s.id === selectedId || t.id === selectedId)) a = Math.max(a, 0.8);
        const color = kind.color ?? (fromProject ? s.node.color : theme.edge);
        const dx = t.x - s.x, dy = t.y - s.y, len = Math.hypot(dx, dy) || 1;
        // Lines meet the node outlines, so arrows and rings stay clean.
        const ux = dx / len, uy = dy / len;
        const ex = t.x - ux * (t.r + (kind.arrow ? 1 : 0)), ey = t.y - uy * (t.r + (kind.arrow ? 1 : 0));
        const path = strokes.path(color, a * fade, Math.round((0.6 + l.strength * 1.6) * 4) / 4, kind.dash);
        path.moveTo(s.x + ux * s.r, s.y + uy * s.r);
        path.lineTo(kind.arrow ? ex - ux * 5 : ex, kind.arrow ? ey - uy * 5 : ey);
        if (kind.arrow && len > s.r + t.r + 6) {
          const head = fills.path(color, a * fade);
          head.moveTo(ex, ey);
          head.lineTo(ex - ux * 6 - uy * 3.2, ey - uy * 6 + ux * 3.2);
          head.lineTo(ex - ux * 6 + uy * 3.2, ey - uy * 6 - ux * 3.2);
          head.closePath();
        }
      }
      strokes.stroke(ctx);
      fills.fill(ctx);
    }

    // Nodes layer by kind (chunks underneath); within a layer, halos, then
    // fills, then outlines.
    const halos = new Batches(), outlines = new Batches();
    const circle = (path: Path2D, n: SimNode) => { path.moveTo(n.x + n.r, n.y); path.arc(n.x, n.y, n.r, 0, Math.PI * 2); };
    // Below a few px a node is a dot: a square costs a fraction of an arc
    // to fill, and outlines and halos would not show.
    const dotBelow = 2.5 / cam.k, detailBelow = 3.5 / cam.k;
    const flush = () => { halos.stroke(ctx); fills.fill(ctx); outlines.stroke(ctx); };
    let layer = -1;
    for (const n of model.nodes) {
      if (outside(n.x, n.y, n.r)) continue;
      if (n.depth !== layer) { flush(); layer = n.depth; }
      const node = n.node;
      // Search hits are drawn in ink, their rank cut out of them.
      const color = node.rank !== undefined ? theme.focusRing : theme.kind[node.kind] || nodeColor(node);
      const a = alphaOf(n.id);
      const strong = node.kind === "project" || !!node.focus;
      const topic = node.kind === "topic";
      const fill = topic ? 0.1 : node.kind === "project" ? 0.95 : 0.85;
      if (n.r < dotBelow && !matches?.has(n.id) && n.id !== selectedId && n.id !== hoverId) {
        fills.path(color, a * Math.max(fill, 0.6)).rect(n.x - n.r, n.y - n.r, n.r * 2, n.r * 2);
        continue;
      }
      if (matches?.has(n.id)) circle(halos.path(MATCH, 0.5 * fade, 12), n);
      if (n.id === selectedId) circle(halos.path(theme.focusRing, 0.22 * a, 14), n);
      else if (n.id === hoverId) circle(halos.path(color, 0.2 * a, 8), n);
      else if (strong) circle(halos.path(color, 0.16 * a, node.focus ? 16 : 10), n);
      circle(fills.path(color, a * fill), n);
      if (n.id === selectedId || node.focus) circle(outlines.path(theme.focusRing, a, 2.5), n);
      else if (topic && n.r >= detailBelow) circle(outlines.path(color, a, 1.5), n);
    }
    flush();

    // A search's hits carry their rank.
    if (emphasis && cam.k >= 0.6) {
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillStyle = theme.labelHalo;
      for (const n of model.nodes) {
        const rank = n.node.rank;
        if (rank === undefined || n.r * cam.k < 6 || outside(n.x, n.y, n.r)) continue;
        ctx.globalAlpha = alphaOf(n.id);
        ctx.font = `600 ${Math.min(n.r * 1.05, 11)}px ${this.font}`;
        ctx.fillText(String(rank), n.x, n.y + 0.5);
      }
    }

    // A large graph drops its labels while the camera moves and places
    // them again when it stops.
    const moving = model.nodes.length > LARGE && (!!this.anim || now - this.movedAt < 160);
    if (moving && !this.labelTimer) this.labelTimer = window.setTimeout(() => { this.labelTimer = 0; this.invalidate(); }, 180);
    this.labelsDrawn = this.showLabels && !moving ? this.drawLabels(alphaOf) : 0;
    ctx.globalAlpha = 1;
    this.onFrame();
  }

  /** Labels in screen space at a readable size, placed by priority and
   * skipped where they would overlap one already placed. */
  private drawLabels(alphaOf: (id: string) => number): number {
    const { ctx, dpr, cam, theme, w, h, model } = this;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const scale = Math.min(1.2, Math.max(0.85, cam.k));
    const forced = (n: SimNode) => n.id === this.hover?.id || n.id === this.selectedId || !!n.node.focus || !!this.matches?.has(n.id);
    const candidates: SimNode[] = [];
    for (const n of model.nodes) {
      const sx = n.x * cam.k + cam.x, sy = n.y * cam.k + cam.y;
      if (sx < -150 || sx > w + 150 || sy < -40 || sy > h + 40) continue;
      // Tiny far-away nodes go unlabelled until zoomed in.
      if (!forced(n) && n.r * cam.k < 3 && n.node.kind === "chunk") continue;
      candidates.push(n);
    }
    candidates.sort((a, b) => Number(forced(b)) - Number(forced(a)) || b.priority - a.priority);
    const cell = 80;
    const grid = new Map<number, Array<[number, number, number, number]>>();
    const placed: Array<{ n: SimNode; x: number; y: number; size: number; weight: number; text: string }> = [];
    for (const n of candidates) {
      if (placed.length >= LABEL_CAP) break;
      const strong = n.node.kind === "project" || !!n.node.focus;
      const weight = strong || n.id === this.selectedId ? 600 : 400;
      const size = (strong ? 12 : 10.5) * scale;
      const text = shortLabel(n.node.label);
      const tw = this.textWidth(text, weight, size);
      const x = n.x * cam.k + cam.x, y = n.y * cam.k + cam.y + n.r * cam.k + 3;
      const rect: [number, number, number, number] = [x - tw / 2 - 2, y, x + tw / 2 + 2, y + size + 3];
      const cx0 = Math.floor(rect[0] / cell), cx1 = Math.floor(rect[2] / cell), cy0 = Math.floor(rect[1] / cell), cy1 = Math.floor(rect[3] / cell);
      let clear = true;
      for (let gx = cx0; gx <= cx1 && clear; gx++) {
        for (let gy = cy0; gy <= cy1 && clear; gy++) {
          for (const r of grid.get(gx * 100003 + gy) ?? []) {
            if (r[0] < rect[2] && r[2] > rect[0] && r[1] < rect[3] && r[3] > rect[1]) { clear = false; break; }
          }
        }
      }
      if (!clear && !forced(n)) continue;
      for (let gx = cx0; gx <= cx1; gx++) for (let gy = cy0; gy <= cy1; gy++) {
        const key = gx * 100003 + gy;
        const list = grid.get(key);
        if (list) list.push(rect); else grid.set(key, [rect]);
      }
      placed.push({ n, x, y, size, weight, text });
    }
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    ctx.lineJoin = "round";
    ctx.lineWidth = 3;
    ctx.strokeStyle = theme.labelHalo;
    ctx.fillStyle = theme.label;
    // Halos cost a second text pass; past a few hundred labels they go.
    const halo = placed.length < 250;
    for (const p of placed) {
      ctx.globalAlpha = alphaOf(p.n.id);
      ctx.font = `${p.weight} ${p.size}px ${this.font}`;
      if (halo) ctx.strokeText(p.text, p.x, p.y);
      ctx.fillText(p.text, p.x, p.y);
    }
    return placed.length;
  }
}

export function GraphCanvas({
  nodes, edges, showLabels, showEdges, forces, selectedId, matches, emphasis, insetBottom, insetLeft, onSelect, onOpen, ref,
}: GraphViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<GraphView | null>(null);
  const tooltip = useRef<TooltipHandle>(null);
  const latest = useRef({ onSelect, onOpen, forces });
  useEffect(() => { latest.current = { onSelect, onOpen, forces }; });

  useImperativeHandle(ref, () => ({
    zoomBy: (ratio) => viewRef.current?.zoomBy(ratio),
    fit: () => viewRef.current?.fit(),
    relayout: () => viewRef.current?.relayout(),
    focus: (id) => viewRef.current?.focus(id),
  }), []);

  // One view for the component's lifetime, with its own input handling.
  useEffect(() => {
    const container = containerRef.current!;
    const view = new GraphView(container, new ForceModel(latest.current.forces));
    viewRef.current = view;
    const canvas = view.canvas;
    const debug: GraphDebug = { screenOf: (id) => view.screenOf(id), labels: 0, renderer: "canvas-2d" };
    (container as HTMLElement & { graphDebug?: GraphDebug }).graphDebug = debug;
    view.onFrame = () => {
      debug.labels = view.labelsDrawn;
      debug.dark = view.dark;
      container.dataset.ready = view.settled ? "true" : "settling";
      container.dataset.nodes = String(view.model.nodes.length);
    };

    const pointers = new Map<number, { x: number; y: number }>();
    let press: { id: number; x: number; y: number; node: SimNode | null; moved: boolean } | null = null;
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
      view.invalidate();
    };
    const onDown = (e: PointerEvent) => {
      const p = local(e);
      pointers.set(e.pointerId, p);
      canvas.setPointerCapture(e.pointerId);
      if (pointers.size === 2) {
        const [a, b] = [...pointers.values()];
        pinch = Math.hypot(a.x - b.x, a.y - b.y);
        if (press?.node) view.model.release(press.node);
        press = null;
        return;
      }
      press = { id: e.pointerId, ...p, node: view.nodeAt(p.x, p.y), moved: false };
    };
    const onMove = (e: PointerEvent) => {
      const p = local(e);
      const prev = pointers.get(e.pointerId);
      if (prev) pointers.set(e.pointerId, p);
      if (pointers.size === 2 && prev) {
        const [a, b] = [...pointers.values()];
        const d = Math.hypot(a.x - b.x, a.y - b.y);
        if (pinch) view.zoomAt((a.x + b.x) / 2, (a.y + b.y) / 2, d / pinch);
        pinch = d;
        return;
      }
      if (press && press.id === e.pointerId && prev) {
        if (!press.moved && Math.hypot(p.x - press.x, p.y - press.y) > 3) {
          press.moved = true;
          setHover(null, p.x, p.y);
        }
        if (!press.moved) return;
        if (press.node) {
          const [wx, wy] = view.toWorld(p.x, p.y);
          view.model.drag(press.node, wx, wy);
          view.invalidate();
        } else {
          view.panBy(p.x - prev.x, p.y - prev.y);
        }
        return;
      }
      if (e.pointerType === "mouse" || e.pointerType === "pen") setHover(view.nodeAt(p.x, p.y), p.x, p.y);
    };
    const onUp = (e: PointerEvent) => {
      pointers.delete(e.pointerId);
      if (pointers.size < 2) pinch = 0;
      if (!press || press.id !== e.pointerId) return;
      const { node, moved } = press;
      press = null;
      if (node && moved) { view.model.release(node); view.invalidate(); return; }
      if (!moved) latest.current.onSelect(node?.id ?? null);
    };
    const onCancel = (e: PointerEvent) => {
      pointers.delete(e.pointerId);
      if (press?.node) view.model.release(press.node);
      press = null;
    };
    const onLeave = () => { if (!press) setHover(null, 0, 0); };
    const onDouble = (e: MouseEvent) => {
      const p = local(e);
      const node = view.nodeAt(p.x, p.y);
      if (node) latest.current.onOpen(node.id);
    };
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const p = local(e);
      const delta = e.deltaY * (e.deltaMode === 1 ? 33 : e.deltaMode === 2 ? 400 : 1);
      // A trackpad pinch arrives as a wheel with ctrlKey and small deltas.
      view.zoomAt(p.x, p.y, Math.exp(-delta * (e.ctrlKey ? 0.01 : 0.0015)));
      setHover(null, p.x, p.y);
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
    // Follow the theme: a class or style change on the root, or the OS scheme.
    const themeObserver = new MutationObserver(() => view.retheme());
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["class", "style", "data-theme"] });
    const scheme = matchMedia("(prefers-color-scheme: dark)");
    const onScheme = () => view.retheme();
    scheme.addEventListener("change", onScheme);
    return () => {
      resize.disconnect();
      themeObserver.disconnect();
      scheme.removeEventListener("change", onScheme);
      view.destroy();
      viewRef.current = null;
      delete (container as HTMLElement & { graphDebug?: GraphDebug }).graphDebug;
    };
  }, []);

  // New data: lay it out (nodes the graph showed start where they were).
  useEffect(() => { viewRef.current?.setData(nodes, edges); }, [nodes, edges]);
  useEffect(() => { viewRef.current?.setForces(forces); }, [forces]);
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    Object.assign(view, { showLabels, showEdges, selectedId, matches, emphasis, insetBottom, insetLeft });
    view.invalidate();
  }, [showLabels, showEdges, selectedId, matches, emphasis, insetBottom, insetLeft]);

  return (
    <div className="absolute inset-0">
      <div ref={containerRef} className="absolute inset-0" data-graph-view="graph" />
      <GraphTooltip ref={tooltip} />
    </div>
  );
}
