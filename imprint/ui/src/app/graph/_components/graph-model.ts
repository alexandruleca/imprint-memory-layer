import { forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY, type Simulation, type SimulationLinkDatum, type SimulationNodeDatum } from "d3-force";
import type { GraphEdge, GraphNode, GraphNodeKind } from "@/lib/types";
import { nodeSize } from "./graph-style";

export interface GraphForces {
  repel: number;
  collide: number;
  centerStrength: number;
}

/** What both views of the graph (the graph canvas and the star field) can
 * be asked to do. */
export interface GraphCanvasHandle {
  zoomBy(ratio: number): void;
  fit(): void;
  relayout(): void;
  /** Center a node, zooming in to a readable size if needed. */
  focus(id: string): void;
}

/** What the page gives either view. */
export interface GraphViewProps {
  nodes: GraphNode[];
  edges: GraphEdge[];
  showLabels: boolean;
  showEdges: boolean;
  forces: GraphForces;
  selectedId: string | null;
  /** Nodes matching the find query; null when there is no query. */
  matches: Set<string> | null;
  /** A search scope's hits and how strongly each is lit (1 = best);
   * everything else recedes. Null outside search scopes. */
  emphasis: Map<string, number> | null;
  /** Fraction of the canvas height covered from below (a bottom sheet);
   * focused nodes are centered in the part above it. */
  insetBottom: number;
  /** Px covered from the left (the search results); the graph is framed
   * and focused in the rest. */
  insetLeft: number;
  onSelect: (id: string | null) => void;
  onOpen: (id: string) => void;
  ref?: import("react").Ref<GraphCanvasHandle>;
}

/** Test and diagnostics hook on a view's container element. */
export interface GraphDebug {
  /** A node's position in px within the container, or null. */
  screenOf(id: string): [number, number] | null;
  /** Labels drawn in the last frame. */
  labels: number;
  renderer: string;
  /** Whether the canvas is drawing for a dark page (graph view). */
  dark?: boolean;
}

export const prefersReducedMotion = () =>
  typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;

export interface SimNode extends SimulationNodeDatum {
  id: string;
  node: GraphNode;
  /** Drawn radius in world units. */
  r: number;
  /** Draw and label order: chunks first, projects and the focus last. */
  depth: number;
  /** Label priority: who keeps a label when labels would overlap. */
  priority: number;
  x: number;
  y: number;
}

export interface SimLink extends SimulationLinkDatum<SimNode> {
  edge: GraphEdge;
  source: SimNode;
  target: SimNode;
  /** 0..1 of the heaviest edge of its kind. */
  strength: number;
}

const LINK_DISTANCE: Record<string, number> = { contains: 90, relates: 150, sequence: 36, similar: 90 };
export const KIND_DEPTH: Record<GraphNodeKind, number> = { chunk: 1, source: 2, topic: 3, project: 4 };
const ALPHA_MIN = 0.001;
const ALPHA_DECAY = 0.035;

/** Where each node was last laid out, across scopes and both views, so a
 * node the graph already showed starts from where it was and switching
 * views keeps the picture. */
const remembered = new Map<string, { x: number; y: number }>();
const REMEMBER_MAX = 5000;

/**
 * The graph's layout: a d3-force simulation the views step a few
 * milliseconds per frame, so no layout ever blocks the page, and read
 * positions from directly.
 */
export class ForceModel {
  nodes: SimNode[] = [];
  links: SimLink[] = [];
  byId = new Map<string, SimNode>();
  /** Neighbour ids per node id. */
  adjacency = new Map<string, Set<string>>();
  private sim: Simulation<SimNode, SimLink>;
  private forces: GraphForces;

  constructor(forces: GraphForces) {
    this.forces = forces;
    this.sim = forceSimulation<SimNode, SimLink>([]).alphaMin(ALPHA_MIN).alphaDecay(ALPHA_DECAY).stop();
    this.applyForces();
  }

  get running(): boolean {
    return this.sim.alpha() >= ALPHA_MIN || this.sim.alphaTarget() > 0;
  }

