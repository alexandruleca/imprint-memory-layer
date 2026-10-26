"""The background LLM tagging backlog (ADR 0067): storing paths mark memories
instead of calling the LLM, and the backlog job tags them newest first, one
memory per write, stepping aside for queued jobs."""

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from qdrant_client import QdrantClient

from imprint import config, queue, tag_backlog, vectorstore as vs

SECRET = 'ghp_' + 'A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8'


def classification(kind='decision', topic='queues'):
    """A stand-in LLM classification, as build_payload_tags(llm=True) returns it."""
    def classify(content, **kwargs):
        assert kwargs.get('llm') is True, 'the backlog classifies with the LLM'
        return {'lang': '', 'layer': '', 'kind': '', 'domain': ['infra'], 'topics': [topic], '_llm_type': kind}
    return classify


class BacklogCase(unittest.TestCase):
    def setUp(self):
        data = tempfile.TemporaryDirectory()
        self.addCleanup(data.cleanup)
        self.data = Path(data.name)
        self.client = QdrantClient(':memory:')
        self.llm = {'IMPRINT_LLM_TAGS': '1'}
        self.patches = [
            patch.dict(os.environ, {'IMPRINT_DATA_DIR': str(self.data), 'IMPRINT_WORKSPACE': '', **self.llm}),
            patch.object(vs, '_get_client', return_value=self.client),
            patch.object(config, 'EMBEDDING_DIM', 3),
            patch('imprint.embeddings.embed_documents_batch', side_effect=lambda texts: [[1., 0., 0.] for _ in texts]),
            patch('imprint.embeddings.embed_document', return_value=[1., 0., 0.]),
            patch('imprint.tagger.derive_zero_shot', return_value=[]),
            patch.object(self.client, 'create_payload_index'),
            patch.object(queue, '_notify_dispatcher'),
        ]
        for item in self.patches:
            item.start()
        vs._collections_ready.clear()
        vs._id_caches.clear()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        from imprint import imprint_graph
        imprint_graph._close_all()  # Windows cannot delete an open database
        vs._collections_ready.clear()
        vs._id_caches.clear()
        for item in reversed(self.patches):
            item.stop()
        self.client.close()

    def store(self, texts, pending=True, start=1000.0, source='project/notes.md'):
        """Store memories with increasing timestamps, the last one newest."""
        for i, text in enumerate(texts):
            with patch('imprint.vectorstore.time.time', return_value=start + i):
                vs.store_batch([{'content': text, 'project': 'project', 'source': f'{source}#{i}',
                                 'tags': {'domain': ['keyword']}, 'type': 'architecture', 'tag_pending': pending}])

    def payloads(self):
        _, coll = vs._ensure_collection()
        points, _ = self.client.scroll(coll, limit=100, with_payload=True)
        return {point.payload['content']: point.payload for point in points}


