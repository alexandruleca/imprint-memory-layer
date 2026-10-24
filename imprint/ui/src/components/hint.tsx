"use client";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

/** A hover and focus hint for a control, rendered as the control itself.
 * The hint only describes: give icon-only controls an aria-label too. */
export function Hint({ label, side, className, children }: {
  label: React.ReactNode;
  side?: React.ComponentProps<typeof TooltipContent>["side"];
  /** Classes for the hint, e.g. to show it only while a label is hidden. */
  className?: string;
  children: React.ReactElement;
}) {
  return (
    <Tooltip>
      <TooltipTrigger render={children} />
      <TooltipContent side={side} className={className}>{label}</TooltipContent>
    </Tooltip>
  );
}
