"use client";

import { Switch } from "@/components/ui/switch";

export interface GraphForces {
  repel: number;
  collide: number;
  centerStrength: number;
}

export interface GraphToggles {
  showLabels: boolean;
  showEdges: boolean;
  showChunks: boolean;
  showSources: boolean;
  showTopics: boolean;
  showProjects: boolean;
  localMode: boolean;
}

export function GraphControls({
  depth,
  onDepth,
  forces,
  onForces,
  toggles,
  onToggles,
  onReset,
}: {
  depth: number;
  onDepth: (n: number) => void;
  forces: GraphForces;
  onForces: (f: GraphForces) => void;
  toggles: GraphToggles;
  onToggles: (t: GraphToggles) => void;
  onReset: () => void;
}) {
  const row = (label: string, control: React.ReactNode) => (
    <div className="flex items-center justify-between gap-3 py-1.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      {control}
    </div>
  );

  const slider = (
    value: number,
    min: number,
    max: number,
    step: number,
    onChange: (v: number) => void,
  ) => (
    <input
      type="range"
      min={min}
      max={max}
      step={step}
      value={value}
      onChange={(e) => onChange(Number(e.target.value))}
      className="w-36 accent-primary"
    />
  );

  const toggle = (v: boolean, onChange: (v: boolean) => void) => (
    <Switch checked={v} onCheckedChange={onChange} />
  );

  return (
    <div className="flex flex-col gap-3 text-sm">
      <section>
        <h4 className="text-xs font-semibold uppercase tracking-wide mb-1 text-muted-foreground">
          Scope
        </h4>
        {row("Depth", slider(depth, 1, 3, 1, onDepth))}
      </section>

      <section>
        <h4 className="text-xs font-semibold uppercase tracking-wide mb-1 text-muted-foreground">
          Forces
        </h4>
        {row(
          "Repel",
          slider(forces.repel, 100, 4000, 100, (v) =>
            onForces({ ...forces, repel: v }),
          ),
        )}
        {row(
          "Collide",
          slider(forces.collide, 4, 80, 2, (v) =>
            onForces({ ...forces, collide: v }),
          ),
        )}
        {row(
          "Gravity",
          slider(forces.centerStrength, 0, 0.3, 0.01, (v) =>
            onForces({ ...forces, centerStrength: v }),
          ),
        )}
      </section>

      <section>
        <h4 className="text-xs font-semibold uppercase tracking-wide mb-1 text-muted-foreground">
          Display
        </h4>
        {row(
          "Labels",
          toggle(toggles.showLabels, (v) =>
            onToggles({ ...toggles, showLabels: v }),
          ),
        )}
        {row(
          "Edges",
          toggle(toggles.showEdges, (v) =>
            onToggles({ ...toggles, showEdges: v }),
          ),
        )}
        {row(
          "Local mode",
          toggle(toggles.localMode, (v) =>
            onToggles({ ...toggles, localMode: v }),
          ),
        )}
      </section>

      <section>
        <h4 className="text-xs font-semibold uppercase tracking-wide mb-1 text-muted-foreground">
          Node kinds
        </h4>
        {row(
          "Projects",
          toggle(toggles.showProjects, (v) =>
            onToggles({ ...toggles, showProjects: v }),
          ),
        )}
        {row(
          "Topics",
          toggle(toggles.showTopics, (v) =>
            onToggles({ ...toggles, showTopics: v }),
          ),
        )}
        {row(
          "Sources",
          toggle(toggles.showSources, (v) =>
            onToggles({ ...toggles, showSources: v }),
          ),
        )}
        {row(
          "Chunks",
          toggle(toggles.showChunks, (v) =>
            onToggles({ ...toggles, showChunks: v }),
          ),
        )}
      </section>

      <button
        onClick={onReset}
        className="mt-2 text-xs text-muted-foreground hover:text-foreground underline self-start"
      >
        Reset to defaults
      </button>
    </div>
  );
}