class BacklogTests(BacklogCase):
    def test_only_marked_memories_are_counted_and_tagged_newest_first(self):
        self.store(['old note', 'middle note', 'new note'])
        vs.store('A decision an agent stored', project='project', type='decision', source='manual')
        self.store(['stored with LLM tagging off'], pending=False, start=5000.0)
        self.assertEqual(tag_backlog.pending_count(), 3)
        order = []
        with patch('imprint.tagger.build_payload_tags', side_effect=lambda content, **kw: (
                order.append(content), classification()(content, **kw))[1]):
            tagged, stopped = tag_backlog.drain()
        self.assertEqual((tagged, stopped), (3, False))
        self.assertEqual(order, ['new note', 'middle note', 'old note'])
        payloads = self.payloads()
        for text in ('old note', 'middle note', 'new note'):
            self.assertEqual((payloads[text]['tag_pending'], payloads[text]['llm_tagged'], payloads[text]['type']),
                             (False, True, 'decision'))
            self.assertEqual(payloads[text]['tags']['topics'], ['queues'])
            self.assertGreater(payloads[text]['tagged_at'], 0)  # ADR 0051: sync carries it
        # The agent's decision and the memory stored with LLM tagging off keep their classification.
        self.assertEqual(payloads['A decision an agent stored']['type'], 'decision')
        self.assertNotIn('tagged_at', payloads['A decision an agent stored'])
        self.assertNotIn('tagged_at', payloads['stored with LLM tagging off'])
        self.assertEqual(tag_backlog.pending_count(), 0)

    def test_a_stop_between_memories_leaves_the_rest_waiting_and_a_rerun_resumes(self):
        self.store(['one', 'two', 'three', 'four'])
        calls = []
        with patch('imprint.tagger.build_payload_tags', side_effect=lambda c, **kw: (calls.append(c),
                                                                                      classification()(c, **kw))[1]):
            tagged, stopped = tag_backlog.drain(should_stop=lambda: len(calls) == 2)
            self.assertEqual((tagged, stopped), (2, True))
            self.assertEqual(tag_backlog.pending_count(), 2)
            self.assertEqual(tag_backlog.drain(), (2, False))
        self.assertEqual(calls, ['four', 'three', 'two', 'one'])

    def test_a_crash_loses_at_most_the_memory_being_classified(self):
        self.store(['one', 'two', 'three'])

        def flaky(content, **kwargs):
            if content == 'two':
                raise RuntimeError('provider connection dropped')
            return classification()(content, **kwargs)

        with patch('imprint.tagger.build_payload_tags', side_effect=flaky), \
                self.assertRaisesRegex(RuntimeError, 'dropped'):
            tag_backlog.drain()
        payloads = self.payloads()
        self.assertEqual({text: payloads[text]['tag_pending'] for text in ('one', 'two', 'three')},
                         {'three': False, 'two': True, 'one': True})
        with patch('imprint.tagger.build_payload_tags', side_effect=classification()):
            self.assertEqual(tag_backlog.drain(), (2, False))

    def test_a_memory_deleted_while_it_was_classified_is_skipped(self):
        self.store(['kept', 'deleted'])

        def delete_first(content, **kwargs):
            if content == 'deleted':
                vs.delete_by_source('project/notes.md#1', project='project')
            return classification()(content, **kwargs)

        def set_existing(client, coll, updates):
            # The Qdrant server answers 404 for a point that is gone.
            if not client.retrieve(coll, ids=[updates[0][0]]):
                raise RuntimeError('Not found: No point with id')
            return set_classifications(client, coll, updates)

        set_classifications = vs.set_classifications
        with patch('imprint.tagger.build_payload_tags', side_effect=delete_first), \
                patch.object(vs, 'set_classifications', side_effect=set_existing):
            self.assertEqual(tag_backlog.drain(), (1, False))
        self.assertEqual(list(self.payloads()), ['kept'])

    def test_a_memory_without_a_timestamp_is_still_reached(self):
        self.store(['dated'])
        _, coll = vs._ensure_collection()
        point = next(iter(self.client.scroll(coll, limit=1)[0]))
        self.client.delete_payload(coll, keys=['timestamp'], points=[point.id])
        with patch('imprint.tagger.build_payload_tags', side_effect=classification()):
            self.assertEqual(tag_backlog.drain(), (1, False))

    def test_conversation_fields_survive_the_classification(self):
        vs.store_batch([{'content': 'how do we queue jobs', 'project': 'p', 'source': 'conversation/1',
                         'tags': {'lang': 'conversation', 'layer': 'session', 'kind': 'qa'}, 'tag_pending': True}])
        with patch('imprint.tagger.build_payload_tags', side_effect=classification()):
            tag_backlog.drain()
        tags = self.payloads()['how do we queue jobs']['tags']
        self.assertEqual((tags['lang'], tags['layer'], tags['kind']), ('conversation', 'session', 'qa'))

    def test_the_backlog_prompt_is_redacted_before_any_provider_sees_it(self):
        # A memory stored before redaction existed still holds its secret.
        self.store(['placeholder'])
        _, coll = vs._ensure_collection()
        point = next(iter(self.client.scroll(coll, limit=1)[0]))
        self.client.set_payload(coll, payload={'content': f'deploy key {SECRET}'}, points=[point.id])
        prompts = []
        from imprint import tagger
        with patch.object(tagger, '_get_llm_provider', return_value='local'), \
                patch.object(tagger, '_load_tagger_model', return_value=(object(), None)), \
                patch('imprint.local_llm.complete', side_effect=lambda llm, prompt, **kw: (
                    prompts.append(prompt), '{"type": "finding", "domains": [], "topics": []}')[1]), \
                patch('imprint.taxonomy.get_all_values', return_value=[]), patch('imprint.taxonomy.record_usage'):
            self.assertEqual(tag_backlog.drain(), (1, False))
        self.assertEqual(len(prompts), 1)
        self.assertNotIn(SECRET, prompts[0])
        self.assertIn('[REDACTED:github-token', prompts[0])

    def test_a_classification_received_from_a_peer_takes_the_memory_off_the_backlog(self):
        self.store(['shared note'])
        mid = self.payloads()['shared note']['_mid']
        received = {'id': mid, 'content': 'shared note', 'project': 'project', 'source': 'project/notes.md#0',
                    'chunk_index': 0, 'vector': [1., 0., 0.], 'embedding_identity': config.embedding_identity(),
                    'tags': {'domain': ['from-peer']}, 'type': 'pattern', 'llm_tagged': True, 'tagged_at': 9e9}
        self.assertEqual(vs.store_batch_precomputed([received])[1], 1)
        payload = self.payloads()['shared note']
        self.assertEqual((payload['tag_pending'], payload['type'], payload['tagged_at']), (False, 'pattern', 9e9))
        self.assertEqual(tag_backlog.pending_count(), 0)

    def test_the_marker_never_leaves_this_device(self):
        from imprint import sync_ws
        self.store(['local note'])
        lines = sync_ws.stream_export_lines('default')
        self.assertIn('local note', ''.join(lines))
        self.assertNotIn('tag_pending', ''.join(lines))


