"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Button, buttonVariants } from "@/components/ui/button";
import { switchMode } from "@/components/mode-switch";
import { pageAt } from "@/lib/pages";
import { useUiMode } from "@/lib/ui-mode";

/** Above a page the simple dashboard does not list: every address works in
 * both dashboards, and this says where the viewer is (ADR 0182). */
export function AdvancedPageNotice() {
  const ui = useUiMode();
  const page = pageAt(usePathname());
  if (!ui.known || !ui.simple || !page || page.simple) return null;
  return (
    <div role="note" className="mx-4 mt-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-muted/50 px-4 py-2.5 text-sm sm:mx-8">
      <p><span className="font-medium">{page.label}</span> is a page of the advanced dashboard. It works here too; the simple dashboard just does not list it.</p>
      <div className="flex items-center gap-2">
        <Button size="sm" variant="outline" onClick={() => void switchMode("advanced")}>Switch to Advanced</Button>
        <Link href="/" className={buttonVariants({ variant: "ghost", size: "sm" })}>Back to Home</Link>
      </div>
    </div>
  );
}
