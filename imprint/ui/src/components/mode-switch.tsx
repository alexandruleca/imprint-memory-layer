"use client";

import { Gauge, ListChecks } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuLabel, DropdownMenuRadioGroup,
  DropdownMenuRadioItem, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { setUiMode, useUiMode, type UiMode } from "@/lib/ui-mode";

const CHOICES: { value: UiMode; label: string; detail: string; icon: typeof Gauge }[] = [
  { value: "simple", label: "Simple", detail: "Everyday pages, plain words", icon: ListChecks },
  { value: "advanced", label: "Advanced", detail: "Every page and setting", icon: Gauge },
];

export function switchMode(mode: UiMode) {
  return setUiMode(mode).catch((error) => {
    toast.error("Could not switch the dashboard", { description: error instanceof Error ? error.message : String(error) });
  });
}

/** Top-bar menu: the simple or the advanced dashboard (ADR 0182). */
export function ModeSwitch() {
  const ui = useUiMode();
  if (!ui.known) return null;
  const current = CHOICES.find((choice) => choice.value === ui.mode) ?? CHOICES[1];
  const Icon = current.icon;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={<Button variant="ghost" size="sm" className="app-no-drag gap-1.5 px-2 text-muted-foreground hover:text-foreground" />}
        aria-label={`Dashboard: ${current.label}`}
        data-tour="mode-switch"
      >
        <Icon aria-hidden />
        <span className="hidden md:inline">{current.label}</span>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-60">
        <DropdownMenuGroup>
          <DropdownMenuLabel>Dashboard</DropdownMenuLabel>
          <DropdownMenuRadioGroup value={ui.mode} onValueChange={(value) => void switchMode(value as UiMode)}>
            {CHOICES.map(({ value, label, detail, icon: ItemIcon }) => (
              <DropdownMenuRadioItem key={value} value={value}>
                <ItemIcon aria-hidden />
                <span className="flex min-w-0 flex-col">
                  <span>{label}</span>
                  <span className="text-xs text-muted-foreground">{detail}</span>
                </span>
              </DropdownMenuRadioItem>
            ))}
          </DropdownMenuRadioGroup>
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
