// Benchmark deltas are stored with ASCII hyphens so they stay parseable;
// display them with a typographic minus.
export function signed(pct: string | undefined, fallback = ''): string {
  return (pct ?? fallback).replace(/^-/, '−');
}
