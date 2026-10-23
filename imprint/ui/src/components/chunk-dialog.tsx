"use client";

import { Modal } from "./modal";
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
  if (!chunk) return null;

  return (
    <Modal label={chunk.label || "Memory content"} onClose={onClose} className="max-w-3xl p-0">
      <div className="flex max-h-[85dvh] flex-col">
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
            aria-label="Close memory"
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
    </Modal>
  );
}
