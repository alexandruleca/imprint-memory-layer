#!/usr/bin/env python3
"""Paired source-memory coverage/retrieval experiment, never a production reindex.

Requires a baseline checkout and the provisioned Python runtime. Synthetic facts
are intentionally present in tool evidence, visible analysis, brief messages and
small source files. This measures evidence availability, not general task quality.
--local-transcripts adds aggregate, text-free coverage statistics from local logs.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch
import zlib

ROOT = Path(__file__).resolve().parents[1]


def fixtures():
    long_answer = 'The implementation has been checked against the documented requirements and the integration tests. '
    def conversation(name, question, content, marker, query):
        return {'name': name, 'kind': 'conversation', 'rows': [
            {'type': 'user', 'message': {'role': 'user', 'content': question}},
            {'type': 'assistant', 'message': {'role': 'assistant', 'content': content}}],
            'marker': marker, 'query': query}
    text = lambda value: {'type': 'text', 'text': value}
    cases = [
        conversation('visible-decision', 'What retry policy should payments use?',
            [text('Never retry a timed-out charge automatically because the first request may already have charged the card. ' + long_answer)],
            'may already have charged', 'Why should timed-out payment charges not be retried automatically?'),
        conversation('tool-database', 'Investigate the importer concurrency failure.', [
            {'type': 'tool_result', 'tool_use_id': 'sql-1', 'content': 'Observed fix: journal_mode=WAL permits the importer reader and writer to coexist.'},
            text(long_answer)], 'journal_mode=WAL', 'What SQLite journal mode fixed importer reader writer contention?'),
        conversation('tool-deploy', 'Inspect the tenant deployment command.', [
            {'type': 'tool_use', 'name': 'Shell', 'id': 'deploy-1', 'input': {'command': 'deploy --schema tenant_rotation_ledger --region staging-east'}},
            text(long_answer)], 'tenant_rotation_ledger', 'Which schema does the tenant deployment command select?'),
        conversation('short-answer', 'Which journal mode did we choose?', [text('WAL.')],
            'WAL.', 'Which journal mode did we choose?'),
        conversation('html-question', '<button data-mode="manual">Pay invoice</button>',
            [text('Attach the explicit click handler to this manual payment button, keeping the invoice action idempotent. ' + long_answer)],
            'explicit click handler', 'How should the manual Pay invoice button be wired?'),
        conversation('analysis-rationale', 'Evaluate the cache architecture.', [
            {'type': 'thinking', 'thinking': 'We rejected Redis because recovery required a durable append-only log, rather than a disposable cache.'},
            text(long_answer)], 'rejected Redis', 'Why was Redis rejected for recovery of the append-only log?'),
        conversation('tool-test-finding', 'Check the payout acceptance test.', [
            {'type': 'tool_result', 'tool_use_id': 'test-1', 'content': 'payout_lag_budget_seconds=45 is the observed passing acceptance threshold.'},
            text(long_answer)], 'payout_lag_budget_seconds=45', 'What is the passing payout lag budget in seconds?'),
        conversation('visible-root-cause', 'Explain why refresh lost configuration.',
            [text('The root cause was replacing the entire client configuration instead of merging only the Imprint entry. ' + long_answer)],
            'merging only the Imprint entry', 'What caused refresh to lose unrelated client configuration?'),
        {'name': 'tiny-code', 'kind': 'code', 'text': 'FEATURE_ENABLED = True\n', 'marker': 'FEATURE_ENABLED',
         'query': 'Which feature enabled constant turns the feature on?'},
        {'name': 'fallback-import', 'kind': 'code', 'fallback': True,
         'text': 'import ledger_protocol\n\ndef enqueue_recovery():\n' + '    # Preserve the recovery order while checking every queued operation.\n' * 5 + '    return True\n',
         'marker': 'import ledger_protocol', 'query': 'Which protocol module does enqueue recovery import?'},
        {'name': 'normal-code', 'kind': 'code',
         'text': 'def rotate_refresh_token(token):\n    """Rotate once because old refresh tokens must become invalid."""\n    invalidate_old_refresh_token(token)\n    return issue_new_refresh_token(token)\n',
         'marker': 'invalidate_old_refresh_token', 'query': 'How does refresh token rotation invalidate the old token?'},
        conversation('visible-alternative', 'Explain the storage decision.',
            [text('We chose SQLite because transactional writes were required. JSON files were rejected because interrupted writes could corrupt them. ' + long_answer)],
            'transactional writes were required', 'Why was SQLite chosen instead of JSON files?'),
    ]
    for n, topic in enumerate(('font selection', 'avatar animation', 'browser bookmarks', 'weather display', 'email layout', 'color palette')):
        cases.append(conversation(f'noise-{n}', 'Review ' + topic, [text('This component concerns ' + topic + '. ' + long_answer)], '', ''))
    return cases


def prepare_worker(root, dataset, output, candidate):
    sys.path.insert(0, str(root))
    from imprint import chunker
    from imprint import cli_conversations as learner
    from imprint.redaction import redact_all
    result = []
    with patch.object(chunker, '_get_semantic_chunker', return_value=None):
        for row in json.loads(Path(dataset).read_text()):
            path = Path(row['path'])
            if candidate:
                from imprint import source_memory as sm
                if row['kind'] == 'conversation':
                    from imprint.conversation_source import prepare
                    doc = prepare(str(path))
                    chunks = sm.records(doc)
                else:
                    with path.open(encoding='utf-8', newline='') as stream:
                        doc = sm.document(stream.read())
                    filename = row.get('filename', row['name'] + '.py')
                    chunks = sm.records(doc, prefix=f"[{filename}]\n", code=Path(filename).suffix.lower() in chunker._CODE_LANG)
                verified = sm.reconstruct(chunks, len(doc['text'])) == doc['text']
                bodies = [chunk['content'] for chunk in chunks]
                snapshot_bytes = len(zlib.compress(doc['text'].encode(), 6))
                canonical = doc['text']
            else:
                if row['kind'] == 'conversation':
                    bodies = [piece for ex in learner.parse_exchanges(str(path)) if len(ex['text']) >= learner.MIN_EXCHANGE_LEN
                              for piece in (chunker.chunk_conversation(ex['text']) or [ex['text']])]
                else:
                    with path.open(encoding='utf-8', newline='') as stream:
                        content = stream.read()
                    with ExitStack() as stack:
                        if row.get('fallback'):
                            stack.enter_context(patch.object(chunker, '_get_code_chunker', return_value=None))
                        bodies = [text for text, _ in chunker.chunk_file(content, row.get('filename', row['name'] + '.py'))]
                bodies = redact_all(bodies)
                canonical, snapshot_bytes, verified = '\n'.join(bodies), 0, False
            result.append({**{k: row[k] for k in ('name', 'kind', 'marker', 'query')}, 'chunks': bodies,
                           'canonical_chars': len(canonical), 'snapshot_compressed_bytes': snapshot_bytes,
                           'reconstructs_snapshot': verified})
    Path(output).write_text(json.dumps(result, ensure_ascii=False))


def retrieval(prepared, data_directory):
    from qdrant_client import QdrantClient
    from imprint import config, vectorstore as vs
    client = QdrantClient(':memory:')
    started = time.monotonic()
    with patch.dict(os.environ, {'IMPRINT_DATA_DIR': str(data_directory), 'IMPRINT_WORKSPACE': 'default'}), \
            patch.object(vs, '_get_client', return_value=client), patch.object(client, 'create_payload_index'):
        vs._collections_ready.clear(); vs._id_caches.clear()
        for source in prepared:
            records = [{'content': text, 'project': 'paired-experiment', 'source': source['name'], 'chunk_index': i, 'type': 'finding'}
                       for i, text in enumerate(source['chunks'])]
            if records:
                vs.store_batch(records)
        indexing_seconds = time.monotonic() - started
        rows, query_started = [], time.monotonic()
        for case in prepared:
            if not case['query']:
                continue
            hits = vs.search(case['query'], limit=5, project='paired-experiment')
            rank = next((i for i, hit in enumerate(hits, 1)
                         if hit['source'] == case['name'] and case['marker'] in hit['content']), None)
            rows.append({'case': case['name'], 'evidence_rank': rank})
        client.close(); vs._collections_ready.clear(); vs._id_caches.clear()
    return {'cases': rows, 'evidence_recall_at_1': sum(r['evidence_rank'] == 1 for r in rows) / len(rows),
            'evidence_recall_at_5': sum(r['evidence_rank'] is not None for r in rows) / len(rows),
            'evidence_mrr_at_5': sum(1 / r['evidence_rank'] if r['evidence_rank'] else 0 for r in rows) / len(rows),
            'indexing_seconds': indexing_seconds, 'query_seconds': time.monotonic() - query_started,
            'embedding_model': config.MODEL_NAME}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-root', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--model-cache', type=Path)
    parser.add_argument('--local-transcripts', action='store_true')
    parser.add_argument('--local-code', action='store_true', help='Compare supported tracked code/text files from the frozen baseline checkout')
    parser.add_argument('--retrieval', action='store_true')
    parser.add_argument('--worker-root', type=Path)
    parser.add_argument('--dataset', type=Path)
    parser.add_argument('--candidate', action='store_true')
    args = parser.parse_args()
    if args.worker_root:
        prepare_worker(args.worker_root, args.dataset, args.output, args.candidate)
        return
    if not args.baseline_root or not args.output:
        parser.error('--baseline-root and --output are required')
    with tempfile.TemporaryDirectory(prefix='imprint-source-experiment-') as temporary:
        temporary = Path(temporary)
        os.environ.update(IMPRINT_DATA_DIR=str(temporary / 'data'), IMPRINT_WORKSPACE='default', IMPRINT_LLM_TAGS='0',
                          IMPRINT_REDACT_PRIVACY_FILTER='false', HF_HUB_OFFLINE='1')
        if args.model_cache:
            (temporary / 'data').mkdir()
            (temporary / 'data' / 'models').symlink_to(args.model_cache.resolve(), target_is_directory=True)
        corpus = []
        for n, row in enumerate(fixtures()):
            path = temporary / (f'fixture-{n}.jsonl' if row['kind'] == 'conversation' else f'fixture-{n}.py')
            path.write_text('\n'.join(json.dumps(r) for r in row['rows']) if row['kind'] == 'conversation' else row['text'])
            corpus.append({**row, 'path': str(path)})
        fixture_count = len(corpus)
        if args.local_transcripts:
            paths = [*Path.home().glob('.cursor/projects/*/agent-transcripts/*/*.jsonl'),
                     *Path.home().glob('.claude/projects/*/*.jsonl'), *Path.home().glob('.codex/sessions/**/rollout-*.jsonl')]
            for n, source in enumerate(paths):
                path = temporary / f'local-{n}.jsonl'; path.write_bytes(source.read_bytes())
                corpus.append({'name': f'local-{n}', 'kind': 'conversation', 'path': str(path), 'marker': '', 'query': ''})
        if args.local_code:
            sys.path.insert(0, str(ROOT))
            from imprint.cli_index import EXTENSIONS
            files = subprocess.check_output(['git', '-C', str(args.baseline_root), 'ls-files', '-z'], text=True).split('\0')
            for n, filename in enumerate(files):
                source = args.baseline_root / filename
                if source.suffix.lower() not in EXTENSIONS or not source.is_file():
                    continue
                try:
                    source.read_bytes().decode('utf-8')
                except UnicodeDecodeError:
                    continue
                path = temporary / f'code-{n}{source.suffix}'; path.write_bytes(source.read_bytes())
                corpus.append({'name': f'code-{n}', 'filename': filename, 'kind': 'code', 'path': str(path), 'marker': '', 'query': ''})
        dataset = temporary / 'dataset.json'; dataset.write_text(json.dumps(corpus))
        result = {'baseline_revision': subprocess.check_output(['git', '-C', str(args.baseline_root), 'rev-parse', 'HEAD'], text=True).strip(),
                  'candidate_revision': subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip(),
                  'candidate_source_hash': hashlib.sha256(b''.join((ROOT / 'imprint' / name).read_bytes()
                      for name in ('source_memory.py', 'source_snapshots.py', 'conversation_source.py', 'chunker.py'))).hexdigest(),
                  'method': 'Paired synthetic evidence retrieval; optional aggregate local transcript coverage. Baseline semantic sub-splitting is bypassed for preparation only. Real retrieval uses the same local embedding model and hybrid search.',
                  'limitations': ['Synthetic regression corpus, not a held-out quality benchmark.', 'No production memories are written.',
                                  'Local transcript text and paths are excluded from this report.', 'Timing is one run and order-dependent.']}
        for label, root, candidate in [('before', args.baseline_root, False), ('after', ROOT, True)]:
            output = temporary / (label + '.json')
            command = [sys.executable, __file__, '--worker-root', str(root), '--dataset', str(dataset), '--output', str(output)]
            if candidate: command.append('--candidate')
            start = time.monotonic(); subprocess.run(command, check=True, cwd=root)
            prepared = json.loads(output.read_text())
            summary = {'sources': len(prepared), 'chunks': sum(len(r['chunks']) for r in prepared),
                       'chunk_characters': sum(sum(len(c) for c in r['chunks']) for r in prepared),
                       'snapshot_compressed_bytes': sum(r['snapshot_compressed_bytes'] for r in prepared),
                       'verified_source_snapshots': sum(r['reconstructs_snapshot'] for r in prepared),
                       'preparation_seconds': time.monotonic() - start}
            summary['synthetic_evidence_present'] = sum(any(r['marker'] in c for c in r['chunks']) for r in prepared[:fixture_count] if r['query'])
            summary['corpora'] = {name: {'sources': len(rows), 'chunks': sum(len(r['chunks']) for r in rows),
                'chunk_characters': sum(sum(len(c) for c in r['chunks']) for r in rows),
                'verified_source_snapshots': sum(r['reconstructs_snapshot'] for r in rows),
                'snapshot_compressed_bytes': sum(r['snapshot_compressed_bytes'] for r in rows)}
                for name, rows in [('synthetic', prepared[:fixture_count]),
                    ('local_conversations', [r for r in prepared[fixture_count:] if r['kind'] == 'conversation']),
                    ('repository_files', [r for r in prepared[fixture_count:] if r['kind'] == 'code'])]}
            if args.retrieval:
                sys.path.insert(0, str(ROOT))
                summary['retrieval'] = retrieval(prepared[:fixture_count], temporary / 'data')
            result[label] = summary
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