  setData(nodes: GraphNode[], edges: GraphEdge[]): void {
    const maxCount = { project: 1, topic: 1, source: 1, chunk: 1 } as Record<GraphNodeKind, number>;
    for (const n of nodes) maxCount[n.kind] = Math.max(maxCount[n.kind], n.count);
    const previous = this.byId;
    let known = 0;
    // Search hits grow with their rank: the best is the biggest.
    const worst = Math.max(1, ...nodes.map((n) => n.rank ?? 1));
    this.nodes = nodes.map((n, i) => {
      const size = n.rank !== undefined ? 13 + 13 * (1 - (n.rank - 1) / worst) : nodeSize(n, maxCount);
      const old = previous.get(n.id) ?? remembered.get(n.id);
      if (old) known += 1;
      // New nodes start on a sunflower spiral around the middle (d3's own
      // start), which settles fastest and the same way every time.
      const angle = i * Math.PI * (3 - Math.sqrt(5));
      const radius = 10 * Math.sqrt(0.5 + i);
      return {
        id: n.id,
        node: n,
        r: size / 2,
        depth: KIND_DEPTH[n.kind] + (n.focus ? 10 : 0),
        priority: (n.focus ? 10 : 0) + KIND_DEPTH[n.kind] + size / 100,
        x: old?.x ?? radius * Math.cos(angle),
        y: old?.y ?? radius * Math.sin(angle),
      };
    });
    this.byId = new Map(this.nodes.map((n) => [n.id, n]));
    const maxWeight: Record<string, number> = {};
    for (const e of edges) maxWeight[e.kind] = Math.max(maxWeight[e.kind] ?? 1, e.weight);
    this.links = [];
    this.adjacency = new Map(this.nodes.map((n) => [n.id, new Set<string>()]));
    for (const e of edges) {
      const source = this.byId.get(e.source), target = this.byId.get(e.target);
      if (!source || !target) continue;
      this.links.push({ edge: e, source, target, strength: e.weight / (maxWeight[e.kind] ?? 1) });
      this.adjacency.get(source.id)!.add(target.id);
      this.adjacency.get(target.id)!.add(source.id);
    }
    // Draw order: small kinds underneath.
    this.nodes.sort((a, b) => a.depth - b.depth);
    this.sim.nodes(this.nodes);
    // A large graph settles in fewer, costlier ticks.
    this.sim.alphaDecay(this.nodes.length > 800 ? 0.05 : ALPHA_DECAY);
    this.applyForces();
    // A graph that mostly kept its nodes only needs to settle the new ones.
    this.sim.alpha(this.nodes.length && known / this.nodes.length > 0.8 ? 0.3 : 1);
  }

  setForces(forces: GraphForces): void {
    this.forces = forces;
    this.applyForces();
    this.sim.alpha(Math.max(this.sim.alpha(), 0.5));
  }

  reheat(alpha = 1): void {
    this.sim.alpha(alpha);
  }

  /** Run the layout for at most ``budgetMs``; true while it has more to do. */
  step(budgetMs: number): boolean {
    const end = performance.now() + budgetMs;
    let ticks = 0;
    while (this.running && (ticks === 0 || performance.now() < end)) {
      this.sim.tick();
      ticks += 1;
    }
    if (ticks && !this.running) this.remember();
    return this.running;
  }

  /** Hold a node where it is dragged, and let the rest follow. */
  drag(node: SimNode, x: number, y: number): void {
    node.fx = x;
    node.fy = y;
    this.sim.alphaTarget(0.3);
    if (this.sim.alpha() < 0.3) this.sim.alpha(0.3);
  }

  release(node: SimNode): void {
    node.fx = null;
    node.fy = null;
    this.sim.alphaTarget(0);
  }

  bounds(): { minX: number; minY: number; maxX: number; maxY: number } | null {
    if (!this.nodes.length) return null;
    let [minX, minY, maxX, maxY] = [Infinity, Infinity, -Infinity, -Infinity];
    for (const n of this.nodes) {
      minX = Math.min(minX, n.x - n.r);
      maxX = Math.max(maxX, n.x + n.r);
      minY = Math.min(minY, n.y - n.r);
      // Room for the label below.
      maxY = Math.max(maxY, n.y + n.r + 18);
    }
    return { minX, minY, maxX, maxY };
  }

  stop(): void {
    this.sim.stop();
    this.remember();
  }

  remember(): void {
    for (const n of this.nodes) {
      remembered.delete(n.id);
      remembered.set(n.id, { x: n.x, y: n.y });
    }
    for (const key of remembered.keys()) {
      if (remembered.size <= REMEMBER_MAX) break;
      remembered.delete(key);
    }
  }

  private applyForces(): void {
    const { repel, collide, centerStrength } = this.forces;
    this.sim
      .force("link", forceLink<SimNode, SimLink>(this.links)
        .distance((l) => LINK_DISTANCE[l.edge.kind] ?? 110)
        .strength(0.45))
      // Larger nodes push harder, so hubs get room around them.
      .force("charge", forceManyBody<SimNode>().strength((n) => -repel * (0.35 + (n.r * 2) / 80)).theta(0.9))
      // Collision uses each node's drawn size plus the padding setting.
      .force("collide", forceCollide<SimNode>((n) => n.r + collide).strength(0.9))
      // Gravity toward the middle keeps disconnected nodes from drifting off.
      .force("x", forceX<SimNode>(0).strength(centerStrength))
      .force("y", forceY<SimNode>(0).strength(centerStrength));
  }
}
