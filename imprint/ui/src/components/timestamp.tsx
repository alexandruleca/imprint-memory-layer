"use client";

import { useEffect, useState } from "react";

/** The current time, refreshed every `everyMs` so relative times and time
 * groups move on while a page stays open. */
export function useNow(everyMs = 30_000): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), everyMs);
    return () => clearInterval(timer);
  }, [everyMs]);
  return now;
}

/** The full local date and time, for a tooltip or a screen reader. */
export function absoluteTime(seconds: number): string {
  return new Date(seconds * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "medium" });
}

/** "just now", "42 s ago", "5 min ago", "3 h ago", "2 d ago", then the date. */
export function relativeTime(seconds: number, now: Date): string {
  const diff = now.getTime() / 1000 - seconds;
  if (diff < 10) return "just now";
  if (diff < 60) return `${Math.floor(diff)} s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} h ago`;
  if (diff < 7 * 86400) return `${Math.floor(diff / 86400)} d ago`;
  return dayLabel(seconds, now);
}

/** "3 Nov", with the year when it is not this year's. */
export function dayLabel(seconds: number, now: Date): string {
  const date = new Date(seconds * 1000);
  return date.toLocaleDateString(undefined, date.getFullYear() === now.getFullYear()
    ? { month: "short", day: "numeric" } : { month: "short", day: "numeric", year: "numeric" });
}

/** The time of day, "14:05". */
export function clockTime(seconds: number): string {
  return new Date(seconds * 1000).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

/** A moment as `text` (relative by default), the full date and time on hover. */
export function Timestamp({ seconds, now, text, className }: { seconds: number; now: Date; text?: string; className?: string }) {
  const full = absoluteTime(seconds);
  return (
    <time dateTime={new Date(seconds * 1000).toISOString()} title={full} className={className}>
      {text ?? relativeTime(seconds, now)}
    </time>
  );
}
