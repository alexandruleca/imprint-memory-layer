"""Regressions for the first-transcript stall and retryable batch failures."""
import contextlib
import io
import tempfile
import unittest
from unittest.mock import patch

from imprint import chunker, cli_conversations as learner, vectorstore as vs


class ConversationIngestionTests(unittest.TestCase):
    def test_long_exchange_chunks_without_model_inference(self):
        content = '\n\n'.join(f'Paragraph {i}: ' + 'useful conversation text ' * 30 for i in range(100))
        with patch.object(chunker, '_get_semantic_chunker', side_effect=AssertionError('model inference')):
            chunks = chunker.chunk_conversation(content)
        self.assertGreater(len(chunks), 8)
        self.assertTrue(all(0 < len(c) <= chunker.HARD_MAX for c in chunks))
        for i in range(100):
            self.assertIn(f'Paragraph {i}:', '\n'.join(chunks))

    def test_watermark_published_only_after_all_batches_and_indices_are_unique(self):
        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as data, \
                    patch.dict('os.environ', {'IMPRINT_DATA_DIR': data}), \
                    patch.object(learner, 'identify', return_value=('conversation/test', 'app')), \
                    patch.object(learner, 'owned', return_value=True), \
                    patch('imprint.config.BATCH_SIZE', 8), \
                    patch('imprint.workspace_policy.is_excluded', return_value=False), \
                    patch.object(learner, 'parse_exchanges', return_value=[{'text': 'Exchange ' * 30}] * 2), \
                    patch.object(chunker, 'chunk_conversation', return_value=['Piece ' * 30] * 5), \
                    patch.object(learner.tagger, 'build_payload_tags', return_value={}), \
                    patch.object(vs, 'complete_conversation') as complete:
                records, events = [], []
                def store(batch):
                    events.append('store')
                    records.extend(batch)
                    if fail and len(records) > 8:
                        raise RuntimeError('write failed')

                def replace(chunks, source, project, *, batch_size, progress):
                    # The transcript's memories are replaced batch by batch (ADR 0080).
                    for start in range(0, len(chunks), batch_size):
                        store(chunks[start:start + batch_size])
                        progress(min(start + batch_size, len(chunks)), len(chunks))
                    return len(chunks), 0
                with patch.object(vs, 'replace_source', side_effect=replace):
                    if fail:
                        with self.assertRaisesRegex(RuntimeError, 'write failed'):
                            learner.index_transcript('test.jsonl', 123, lambda *args: events.append('progress'))
                        complete.assert_not_called()
                    else:
                        self.assertEqual(learner.index_transcript('test.jsonl', 123,
                                         lambda *args: events.append('progress')), (10, 0))
                        complete.assert_called_once_with('conversation/test', 123, learner.LEARNER_VERSION)
                self.assertEqual([r['chunk_index'] for r in records], list(range(10)))
                self.assertTrue(all(r['source_mtime'] == 0 for r in records))
                self.assertEqual(events[0], 'progress')

    def test_partial_source_watermarks_force_retry_regardless_of_scroll_order(self):
        for times in ([123, 0], [0, 123]):
            with patch.object(vs, '_scroll_all', return_value=[{'source': 'conversation/test', 'source_mtime': t} for t in times]):
                self.assertEqual(vs.get_source_mtimes(), {'conversation/test': 0})

    def test_another_learner_versions_memories_have_no_watermark(self):
        # A transcript another learner version read is learned again (ADR 0080).
        points = [{'source': 'conversation/new', 'source_mtime': 123, 'learner_version': learner.LEARNER_VERSION},
                  {'source': 'conversation/old', 'source_mtime': 123, 'learner_version': learner.LEARNER_VERSION - 1},
                  {'source': 'conversation/older', 'source_mtime': 123}]
        with patch.object(vs, '_scroll_all', return_value=points):
            self.assertEqual(vs.get_source_mtimes(learner_version=learner.LEARNER_VERSION),
                             {'conversation/new': 123, 'conversation/old': 0, 'conversation/older': 0})

    def test_failed_transcripts_are_reported_and_cancellation_is_nonzero(self):
        from pathlib import Path
        for error, expected in ((RuntimeError('embedding broke'), 1), (KeyboardInterrupt(), 130)):
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as data, \
                    patch.dict('os.environ', {'IMPRINT_DATA_DIR': data}), \
                    patch.object(learner, 'store_transcripts', return_value=[Path(__file__)]), \
                    patch.object(learner, 'identify', return_value=('conversation/test', 'app')), \
                    patch.object(learner, 'owned', return_value=True), \
                    patch.object(vs, 'get_source_mtimes', return_value={}), \
                    patch.object(learner, 'index_transcript', side_effect=error), \
                    patch('imprint.tag_backlog.report'), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(learner.learn([('cursor', data)]), expected)
                self.assertNotIn('100%', output.getvalue())
                if expected == 1:
                    self.assertIn('embedding broke', output.getvalue())
                    self.assertIn('Errors:', output.getvalue())


if __name__ == '__main__':
    unittest.main()
