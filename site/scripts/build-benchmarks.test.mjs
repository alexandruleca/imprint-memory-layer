import { test } from 'node:test';
import assert from 'node:assert/strict';
import { validateReport, loadBenchmarks } from './build-benchmarks.mjs';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';

function fixture() {
  return { schemaVersion: 2, status: 'complete', generatedAt: '2026-10-23T12:00:00Z',
    sourceRevision: 'a'.repeat(40), runCount: 4, expectedRuns: 4, successfulRuns: 4,
    failedRuns: 0, validPairs: 2, modelIds: ['fixture-model'], promptCount: 1,
    overall: {tokensOff: 100, tokensOn: 120, costOff: .1, costOn: .08, qualityPct: null},
    categories: [{rows: [{pairs: 2}]}] };
}

test('accepts measured increases as well as savings and retains measurement time', () => {
  const report = fixture();
  assert.equal(validateReport(report), report);
  assert.equal(report.generatedAt, '2026-10-23T12:00:00Z');
});

test('loads a version comparison only with matching corpus, tasks, model and embedding identity', () => {
  const root = mkdtempSync(join(tmpdir(), 'imprint-benchmark-adapter-'));
  const put = (file, data) => {
    const target = join(root, file);
    mkdirSync(dirname(target), { recursive: true });
    writeFileSync(target, JSON.stringify(data));
  };
  const manifest = { sourceRevision: 'a'.repeat(40), runtimeRevision: 'b'.repeat(40),
    corpus: {files: [{path: 'source.py', sha256: 'fixture-hash'}]}, prompts: [{id: 'task'}],
    index: {embeddingIdentity: 'fixture-model'}, hardware: {os: 'fixture'} };
  try {
    put('benchmark/latest.json', {schemaVersion: 2, runId: 'new', previousRunId: 'old'});
    for (const runId of ['old', 'new']) {
      put(`benchmark/published/${runId}/summary.json`, {...fixture(), runId, repetitions: 2});
      put(`benchmark/published/${runId}/manifest.json`, {...manifest, runId});
    }
    const result = loadBenchmarks(root);
    assert.equal(result.previous.runId, 'old');
    assert.equal(result.measurement.runtimeRevision, 'b'.repeat(40));
    assert.equal(result.measurement.corpusRevision, 'a'.repeat(40));
    put('benchmark/published/old/manifest.json', {...manifest, runId: 'old', prompts: [{id: 'different'}]});
    assert.throws(() => loadBenchmarks(root), /Incompatible benchmark comparison/);
  } finally { rmSync(root, {recursive: true, force: true}); }
});

test('refuses missing usage, misleading run counts, unpaired data and quality claims', () => {
  for (const mutate of [
    r => { r.runCount = 150; }, r => { r.validPairs = 3; },
    r => { r.overall.tokensOff = null; }, r => { r.overall.qualityPct = '+40%'; },
    r => { r.modelIds.push('another-model'); }, r => { r.status = 'incomplete'; },
    r => { r.categories[0].rows[0].pairs = 1; },
  ]) {
    const report = fixture(); mutate(report);
    assert.throws(() => validateReport(report), /Invalid benchmark/);
  }
});
