"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { decideProjectAssociation, enqueueCommand, getProjectAssociations, type AssociationReview } from "@/lib/api";
import { SourceReader, type ReaderTarget } from "@/components/source-reader";

/** Project links preserve one canonical source, independently of primary moves. */
export function ProjectAssociations({ onClose, onChanged }: { onClose: () => void; onChanged: () => void }) {
  const [data, setData] = useState<AssociationReview | null>(null);
  const [selected, setSelected] = useState("");
  const [target, setTarget] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [model, setModel] = useState(false);
  const [reading, setReading] = useState<ReaderTarget | null>(null);
  const requestId = useRef(0);
  const source = data?.sources.find((s) => JSON.stringify([s.project, s.source]) === selected);

  useEffect(() => {
    let live = true;
    getProjectAssociations().then((result) => { if (live) setData(result); }).catch((e) => { if (live) setError(String(e)); });
    return () => { live = false; };
  }, []);

  async function refresh(value = selected) {
    const id = ++requestId.current;
    const [project, key] = value ? JSON.parse(value) as [string, string] : [undefined, undefined];
    const result = await getProjectAssociations(key, project);
    if (id === requestId.current) setData(result);
  }

  async function change(targetProject: string, status: "accepted" | "rejected") {
    if (!source || !data) return;
    setBusy(true); setError("");
    try {
      setData(await decideProjectAssociation({ workspace: data.workspace, source: source.source,
        project: source.project, target: targetProject, status }));
      setTarget(""); onChanged();
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }

  async function scan() {
    if (!data) return;
    setBusy(true); setError("");
    try {
      await enqueueCommand("associate-projects", { workspace: data.workspace, model });
      setNotice("Project analysis queued. Follow it in Queue, then refresh these suggestions.");
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }

  const links = data?.associations.filter((a) => !source || (a.source === source.source && a.project === source.project)) ?? [];
  return <>
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className="flex max-h-[90dvh] flex-col sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>Project associations</DialogTitle>
          <DialogDescription>Link a conversation or file to several projects. The original source stays in its primary project. Topics remain separate.</DialogDescription>
        </DialogHeader>
        <div className="space-y-4 overflow-y-auto">
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          {notice && <p role="status" className="text-sm">{notice}</p>}
          <div className="flex flex-wrap items-center gap-3">
            <Button variant="outline" disabled={busy || !data} onClick={scan}>Analyze existing sources</Button>
            <Button variant="outline" disabled={busy} onClick={() => { refresh().catch((e) => setError(String(e))); }}>Refresh suggestions</Button>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={model} onChange={(e) => setModel(e.target.checked)} />Use installed local helper</label>
          </div>
          <p className="text-xs text-muted-foreground">Workspace: {data?.workspace ?? "Loading…"}. Name and path suggestions run after ingestion. The optional helper reads bounded sections locally and needs an installed tagging model. Manual decisions survive re-ingestion.</p>
          <div className="space-y-2">
            <Label htmlFor="association-source">Source</Label>
            <select id="association-source" disabled={busy} className="w-full rounded-md border bg-background p-2 text-sm" value={selected}
              onChange={(e) => { const value = e.target.value; setSelected(value); refresh(value).catch((err) => setError(String(err))); }}>
              <option value="">All sources — select one to add a link</option>
              {data?.sources.map((s) => <option key={JSON.stringify([s.project, s.source])} value={JSON.stringify([s.project, s.source])}>{s.project || "Unassigned"} · {s.title}</option>)}
            </select>
          </div>
          {source && <div className="space-y-2">
            <Button variant="outline" size="sm" onClick={() => setReading(source)}>Read original source</Button>
            <Label htmlFor="association-target">Link to project</Label>
            <div className="flex gap-2">
              <Input id="association-target" list="association-projects" value={target} onChange={(e) => setTarget(e.target.value)} placeholder="Existing or new project name" />
              <datalist id="association-projects">{data?.projects.filter((p) => p !== source.project).map((p) => <option key={p} value={p} />)}</datalist>
              <Button disabled={busy || !target.trim()} onClick={() => change(target.trim(), "accepted")}>Add link</Button>
            </div>
          </div>}
          {!data && <p className="text-sm">Loading associations…</p>}
          {data && !links.length && <p className="text-sm text-muted-foreground">No project suggestions here yet. Analyze existing sources or select a source to add a link manually.</p>}
          {!source && links.length >= 1000 && <p className="text-sm">Showing the first 1,000 associations. Select a source to review its links.</p>}
          {links.map((link) => <article key={JSON.stringify([link.origin, link.source, link.target])} className="space-y-2 rounded-md border p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="min-w-0"><p className="text-sm font-medium">{link.target} · {link.status}</p><p className="break-all text-xs text-muted-foreground">{link.source} · primary: {link.project || "Unassigned"}</p></div>
              <div className="flex gap-2">
                <Button size="sm" variant="outline" disabled={busy} onClick={() => setReading({ source: link.source, project: link.project })}>Read source</Button>
                <Button size="sm" disabled={busy || link.status === "accepted"} onClick={async () => {
                  setBusy(true); setError("");
                  try { await decideProjectAssociation({ ...link, workspace: data!.workspace, status: "accepted" }); await refresh(); onChanged(); }
                  catch (e) { setError(String(e)); } finally { setBusy(false); }
                }}>Accept</Button>
                <Button size="sm" variant="outline" disabled={busy || link.status === "rejected"} onClick={async () => {
                  setBusy(true); setError("");
                  try { await decideProjectAssociation({ ...link, workspace: data!.workspace, status: "rejected" }); await refresh(); onChanged(); }
                  catch (e) { setError(String(e)); } finally { setBusy(false); }
                }}>{link.status === "accepted" ? "Remove link" : "Reject"}</Button>
              </div>
            </div>
            <p className="text-xs text-muted-foreground">{link.manual ? "Your decision" : link.method === "local-model" ? "Local model suggestion" : `${link.method} match`}{link.stale ? " · Source changed; read it again to review evidence." : ""}</p>
            {link.evidence.map((e, i) => <blockquote key={`${e.start}:${e.end}:${i}`} className="whitespace-pre-wrap border-l-2 pl-3 text-xs">{e.text}</blockquote>)}
          </article>)}
        </div>
      </DialogContent>
    </Dialog>
    <SourceReader target={reading} onClose={() => setReading(null)} />
  </>;
}
