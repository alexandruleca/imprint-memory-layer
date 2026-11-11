"use client";

import Link from "next/link";
import { useId, useState, useSyncExternalStore } from "react";
import { toast } from "sonner";
import {
  AppWindow, AudioLines, Bot, Disc3, ExternalLink, Info, MonitorSmartphone, ShieldCheck, WandSparkles, type LucideIcon,
} from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Spinner } from "@/components/loaders";
import { PageHeader } from "@/components/split-page";
import { switchMode } from "@/components/mode-switch";
import { applySimpleSetting, getSimpleSettings, type SimpleGroup, type SimpleItem } from "@/lib/api";
import { setTheme, useThemeChoice, type ThemeChoice } from "@/lib/theme";
import { refreshUiState } from "@/lib/ui-mode";
import { useCached } from "@/lib/use-cached";
import { cn } from "@/lib/utils";

/** Settings in the simple dashboard (ADR 0182): a short list of choices in
 * plain words. Each one writes settings the advanced dashboard shows too;
 * values set there that match no option here show as "Custom" and stay as
 * they are until an option is picked. The list and its words come from the
 * server (imprint/config_simple.py). */

const ICONS: Record<string, LucideIcon> = {
  assistant: Bot, echo: AudioLines, recordings: Disc3, tidy: WandSparkles, privacy: ShieldCheck,
  devices: MonitorSmartphone, app: AppWindow,
};
const THEMES: { value: ThemeChoice; label: string; detail: string }[] = [
  { value: "light", label: "Light", detail: "" },
  { value: "dark", label: "Dark", detail: "" },
  { value: "system", label: "Same as this computer", detail: "Follows its light or dark setting" },
];

const message = (error: unknown) => (error instanceof Error ? error.message : String(error));

