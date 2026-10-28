"use client";

import { BookOpenText } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { DeviceProvenance } from "./device-provenance";
import type { DeviceIdentity, PathContext } from "@/lib/types";

export type ChunkPreview = {
  recorded_device?: DeviceIdentity;
  path_context?: PathContext;
  label?: string;
  source?: string;
  project?: string;
  chunk_index?: number;
  type?: string;
  tags?: { lang?: string; topics?: string[] };
  content: string;
};

type Props = {
  chunk: ChunkPreview | null;
  onClose: () => void;
  /** Offered when given: read the whole source this chunk came from, at this chunk. */
  onReadSource?: (chunk: ChunkPreview) => void;
};

export function ChunkDialog({ chunk, onClose, onReadSource }: Props) {
  if (!chunk) return null;

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className="flex max-h-[85dvh] flex-col gap-0 p-0 sm:max-w-3xl">
        <DialogHeader className="shrink-0 gap-1.5 border-b border-border p-4 pr-10">
          <DialogTitle className={chunk.label ? "" : "sr-only"}>{chunk.label || "Memory content"}</DialogTitle>
          {chunk.source && (
            <DialogDescription className="text-xs break-all">{chunk.source}</DialogDescription>
          )}
          <DeviceProvenance origin={chunk.recorded_device} paths={chunk.path_context} />
          <div className="flex gap-1 flex-wrap">
            {chunk.project && <Badge variant="outline" className="text-xs">{chunk.project}</Badge>}
            {chunk.type && <Badge variant="secondary" className="text-xs">{chunk.type}</Badge>}
            {chunk.tags?.lang && <Badge className="text-xs">{chunk.tags.lang}</Badge>}
            {chunk.tags?.topics?.slice(0, 4).map((t) => (
              <Badge key={t} className="text-xs">{t}</Badge>
            ))}
          </div>
          {onReadSource && chunk.source && (
            <Button size="sm" variant="secondary" className="self-start" onClick={() => onReadSource(chunk)}>
              <BookOpenText aria-hidden /> Read in context
            </Button>
          )}
        </DialogHeader>
        <div className="overflow-y-auto p-4 flex-1">
          <pre className="text-sm whitespace-pre-wrap font-sans leading-relaxed">
            {chunk.content || "(empty)"}
          </pre>
        </div>
      </DialogContent>
    </Dialog>
  );
}
