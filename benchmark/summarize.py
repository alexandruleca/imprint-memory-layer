#!/usr/bin/env python3
"""Calculate auditable paired statistics, Markdown and website-compatible JSON."""
import argparse
import hashlib
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmark.analysis import aggregate, paired_runs, percentile, retrieval_summary
from benchmark.run import write_json

CATEGORIES = [('information', 'Information'), ('decision', 'Decision Recall'),
              ('debug', 'Debugging'), ('cross-project', 'Cross-Project'),
              ('summary', 'Session Summary'), ('creation', 'Creation')]


def pct(value):
    return f'{value:+.1f}%' if value is not None else '—'


def summarize(raw):
    manifest = json.loads((raw/'manifest.json').read_text())
    trials = json.loads((raw/'trials.json').read_text()) if (raw/'trials.json').exists() else []
    if not trials:
        for path in sorted(raw.glob('*_*.json')):
            data = json.loads(path.read_text())
            if 'prompt_id' in data and 'mode' in data:
                trials.append(data)
    pairs = paired_runs(trials)
    stats = aggregate(pairs)
    categories = []
    for key, label in CATEGORIES:
        prompts = [p for p in manifest['prompts'] if p['category'] == key]
        if not prompts:
            continue
        rows = []
        selected = [pair for pair in pairs if pair['off']['category'] == key]
        for prompt in prompts:
            subset = [pair for pair in pairs if pair['prompt_id'] == prompt['id']]
            if not subset:
                continue
            med = {mode: {k: statistics.median(p[mode]['metrics'][k] for p in subset)
                          for k in ('total_tokens', 'cost')} for mode in ('off', 'on')}
            off, on = med['off'], med['on']
            rows.append({'id': prompt['id'], 'prompt': prompt['name'], 'pairs': len(subset),
                         'offTokens': off['total_tokens'], 'onTokens': on['total_tokens'],
                         'savings': off['total_tokens']-on['total_tokens'],
                         'pct': pct((on['total_tokens']/off['total_tokens']-1)*100) if off['total_tokens'] else '—',
                         'offCost': f'${off["cost"]:.4f}', 'onCost': f'${on["cost"]:.4f}',
                         'costDelta': f'${on["cost"]-off["cost"]:+.4f}'})
        cat = aggregate(selected)
        categories.append({'key': 'cross' if key == 'cross-project' else key,
                           'heading': label+' Prompts', 'label': label, 'rows': rows,
                           'avgTokens': pct(cat['tokenChangePct']), 'avgCost': pct(cat['costChangePct']),
                           'quality': None, 'note': None, 'statistics': cat})
    success = [t for t in trials if t['status'] == 'success']
    latencies = {mode: [p[mode]['wallMs'] for p in pairs] for mode in ('off','on')}
    coverage = {mode: [p[mode]['referenceCoverage'] for p in pairs if p[mode]['referenceCoverage'] is not None] for mode in ('off','on')}
    model_ids = sorted({t['primaryModel'] for t in success if t.get('primaryModel')})
    expected = len(manifest['prompts'])*manifest['repetitions']*2
    errors = [t for t in trials if t['status'] != 'success']
    measured_at = max((t['recordedAt'] for t in trials), default=manifest['createdAt'])
    report = {'schemaVersion': 2, 'generatedAt': measured_at, 'measuredAt': measured_at,
              'calculatedAt': datetime.now(timezone.utc).isoformat(),
              'analysisSha256': hashlib.sha256(Path(__file__).read_bytes()+Path(__file__).with_name('analysis.py').read_bytes()).hexdigest(),
              'runId': manifest['runId'], 'sourceRevision': manifest['sourceRevision'],
              'status': 'complete' if len(trials) == expected else 'incomplete',
              'runCount': len(trials), 'successfulRuns': len(success), 'failedRuns': len(errors),
              'expectedRuns': expected, 'validPairs': len(pairs), 'repetitions': manifest['repetitions'],
              'promptCount': len(manifest['prompts']), 'modelIds': model_ids,
              'overall': {**stats, 'tokenPct': pct(stats['tokenChangePct']), 'costPct': pct(stats['costChangePct']),
                          'qualityPct': None,
                          'referenceCoverageOff': statistics.mean(coverage['off']) if coverage['off'] else None,
                          'referenceCoverageOn': statistics.mean(coverage['on']) if coverage['on'] else None,
                          'latency': {mode: {'p50Ms': percentile(values,.5), 'p95Ms': percentile(values,.95)} for mode,values in latencies.items()},
                          'cacheTokens': {mode: {k: sum(p[mode]['metrics'][k] for p in pairs) for k in ('input_tokens','output_tokens','cache_read','cache_create')} for mode in ('off','on')},
                          'mcpUseRate': sum(p['on']['mcpCalls'] > 0 for p in pairs)/len(pairs) if pairs else None,
                          'toolErrors': {mode: sum(p[mode]['toolErrors'] for p in pairs) for mode in ('off','on')}},
              'categories': categories, 'env': {'os': manifest['hardware']['os'], 'python': manifest['hardware']['python'],
                                               'model': ', '.join(model_ids), 'imprint': manifest.get('runtimeRevision', manifest['sourceRevision'])[:12],
                                               'memories': manifest['index']['passages']},
              'manifest': manifest,
              'methodology': {'overview': 'Actual sums across complete successful pairs; cache input included. Per-task rows are medians, not summed component medians.',
                              'uncertainty': '95% paired hierarchical bootstrap, resampling task IDs then repetitions; 2,000 samples, seed 1729. Describes this fixed suite.',
                              'quality': 'No answer-quality uplift is claimed. Reference-check coverage is a lexical diagnostic, not verified correctness.',
                              'cache': manifest['cachePolicy'], 'cost': manifest['costBasis'],
                              'comparison': 'Protocol v2 and the fixed public corpus differ from the legacy v0.5.1 experiment; results are not a model/version improvement claim.'},
              'failures': [{'prompt': t['prompt_id'], 'mode': t['mode'], 'repeat': t['repeat'], 'error': t.get('error')} for t in errors],
              'trials': trials}
    if (raw/'retrieval-report.json').exists():
        report['retrieval'] = json.loads((raw/'retrieval-report.json').read_text())
    elif (raw/'retrieval-trials.json').exists():
        report['retrieval'] = json.loads((raw/'summary.json').read_text())['retrieval']
        report['retrieval']['queries'] = json.loads((raw/'retrieval-trials.json').read_text())
    if 'retrieval' in report:
        report['retrieval'].update(retrieval_summary(report['retrieval']['queries']))
    return report


