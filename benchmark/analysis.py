"""Statistics shared by CLI reports and the website, using only the stdlib."""
from collections import defaultdict
import math
import random
import statistics

TOKEN_FIELDS = ('input_tokens', 'output_tokens', 'cache_create', 'cache_read')


def metrics(result):
    """Reject missing/error usage instead of interpreting it as a free answer."""
    if result.get('is_error') or result.get('subtype') != 'success':
        raise ValueError('Claude did not complete successfully')
    models = result.get('modelUsage')
    if models:
        if not isinstance(models, dict) or any(not isinstance(m, dict) or not all(k in m for k in ('inputTokens','outputTokens')) for m in models.values()):
            raise ValueError('Incomplete per-model token usage')
        names = ('inputTokens', 'outputTokens', 'cacheCreationInputTokens', 'cacheReadInputTokens')
        if any(type(m.get(key,0)) not in (int,float) or not math.isfinite(m.get(key,0)) or m.get(key,0)<0 for m in models.values() for key in names):
            raise ValueError('Invalid per-model token usage')
        values = [sum(m.get(key, 0) for m in models.values()) for key in names]
        if 'total_cost_usd' in result:
            cost = result['total_cost_usd']
        elif all(type(m.get('costUSD')) in (int,float) for m in models.values()):
            cost = sum(m['costUSD'] for m in models.values())
        else:
            raise ValueError('Missing reported cost')
    elif isinstance(result.get('usage'), dict):
        usage = result['usage']
        if not all(k in usage for k in ('input_tokens', 'output_tokens')):
            raise ValueError('Missing token usage')
        values = [usage.get(k, 0) for k in ('input_tokens', 'output_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens')]
        cost = result.get('total_cost_usd')
    else:
        raise ValueError('Missing token usage')
    if any(type(v) not in (int,float) or not math.isfinite(v) or v < 0 for v in [*values, cost]) or not sum(values):
        raise ValueError('Invalid token usage or cost')
    return {**dict(zip(TOKEN_FIELDS, values)), 'total_tokens': sum(values),
            'cost': cost, 'turns': result.get('num_turns', 0),
            'duration_ms': result.get('duration_ms', 0)}


def percentile(values, q):
    if not values:
        return None
    values = sorted(values)
    pos = (len(values) - 1) * q
    lo = int(pos)
    return values[lo] + (values[min(lo + 1, len(values) - 1)] - values[lo]) * (pos - lo)


def change(off, on):
    return (on / off - 1) * 100 if off else None


def interval(pairs, key, seed=1729, samples=2000):
    """Paired hierarchical bootstrap: prompts, then repetitions within prompt.

    Intervals describe this fixed task suite, not arbitrary users or workloads.
    """
    by_prompt = defaultdict(list)
    for pair in pairs:
        by_prompt[pair['prompt_id']].append(pair)
    ids = sorted(by_prompt)
    if len(pairs) < 2:
        return None
    rng = random.Random(seed)
    estimates = []
    for _ in range(samples):
        selected = [rng.choice(ids) for _ in ids]
        off = on = 0
        for pid in selected:
            rows = by_prompt[pid]
            drawn = [rng.choice(rows) for _ in rows]
            off += sum(r['off']['metrics'][key] for r in drawn)
            on += sum(r['on']['metrics'][key] for r in drawn)
        if off:
            estimates.append(change(off, on))
    return [percentile(estimates, .025), percentile(estimates, .975)] if estimates else None


def paired_runs(trials):
    grouped = defaultdict(dict)
    for trial in trials:
        key = (trial['prompt_id'], trial['repeat'])
        if trial['mode'] in grouped[key]:
            raise ValueError(f'Duplicate trial: {key}, {trial["mode"]}')
        grouped[key][trial['mode']] = trial
    pairs = []
    for (pid, repeat), modes in sorted(grouped.items()):
        if all(modes.get(mode, {}).get('status') == 'success' for mode in ('off', 'on')):
            pairs.append({'prompt_id': pid, 'repeat': repeat, **modes})
    return pairs