export function SimpleSettings() {
  const settings = useCached("settings-simple", getSimpleSettings, { global: true, persist: false, revalidateOnFocus: false, revalidateOnJobs: false });
  const desktop = useSyncExternalStore(() => () => {}, () => !!window.electronAPI?.isElectron, () => false);
  const [busy, setBusy] = useState<string | null>(null);
  const groups = settings.data?.groups ?? [];

  async function apply(item: SimpleItem, choice: { on: boolean } | { option: number }) {
    setBusy(item.id);
    try {
      const answer = await applySimpleSetting(item.id, choice);
      settings.mutate({ groups: answer.groups });
      if (answer.keys.some((key) => key.startsWith("ui."))) await refreshUiState();
    } catch (error) {
      toast.error(`Could not change “${item.title}”`, { description: message(error) });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="mx-auto w-full max-w-3xl">
      <PageHeader
        title="Settings"
        updating={settings.refreshing}
        description="The choices most people need, saved on this device as soon as you make them. Every other setting is in the advanced dashboard."
        actions={<Button variant="outline" size="sm" onClick={() => void switchMode("advanced")}>All settings (Advanced)</Button>}
      />
      <div className="space-y-4 px-4 pb-8 sm:px-8">
        {settings.error && !settings.data && (
          <p role="alert" className="text-sm text-destructive">Settings could not load: {settings.error.message}</p>
        )}
        {settings.loading && [0, 1, 2].map((n) => <Skeleton key={n} className="h-48 w-full rounded-xl" />)}
        {groups.map((group) => (
          <GroupCard key={group.id} group={group} desktop={desktop} busy={busy} onApply={apply} />
        ))}
      </div>
    </div>
  );
}

function GroupCard({ group, desktop, busy, onApply }: {
  group: SimpleGroup;
  desktop: boolean;
  busy: string | null;
  onApply: (item: SimpleItem, choice: { on: boolean } | { option: number }) => void;
}) {
  const Icon = ICONS[group.id] ?? Bot;
  // What only the desktop app uses is left out of a browser.
  const items = group.items.filter((item) => desktop || !item.desktop);
  if (!items.length) return null;
  return (
    <Card id={`simple-${group.id}`} data-tour={`settings-${group.id}`} className="gap-0 pb-0">
      <CardHeader className="flex items-center gap-3">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground">
          <Icon className="size-4" aria-hidden />
        </span>
        <div className="min-w-0">
          <CardTitle><h2>{group.title}</h2></CardTitle>
          <CardDescription className="text-xs">{group.desc}</CardDescription>
        </div>
      </CardHeader>
      <CardContent className="mt-4 divide-y divide-border border-t border-border px-0">
        {items.map((item) => (
          <div key={item.id} className="px-4 py-4 sm:px-6">
            <ItemRow item={item} busy={busy === item.id} disabled={busy !== null} onApply={onApply} />
          </div>
        ))}
        {group.advanced.length > 0 && (
          <p className="px-4 py-3 text-xs text-muted-foreground sm:px-6">
            More in the advanced settings:{" "}
            {group.advanced.map((section, index) => (
              <span key={section.id}>
                {index > 0 && ", "}
                <a href={`#${section.id}`} className="rounded-sm underline underline-offset-2 outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50">{section.title}</a>
              </span>
            ))}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function Heading({ id, item, busy }: { id: string; item: SimpleItem; busy: boolean }) {
  return (
    <div className="min-w-0 space-y-0.5">
      <p id={id} className="flex items-center gap-2 text-sm font-medium">
        {item.title}
        {busy && <Spinner className="size-3" />}
      </p>
      {item.desc && <p id={`${id}-desc`} className="max-w-prose text-xs text-muted-foreground">{item.desc}</p>}
    </div>
  );
}

function Locked({ env }: { env: string }) {
  if (!env) return null;
  return (
    <p className="mt-2 flex items-start gap-1.5 text-xs text-muted-foreground">
      <Info className="mt-0.5 size-3 shrink-0" aria-hidden />
      Set outside the dashboard, by the environment variable {env}, so it cannot be changed here.
    </p>
  );
}

function ItemRow({ item, busy, disabled, onApply }: {
  item: SimpleItem;
  busy: boolean;
  disabled: boolean;
  onApply: (item: SimpleItem, choice: { on: boolean } | { option: number }) => void;
}) {
  const id = useId();
  const theme = useThemeChoice();

  if (item.kind === "switch") {
    return (
      <div>
        <div className="flex items-start justify-between gap-4">
          <Heading id={id} item={item} busy={busy} />
          <Switch aria-labelledby={id} aria-describedby={item.desc ? `${id}-desc` : undefined} checked={item.on}
            disabled={disabled || !!item.locked} onCheckedChange={(on) => onApply(item, { on })} className="mt-0.5" />
        </div>
        <Locked env={item.locked} />
      </div>
    );
  }

  if (item.kind === "link") {
    return (
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <Heading id={id} item={item} busy={false} />
        {/* A real link, styled as a button. A section of this page is a plain
            anchor: the page follows the address's hash. */}
        {item.href.startsWith("#")
          ? <a href={item.href} className={buttonVariants({ variant: "outline", size: "sm" })}>{item.label}</a>
          : <Link href={item.href} className={buttonVariants({ variant: "outline", size: "sm" })}>{item.label}<ExternalLink aria-hidden /></Link>}
      </div>
    );
  }

  if (item.kind === "report") {
    return (
      <div className="space-y-2">
        <Heading id={id} item={item} busy={false} />
        <ul aria-labelledby={id} className={cn("space-y-1 rounded-lg px-3 py-2 text-sm", item.ok ? "bg-success/10" : "bg-warning/10")}>
          {item.lines.map((line) => (
            <li key={line} className="flex items-start gap-2">
              <ShieldCheck className={cn("mt-0.5 size-4 shrink-0", item.ok ? "text-success" : "text-warning")} aria-hidden />
              <span>{line}</span>
            </li>
          ))}
        </ul>
        <p className="text-xs text-muted-foreground">{item.note}</p>
      </div>
    );
  }

  if (item.kind === "theme") {
    return (
      <Options id={id} item={item} busy={false} disabled={false} value={String(THEMES.findIndex((t) => t.value === theme))}
        options={THEMES.map((t) => ({ label: t.label, detail: t.detail, unavailable: "" }))}
        onChoose={(index) => setTheme(THEMES[index].value)} />
    );
  }

  return (
    <div>
      <Options id={id} item={item} busy={busy} disabled={disabled || !!item.locked}
        value={item.selected === null ? "" : String(item.selected)} options={item.options}
        onChoose={(option) => onApply(item, { option })} />
      {item.selected === null && (
        <p className="mt-2 flex items-start gap-1.5 text-xs text-muted-foreground">
          <Info className="mt-0.5 size-3 shrink-0" aria-hidden />
          Custom: this was set in the advanced settings to something not listed here. It stays as it is until you pick an option.
        </p>
      )}
      <Locked env={item.locked} />
    </div>
  );
}

function Options({ id, item, busy, disabled, value, options, onChoose }: {
  id: string;
  item: SimpleItem;
  busy: boolean;
  disabled: boolean;
  value: string;
  options: { label: string; detail: string; unavailable: string }[];
  onChoose: (index: number) => void;
}) {
  return (
    <div className="space-y-2.5">
      <Heading id={id} item={item} busy={busy} />
      <RadioGroup aria-labelledby={id} aria-describedby={item.desc ? `${id}-desc` : undefined} value={value} disabled={disabled}
        onValueChange={(next) => onChoose(Number(next))}
        className={cn("gap-2", options.length > 3 ? "sm:grid-cols-2" : options.length === 3 ? "sm:grid-cols-3" : "sm:grid-cols-2")}>
        {options.map((option, index) => {
          const selected = value === String(index);
          const off = disabled || !!option.unavailable;
          return (
            <label key={option.label}
              className={cn(
                "flex items-start gap-2.5 rounded-xl border bg-background p-3 text-xs transition-colors has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-ring",
                selected ? "border-foreground ring-1 ring-foreground" : "border-border",
                off ? "opacity-60" : "cursor-pointer hover:border-foreground/30",
              )}>
              <RadioGroupItem value={String(index)} disabled={off} className="mt-0.5" />
              <span className="min-w-0">
                <span className="block text-sm font-medium">{option.label}</span>
                {(option.unavailable || option.detail) && (
                  <span className="block text-muted-foreground">{option.unavailable || option.detail}</span>
                )}
              </span>
            </label>
          );
        })}
      </RadioGroup>
    </div>
  );
}
