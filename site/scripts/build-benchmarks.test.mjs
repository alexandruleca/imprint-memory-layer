import { test } from 'node:test';
import assert from 'node:assert/strict';
import { validateReport } from './build-benchmarks.mjs';

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
