"use client";

import { ChevronRight, Home } from "lucide-react";

export interface ScopeCrumb {
  scope: string;
  label: string;
}

export function GraphBreadcrumbs({
  crumbs,
  onGo,
}: {
  crumbs: ScopeCrumb[];
  onGo: (index: number) => void;
}) {
  return (
    <div className="flex items-center gap-1 text-sm flex-wrap">
      {crumbs.map((c, i) => {
        const isLast = i === crumbs.length - 1;
        return (
          <div key={`${c.scope}-${i}`} className="flex items-center gap-1">
            {i > 0 && <ChevronRight className="h-3 w-3 text-muted-foreground" />}
            <button
              onClick={() => !isLast && onGo(i)}
              disabled={isLast}
              className={
                isLast
                  ? "font-medium text-foreground px-1"
                  : "text-muted-foreground hover:text-foreground px-1 cursor-pointer"
              }
            >
              {i === 0 ? (
                <span className="flex items-center gap-1">
                  <Home className="h-3 w-3" /> root
                </span>
              ) : (
                c.label
              )}
            </button>
          </div>
        );
      })}
    </div>
  );
}
