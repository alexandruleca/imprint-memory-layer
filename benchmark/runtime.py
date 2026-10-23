"""Isolated real-model indexing and retrieval. Invoked by the stdlib runner."""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import random
import resource
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmark.analysis import percentile, retrieval_metrics, retrieval_comparison
from benchmark.corpus import passages, digest


def prepare(state):
    from imprint import config, embeddings, vectorstore as vs, workspace_policy as policy
    config.register_workspace('benchmark')
    config.register_workspace('unrelated')
    policy.add_root(state['snapshot'], 'benchmark')
    policy.map_project('imprint', state['snapshot'], 'benchmark')
    policy.map_project('reference', str(Path(state['snapshot']) / 'reference'), 'benchmark')
    records = []
    for file in state['corpus']['files']:
        source = Path(state['snapshot']) / file['path']
        for i, passage in enumerate(passages(source.read_text())):
            records.append({'content': f"Source: {file['path']}\n{passage}",
                            'project': file['project'], 'source': file['path'], 'type': 'finding',
                            'source_path': str(source), 'source_relative_path': file['path'].removeprefix('reference/') if file['project']=='reference' else file['path'],
                            'source_type': 'file', 'source_hash': file['sha256'], 'chunk_index': i})
    started = time.perf_counter()
    for offset in range(0, len(records), 16):
        inserted, skipped = vs.store_batch(records[offset:offset+16], workspace='benchmark')
        print(f'Indexed {offset+inserted+skipped}/{len(records)} passages', file=sys.stderr, flush=True)
    # Real workspace sentinel: it must never appear in the benchmark workspace.
    vs.store('The unrelated workspace secret sentinel is ORCHID-COPPER-731.', project='unrelated',
             source='unrelated/sentinel', workspace='unrelated')
    from imprint import imprint_graph as kg
    kg.add('reference', 'uses', 'in-memory FIFO Queue and daemon Thread',
           source='reference/worker.py', workspace='benchmark')
    kg.add('imprint', 'uses', 'durable SQLite FIFO queue and process-shared advisory lock',
           source='imprint/queue.py', workspace='benchmark')
    return {'passages': len(records), 'indexSeconds': time.perf_counter()-started,
            'embeddingIdentity': config.embedding_identity(), 'model': config.MODEL_NAME,
            'modelRevision': config.MODEL_REVISION, 'modelFile': config.MODEL_FILE,
            'dimensions': config.EMBEDDING_DIM, 'sequenceLength': config.MAX_SEQ_LENGTH,
            'threads': int(os.environ['IMPRINT_ONNX_THREADS']),
            'passageWindowChars': 1800, 'passageOverlapChars': 180,
            'tagging': 'disabled; retrieval benchmark uses declared source/project metadata',
            'dependencies': {name: importlib.metadata.version(name) for name in ('onnxruntime', 'qdrant-client', 'mcp')},
            'peakRssMiB': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 if sys.platform != 'darwin' else 1024**2)}


def evaluate(state, dataset, repeats, seed):
    from imprint import embeddings, vectorstore as vs
    started = time.perf_counter()
    embeddings.embed_query('benchmark warmup')
    cold = (time.perf_counter() - started) * 1000
    rng = random.Random(seed)
    tasks = [(q, mode, repeat) for q in dataset for mode in ('semantic', 'hybrid') for repeat in range(repeats)]
    rng.shuffle(tasks)
    rows = []
    for q, mode, repeat in tasks:
        start = time.perf_counter()
        hits = vs.search(q['query'], limit=10, mode=mode, workspace=q.get('workspace', 'benchmark'),
                         project=q.get('project', ''), source=q.get('source', ''))
        duration = (time.perf_counter() - start) * 1000
        sources = [h['source'] for h in hits]
        scores = {str(k): retrieval_metrics(sources, q.get('relevance', {}), k) for k in (1, 5, 10)}
        boundary = (not hits if q.get('expect_empty') else all(h['project'] != 'unrelated' for h in hits))
        boundary = boundary and all(not q.get('project') or h['project']==q['project'] for h in hits)
        boundary = boundary and all(not q.get('source') or h['source']==q['source'] for h in hits)
        rows.append({'id': q['id'], 'family': q['family'], 'mode': mode, 'repeat': repeat,
                     'durationMs': duration, 'sources': sources, 'scores': scores,
                     'projects': [h['project'] for h in hits],
                     'negative': not q.get('relevance'),
                     'boundaryCase': not q.get('relevance') or bool(q.get('project') or q.get('source')),
                     'boundaryPass': boundary})
    groups = {}
    for mode in ('semantic', 'hybrid'):
        subset = [r for r in rows if r['mode'] == mode]
        # Each query gets one vote, regardless of timing repetitions.
        first = [r for r in subset if r['repeat'] == 0 and not r['negative']]
        groups[mode] = {'queries': len(first), 'timedSearches': len(subset),
                        'p50Ms': percentile([r['durationMs'] for r in subset], .5),
                        'p95Ms': percentile([r['durationMs'] for r in subset], .95),
                        'boundaryPasses': sum(r['boundaryPass'] for r in subset if r['boundaryCase']),
                        'boundaryChecks': sum(r['boundaryCase'] for r in subset),
                        'workspaceIsolationPasses': sum(r['boundaryPass'] for r in subset),
                        'at': {str(k): {metric: statistics.mean(r['scores'][str(k)][metric] for r in first)
                                       for metric in ('recall', 'reciprocal_rank', 'ndcg', 'hit')}
                               for k in (1, 5, 10)}}
    return {'schemaVersion': 2, 'seed': seed, 'repetitions': repeats,
            'coldModelLoadAndFirstQueryMs': cold, 'groups': groups, 'queries': rows,
            'comparison': retrieval_comparison(rows),
            'rankingRepeatDisagreements': sum(len({tuple(r['sources']) for r in rows if r['id']==q['id'] and r['mode']==mode})>1 for q in dataset for mode in ('semantic','hybrid')),
            'metricUnit': 'Distinct gold sources in the first k returned chunks; duplicate source gains are zero.',
            'limitations': ['Fixed public task suite, not unseen customer workloads.',
                            'Source relevance does not establish generated-answer correctness.',
                            'Warm timings exclude model download and run serially on this host.'],
            'peakRssMiB': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 if sys.platform != 'darwin' else 1024**2)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['prepare', 'retrieval', 'stop'])
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--queries', type=Path, default=Path(__file__).with_name('retrieval.json'))
    parser.add_argument('--runs', type=int, default=5)
    parser.add_argument('--seed', type=int, default=1729)
    args = parser.parse_args()
    state = json.loads(args.state.read_text())
    os.environ.update(state['env'])
    if args.operation == 'stop':
        from imprint import qdrant_runner
        qdrant_runner.stop()
        return
    result = prepare(state) if args.operation == 'prepare' else evaluate(state, json.loads(args.queries.read_text()), args.runs, args.seed)
    if args.operation == 'retrieval':
        result['datasetSha256'] = digest(args.queries.read_bytes())
    args.out.write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
