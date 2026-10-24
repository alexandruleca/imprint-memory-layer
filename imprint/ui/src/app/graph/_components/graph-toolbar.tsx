"use client";

import { useId } from "react";
import { Maximize, Shuffle, SlidersHorizontal, ZoomIn, ZoomOut, type LucideIcon } from "lucide-react";
import { Hint } from "@/components/hint";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTitle, PopoverTrigger } from "@/components/ui/popover";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Separator } from "@/components/ui/separator";
import { Slider } from "@/components/ui/slider";
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

function ToolButton({ icon: Icon, label, onClick }: { icon: LucideIcon; label: string; onClick: () => void }) {
  return (
    <Hint label={label} side="left">
      <Button variant="ghost" size="icon" onClick={onClick} aria-label={label} className="text-muted-foreground">
        <Icon aria-hidden />
      </Button>
    </Hint>
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
      <Separator className="mx-1.5 my-0.5 data-horizontal:w-auto" />
      <ToolButton icon={Shuffle} label="Re-run layout" onClick={onRelayout} />
      <Popover>
        <Hint label="Graph settings" side="left">
          <PopoverTrigger render={<Button variant="ghost" size="icon" aria-label="Graph settings" className="text-muted-foreground" />}>
            <SlidersHorizontal aria-hidden />
          </PopoverTrigger>
        </Hint>
        <PopoverContent side="left" align="end" sideOffset={10} className="w-72 gap-0 p-4">
          <PopoverTitle className="font-semibold">Graph settings</PopoverTitle>

          <Section title="Display">
            <Row label="Labels" htmlFor="graph-labels">
              <Switch id="graph-labels" checked={settings.showLabels} onCheckedChange={(v) => set({ showLabels: v })} />
            </Row>
            <Row label="Connections" htmlFor="graph-edges">
              <Switch id="graph-edges" checked={settings.showEdges} onCheckedChange={(v) => set({ showEdges: v })} />
            </Row>
          </Section>

          <Section title="Items per group">
            <RadioGroup
              aria-label="Items per group"
              value={settings.depth}
              onValueChange={(depth) => set({ depth: depth as number })}
              disabled={!breadthApplies}
              className="flex gap-4"
            >
              {BREADTH.map((b) => (
                <Label key={b.depth} className="font-normal has-data-disabled:cursor-not-allowed has-data-disabled:opacity-50">
                  <RadioGroupItem value={b.depth} />
                  {b.label}
                </Label>
              ))}
            </RadioGroup>
            <p className="mt-1.5 text-xs text-muted-foreground">
              {breadthApplies
                ? "How many topics, sources and chunks to show."
                : "Applies inside a project, topic or source."}
            </p>
          </Section>

          <Section title="Layout">
            <ForceSlider label="Spacing" value={settings.forces.repel} min={200} max={4000} step={100} onChange={(v) => force({ repel: v })} />
            <ForceSlider label="Node padding" value={settings.forces.collide} min={2} max={60} step={2} onChange={(v) => force({ collide: v })} />
            <ForceSlider label="Gravity" value={settings.forces.centerStrength} min={0} max={0.3} step={0.01} onChange={(v) => force({ centerStrength: v })} />
          </Section>

          <Button variant="link" size="xs" onClick={onReset} className="mt-4 self-start px-0 text-muted-foreground hover:text-foreground">
            Reset to defaults
          </Button>
        </PopoverContent>
      </Popover>
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
      <Label htmlFor={htmlFor} className="font-normal">{label}</Label>
      {children}
    </div>
  );
}

function ForceSlider({ label, value, min, max, step, onChange }: {
  label: string; value: number; min: number; max: number; step: number; onChange: (v: number) => void;
}) {
  const labelId = useId();
  return (
    <div className="space-y-2.5 py-1">
      <Label id={labelId} className="font-normal">{label}</Label>
      <Slider
        aria-labelledby={labelId}
        value={[value]}
        min={min}
        max={max}
        step={step}
        onValueChange={(v) => onChange(Array.isArray(v) ? v[0] : (v as number))}
      />
    </div>
  );
}