class WorkerTests(BacklogCase):
    """The dispatcher's background job: python -m imprint.tag_backlog <job_id>."""

    def test_it_yields_to_a_queued_job_and_resumes_where_it_stopped(self):
        self.store(['one', 'two', 'three'])

        def queue_a_job_after_the_first(content, **kwargs):
            if content == 'three':
                queue.enqueue('status', {})
            return classification()(content, **kwargs)

        with patch('imprint.tagger.build_payload_tags', side_effect=queue_a_job_after_the_first), \
                patch('imprint.tagger.llm_unavailable', return_value=None):
            self.assertEqual(tag_backlog.run('a' * 32), tag_backlog.YIELDED)
            self.assertEqual(tag_backlog.pending_count(), 2)
            # Once that job has run, the next start tags the rest.
            with queue._db_lock, queue._connect() as conn:
                conn.execute("UPDATE jobs SET status='done'")
            self.assertEqual(tag_backlog.run('b' * 32), 0)
        self.assertEqual(tag_backlog.pending_count(), 0)

    def test_it_refuses_to_run_without_a_usable_tagger(self):
        self.store(['one'])
        with patch('imprint.tagger.llm_unavailable', return_value='llama-cpp-python not installed'), \
                patch('imprint.tagger.build_payload_tags') as classify:
            self.assertEqual(tag_backlog.run('a' * 32), 1)
        classify.assert_not_called()
        self.assertEqual(tag_backlog.pending_count(), 1)

    def test_it_does_nothing_while_llm_tagging_is_off(self):
        self.store(['one'])
        with patch.dict(os.environ, {'IMPRINT_LLM_TAGS': '0'}), \
                patch('imprint.tagger.build_payload_tags') as classify:
            self.assertEqual(tag_backlog.run('a' * 32), 0)
        classify.assert_not_called()

    def test_it_covers_every_workspace_the_active_one_first(self):
        config.register_workspace('other')
        self.store(['in default'])
        vs.store_batch([{'content': 'in other', 'project': 'p', 'source': 's', 'tag_pending': True}], workspace='other')
        order = []
        with patch('imprint.tagger.build_payload_tags', side_effect=lambda c, **kw: (
                order.append(c), classification()(c, **kw))[1]), \
                patch('imprint.tagger.llm_unavailable', return_value=None):
            self.assertEqual(tag_backlog.run('a' * 32), 0)
        self.assertEqual(order, ['in default', 'in other'])

    def test_it_holds_the_lock_as_a_preemptible_holder(self):
        from imprint import queue_lock
        seen = []
        self.store(['one'])
        with patch('imprint.tagger.build_payload_tags', side_effect=lambda c, **kw: (
                seen.append(queue_lock.read_holder()), classification()(c, **kw))[1]), \
                patch('imprint.tagger.llm_unavailable', return_value=None):
            tag_backlog.run('c' * 32)
        self.assertEqual((seen[0]['command'], seen[0]['job_id'], seen[0]['preemptible']), ('tag-backlog', 'c' * 32, True))
        descriptor = queue_lock.acquire('purge', '', preemptible=False)
        self.assertIsNotNone(descriptor, 'the job released the lock when it finished')
        queue_lock.release(descriptor)

    def test_retag_pending_drains_the_backlog_in_the_foreground(self):
        from imprint import cli_retag
        self.store(['one', 'two'])
        vs.store('An agent decision', project='project', type='decision', source='manual')
        with patch('imprint.tagger.build_payload_tags', side_effect=classification()):
            self.assertEqual(cli_retag.retag_pending(), 2)
        self.assertEqual(self.payloads()['An agent decision']['type'], 'decision')
        with patch.object(sys, 'argv', ['cli_retag', '--pending', '--all']), self.assertRaises(SystemExit):
            cli_retag.main()


