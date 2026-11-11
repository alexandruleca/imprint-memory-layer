"use client";

import { usePathname } from "next/navigation";
import { CircleHelp } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuCheckboxItem, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem, DropdownMenuLabel,
  DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { pageAt, pageName } from "@/lib/pages";
import { requestTour, resumeTours, useSeenTours } from "@/lib/tour-state";
import { tourAt, WELCOME } from "@/lib/tours";
import { setTutorials, useUiMode } from "@/lib/ui-mode";

/** Top-bar menu: replay the open page's tutorial or the welcome, and turn
 * tutorials that start by themselves on or off (ADR 0183). */
export function HelpMenu() {
  const pathname = usePathname();
  const ui = useUiMode();
  const { forgetAll } = useSeenTours();
  const page = pageAt(pathname);
  const tour = tourAt(pathname);
  if (!ui.known) return null;

  const tutorials = (on: boolean) => setTutorials(on).catch((error) => {
    toast.error("Could not change tutorials", { description: error instanceof Error ? error.message : String(error) });
  });

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={<Button variant="ghost" size="icon" className="app-no-drag text-muted-foreground hover:text-foreground" />}
        aria-label="Help and tutorials"
        data-tour="help"
      >
        <CircleHelp aria-hidden />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64">
        <DropdownMenuGroup>
          <DropdownMenuLabel>Tutorials</DropdownMenuLabel>
          <DropdownMenuItem disabled={!tour} onClick={() => tour?.page && requestTour(tour.page)}>
            {page && tour ? `Show me around ${pageName(page, ui.simple)}` : "No tutorial for this page"}
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => requestTour(WELCOME.id)}>Show the welcome again</DropdownMenuItem>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuGroup>
          <DropdownMenuCheckboxItem checked={ui.tutorials} onCheckedChange={(on) => void tutorials(on)}>
            Start tutorials by themselves
          </DropdownMenuCheckboxItem>
          <DropdownMenuItem onClick={() => { forgetAll(); resumeTours(); void tutorials(true); }}>Show every tutorial again</DropdownMenuItem>
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
