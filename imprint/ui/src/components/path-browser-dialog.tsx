"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Empty, EmptyDescription } from "@/components/ui/empty";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Switch } from "@/components/ui/switch";
import { Hint } from "@/components/hint";
import { Spinner } from "@/components/loaders";
import {
  getFsRoots,
  listFs,
  type FsEntry,
  type FsLocation,
} from "@/lib/api";
import {
  ArrowUp,
  File as FileIcon,
  Folder,
  FolderOpen,
  Home,
} from "lucide-react";

type Mode = "dir" | "file" | "any";

type Props = {
  open: boolean;
  mode?: Mode;
  title?: string;
  initialPath?: string;
  onSelect: (absPath: string) => void;
  onCancel: () => void;
};

export function PathBrowserDialog({
  open,
  mode = "dir",
  title,
  initialPath,
  onSelect,
  onCancel,
}: Props) {
  const [roots, setRoots] = useState<FsLocation[]>([]);
  const [cwd, setCwd] = useState("");
  // The server builds these: it knows its own path syntax (/, C:\, \\server\share\).
  const [crumbs, setCrumbs] = useState<FsLocation[]>([]);
  const [entries, setEntries] = useState<FsEntry[]>([]);
  const [parent, setParent] = useState<string | null>(null);
  const [selected, setSelected] = useState<FsEntry | null>(null);
  const [manual, setManual] = useState("");
  const [showHidden, setShowHidden] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const currentPath = useRef(initialPath?.trim() || "");
  const navigation = useRef(0);

  const navigate = useCallback(
    async (path: string) => {
      const request = ++navigation.current;
      setLoading(true);
      setError(null);
      setSelected(null);
      try {
        const data = await listFs(path, showHidden);
        if (request !== navigation.current) return;
        currentPath.current = data.path;
        if (data.error) {
          setError(data.error);
          if (request === navigation.current) setLoading(false);
          return;
        }
        if (data.is_file) {
          // Jump to parent; preselect the file if mode allows file selection.
          if (data.parent) {
            const parentData = await listFs(data.parent, showHidden);
            if (!parentData.error) {
              setCwd(parentData.path);
              setCrumbs(parentData.crumbs);
              setParent(parentData.parent);
              setEntries(parentData.entries);
              if (mode !== "dir") {
                const match = parentData.entries.find(
                  (e) => e.abs_path === data.path,
                );
                if (match) setSelected(match);
              }
            }
          }
        } else {
          setCwd(data.path);
          setCrumbs(data.crumbs);
          setParent(data.parent);
          setEntries(data.entries);
        }
        listRef.current?.scrollTo({ top: 0 });
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (request === navigation.current) setLoading(false);
      }
    },
    [showHidden, mode],
  );

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    getFsRoots().then((data) => {
      if (cancelled) return;
      setRoots(data.roots);
      return navigate(currentPath.current);
    }).catch((error) => { if (!cancelled) setError(String(error)); });
    return () => { cancelled = true; };
  }, [open, navigate]);

  if (!open) return null;

  function onRowClick(entry: FsEntry) {
    if (entry.is_dir) {
      navigate(entry.abs_path);
    } else if (mode !== "dir") {
      setSelected(entry);
    }
  }

  function onRowDoubleClick(entry: FsEntry) {
    if (entry.is_dir) {
      navigate(entry.abs_path);
    } else if (mode !== "dir") {
      onSelect(entry.abs_path);
    }
  }

  function confirmSelection() {
    if (selected && mode !== "dir") {
      onSelect(selected.abs_path);
      return;
    }
    // Dir mode (or no file picked): return current dir.
    if (cwd) onSelect(cwd);
  }

  const canConfirm = Boolean(
    cwd && (mode === "dir" || mode === "any" || selected),
  );
  const confirmLabel =
    mode === "file"
      ? selected
        ? "Select file"
        : "Pick a file"
      : mode === "any"
      ? selected
        ? `Select ${selected.name}`
        : "Select this folder"
      : "Select this folder";

  const dialogTitle =
    title ?? (mode === "file" ? "Pick file" : mode === "any" ? "Pick path" : "Pick directory");

  return (
    <Dialog open onOpenChange={(next) => { if (!next) onCancel(); }}>
      <DialogContent showCloseButton={false} className="flex h-[min(560px,calc(90dvh-2px))] flex-col gap-3 sm:max-w-3xl">
        <div className="flex items-center justify-between">
          <DialogTitle>{dialogTitle}</DialogTitle>
          <Label className="text-xs font-normal text-muted-foreground">
            <Switch
              checked={showHidden}
              onCheckedChange={(v: boolean) => setShowHidden(v)}
            />
            Show hidden
          </Label>
        </div>

        {/* Path bar */}
        <div className="flex items-center gap-2">
          <Hint label="Parent directory">
            <Button
              variant="outline"
              size="icon"
              aria-label="Parent directory"
              onClick={() => parent && navigate(parent)}
              disabled={!parent || loading}
            >
              <ArrowUp />
            </Button>
          </Hint>
          <Input
            aria-label="Path"
            value={manual || cwd}
            onChange={(e) => setManual(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && manual.trim()) {
                navigate(manual.trim());
                setManual("");
              }
            }}
            onBlur={() => setManual("")}
            placeholder="Type absolute path and press Enter"
            className="flex-1 font-mono text-xs"
          />
        </div>

        {/* Breadcrumbs */}
        {crumbs.length > 0 && (
          <nav aria-label="Path breadcrumbs" className="flex flex-wrap items-center gap-0.5">
            {crumbs.map((c, i) => (
              <Button
                key={c.path}
                variant="ghost"
                size="xs"
                onClick={() => navigate(c.path)}
                className="px-1 font-mono text-[11px] font-normal text-muted-foreground"
              >
                {c.label}
                {i < crumbs.length - 1 && (
                  <span className="text-muted-foreground/40">/</span>
                )}
              </Button>
            ))}
          </nav>
        )}

        {/* Body */}
        <div className="flex min-h-0 flex-1 gap-3">
          {/* Roots sidebar */}
          <div className="w-40 shrink-0 rounded-md border border-border/60 bg-muted/20">
            <ScrollArea className="h-full">
              <div className="p-1.5 space-y-0.5">
                {roots.map((r) => (
                  <Hint key={r.path} label={r.path} side="right">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => navigate(r.path)}
                      className="w-full justify-start font-normal"
                    >
                      {r.label === "Home" ? <Home aria-hidden /> : <Folder aria-hidden />}
                      <span className="truncate">{r.label}</span>
                    </Button>
                  </Hint>
                ))}
              </div>
            </ScrollArea>
          </div>

          {/* Entry list */}
          <div className="flex-1 rounded-md border border-border/60">
            <ScrollArea className="h-full">
              <div ref={listRef} className="p-1.5">
                {loading ? (
                  <div role="status" className="flex items-center gap-2 p-3 text-xs text-muted-foreground">
                    <Spinner className="size-3.5" /> Loading…
                  </div>
                ) : error ? (
                  <p role="alert" className="p-3 text-xs text-destructive font-mono break-all">
                    {error}
                  </p>
                ) : entries.length === 0 ? (
                  <Empty className="p-3">
                    <EmptyDescription className="text-xs">Empty directory.</EmptyDescription>
                  </Empty>
                ) : (
                  entries.map((entry) => {
                    const isSel = selected?.abs_path === entry.abs_path;
                    const disabled = !entry.is_dir && mode === "dir";
                    return (
                      <Button
                        key={entry.abs_path}
                        variant="ghost"
                        size="sm"
                        aria-pressed={entry.is_dir ? undefined : isSel}
                        onClick={() => onRowClick(entry)}
                        onDoubleClick={() => onRowDoubleClick(entry)}
                        disabled={disabled}
                        className={`w-full justify-start font-normal ${isSel ? "bg-muted" : ""}`}
                      >
                        {entry.is_dir ? (
                          <FolderOpen className="text-muted-foreground" aria-hidden />
                        ) : (
                          <FileIcon className="text-muted-foreground" aria-hidden />
                        )}
                        <span className="truncate font-mono text-xs">{entry.name}</span>
                      </Button>
                    );
                  })
                )}
              </div>
            </ScrollArea>
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between gap-2 pt-1">
          <p className="text-[11px] text-muted-foreground truncate font-mono">
            {selected ? selected.abs_path : cwd}
          </p>
          <div className="flex gap-2">
            <Button variant="ghost" size="sm" onClick={onCancel}>
              Cancel
            </Button>
            <Button
              size="sm"
              onClick={confirmSelection}
              disabled={!canConfirm}
            >
              {confirmLabel}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
