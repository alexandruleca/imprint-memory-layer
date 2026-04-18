"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Switch } from "@/components/ui/switch";
import { Spinner } from "@/components/loaders";
import {
  getFsRoots,
  listFs,
  type FsEntry,
  type FsRoot,
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
  const [roots, setRoots] = useState<FsRoot[]>([]);
  const [cwd, setCwd] = useState("");
  const [entries, setEntries] = useState<FsEntry[]>([]);
  const [parent, setParent] = useState<string | null>(null);
  const [selected, setSelected] = useState<FsEntry | null>(null);
  const [manual, setManual] = useState("");
  const [showHidden, setShowHidden] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const navigate = useCallback(
    async (path: string) => {
      setLoading(true);
      setError(null);
      setSelected(null);
      try {
        const data = await listFs(path, showHidden);
        if (data.error) {
          setError(data.error);
          setLoading(false);
          return;
        }
        if (data.is_file) {
          // Jump to parent; preselect the file if mode allows file selection.
          if (data.parent) {
            const parentData = await listFs(data.parent, showHidden);
            if (!parentData.error) {
              setCwd(parentData.path);
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
          setParent(data.parent);
          setEntries(data.entries);
        }
        listRef.current?.scrollTo({ top: 0 });
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setLoading(false);
      }
    },
    [showHidden, mode],
  );

  useEffect(() => {
    if (!open) return;
    getFsRoots()
      .then((r) => setRoots(r.roots))
      .catch(() => {});
    const start = initialPath?.trim() || "";
    navigate(start);
  }, [open, initialPath, navigate]);

  useEffect(() => {
    if (!open || !cwd) return;
    navigate(cwd);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showHidden]);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onCancel();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

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

  const crumbs = buildCrumbs(cwd);
  const dialogTitle =
    title ?? (mode === "file" ? "Pick file" : mode === "any" ? "Pick path" : "Pick directory");

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/60" onClick={onCancel} />
      <div className="relative z-10 flex h-[560px] w-full max-w-3xl flex-col gap-3 rounded-lg border border-border bg-background p-4 shadow-lg">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold">{dialogTitle}</h3>
          <div className="flex items-center gap-2">
            <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
              <Switch
                checked={showHidden}
                onCheckedChange={(v: boolean) => setShowHidden(v)}
              />
              Show hidden
            </label>
          </div>
        </div>

        {/* Path bar */}
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => parent && navigate(parent)}
            disabled={!parent || loading}
            title="Parent directory"
          >
            <ArrowUp className="w-3.5 h-3.5" />
          </Button>
          <Input
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
            className="flex-1 h-8 text-xs font-mono"
          />
        </div>

        {/* Breadcrumbs */}
        {crumbs.length > 0 && (
          <div className="flex flex-wrap items-center gap-0.5 text-[11px] text-muted-foreground">
            {crumbs.map((c, i) => (
              <button
                key={c.path}
                onClick={() => navigate(c.path)}
                className="hover:text-foreground font-mono px-1 py-0.5 rounded hover:bg-muted"
              >
                {c.label}
                {i < crumbs.length - 1 && (
                  <span className="text-muted-foreground/40 ml-0.5">/</span>
                )}
              </button>
            ))}
          </div>
        )}

        {/* Body */}
        <div className="flex min-h-0 flex-1 gap-3">
          {/* Roots sidebar */}
          <div className="w-40 shrink-0 rounded-md border border-border/60 bg-muted/20">
            <ScrollArea className="h-full">
              <div className="p-1.5 space-y-0.5">
                {roots.map((r) => (
                  <button
                    key={r.path}
                    onClick={() => navigate(r.path)}
                    className="flex w-full items-center gap-1.5 rounded px-2 py-1 text-left text-xs hover:bg-muted"
                    title={r.path}
                  >
                    {r.label === "Home" ? (
                      <Home className="w-3.5 h-3.5 shrink-0" />
                    ) : (
                      <Folder className="w-3.5 h-3.5 shrink-0" />
                    )}
                    <span className="truncate">{r.label}</span>
                  </button>
                ))}
              </div>
            </ScrollArea>
          </div>

          {/* Entry list */}
          <div className="flex-1 rounded-md border border-border/60">
            <ScrollArea className="h-full">
              <div ref={listRef} className="p-1.5">
                {loading ? (
                  <div className="flex items-center gap-2 p-3 text-xs text-muted-foreground">
                    <Spinner className="w-3.5 h-3.5" /> Loading…
                  </div>
                ) : error ? (
                  <div className="p-3 text-xs text-destructive font-mono break-all">
                    {error}
                  </div>
                ) : entries.length === 0 ? (
                  <div className="p-3 text-xs text-muted-foreground">
                    Empty directory.
                  </div>
                ) : (
                  entries.map((entry) => {
                    const isSel = selected?.abs_path === entry.abs_path;
                    const disabled = !entry.is_dir && mode === "dir";
                    return (
                      <button
                        key={entry.abs_path}
                        onClick={() => onRowClick(entry)}
                        onDoubleClick={() => onRowDoubleClick(entry)}
                        disabled={disabled}
                        className={`flex w-full items-center gap-2 rounded px-2 py-1 text-left text-xs ${
                          isSel ? "bg-primary/20" : "hover:bg-muted"
                        } ${disabled ? "opacity-40 cursor-not-allowed" : ""}`}
                      >
                        {entry.is_dir ? (
                          <FolderOpen className="w-3.5 h-3.5 text-sky-400 shrink-0" />
                        ) : (
                          <FileIcon className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
                        )}
                        <span className="truncate font-mono">{entry.name}</span>
                      </button>
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
      </div>
    </div>
  );
}

function buildCrumbs(path: string): { label: string; path: string }[] {
  if (!path) return [];
  // Windows: "C:\foo\bar" → ["C:", "foo", "bar"]
  const isWindows = /^[A-Za-z]:[\\/]/.test(path);
  if (isWindows) {
    const parts = path.split(/[\\/]/).filter(Boolean);
    const crumbs: { label: string; path: string }[] = [];
    let acc = "";
    parts.forEach((p, i) => {
      acc = i === 0 ? `${p}\\` : `${acc}${p}\\`;
      crumbs.push({ label: p, path: acc });
    });
    return crumbs;
  }
  // POSIX
  const parts = path.split("/").filter(Boolean);
  const crumbs: { label: string; path: string }[] = [{ label: "/", path: "/" }];
  let acc = "";
  parts.forEach((p) => {
    acc = `${acc}/${p}`;
    crumbs.push({ label: p, path: acc });
  });
  return crumbs;
}
