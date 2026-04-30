"use client";

import { useEffect } from "react";
import { Badge } from "@/components/ui/badge";
import { X } from "lucide-react";

export type ChunkPreview = {
  label?: string;
  source?: string;
  project?: string;
  type?: string;
  tags?: { lang?: string; topics?: string[] };
  content: string;
};

type Props = {
  chunk: ChunkPreview | null;
  onClose: () => void;
};

export function ChunkDialog({ chunk, onClose }: Props) {
  useEffect(() => {
    if (!chunk) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [chunk, onClose]);

  if (!chunk) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-6 bg-black/60 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="bg-background border border-border rounded-lg w-full max-w-3xl max-h-[85vh] flex flex-col shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3 p-4 border-b border-border shrink-0">
          <div className="space-y-1.5 min-w-0">
            {chunk.label && (
              <p className="text-sm font-medium">{chunk.label}</p>
            )}
            {chunk.source && (
              <p className="text-xs text-muted-foreground break-all">{chunk.source}</p>
            )}
            <div className="flex gap-1 flex-wrap">
              {chunk.project && <Badge variant="outline" className="text-xs">{chunk.project}</Badge>}
              {chunk.type && <Badge variant="secondary" className="text-xs">{chunk.type}</Badge>}
              {chunk.tags?.lang && <Badge className="text-xs">{chunk.tags.lang}</Badge>}
              {chunk.tags?.topics?.slice(0, 4).map((t) => (
                <Badge key={t} className="text-xs">{t}</Badge>
              ))}
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-muted-foreground hover:text-foreground shrink-0 mt-0.5"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="overflow-y-auto p-4 flex-1">
          <pre className="text-sm whitespace-pre-wrap font-sans leading-relaxed">
            {chunk.content || "(empty)"}
          </pre>
        </div>
      </div>
    </div>
  );
}
