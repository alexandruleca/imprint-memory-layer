"use client";

import { ChevronRight, House } from "lucide-react";
import type { GraphNodeKind } from "@/lib/types";
import { KINDS } from "./graph-style";

export interface ScopeCrumb {
  scope: string;
  label: string;
}

export function scopeKind(scope: string): GraphNodeKind | null {
  const kind = scope.split(":", 1)[0];
  return kind in KINDS ? (kind as GraphNodeKind) : null;
}

export function GraphBreadcrumbs({
  crumbs,
  onGo,
}: {
  crumbs: ScopeCrumb[];
  onGo: (index: number) => void;
}) {
  return (
    <nav aria-label="Graph scope" className="min-w-0">
      <ol className="flex min-w-0 items-center gap-0.5 text-sm">
        {crumbs.map((c, i) => {
          const isLast = i === crumbs.length - 1;
          const kind = scopeKind(c.scope);
          const Icon = kind ? KINDS[kind].icon : House;
          return (
            <li key={`${c.scope}-${i}`} className={`flex items-center gap-0.5 ${isLast ? "min-w-0" : "shrink-0"}`}>
              {i > 0 && <ChevronRight className="size-3.5 shrink-0 text-muted-foreground/60" aria-hidden />}
              <button
                type="button"
                onClick={() => onGo(i)}
                disabled={isLast}
                aria-current={isLast ? "page" : undefined}
                title={c.label}
                className={`flex min-w-0 items-center gap-1.5 rounded-md px-1.5 py-1 transition-colors ${
                  isLast
                    ? "font-medium text-foreground"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground"
                }`}
              >
                <Icon className="size-3.5 shrink-0" aria-hidden />
                <span className={`truncate ${isLast ? "max-w-[16rem]" : "max-w-[9rem]"}`}>
                  {i === 0 ? "Overview" : c.label}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
