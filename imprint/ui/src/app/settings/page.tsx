"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { getConfig, resetConfigValue, setConfigValue } from "@/lib/api";
import { SettingsSkeleton } from "@/components/loaders";
import { WorkspaceManager } from "@/components/workspace-manager";
import type { ConfigSetting } from "@/lib/types";

export default function SettingsPage() {
  const [settings, setSettings] = useState<ConfigSetting[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getConfig()
      .then((d) => setSettings(d.settings))
      .finally(() => setLoading(false));
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

  async function resetValue(key: string) {
    setSaving(true);
    try {
      await resetConfigValue(key);
      const d = await getConfig();
      setSettings(d.settings);
      setEditing(null);
    } finally {
      setSaving(false);
    }
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
        <CardHeader><CardTitle className="text-sm">Workspaces</CardTitle></CardHeader>
        <CardContent>
          <WorkspaceManager />
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
                          aria-label={s.key}
                          {...(s.secret ? { type: "password", autoComplete: "off", placeholder: "Paste a new value" } : {})}
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
                      <div className="flex items-center gap-2">
                        {/* Secrets arrive masked, so editing one starts empty. */}
                        <button
                          type="button"
                          className="flex-1 text-left font-mono text-xs cursor-pointer hover:bg-muted p-1 rounded"
                          onClick={() => { setEditing(s.key); setEditValue(s.secret ? "" : String(s.value)); }}
                        >
                          {s.secret && !s.value ? <span className="text-muted-foreground">Not set</span> : String(s.value)}
                        </button>
                        {s.source === "config" && (
                          <button
                            type="button"
                            onClick={() => resetValue(s.key)}
                            className="text-xs text-muted-foreground hover:text-foreground"
                            disabled={saving}
                          >
                            {s.secret ? "Remove" : "Reset"}
                          </button>
                        )}
                      </div>
                    )}
                    {s.secret && s.source === "env" && (
                      <p className="text-xs text-muted-foreground mt-1">{s.env} is set in the environment and takes precedence.</p>
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
