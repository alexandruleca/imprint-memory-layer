"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { getConfig, setConfigValue, getWorkspaces, switchWorkspace } from "@/lib/api";
import { SettingsSkeleton } from "@/components/loaders";
import type { ConfigSetting } from "@/lib/types";

export default function SettingsPage() {
  const [settings, setSettings] = useState<ConfigSetting[]>([]);
  const [workspaces, setWorkspaces] = useState<{ active: string; workspaces: string[] }>({ active: "", workspaces: [] });
  const [editing, setEditing] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      getConfig().then((d) => setSettings(d.settings)),
      getWorkspaces().then(setWorkspaces),
    ]).finally(() => setLoading(false));
  }, []);

  async function saveValue(key: string) {
    setSaving(true);
    try {
      await setConfigValue(key, editValue);
      const d = await getConfig();
      setSettings(d.settings);
      setEditing(null);
    } finally {
      setSaving(false);
    }
  }

  async function doSwitchWorkspace(name: string) {
    await switchWorkspace(name);
    const ws = await getWorkspaces();
    setWorkspaces(ws);
  }

  const groups: Record<string, ConfigSetting[]> = {};
  for (const s of settings) {
    const group = s.key.split(".")[0];
    (groups[group] ||= []).push(s);
  }

  if (loading) return <SettingsSkeleton />;

  return (
    <div className="p-8 space-y-6">
      <h2 className="text-2xl font-bold">Settings</h2>

      <Card>
        <CardHeader><CardTitle className="text-sm">Workspace</CardTitle></CardHeader>
        <CardContent>
          <div className="flex gap-2 flex-wrap">
            {workspaces.workspaces.map((ws) => (
              <button
                key={ws}
                className={`px-3 py-1 rounded text-sm ${
                  ws === workspaces.active
                    ? "bg-primary text-primary-foreground"
                    : "bg-muted hover:bg-muted/80"
                }`}
                onClick={() => doSwitchWorkspace(ws)}
              >
                {ws}
              </button>
            ))}
          </div>
        </CardContent>
      </Card>

      {Object.entries(groups).map(([group, items]) => (
        <Card key={group}>
          <CardHeader>
            <CardTitle className="text-sm capitalize">{group}</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              {items.map((s) => (
                <div key={s.key} className="flex items-center gap-4 text-sm py-1 border-b border-border last:border-0">
                  <div className="w-56">
                    <p className="font-mono text-xs">{s.key}</p>
                    <p className="text-xs text-muted-foreground">{s.desc}</p>
                  </div>
                  <div className="flex-1">
                    {editing === s.key ? (
                      <div className="flex gap-2">
                        <Input
                          value={editValue}
                          onChange={(e) => setEditValue(e.target.value)}
                          className="h-7 text-xs"
                          autoFocus
                          onKeyDown={(e) => e.key === "Enter" && saveValue(s.key)}
                        />
                        <button
                          onClick={() => saveValue(s.key)}
                          className="text-xs bg-primary text-primary-foreground px-2 rounded"
                          disabled={saving}
                        >
                          Save
                        </button>
                        <button onClick={() => setEditing(null)} className="text-xs text-muted-foreground">
                          Cancel
                        </button>
                      </div>
                    ) : (
                      <div
                        className="font-mono text-xs cursor-pointer hover:bg-muted p-1 rounded"
                        onClick={() => { setEditing(s.key); setEditValue(String(s.value)); }}
                      >
                        {String(s.value)}
                      </div>
                    )}
                  </div>
                  <Badge variant={s.source === "default" ? "secondary" : "outline"} className="text-xs">
                    {s.source}
                  </Badge>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
