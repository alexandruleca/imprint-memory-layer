#!/usr/bin/env node
// Parse ../../BENCHMARK.md into site/src/data/benchmarks.json at build time.
// Zero runtime fetch — Astro pages import the JSON and render statically.

import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const BENCHMARK_MD = resolve(__dirname, '../../BENCHMARK.md');
const OUT = resolve(__dirname, '../src/data/benchmarks.json');

const md = readFileSync(BENCHMARK_MD, 'utf8');

function parseCategoryTable(heading) {
  const re = new RegExp(
    `### ${heading}[\\s\\S]*?\\n\\n((?:\\|[^\\n]+\\n){3,})\\n\\*\\*Average\\*\\*: ([+-]?\\d+\\.\\d+%) tokens, ([+-]?\\d+\\.\\d+%) cost`,
    'm',
  );
  const m = md.match(re);
  if (!m) return null;
  const [, tableBlock, avgTokens, avgCost] = m;
  const rows = tableBlock
    .split('\n')
    .filter((l) => l.startsWith('|') && !l.startsWith('|---') && !l.includes('OFF tokens'))
    .map((l) => l.split('|').map((c) => c.trim()).filter(Boolean))
    .filter((cells) => cells.length >= 7)
    .map(([prompt, offTokens, onTokens, savings, pct, offCost, onCost, costDelta]) => ({
      prompt,
      offTokens: parseInt(offTokens.replace(/,/g, ''), 10),
      onTokens: parseInt(onTokens.replace(/,/g, ''), 10),
      savings: parseInt(savings.replace(/,/g, ''), 10),
      pct,
      offCost,
      onCost,
      costDelta,
    }));
  return { avgTokens, avgCost, rows };
}

const categories = [
  { key: 'information', heading: 'Information Prompts', label: 'Information' },
  { key: 'decision', heading: 'Decision Recall Prompts', label: 'Decision Recall' },
  { key: 'debug', heading: 'Debugging Prompts', label: 'Debugging' },
  { key: 'cross', heading: 'Cross-Project Prompts', label: 'Cross-Project' },
  { key: 'summary', heading: 'Session Summary Prompts', label: 'Session Summary' },
  { key: 'creation', heading: 'Creation Prompts', label: 'Creation' },
].map((c) => ({ ...c, ...parseCategoryTable(c.heading) }));

const overallMatch = md.match(
  /### Overall\s*\n\s*- \*\*Token savings\*\*: ([+-]?\d+\.\d+%) \(([\d,]+) → ([\d,]+)\)\s*\n\s*- \*\*Cost savings\*\*: ([+-]?\d+\.\d+%) \(\$([\d.]+) → \$([\d.]+)\)/,
);

const overall = overallMatch
  ? {
      tokenPct: overallMatch[1],
      tokensOff: parseInt(overallMatch[2].replace(/,/g, ''), 10),
      tokensOn: parseInt(overallMatch[3].replace(/,/g, ''), 10),
      costPct: overallMatch[4],
      costOff: parseFloat(overallMatch[5]),
      costOn: parseFloat(overallMatch[6]),
    }
  : null;

const envMatch = md.match(/OS:\s+(\S+)[\s\S]*?Python:\s+(\S+)[\s\S]*?Model:\s+([^\n]+)[\s\S]*?Imprint:\s+(\S+)[\s\S]*?Memories:\s+(\d+)/);
const env = envMatch
  ? {
      os: envMatch[1],
      python: envMatch[2],
      model: envMatch[3].trim(),
      imprint: envMatch[4],
      memories: parseInt(envMatch[5], 10),
    }
  : null;

const payload = {
  generatedAt: new Date().toISOString(),
  overall,
  categories,
  env,
};

mkdirSync(dirname(OUT), { recursive: true });
writeFileSync(OUT, JSON.stringify(payload, null, 2));

const summary = overall
  ? `${overall.tokenPct} tokens / ${overall.costPct} cost`
  : 'no overall numbers parsed';
console.log(`[build-benchmarks] ${summary} → ${OUT}`);
