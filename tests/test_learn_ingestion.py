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
        import json
        from pathlib import Path
        from imprint import config
        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as data, \
                    patch.dict('os.environ', {'IMPRINT_DATA_DIR': data}), \
                    patch.object(learner, 'identify', return_value=('conversation/test', 'app')), \
                    patch.object(learner, 'owned', return_value=True), \
                    patch.object(config, 'BATCH_SIZE', 8), patch.object(config, 'CHUNK_SIZE_PROSE', 150), \
                    patch('imprint.workspace_policy.is_excluded', return_value=False), \
                    patch.object(learner.tagger, 'build_payload_tags', return_value={}), \
                    patch.object(vs, 'complete_conversation') as complete:
                path = Path(data) / 'test.jsonl'
                path.write_text(json.dumps({'type': 'assistant', 'message': {'content': 'Long visible rationale. ' * 100}}))
                records, events = [], []
                def replace(rows, *args, **kwargs):
                    records.extend(rows)
                    kwargs['progress'](0, len(rows))
                    if fail:
                        raise RuntimeError('write failed')
                    kwargs['progress'](len(rows), len(rows))
                    return len(rows), 0
                with patch.object(vs, 'replace_source', side_effect=replace):
                    if fail:
                        with self.assertRaisesRegex(RuntimeError, 'write failed'):
                            learner.index_transcript(str(path), 123, lambda *args: events.append('progress'))
                        complete.assert_not_called()
                    else:
                        stored, _ = learner.index_transcript(str(path), 123, lambda *args: events.append('progress'))
                        self.assertEqual(stored, len(records))
                        complete.assert_called_once_with('conversation/test', 123, learner.LEARNER_VERSION)
                self.assertEqual([r['chunk_index'] for r in records], list(range(len(records))))
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
