"""Isolated real-model indexing and retrieval. Invoked by the stdlib runner."""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import random
import re
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


# sequential first: recall_plan_matched gets the characters it returned.
RECALL_MODES = ('sequential', 'recall_plan', 'recall_plan_matched', 'recall_intent_off')


def recall_lookups(tasks, repeats, model=None, timeouts=()):
    """Caller-visible size and server time of multi-part lookups: the plan's
    steps as separate tool calls, against one recall (ADR 0058).

    Tokens use the index's Qwen3 tokenizer as a proxy; the caller's own
    tokenizer differs. Times are server-side only: every extra round trip
    also costs the caller a model turn, which is not measured here."""
    from tokenizers import Tokenizer
    from imprint import embeddings, server
    tokenizer = Tokenizer.from_file(str(sorted(Path(os.environ['IMPRINT_DATA_DIR']).glob('models/embeddings/*/tokenizer.json'))[0]))

    def tokens(text):
        return len(tokenizer.encode(text, add_special_tokens=False).ids)

    started = time.perf_counter()
    embeddings.embed_query('benchmark warmup')
    cold = (time.perf_counter() - started) * 1000

    def resolve(value):
        # "@top:<query>:<rank>" stands for an id the caller already holds
        # from an earlier search.
        if isinstance(value, str) and value.startswith('@top:'):
            query, rank = value[5:].rsplit(':', 1)
            return server.search_rows(query, workspace='benchmark')[int(rank)]['id']
        return value

    for task in tasks:
        task['plan'] = [{'tool': step['tool'], 'args': {k: resolve(v) for k, v in step['args'].items()}}
                        for step in task['plan']]

    def calls(task, mode):
        question, plan = task['question'], task['plan']
        if mode == 'sequential':
            return [(step['tool'], step['args'], lambda step=step: getattr(server, step['tool'])(**step['args']))
                    for step in plan]
        if mode in ('recall_plan', 'recall_plan_on'):
            return [('recall', {'question': question, 'plan': plan}, lambda: server.recall(question, plan=plan))]
        if mode == 'recall_plan_matched':
            budget = max(1200, min(12000, task['sequentialChars']))
            return [('recall', {'question': question, 'plan': plan, 'max_chars': budget},
                     lambda: server.recall(question, plan=plan, max_chars=budget))]
        return [('recall', {'question': question}, lambda: server.recall(question))]

    def measure(task, mode):
        outputs, durations = [], []
        for name, arguments, call in calls(task, mode):
            start = time.perf_counter()
            outputs.append(call())
            durations.append((time.perf_counter() - start) * 1000)
        text = '\n'.join(outputs)
        argument_tokens = sum(tokens(json.dumps({'name': name, 'arguments': arguments}))
                              for name, arguments, _ in calls(task, mode))
        return {'roundTrips': len(outputs), 'argumentTokens': argument_tokens, 'resultTokens': sum(map(tokens, outputs)),
                'resultChars': sum(map(len, outputs)), 'serverMs': sum(durations),
                'goldVisible': sorted(g for g in task['gold'] if g in text)}, text

    rows = []
    for task in tasks:
        for mode in RECALL_MODES:
            runs = [measure(task, mode) for _ in range(repeats)]
            row = dict(runs[0][0])
            row['serverMs'] = statistics.median(run[0]['serverMs'] for run in runs)
            rows.append({'task': task['id'], 'mode': mode, 'repeats': repeats, **row})
            task.setdefault('sequentialChars', row['resultChars'])
    if model:
        os.environ.update({'IMPRINT_RECALL_LLM': '1', 'IMPRINT_LLM_TAGGER_PROVIDER': 'local',
                           'IMPRINT_TAGGER_MODEL_PATH': str(model), 'IMPRINT_TAGGER_N_GPU_LAYERS': '0'})
        from imprint import recall, tagger
        start = time.perf_counter()
        llm, error = tagger._load_tagger_model()
        if error:
            raise RuntimeError(error)
        load_ms = (time.perf_counter() - start) * 1000
        for timeout in timeouts:
            os.environ['IMPRINT_RECALL_LLM_TIMEOUT'] = str(timeout)
            for task in tasks:
                for mode in ('recall_intent_on', 'recall_plan_on'):
                    row, text = measure(task, mode)
                    plan_s = re.search(r'Plan \(local model, ([\d.]+) s\)', text)
                    answer_s = re.search(r'Answer \(local model, ([\d.]+) s\)', text)
                    rows.append({'task': task['id'], 'mode': f'{mode}_{timeout}s', 'repeats': 1, **row,
                                 'planSeconds': float(plan_s.group(1)) if plan_s else None,
                                 'answerSeconds': float(answer_s.group(1)) if answer_s else None,
                                 'answered': bool(answer_s),
                                 'notes': [line for line in text.splitlines() if line.startswith('Local model')],
                                 'planLines': [line for line in text.splitlines() if re.match(r'\d+\. \w+\(', line)],
                                 'answer': (text.split(answer_s.group(0) + ':\n', 1)[1].split('\nCited evidence', 1)[0]
                                            if answer_s else '')})
    modes = list(dict.fromkeys(row['mode'] for row in rows))
    totals = {mode: {key: sum(row[key] for row in rows if row['mode'] == mode)
                     for key in ('roundTrips', 'argumentTokens', 'resultTokens', 'resultChars', 'serverMs')}
              | {'goldVisible': sum(len(row['goldVisible']) for row in rows if row['mode'] == mode),
                 'goldTotal': sum(len(task['gold']) for task in tasks)}
              for mode in modes}
    for total in totals.values():
        total['callerTokens'] = total['argumentTokens'] + total['resultTokens']
    return {'schemaVersion': 1, 'tasks': len(tasks), 'repeats': repeats,
            'coldModelLoadAndFirstQueryMs': cold,
            'localModel': {'file': Path(model).name, 'loadMs': load_ms, 'device': 'cpu', 'threads': 4,
                           'timeoutsSeconds': list(timeouts)} if model else None,
            'totals': totals, 'rows': rows,
            'tokenUnit': 'Qwen3-Embedding tokenizer count of tool-call arguments and results, a proxy for caller tokens.',
            'limitations': ['Server-side time only; each extra round trip also costs a caller model turn.',
                            'Gold-source visibility is a coverage diagnostic, not answer correctness.',
                            'Local-model modes run once per task on this CPU; answers are recorded for review.']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['prepare', 'retrieval', 'recall', 'stop'])
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--queries', type=Path, default=Path(__file__).with_name('retrieval.json'))
    parser.add_argument('--lookups', type=Path, default=Path(__file__).with_name('recall.json'))
    parser.add_argument('--local-model', type=Path, help='GGUF for the recall local-model modes (CPU)')
    parser.add_argument('--llm-timeouts', default='30', help='comma-separated recall.llm_timeout_sec values')
    parser.add_argument('--runs', type=int, default=5)
    parser.add_argument('--seed', type=int, default=1729)
    args = parser.parse_args()
    state = json.loads(args.state.read_text())
    os.environ.update(state['env'])
    if args.operation == 'stop':
        from imprint import qdrant_runner
        qdrant_runner.stop()
        return
    if args.operation == 'recall':
        result = recall_lookups(json.loads(args.lookups.read_text()), args.runs, args.local_model,
                                [int(value) for value in args.llm_timeouts.split(',') if value])
        result['datasetSha256'] = digest(args.lookups.read_bytes())
    elif args.operation == 'prepare':
        result = prepare(state)
    else:
        result = evaluate(state, json.loads(args.queries.read_text()), args.runs, args.seed)
        result['datasetSha256'] = digest(args.queries.read_bytes())
    args.out.write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
