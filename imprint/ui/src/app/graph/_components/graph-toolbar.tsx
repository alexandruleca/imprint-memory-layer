"use client";

import { Popover } from "@base-ui/react/popover";
import { Maximize, Shuffle, SlidersHorizontal, ZoomIn, ZoomOut, type LucideIcon } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import type { GraphForces } from "./graph-canvas";

export interface GraphSettings {
  showLabels: boolean;
  showEdges: boolean;
  depth: number;
  forces: GraphForces;
}

const BREADTH = [
  { depth: 1, label: "Top" },
  { depth: 2, label: "More" },
  { depth: 3, label: "Most" },
];

const iconButton =
  "grid size-8 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring data-[popup-open]:bg-muted data-[popup-open]:text-foreground";

function ToolButton({ icon: Icon, label, onClick }: { icon: LucideIcon; label: string; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} aria-label={label} title={label} className={iconButton}>
      <Icon className="size-4" aria-hidden />
    </button>
  );
}

export function GraphToolbar({
  settings,
  onSettings,
  breadthApplies,
  onZoom,
  onFit,
  onRelayout,
  onReset,
}: {
  settings: GraphSettings;
  onSettings: (s: GraphSettings) => void;
  /** Whether the current scope lists more items at a higher breadth. */
  breadthApplies: boolean;
  onZoom: (ratio: number) => void;
  onFit: () => void;
  onRelayout: () => void;
  onReset: () => void;
}) {
  const set = (patch: Partial<GraphSettings>) => onSettings({ ...settings, ...patch });
  const force = (patch: Partial<GraphForces>) => set({ forces: { ...settings.forces, ...patch } });

  return (
    <div className="flex flex-col gap-0.5 rounded-xl border border-border bg-card/90 p-1 shadow-lg backdrop-blur" role="toolbar" aria-label="Graph view" aria-orientation="vertical">
      <ToolButton icon={ZoomIn} label="Zoom in" onClick={() => onZoom(1.3)} />
      <ToolButton icon={ZoomOut} label="Zoom out" onClick={() => onZoom(1 / 1.3)} />
      <ToolButton icon={Maximize} label="Fit to view" onClick={onFit} />
      <div className="mx-1.5 my-0.5 h-px bg-border" />
      <ToolButton icon={Shuffle} label="Re-run layout" onClick={onRelayout} />
      <Popover.Root>
        <Popover.Trigger className={iconButton} aria-label="Graph settings" title="Graph settings">
          <SlidersHorizontal className="size-4" aria-hidden />
        </Popover.Trigger>
        <Popover.Portal>
          <Popover.Positioner side="left" align="end" sideOffset={10} className="z-50">
            <Popover.Popup className="w-72 rounded-xl border border-border bg-popover p-4 text-sm text-popover-foreground shadow-2xl outline-none transition-[opacity,transform] data-[ending-style]:scale-95 data-[ending-style]:opacity-0 data-[starting-style]:scale-95 data-[starting-style]:opacity-0">
              <Popover.Title className="text-sm font-semibold">Graph settings</Popover.Title>

              <Section title="Display">
                <Row label="Labels" htmlFor="graph-labels">
                  <Switch id="graph-labels" checked={settings.showLabels} onCheckedChange={(v) => set({ showLabels: v })} />
                </Row>
                <Row label="Connections" htmlFor="graph-edges">
                  <Switch id="graph-edges" checked={settings.showEdges} onCheckedChange={(v) => set({ showEdges: v })} />
                </Row>
              </Section>

              <Section title="Items per group">
                <div role="radiogroup" aria-label="Items per group" className="grid grid-cols-3 gap-1 rounded-lg bg-muted p-1">
                  {BREADTH.map((b) => (
                    <button
                      key={b.depth}
                      type="button"
                      role="radio"
                      aria-checked={settings.depth === b.depth}
                      disabled={!breadthApplies}
                      onClick={() => set({ depth: b.depth })}
                      className="rounded-md px-2 py-1 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground disabled:pointer-events-none disabled:opacity-50 aria-checked:bg-background aria-checked:text-foreground aria-checked:shadow-sm"
                    >
                      {b.label}
                    </button>
                  ))}
                </div>
                <p className="mt-1.5 text-xs text-muted-foreground">
                  {breadthApplies
                    ? "How many topics, sources and chunks to show."
                    : "Applies inside a project, topic or source."}
                </p>
              </Section>

              <Section title="Layout">
                <Slider id="graph-repel" label="Spacing" value={settings.forces.repel} min={200} max={4000} step={100} onChange={(v) => force({ repel: v })} />
                <Slider id="graph-collide" label="Node padding" value={settings.forces.collide} min={2} max={60} step={2} onChange={(v) => force({ collide: v })} />
                <Slider id="graph-gravity" label="Gravity" value={settings.forces.centerStrength} min={0} max={0.3} step={0.01} onChange={(v) => force({ centerStrength: v })} />
              </Section>

              <button type="button" onClick={onReset} className="mt-4 text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline">
                Reset to defaults
              </button>
            </Popover.Popup>
          </Popover.Positioner>
        </Popover.Portal>
      </Popover.Root>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-4">
      <h3 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">{title}</h3>
      {children}
    </section>
  );
}

function Row({ label, htmlFor, children }: { label: string; htmlFor: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between py-1">
      <label htmlFor={htmlFor} className="text-sm">{label}</label>
      {children}
    </div>
  );
}

function Slider({ id, label, value, min, max, step, onChange }: {
  id: string; label: string; value: number; min: number; max: number; step: number; onChange: (v: number) => void;
}) {
  return (
    <div className="py-1">
      <label htmlFor={id} className="text-sm">{label}</label>
      <input
        id={id}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="mt-1 block w-full accent-primary"
      />
    </div>
  );
}
