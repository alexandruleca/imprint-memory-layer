const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8420";

export type DesktopExportEntry = {
  path: string;
  origin: string;
  indexed_at: number;
  chunks: number;
};

export type DesktopHistory = {
  seen: Record<string, DesktopExportEntry>;
  count: number;
};

export type DesktopScanResult = {
  roots: string[];
  scanned: number;
  skipped_seen: number;
  indexed_zips: number;
  inserted_chunks: number;
  indexed: DesktopExportEntry[];
};

export async function getDesktopHistory(): Promise<DesktopHistory> {
  const res = await fetch(`${API_BASE}/api/desktop-learn/history`, {
    cache: "no-store",
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || `History load failed: ${res.status}`);
  }
  return res.json();
}

export async function scanDesktopExports(
  paths: string[] = [],
): Promise<DesktopScanResult> {
  const res = await fetch(`${API_BASE}/api/desktop-learn/scan`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ paths }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || `Scan failed: ${res.status}`);
  }
  return res.json();
}
