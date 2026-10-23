#!/usr/bin/env python3
"""Describe two compatible published studies; do not infer a causal effect."""
import argparse
import json
from pathlib import Path


def load(directory):
    return {name: json.loads((directory / f'{name}.json').read_text())
            for name in ('manifest', 'summary', 'trials', 'usage-diagnostics')}


def compare(before, after):
    a, b = before['manifest'], after['manifest']
    for key in ('sourceRevision', 'corpus', 'prompts', 'repetitions'):
        if a[key] != b[key]:
            raise ValueError(f'Incompatible comparison: {key}')
    if a['index']['embeddingIdentity'] != b['index']['embeddingIdentity']:
        raise ValueError('Incompatible embedding identity')
    if before['summary']['modelIds'] != after['summary']['modelIds']:
        raise ValueError('Incompatible resolved models')
    if any(r['summary']['status'] != 'complete' for r in (before, after)):
        raise ValueError('Complete published runs required')
    def changes(key):
        left, right = before['summary']['overall'][key], after['summary']['overall'][key]
        return {'before': left, 'after': right, 'differencePercentagePoints': right - left}

    tools = {}
    for name in sorted(before['usage-diagnostics']['toolUsage']['on'].keys() | after['usage-diagnostics']['toolUsage']['on'].keys()):
        old = before['usage-diagnostics']['toolUsage']['on'].get(name, {'calls': 0, 'resultChars': 0})
        new = after['usage-diagnostics']['toolUsage']['on'].get(name, {'calls': 0, 'resultChars': 0})
        tools[name] = {'before': old, 'after': new,
                       'resultCharacterChangePct': (new['resultChars'] / old['resultChars'] - 1) * 100 if old['resultChars'] else None}
    return {'schemaVersion': 1, 'beforeRunId': a['runId'], 'afterRunId': b['runId'],
            'tokenChangeOnVsOff': changes('tokenChangePct'), 'costChangeOnVsOff': changes('costChangePct'),
            'agentTurns': {label: {mode: sum(t['metrics']['turns'] for t in run['trials'] if t['mode'] == mode)
                                  for mode in ('off', 'on')} for label, run in (('before', before), ('after', after))},
            'onToolUsage': tools,
            'limitations': ['Sequential studies with provider-managed caches and shared hardware; not a randomized causal version comparison.',
                            'Intervals in each summary describe its matched ON/OFF trials, not a guarantee about the version difference.',
                            'Serialized tool-result characters are a context-volume diagnostic, not exact token attribution or factual answer quality.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    report = compare(load(args.before), load(args.after))
    args.out.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    print('Wrote descriptive comparison with provenance and tool usage.')


if __name__ == '__main__':
    main()