def aggregate(pairs):
    off = {k: sum(p['off']['metrics'][k] for p in pairs) for k in ('total_tokens', 'cost')}
    on = {k: sum(p['on']['metrics'][k] for p in pairs) for k in ('total_tokens', 'cost')}
    return {'pairs': len(pairs), 'tokensOff': off['total_tokens'], 'tokensOn': on['total_tokens'],
            'costOff': off['cost'], 'costOn': on['cost'],
            'tokenChangePct': change(off['total_tokens'], on['total_tokens']),
            'costChangePct': change(off['cost'], on['cost']),
            'tokenChangeCI95': interval(pairs, 'total_tokens'),
            'costChangeCI95': interval(pairs, 'cost')}


def retrieval_metrics(sources, relevance, k):
    """Distinct relevant sources in the first k chunks; duplicates gain nothing."""
    relevance = {source: grade for source,grade in relevance.items() if grade > 0}
    seen = set()
    gains = []
    for source in sources[:k]:
        gains.append(relevance.get(source, 0) if source not in seen else 0)
        seen.add(source)
    dcg = sum((2 ** gain - 1) / math.log2(i + 2) for i, gain in enumerate(gains))
    ideal = sorted(relevance.values(), reverse=True)[:k]
    idcg = sum((2 ** gain - 1) / math.log2(i + 2) for i, gain in enumerate(ideal))
    rank = next((i + 1 for i, s in enumerate(sources[:k]) if relevance.get(s, 0)), None)
    return {'recall': len(set(sources[:k]) & relevance.keys()) / len(relevance) if relevance else None,
            'reciprocal_rank': 1 / rank if rank else 0,
            'ndcg': dcg / idcg if idcg else None,
            'hit': bool(rank)}


def retrieval_comparison(rows, seed=1729, samples=2000):
    """Paired query-family bootstrap; timing repeats do not inflate sample size."""
    queries = defaultdict(dict)
    for row in rows:
        if row['repeat'] == 0 and not row['negative']:
            queries[row['id']][row['mode']] = row
    families = defaultdict(list)
    for pair in queries.values():
        if 'semantic' in pair and 'hybrid' in pair:
            families[pair['hybrid']['family']].append(pair)
    output = {}
    for name, k, metric in [('recallAt5', '5', 'recall'), ('mrrAt10', '10', 'reciprocal_rank'), ('ndcgAt10', '10', 'ndcg')]:
        difference = lambda pair: pair['hybrid']['scores'][k][metric] - pair['semantic']['scores'][k][metric]
        values = [difference(pair) for rows in families.values() for pair in rows]
        if not values:
            continue
        rng = random.Random(seed)
        estimates = []
        ids = sorted(families)
        for _ in range(samples):
            drawn = [difference(pair) for family in [rng.choice(ids) for _ in ids] for pair in families[family]]
            estimates.append(statistics.mean(drawn))
        output[name] = {'hybridMinusSemantic': statistics.mean(values),
                        'ci95': [percentile(estimates,.025), percentile(estimates,.975)]}
    return output


def retrieval_summary(rows):
    """Recalculate retrieval aggregates from published per-search evidence."""
    groups = {}
    for mode in ('semantic', 'hybrid'):
        subset = [r for r in rows if r['mode'] == mode]
        first = [r for r in subset if r['repeat'] == 0 and not r['negative']]
        groups[mode] = {'queries': len(first), 'timedSearches': len(subset),
                        'p50Ms': percentile([r['durationMs'] for r in subset], .5),
                        'p95Ms': percentile([r['durationMs'] for r in subset], .95),
                        'boundaryPasses': sum(r['boundaryPass'] for r in subset if r['boundaryCase']),
                        'boundaryChecks': sum(r['boundaryCase'] for r in subset),
                        'workspaceIsolationPasses': sum(r['boundaryPass'] for r in subset),
                        'at': {str(k): {metric: statistics.mean(r['scores'][str(k)][metric] for r in first)
                                       for metric in ('recall', 'reciprocal_rank', 'ndcg', 'hit')}
                               for k in (1,5,10)}}
    rankings = defaultdict(set)
    for row in rows:
        rankings[row['id'],row['mode']].add(tuple(row['sources']))
    return {'groups': groups, 'comparison': retrieval_comparison(rows),
            'rankingRepeatDisagreements': sum(len(values)>1 for values in rankings.values())}
