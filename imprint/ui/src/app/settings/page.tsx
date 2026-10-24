"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { getConfig, resetConfigValue, setConfigValue } from "@/lib/api";
import { SettingsSkeleton, Spinner } from "@/components/loaders";
import { WorkspaceManager } from "@/components/workspace-manager";
import type { ConfigSetting } from "@/lib/types";

export default function SettingsPage() {
  const [settings, setSettings] = useState<ConfigSetting[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    getConfig()
      .then((d) => setSettings(d.settings))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, []);

  // Secret values never appear in these messages: only the key does.
  async function saveValue(key: string) {
    setSaving(true);
    try {
      await setConfigValue(key, editValue);
      const d = await getConfig();
      setSettings(d.settings);
      setEditing(null);
      toast.success(`Saved ${key}`);
    } catch (e) {
      toast.error(`Could not save ${key}`, { description: e instanceof Error ? e.message : String(e) });
    } finally {
      setSaving(false);
    }
  }

  async function resetValue(key: string, secret?: boolean) {
    setSaving(true);
    try {
      await resetConfigValue(key);
      const d = await getConfig();
      setSettings(d.settings);
      setEditing(null);
      toast.success(`${secret ? "Removed" : "Reset"} ${key}`);
    } catch (e) {
      toast.error(`Could not ${secret ? "remove" : "reset"} ${key}`, { description: e instanceof Error ? e.message : String(e) });
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
      {error && <p role="alert" className="text-sm text-destructive">Settings could not load: {error}</p>}

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
                        <Button size="xs" onClick={() => saveValue(s.key)} disabled={saving}>
                          {saving && <Spinner />}
                          Save
                        </Button>
                        <Button variant="ghost" size="xs" onClick={() => setEditing(null)} className="text-muted-foreground">
                          Cancel
                        </Button>
                      </div>
                    ) : (
                      <div className="flex items-center gap-2">
                        {/* Secrets arrive masked, so editing one starts empty. */}
                        <Button
                          variant="ghost"
                          size="xs"
                          className="h-auto flex-1 justify-start whitespace-normal break-all p-1 text-left font-mono text-xs font-normal"
                          onClick={() => { setEditing(s.key); setEditValue(s.secret ? "" : String(s.value)); }}
                        >
                          {s.secret && !s.value ? <span className="text-muted-foreground">Not set</span> : String(s.value)}
                        </Button>
                        {s.source === "config" && (
                          <Button
                            variant="ghost"
                            size="xs"
                            onClick={() => resetValue(s.key, s.secret)}
                            className="text-muted-foreground"
                            disabled={saving}
                          >
                            {s.secret ? "Remove" : "Reset"}
                          </Button>
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
