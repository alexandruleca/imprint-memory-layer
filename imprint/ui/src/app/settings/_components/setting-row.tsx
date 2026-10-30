"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, Check, ChevronRight, Lightbulb, RotateCcw, Trash2, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectSeparator, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Hint } from "@/components/hint";
import { Spinner } from "@/components/loaders";
import { cn } from "@/lib/utils";
import type { ConfigSetting } from "@/lib/types";

const CUSTOM = "\u0000custom";
const WORDS: Record<string, string> = {
  api: "API", ctx: "context", gpu: "GPU", grpc: "gRPC", hf: "HF", id: "ID", llm: "LLM",
  mb: "(MB)", ocr: "OCR", sec: "(s)", url: "URL", urls: "URLs",
};

/** "tagger.local.n_gpu_layers" → "GPU layers" */
export function settingTitle(key: string) {
  if (key === "chat.analysis_workers") return "Sequential analysis workers";
  if (key === "chat.research_timeout_sec") return "Research time budget (seconds)";
  if (key === "chat.research_token_budget") return "Research token budget";
  if (key === "chat.research_no_progress_rounds") return "Rounds without new evidence";
  const words = key.split(".").pop()!.split("_");
  if (words[0] === "n" && words.length > 1) words.shift();
  const text = words.map((w) => WORDS[w] ?? w).join(" ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

type Props = {
  setting: ConfigSetting;
  /** Persist a value; resolves false when the server refused it. */
  onSave: (key: string, value: string) => Promise<boolean>;
  onReset: (setting: ConfigSetting) => Promise<boolean>;
};

export function SettingRow({ setting: s, onSave, onReset }: Props) {
  const id = `setting-${s.key}`;
  const title = settingTitle(s.key);
  const fromEnv = s.source === "env";
  const choices = s.choices ?? [];
  const current = choices.find((c) => String(c.value) === String(s.value));
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (!saved) return;
    const timer = setTimeout(() => setSaved(false), 2000);
    return () => clearTimeout(timer);
  }, [saved]);

  async function persist(write: () => Promise<boolean>) {
    setBusy(true);
    try {
      const ok = await write();
      setSaved(ok);
      return ok;
    } finally {
      setBusy(false);
    }
  }
  const save = (value: string) => persist(() => onSave(s.key, value));
  const reset = () => persist(() => onReset(s));

  let control: React.ReactNode;
  if (s.type === "bool") {
    control = (
      <span className="flex h-8 items-center">
        <Switch id={id} checked={!!s.value} disabled={busy || fromEnv} onCheckedChange={(on: boolean) => save(String(on))} />
      </span>
    );
  } else if (s.secret) {
    control = <SecretControl id={id} setting={s} disabled={busy || fromEnv} save={save} />;
  } else if (choices.length) {
    control = <ChoiceControl id={id} setting={s} disabled={busy || fromEnv} save={save} />;
  } else {
    control = <TextControl id={id} setting={s} disabled={busy || fromEnv} save={save} />;
  }

  return (
    <div className="grid gap-3 px-4 py-3.5 @2xl:grid-cols-[minmax(0,1fr)_auto] @2xl:items-start @2xl:gap-6 sm:px-6">
      <div className="min-w-0 space-y-0.5">
        <div className="flex flex-wrap items-center gap-2">
          <Label htmlFor={id} className="text-sm font-medium">{title}</Label>
          {s.source === "config" && <Badge variant="secondary" className="text-[10px]">Changed</Badge>}
          {fromEnv && (
            <Hint label={`Set by ${s.env}; environment variables override config.json`}>
              <Badge variant="outline" className="font-mono text-[10px]">{s.env}</Badge>
            </Hint>
          )}
        </div>
        <p className="text-xs text-muted-foreground">{s.desc}</p>
        <p className="font-mono text-[11px] text-muted-foreground/70">{s.key}</p>
        {(s.help || s.example) && <Explanation id={`${id}-help`} help={s.help} example={s.example} />}
      </div>
      <div className="min-w-0 space-y-1.5 @2xl:w-[22rem]">
        <div className="flex items-start gap-1.5 @2xl:justify-end">
          <div className="flex min-w-0 flex-1 @2xl:justify-end">{control}</div>
          {/* One slot keeps every control's right edge aligned: progress, then the reset action. */}
          <div className="flex size-8 shrink-0 items-center justify-center">
            {busy ? (
              <Spinner className="size-3.5 text-muted-foreground" />
            ) : saved ? (
              <span role="status" className="text-emerald-700 dark:text-emerald-400">
                <Check className="size-4" />
                <span className="sr-only">Saved</span>
              </span>
            ) : s.source === "config" ? (
              <Hint label={s.secret ? "Remove the stored value" : `Reset to the default (${formatValue(s.default)})`}>
                <Button variant="ghost" size="icon-sm" aria-label={s.secret ? "Remove" : "Reset"} className="text-muted-foreground" onClick={reset}>
                  {s.secret ? <Trash2 /> : <RotateCcw />}
                </Button>
              </Hint>
            ) : null}
          </div>
        </div>
        {fromEnv && <p className="text-xs text-muted-foreground @2xl:pr-10 @2xl:text-right">{s.env} is set in the environment and takes precedence.</p>}
        {current?.unavailable && (
          <p role="alert" className="flex items-start gap-1.5 text-xs text-amber-700 @2xl:justify-end @2xl:pr-10 @2xl:text-right dark:text-amber-400">
            <AlertTriangle className="mt-px size-3.5 shrink-0" />
            {current.unavailable}
          </p>
        )}
      </div>
    </div>
  );
}