class StoringPathTests(BacklogCase):
    """Learn, ingest, the Stop hook's import and inline content store without
    calling the LLM, and mark their memories while tagger.llm is on."""

    def no_llm(self):
        return patch('imprint.tagger.derive_llm_classify', side_effect=AssertionError('the LLM ran inline'))

    def test_partial_conversation_is_retried_then_marked_complete(self):
        from imprint import cli_conversations
        original = vs.store_batch
        calls = 0

        def fail_second_batch(records):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('embedding interrupted')
            return original(records)

        with self.no_llm(), patch.object(cli_conversations, 'identify', return_value=('conversation/retry', 'project')), \
                patch.object(cli_conversations, 'owned', return_value=True), \
                patch.object(cli_conversations, 'parse_exchanges', return_value=[{'text': 'Useful exchange ' * 30}]), \
                patch.object(cli_conversations.chunker, 'chunk_conversation',
                             return_value=[f'Piece {i} ' * 30 for i in range(10)]), \
                patch.object(cli_conversations.tagger, 'build_payload_tags', return_value={}), \
                patch.object(config, 'BATCH_SIZE', 8), \
                patch('imprint.workspace_policy.is_excluded', return_value=False):
            with patch.object(vs, 'store_batch', side_effect=fail_second_batch):
                with self.assertRaisesRegex(RuntimeError, 'embedding interrupted'):
                    cli_conversations.index_transcript('retry.jsonl', source_mtime=123)
            self.assertEqual(vs.get_source_mtimes()['conversation/retry'], 0)
            self.assertEqual(len(self.payloads()), 8)
            self.assertEqual(cli_conversations.index_transcript('retry.jsonl', source_mtime=123), (2, 0))
            self.assertEqual(vs.get_source_mtimes()['conversation/retry'], 123)
            self.assertEqual(len(self.payloads()), 10)

    def test_a_learned_conversation_is_marked_not_classified(self):
        from imprint import cli_conversations
        transcript = self.data / 'session.jsonl'
        exchanges = [{'text': 'We decided to queue tagging in the background because it took hours. ' * 3}]
        with self.no_llm(), patch.object(cli_conversations, 'identify', return_value=('conversation/s', 'project')), \
                patch.object(cli_conversations, 'owned', return_value=True), \
                patch.object(cli_conversations, 'parse_exchanges', return_value=exchanges), \
                patch.object(cli_conversations.chunker, 'chunk_conversation', side_effect=lambda text: [text]), \
                patch('imprint.workspace_policy.is_excluded', return_value=False):
            stored, _ = cli_conversations.index_transcript(str(transcript), source_mtime=1.0)
            with patch.dict(os.environ, {'IMPRINT_LLM_TAGS': '0'}), \
                    patch.object(cli_conversations, 'identify', return_value=('conversation/t', 'project')):
                cli_conversations.index_transcript(str(transcript), source_mtime=1.0)
        self.assertGreater(stored, 0)
        _, coll = vs._ensure_collection()
        points, _ = self.client.scroll(coll, limit=100, with_payload=True)
        sources = {point.payload['source']: point.payload.get('tag_pending', False) for point in points}
        self.assertEqual(sources, {'conversation/s': True, 'conversation/t': False})

    def test_an_ingested_file_is_marked_not_classified(self):
        from imprint import cli_index
        project = self.data / 'project'
        project.mkdir()
        (project / 'notes.md').write_text('# Queue\n\nBackground tagging steps aside for other jobs.\n' * 5)
        chunks = [('# Queue\n\nBackground tagging steps aside.', 0), ('It resumes when the queue is idle.', 1)]
        with self.no_llm(), patch('imprint.workspace_policy.ensure_ingest_paths'), \
                patch('imprint.workspace_policy.is_excluded', return_value=False), \
                patch('imprint.workspace_policy.map_project'), patch('imprint.chunker.chunk_file', return_value=chunks), \
                contextlib.redirect_stdout(io.StringIO()):  # its summary is not what this checks
            cli_index.ingest_single_file(str(project / 'notes.md'), 'project', 8)
        self.assertTrue(all(payload['tag_pending'] for payload in self.payloads().values()))
        self.assertEqual(tag_backlog.pending_count(), len(self.payloads()))

    def test_inline_content_is_marked_not_classified(self):
        from imprint.ingest_core import store_inline_content
        with self.no_llm():
            store_inline_content('Background tagging steps aside for every other job.', 'note-1', project='p')
        self.assertEqual(tag_backlog.pending_count(), 1)

    def test_the_stop_hooks_extraction_is_marked_not_classified(self):
        import json
        from imprint import cli_extract
        decision = ('We decided to run LLM tagging as a background job because it took most of the time a learn '
                    'took. The trade-off is that tags arrive later, which is fine because search works without them. '
                    'This is intentional and by design.')
        transcript = self.data / 'session.jsonl'
        transcript.write_text(json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': decision}]}}))
        with self.no_llm(), patch.object(sys, 'argv', ['cli_extract', str(transcript), '--project', 'p']):
            cli_extract.main()
        payload = self.payloads()[decision]
        self.assertEqual((payload['tag_pending'], payload['source']), (True, 'auto-extract'))

    def test_learn_and_ingest_have_no_inline_tagging_phase(self):
        from imprint import cli_conversations, cli_index, cli_ingest_url
        for module in (cli_conversations, cli_index, cli_ingest_url):
            self.assertFalse(hasattr(module, '_llm_tag_recent'), module.__name__)
        source = Path(cli_conversations.__file__).read_text(encoding='utf-8')
        self.assertNotIn('llm=None', source)

    def test_a_storing_run_says_where_its_tagging_went(self):
        self.store(['one'])
        with patch('builtins.print') as printed:
            tag_backlog.report(1)
        self.assertIn('imprint retag --pending', printed.call_args.args[0])
        with patch.dict(os.environ, {'IMPRINT_LLM_TAGS': '0'}), patch('builtins.print') as printed:
            tag_backlog.report(1)
        printed.assert_not_called()


if __name__ == '__main__':
    unittest.main()
