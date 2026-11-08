/** Calendar groups for newest-first lists, in the viewer's time zone:
 * Today, Yesterday, This week, Last week, This month, Last month, and then
 * either one group per earlier month ("September", "December 2025") or a
 * single "Older".
 *
 * - `groupByTime` groups items by these periods. Agents uses it with
 *   monthly groups (the default); `older: "single"` folds everything before
 *   last month into "Older".
 * - `periodStarts` gives the fixed period starts (epoch seconds) a server
 *   counts by, and `groupByPeriod` groups a page of items by those same
 *   starts, so a page and its counts agree (Queue history).
 *
 * An item lands in the first period whose start it is not before, so a week
 * that began last month stays "This week" and an empty period is skipped. */

export const PERIODS = ["Today", "Yesterday", "This week", "Last week", "This month", "Last month", "Older"] as const;
export type Period = (typeof PERIODS)[number];
const PERIOD_KEYS = ["today", "yesterday", "this-week", "last-week", "this-month", "last-month", "older"] as const;

export type TimeGroup<T> = { key: string; label: string; items: T[] };

export type TimeGroupOptions = {
  /** The moment the groups are seen from. Default: now. */
  now?: Date;
  /** First day of the week, 0 Sunday … 6 Saturday (7 is read as Sunday). Default: the viewer's locale. */
  weekStart?: number;
  /** Before last month: one group per month (default) or a single "Older". */
  older?: "monthly" | "single";
};

/** The first day of the week in the viewer's locale: 0 Sunday … 6 Saturday. */
export function localeWeekStart(locale?: string): number {
  try {
    const tag = locale ?? (typeof navigator === "undefined" ? "en-GB" : navigator.language);
    const loc = new Intl.Locale(tag) as Intl.Locale & {
      getWeekInfo?: () => { firstDay: number };
      weekInfo?: { firstDay: number };
    };
    const firstDay = (loc.getWeekInfo?.() ?? loc.weekInfo)?.firstDay;
    if (firstDay) return firstDay % 7; // Intl counts Monday 1 … Sunday 7.
  } catch {}
  return 1;
}

/** Where each period but Older starts, in epoch seconds, newest first. A
 * start later than the one before it is clamped to it, so that period is
 * empty: on the first day of a week "This week" holds nothing that is not
 * already Today. */
export function periodStarts(now: Date = new Date(), weekStart: number = localeWeekStart()): number[] {
  const day = (y: number, m: number, d: number) => new Date(y, m, d).getTime() / 1000;
  const y = now.getFullYear(), m = now.getMonth(), d = now.getDate();
  const sinceWeekStart = (now.getDay() - (weekStart % 7) + 7) % 7;
  const raw = [
    day(y, m, d),
    day(y, m, d - 1),
    day(y, m, d - sinceWeekStart),
    day(y, m, d - sinceWeekStart - 7),
    day(y, m, 1),
    day(y, m - 1, 1),
  ];
  const starts: number[] = [];
  for (const start of raw) starts.push(Math.min(start, starts.at(-1) ?? Infinity));
  return starts;
}

/** The period a moment (epoch seconds) falls in, given `periodStarts`. */
export function periodOf(ts: number, starts: number[]): Period {
  const i = starts.findIndex((start) => ts >= start);
  return PERIODS[i < 0 ? starts.length : i];
}

/** The group of one moment (epoch milliseconds) as seen at `now`. */
export function timeGroupOf(ms: number, options: TimeGroupOptions = {}, starts?: number[]): { key: string; label: string } {
  const now = options.now ?? new Date();
  const bounds = starts ?? periodStarts(now, options.weekStart ?? localeWeekStart());
  const index = PERIODS.indexOf(periodOf(ms / 1000, bounds));
  if (index < bounds.length || options.older === "single") return { key: PERIOD_KEYS[index], label: PERIODS[index] };
  const date = new Date(ms);
  const sameYear = date.getFullYear() === now.getFullYear();
  return {
    key: `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`,
    label: date.toLocaleDateString(undefined, sameYear ? { month: "long" } : { month: "long", year: "numeric" }),
  };
}

/** Groups items in their order; `at` gives each one's moment in epoch ms. */
export function groupByTime<T>(items: T[], at: (item: T) => number, options: TimeGroupOptions = {}): TimeGroup<T>[] {
  const now = options.now ?? new Date();
  const starts = periodStarts(now, options.weekStart ?? localeWeekStart());
  const groups = new Map<string, TimeGroup<T>>();
  for (const item of items) {
    const { key, label } = timeGroupOf(at(item), { ...options, now }, starts);
    const group = groups.get(key) ?? { key, label, items: [] };
    group.items.push(item);
    groups.set(key, group);
  }
  return [...groups.values()];
}

/** Items in runs of the same period, in their order, by fixed `starts`
 * (epoch seconds, from `periodStarts`); `at` gives each one's moment in
 * epoch seconds and `index` is the period's place in PERIODS, which is
 * where a server's per-period counts are. */
export function groupByPeriod<T>(items: T[], at: (item: T) => number, starts: number[]): { period: Period; index: number; items: T[] }[] {
  const groups: { period: Period; index: number; items: T[] }[] = [];
  for (const item of items) {
    const period = periodOf(at(item), starts);
    if (groups.at(-1)?.period === period) groups.at(-1)!.items.push(item);
    else groups.push({ period, index: PERIODS.indexOf(period), items: [item] });
  }
  return groups;
}