/** What the setting is for, folded under its description so the list stays scannable. */
function Explanation({ id, help, example }: { id: string; help?: string; example?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="pt-1">
      <button type="button" aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)}
        className="inline-flex items-center gap-1 rounded text-xs font-medium text-primary hover:underline focus-visible:outline-2 focus-visible:outline-ring">
        <ChevronRight className={cn("size-3.5 transition-transform", open && "rotate-90")} aria-hidden />
        What is this for?
      </button>
      {open && (
        <div id={id} className="mt-1.5 max-w-prose space-y-2 rounded-md border border-border bg-muted/40 px-3 py-2.5 text-xs leading-relaxed">
          {help && <p>{help}</p>}
          {example && (
            <p className="flex items-start gap-1.5 text-muted-foreground">
              <Lightbulb className="mt-px size-3.5 shrink-0 text-amber-600 dark:text-amber-400" aria-hidden />
              <span><span className="font-medium text-foreground">Example:</span> {example}</span>
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function formatValue(value: unknown) {
  return value === "" || value === null || value === undefined ? "empty" : String(value);
}

type ControlProps = {
  id: string;
  setting: ConfigSetting;
  disabled: boolean;
  save: (value: string) => Promise<boolean>;
};

/** Known values in a dropdown; "Custom value…" opens a field when other values are valid. */
function ChoiceControl({ id, setting: s, disabled, save }: ControlProps) {
  const choices = s.choices ?? [];
  const known = choices.some((c) => String(c.value) === String(s.value));
  // A stored value outside the choices is always shown as custom.
  const [picking, setPicking] = useState(false);
  const custom = !known || picking;
  const items = [
    ...choices.map((c) => ({ value: String(c.value), label: c.label })),
    ...(s.custom ? [{ value: CUSTOM, label: known ? "Custom value…" : `Custom: ${s.value}` }] : []),
  ];

  return (
    <div className="flex w-full min-w-0 flex-col gap-1.5 @2xl:w-80">
      <Select
        items={items}
        value={custom ? CUSTOM : String(s.value)}
        disabled={disabled}
        onValueChange={(value) => {
          if (value === CUSTOM) { setPicking(true); return; }
          setPicking(false);
          if (value !== null && value !== String(s.value)) save(value as string);
        }}
      >
        <SelectTrigger id={id} className="w-full" aria-label={settingTitle(s.key)}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent alignItemWithTrigger={false} className="w-auto max-w-[min(28rem,calc(100vw-2rem))] min-w-(--anchor-width)">
          {choices.map((c) => (
            <SelectItem key={String(c.value)} value={String(c.value)} disabled={!!c.unavailable && String(c.value) !== String(s.value)}>
              <span className="flex min-w-0 flex-col whitespace-normal">
                <span>{c.label}</span>
                {(c.unavailable || c.detail) && (
                  <span className={cn("text-xs", c.unavailable ? "text-amber-700 dark:text-amber-400" : "text-muted-foreground")}>
                    {c.unavailable || c.detail}
                  </span>
                )}
              </span>
            </SelectItem>
          ))}
          {s.custom && (
            <>
              <SelectSeparator />
              <SelectItem value={CUSTOM}>Custom value…</SelectItem>
            </>
          )}
        </SelectContent>
      </Select>
      {custom && (
        <TextControl
          id={`${id}-custom`}
          setting={s}
          disabled={disabled}
          save={async (value) => { const saved = await save(value); if (saved) setPicking(false); return saved; }}
          initial={known ? "" : String(s.value)}
          label={`Custom ${settingTitle(s.key)}`}
          autoFocus={known}
          onCancel={known ? () => setPicking(false) : undefined}
        />
      )}
    </div>
  );
}

/** An always-editable field: Save and Cancel appear once it differs from the stored value. */
function TextControl({ id, setting: s, disabled, save, initial, label, autoFocus, onCancel }: ControlProps & {
  initial?: string;
  label?: string;
  autoFocus?: boolean;
  onCancel?: () => void;
}) {
  const stored = initial ?? String(s.value ?? "");
  const [draft, setDraft] = useState(stored);
  const [base, setBase] = useState(stored);
  // A new stored value (saved here or reset) replaces an untouched draft.
  if (stored !== base) { setBase(stored); setDraft(stored); }
  const dirty = draft !== stored;
  const numeric = s.type === "int" || s.type === "float";

  async function commit() {
    if (dirty && draft.trim() !== "" && (await save(draft.trim()))) setDraft(draft.trim());
  }
  function cancel() {
    setDraft(stored);
    onCancel?.();
  }

  return (
    <div className="flex w-full min-w-0 items-center gap-1 @2xl:w-80">
      <Input
        id={id}
        value={draft}
        disabled={disabled}
        autoFocus={autoFocus}
        aria-label={label ?? settingTitle(s.key)}
        inputMode={numeric ? (s.type === "int" ? "numeric" : "decimal") : undefined}
        placeholder={s.default === "" ? "Empty" : String(s.default)}
        className="h-8 min-w-0 flex-1 font-mono text-xs"
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit();
          if (e.key === "Escape") cancel();
        }}
      />
      {(dirty || onCancel) && (
        <>
          <Hint label="Save">
            <Button size="icon-sm" aria-label={`Save ${s.key}`} disabled={disabled || !dirty || draft.trim() === ""} onClick={commit}>
              <Check />
            </Button>
          </Hint>
          <Hint label="Cancel">
            <Button variant="ghost" size="icon-sm" aria-label="Cancel" className="text-muted-foreground" disabled={disabled} onClick={cancel}>
              <X />
            </Button>
          </Hint>
        </>
      )}
    </div>
  );
}

/** Secrets arrive masked, so replacing one starts from an empty, hidden field. */
function SecretControl({ id, setting: s, disabled, save }: ControlProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");

  async function commit() {
    if (draft && (await save(draft))) { setEditing(false); setDraft(""); }
  }

  if (!editing) {
    // Named by its content (the masked value), not the row label.
    return (
      <Button
        variant="outline"
        size="sm"
        disabled={disabled}
        className="h-8 w-full justify-start font-mono text-xs font-normal @2xl:w-80"
        onClick={() => setEditing(true)}
      >
        {s.value ? String(s.value) : <span className="font-sans text-muted-foreground">Not set</span>}
      </Button>
    );
  }
  return (
    <div className="flex w-full min-w-0 items-center gap-1 @2xl:w-80">
      <Input
        id={id}
        type="password"
        autoComplete="off"
        autoFocus
        value={draft}
        placeholder="Paste a new value"
        aria-label={s.key}
        className="h-8 min-w-0 flex-1 text-xs"
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit();
          if (e.key === "Escape") setEditing(false);
        }}
      />
      <Button size="sm" disabled={disabled || !draft} onClick={commit}>Save</Button>
      <Button variant="ghost" size="sm" className="text-muted-foreground" onClick={() => { setEditing(false); setDraft(""); }}>
        Cancel
      </Button>
    </div>
  );
}
