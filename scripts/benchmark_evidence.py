#!/usr/bin/env python3
"""Audit raw CLI usage against trial rows and retain tool/context diagnostics.

This postprocessor does not change trials, their instructions or ranking labels.
Only allowlisted usage fields and aggregate tool sizes leave the raw directory.
"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmark.analysis import metrics, retrieval_metrics


def audit(raw):
    ledger = []
    tools = {mode: defaultdict(lambda: {'calls': 0, 'resultChars': 0, 'protocolErrors': 0}) for mode in ('off','on')}
    for path in sorted(raw.glob('*_*.json')):
        trial = json.loads(path.read_text())
        if 'prompt_id' not in trial:
            continue
        stream = path.with_suffix('.jsonl')
        events = []
        for line in stream.read_text().splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        result = next((e for e in reversed(events) if e.get('type') == 'result'), {})
        if trial['status'] == 'success' and metrics(result) != trial['metrics']:
            raise ValueError(f'Usage changed after capture: {path.name}')
        calls = {}
        for event in events:
            blocks = event.get('message', {}).get('content', [])
            if not isinstance(blocks, list):
                continue
            for block in blocks:
                if block.get('type') == 'tool_use':
                    calls[block['id']] = block['name']
                    tools[trial['mode']][block['name']]['calls'] += 1
                elif block.get('type') == 'tool_result':
                    name = calls.get(block.get('tool_use_id'), 'unknown')
                    content = block.get('content', '')
                    size = len(content if isinstance(content,str) else json.dumps(content,ensure_ascii=False))
                    tools[trial['mode']][name]['resultChars'] += size
                    tools[trial['mode']][name]['protocolErrors'] += bool(block.get('is_error'))
        allowed = ('inputTokens','outputTokens','cacheCreationInputTokens','cacheReadInputTokens','costUSD','canonicalModel','provider','costBasis')
        ledger.append({'trial': path.stem, 'traceSha256': hashlib.sha256(stream.read_bytes()).hexdigest(),
                       'status': trial['status'], 'totalCostUsd': result.get('total_cost_usd'),
                       'models': {name: {key: value[key] for key in allowed if key in value}
                                  for name,value in result.get('modelUsage',{}).items()}})
    manifest = json.loads((raw/'manifest.json').read_text())
    state = json.loads((raw/'state.json').read_text())
    for file in manifest['corpus']['files']:
        data = (Path(state['snapshot'])/file['path']).read_bytes()
        if hashlib.sha256(data).hexdigest() != file['sha256']:
            raise ValueError(f'Frozen evidence changed: {file["path"]}')
    retrieval_count = 0
    if (raw/'retrieval-report.json').exists():
        dataset = Path(__file__).resolve().parents[1]/'benchmark/retrieval.json'
        relevance = {q['id']:q.get('relevance',{}) for q in json.loads(dataset.read_text())}
        report = json.loads((raw/'retrieval-report.json').read_text())
        if hashlib.sha256(dataset.read_bytes()).hexdigest() != report['datasetSha256']:
            raise ValueError('Retrieval labels changed after measurement')
        for row in report['queries']:
            for k in (1,5,10):
                if retrieval_metrics(row['sources'],relevance[row['id']],k) != row['scores'][str(k)]:
                    raise ValueError(f'Retrieval score mismatch: {row["id"]}, {row["mode"]}, {k}')
            retrieval_count += 1
    return {'schemaVersion': 2, 'trialsAudited': len(ledger), 'frozenFilesVerified': len(manifest['corpus']['files']),
            'retrievalSearchesAudited': retrieval_count,
            'analysisSha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'toolUsage': {mode: dict(counts) for mode,counts in tools.items()},
            'usageLedger': ledger,
            'limitations': 'Tool result character counts describe returned payloads; they are not token attribution or answer-quality scores.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('raw',type=Path)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    report=audit(args.raw)
    args.out.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    print(f"Audited {report['trialsAudited']} traces and {report['frozenFilesVerified']} frozen files")


if __name__=='__main__':
    main()
