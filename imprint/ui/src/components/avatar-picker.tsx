"use client";

import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import type { ConfigChoice } from "@/lib/types";

/** Echo's looks as preview cards. The choices are the voice.avatar setting's,
 * so Settings and this picker always offer the same looks. */
export function AvatarPicker({ choices, value, onChange, compact = false }: {
  choices: ConfigChoice[]; value: string; onChange: (value: string) => void; compact?: boolean;
}) {
  return <ToggleGroup aria-label="Echo's look" className={`echo-avatars ${compact ? "echo-avatars-compact" : ""}`}
    value={[value]} onValueChange={(next) => { if (next[0]) onChange(String(next[0])); }}>
    {choices.map((choice) => {
      const id = String(choice.value);
      return <ToggleGroupItem key={id} value={id} className="echo-avatar-option" aria-label={`${choice.label}: ${choice.detail}`}>
        <img src={`/models/imprint-${id}.jpg`} alt="" width={compact ? 52 : 84} height={compact ? 52 : 84}/>
        <span><b>{choice.label}</b>{!compact && <small>{choice.detail}</small>}</span>
      </ToggleGroupItem>;
    })}
  </ToggleGroup>;
}