def markdown(report):
    overall = report['overall']
    ci = lambda values: f'{pct(values[0])} to {pct(values[1])}' if values else 'not estimated'
    lines = ['# Imprint benchmark results', '',
             f"Measured {report['generatedAt'][:10]} using {', '.join(report['modelIds'])}. Runtime `{report['env']['imprint']}`; frozen corpus `{report['sourceRevision'][:12]}`.", '',
             f"{report['runCount']}/{report['expectedRuns']} trials recorded; {report['validPairs']} complete successful pairs; {report['failedRuns']} failed trials.", '',
             f"Total processed tokens: {overall['tokenPct']} ({overall['tokensOff']:,} OFF → {overall['tokensOn']:,} ON).",
             f"Reported list-price cost: {overall['costPct']} (${overall['costOff']:.4f} OFF → ${overall['costOn']:.4f} ON).", '',
             f"Token-change 95% interval: {ci(overall['tokenChangeCI95'])}. Cost-change 95% interval: {ci(overall['costChangeCI95'])}.", '',
             'Totals sum all complete paired trials. Tables below show per-task medians. These are different estimands.', '',
             'No answer-quality uplift is claimed. The old biased LLM judge and response-length proxy are retired.', '']
    if overall['costChangeCI95'] and overall['costChangeCI95'][0] <= 0 <= overall['costChangeCI95'][1]:
        lines += ['The cost interval includes no change: the point estimate does not establish a clear saving.', '']
    for category in report['categories']:
        lines += ['## '+category['label'], '', '| Task | Pairs | OFF tokens | ON tokens | Change | OFF cost | ON cost |',
                  '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
        for row in category['rows']:
            lines.append(f"| {row['prompt']} | {row['pairs']} | {row['offTokens']:,} | {row['onTokens']:,} | {row['pct']} | {row['offCost']} | {row['onCost']} |")
        lines += ['']
    if 'retrieval' in report:
        lines += ['## Retrieval on the labeled public corpus', '', '| Mode | Recall@5 | MRR@10 | nDCG@10 | Warm p50 | Warm p95 |',
                  '| --- | ---: | ---: | ---: | ---: | ---: |']
        for mode, group in report['retrieval']['groups'].items():
            lines.append(f"| {mode} | {group['at']['5']['recall']:.3f} | {group['at']['10']['reciprocal_rank']:.3f} | {group['at']['10']['ndcg']:.3f} | {group['p50Ms']:.1f} ms | {group['p95Ms']:.1f} ms |")
        lines += ['', report['retrieval']['metricUnit'], '']
    lines += ['## Method and limits', ''] + ['- '+value for value in report['methodology'].values()]
    lines += ['', 'See the methodology in `docs/benchmarks.md` and the manifest, sanitized trials and summary alongside this report.', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', type=Path)
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    if not (args.results/'manifest.json').exists():
        parser.error('Protocol v2 manifest.json required. Legacy raw data cannot establish a frozen corpus or fair pairing.')
    report = summarize(args.results)
    write_json(args.results/'summary.json', report)
    (args.results/'summary.md').write_text(markdown(report))
    if args.publish:
        if report['status'] != 'complete' or not report['validPairs'] or len(report['modelIds']) != 1:
            parser.error('Publish requires a complete run with successful pairs and one resolved primary model')
        destination = Path(__file__).parent/'published'/report['runId']
        destination.mkdir(parents=True, exist_ok=True)
        write_json(destination/'manifest.json', report['manifest'])
        write_json(destination/'trials.json', report['trials'])
        compact = {k:v for k,v in report.items() if k not in ('trials','manifest')}
        if 'retrieval' in compact:
            compact['retrieval'] = {k:v for k,v in report['retrieval'].items() if k != 'queries'}
            write_json(destination/'retrieval-trials.json', report['retrieval']['queries'])
        write_json(destination/'summary.json', compact)
        (destination/'README.md').write_text(markdown(report))
        write_json(Path(__file__).parent/'latest.json', {'schemaVersion': 2, 'runId': report['runId']})
        (Path(__file__).resolve().parents[1]/'BENCHMARK.md').write_text(markdown(report))
    print(f"{report['runCount']}/{report['expectedRuns']} trials; {report['validPairs']} pairs; tokens {report['overall']['tokenPct']}; cost {report['overall']['costPct']}")


if __name__ == '__main__':
    main()
