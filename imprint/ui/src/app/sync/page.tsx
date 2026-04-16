"use client";

import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { syncExport, syncImport } from "@/lib/api";

export default function SyncPage() {
  const [exportResult, setExportResult] = useState<string | null>(null);
  const [importPath, setImportPath] = useState("");
  const [importResult, setImportResult] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function doExport() {
    setLoading(true);
    setExportResult(null);
    try {
      const res = await syncExport() as Record<string, unknown>;
      if (res.ok) {
        setExportResult(`Export saved to: ${res.path}`);
      } else {
        setExportResult(`Error: ${res.error}`);
      }
    } catch (e: unknown) {
      setExportResult(`Error: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setLoading(false);
    }
  }

  async function doImport() {
    if (!importPath.trim()) return;
    setLoading(true);
    setImportResult(null);
    try {
      const res = await syncImport(importPath) as Record<string, unknown>;
      if (res.ok) {
        setImportResult("Import complete. Memories restored.");
      } else {
        setImportResult(`Error: ${res.error}`);
      }
    } catch (e: unknown) {
      setImportResult(`Error: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="p-8 space-y-6">
      <h2 className="text-2xl font-bold">Sync</h2>
      <p className="text-muted-foreground text-sm">
        Export and import memory snapshots between devices. No re-embedding needed on the receiving device.
      </p>

      <div className="grid grid-cols-2 gap-6">
        <Card>
          <CardHeader><CardTitle className="text-sm">Export</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            <p className="text-xs text-muted-foreground">
              Creates a Qdrant snapshot + knowledge graph backup.
              Transfer the bundle directory to another device.
            </p>
            <button
              onClick={doExport}
              disabled={loading}
              className="px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm"
            >
              {loading ? "Exporting..." : "Export Snapshot"}
            </button>
            {exportResult && (
              <p className={`text-xs ${exportResult.startsWith("Error") ? "text-destructive" : "text-green-400"}`}>
                {exportResult}
              </p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle className="text-sm">Import</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            <p className="text-xs text-muted-foreground">
              Restore a snapshot bundle from another device.
              Provide the path to the export directory.
            </p>
            <Input
              value={importPath}
              onChange={(e) => setImportPath(e.target.value)}
              placeholder="/path/to/imprint-export-..."
              className="text-sm"
            />
            <button
              onClick={doImport}
              disabled={loading || !importPath.trim()}
              className="px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm"
            >
              {loading ? "Importing..." : "Import Snapshot"}
            </button>
            {importResult && (
              <p className={`text-xs ${importResult.startsWith("Error") ? "text-destructive" : "text-green-400"}`}>
                {importResult}
              </p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
