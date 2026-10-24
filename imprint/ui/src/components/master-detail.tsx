import { ScrollArea } from "@/components/ui/scroll-area";

/** A list beside the selected item's details (Topics, Projects, Sources).
 * The details flow with the page, so the page scroller is the only one they
 * need. The list is a capped side pane that scrolls on its own, so a long list
 * never pushes the details out of reach. Narrow screens stack the two. */
export function MasterDetail({ list, filter, children }: {
  list: React.ReactNode;
  /** Shown above the list, outside its scroll pane. */
  filter?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
      <div className="min-w-0 space-y-2">
        {filter}
        <ScrollArea className="max-h-80 lg:max-h-150">{list}</ScrollArea>
      </div>
      <div className="min-w-0">{children}</div>
    </div>
  );
}
