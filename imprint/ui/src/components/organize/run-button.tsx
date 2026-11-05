"use client";

import { useRouter } from "next/navigation";
import { ChevronDown, WandSparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";

/** Run Organize as a split button: the main part queues a run, the menu
 * runs it without the local helper or opens the Queue. */
export function RunButton({ disabled, onRun }: { disabled: boolean; onRun: (withoutModel: boolean) => void }) {
  const router = useRouter();
  return (
    <div className="flex">
      <Button disabled={disabled} onClick={() => onRun(false)} className="rounded-r-none">
        <WandSparkles /> Run Organize
      </Button>
      <DropdownMenu>
        <DropdownMenuTrigger render={
          <Button aria-label="More ways to run" className="rounded-l-none border-l border-primary-foreground/25 px-2" />
        }>
          <ChevronDown />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-60">
          <DropdownMenuItem disabled={disabled} onClick={() => onRun(true)}>Run without the local helper</DropdownMenuItem>
          <DropdownMenuItem onClick={() => router.push("/queue/")}>Open Queue</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
