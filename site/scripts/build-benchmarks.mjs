#!/usr/bin/env node
// Build from full-precision measured JSON; retain the presentation interface.
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

export function validateReport(report) {
  const check = (ok, message) => { if (!ok) throw new Error(`Invalid benchmark: ${message}`); };
  check(report.schemaVersion === 2 && report.status === 'complete', 'a complete protocol-v2 run is required');
  check(Number.isFinite(Date.parse(report.generatedAt)), 'measurement date is missing');
  check(/^[a-f0-9]{40}$/.test(report.sourceRevision), 'source revision is missing');
  check(report.runCount === report.expectedRuns && report.runCount > 0, 'trial count mismatch');
  check(report.runCount === report.successfulRuns + report.failedRuns, 'failure accounting mismatch');
  check(report.validPairs > 0 && report.validPairs * 2 <= report.successfulRuns, 'invalid pairing');
  check(report.modelIds?.length === 1, 'mixed or absent resolved models');
  for (const key of ['tokensOff', 'tokensOn', 'costOff', 'costOn']) {
    check(Number.isFinite(report.overall?.[key]) && report.overall[key] >= 0, `missing ${key}`);
  }
  check(report.overall.qualityPct === null, 'unvalidated quality uplift');
  check(report.categories.reduce((n, c) => n + c.rows.length, 0) === report.promptCount, 'missing task rows');
  check(report.categories.flatMap(c => c.rows).reduce((n, r) => n + r.pairs, 0) === report.validPairs, 'task pairing mismatch');
  return report;
}

export function loadBenchmarks(root) {
  const pointer = JSON.parse(readFileSync(resolve(root, 'benchmark/latest.json'), 'utf8'));
  if (pointer.schemaVersion !== 2 || !/^[\w-]+$/.test(pointer.runId)) throw new Error('Invalid benchmark run selector');
  const report = validateReport(JSON.parse(readFileSync(resolve(root, 'benchmark/published', pointer.runId, 'summary.json'), 'utf8')));
  if (report.runId !== pointer.runId) throw new Error('Benchmark run ID mismatch');
  const manifest = JSON.parse(readFileSync(resolve(root, 'benchmark/published', pointer.runId, 'manifest.json'), 'utf8'));
  if (manifest.runId !== report.runId || manifest.sourceRevision !== report.sourceRevision) throw new Error('Benchmark manifest mismatch');
  let previous = null;
  if (pointer.previousRunId) {
    if (!/^[\w-]+$/.test(pointer.previousRunId)) throw new Error('Invalid previous benchmark selector');
    previous = validateReport(JSON.parse(readFileSync(resolve(root, 'benchmark/published', pointer.previousRunId, 'summary.json'), 'utf8')));
    const priorManifest = JSON.parse(readFileSync(resolve(root, 'benchmark/published', pointer.previousRunId, 'manifest.json'), 'utf8'));
    if (previous.runId !== pointer.previousRunId || priorManifest.runId !== previous.runId
        || previous.sourceRevision !== report.sourceRevision || previous.modelIds.join() !== report.modelIds.join()
        || JSON.stringify(priorManifest.corpus) !== JSON.stringify(manifest.corpus)
        || JSON.stringify(priorManifest.prompts) !== JSON.stringify(manifest.prompts)
        || priorManifest.index.embeddingIdentity !== manifest.index.embeddingIdentity
        || previous.repetitions !== report.repetitions || previous.promptCount !== report.promptCount) throw new Error('Incompatible benchmark comparison');
  }
  return { ...report, previous, measurement: {hardware: manifest.hardware, index: manifest.index,
    runtimeRevision: manifest.runtimeRevision ?? manifest.sourceRevision, corpusRevision: manifest.sourceRevision} };
}

const here = fileURLToPath(import.meta.url);
if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  const root = resolve(dirname(here), '../..');
  const report = loadBenchmarks(root);
  const output = resolve(root, 'site/src/data/benchmarks.json');
  mkdirSync(dirname(output), { recursive: true });
  writeFileSync(output, JSON.stringify(report, null, 2) + '\n');
  console.log(`[build-benchmarks] ${report.runCount} trials / ${report.sourceRevision.slice(0, 12)} / ${report.overall.tokenPct} tokens`);
}
