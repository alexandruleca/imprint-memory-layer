"""The Stop hook's learner: one waiting learner per transcript, under the queue lock (ADR 0077)."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from imprint import queue_lock, session_learn


class SessionLearnTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data = Path(directory.name)
        environment = patch.dict(os.environ, {'IMPRINT_DATA_DIR': str(self.data), 'IMPRINT_WORKSPACE': 'default'})
        environment.start()
        self.addCleanup(environment.stop)
        self.transcript = str(self.data / 'session.jsonl')
        self.slot = session_learn.slot_path(self.transcript)

    def slot_free(self):
        fd = queue_lock.try_lock(self.slot)
        if fd is None:
            return False
        queue_lock.unlock(fd, self.slot)
        return True

    def test_a_waiting_learner_absorbs_later_turns(self):
        held = queue_lock.try_lock(self.slot)
        try:
            with patch.object(session_learn.time, 'sleep') as settle, \
                    patch.object(queue_lock, 'acquire') as acquire, \
                    patch.object(session_learn, '_learn') as learned:
                session_learn.learn(self.transcript)
            settle.assert_not_called()
            acquire.assert_not_called()
            learned.assert_not_called()
        finally:
            queue_lock.unlock(held, self.slot)

    def test_the_slot_is_given_up_after_the_queue_lock_and_before_reading(self):
        events = []

        def settle(seconds):
            events.append(('settle', seconds, self.slot_free()))

        def acquire(command, job_id, block=False, preemptible=False):
            events.append(('queue lock', command, block, self.slot_free()))
            return 42

        def learned(transcript):
            events.append(('learn', transcript, self.slot_free()))

        with patch.object(session_learn.time, 'sleep', side_effect=settle), \
                patch.object(queue_lock, 'acquire', side_effect=acquire), \
                patch.object(queue_lock, 'release', side_effect=lambda fd: events.append(('release', fd))), \
                patch.object(session_learn, '_learn', side_effect=learned):
            session_learn.learn(self.transcript)
        self.assertEqual(events, [('settle', session_learn.SETTLE_SECONDS, False),
                                  ('queue lock', 'learn', True, False),
                                  ('learn', self.transcript, True),
                                  ('release', 42)])
        self.assertFalse(self.slot.exists())

    def test_a_blocking_attempt_that_gives_up_is_repeated(self):
        # msvcrt's blocking lock gives up after about ten seconds.
        with patch.object(session_learn.time, 'sleep'), \
                patch.object(queue_lock, 'acquire', side_effect=[None, None, 7]) as acquire, \
                patch.object(queue_lock, 'release') as release, \
                patch.object(session_learn, '_learn'):
            session_learn.learn(self.transcript)
        self.assertEqual(acquire.call_count, 3)
        release.assert_called_once_with(7)

    def test_the_lock_is_released_when_learning_fails(self):
        with patch.object(session_learn.time, 'sleep'), \
                patch.object(queue_lock, 'acquire', return_value=7), \
                patch.object(queue_lock, 'release') as release, \
                patch.object(session_learn, '_learn', side_effect=SystemExit(143)):
            with self.assertRaises(SystemExit):
                session_learn.learn(self.transcript)
        release.assert_called_once_with(7)
        self.assertTrue(self.slot_free())

    def test_each_workspace_has_its_own_slot(self):
        with patch.dict(os.environ, {'IMPRINT_WORKSPACE': 'beta'}):
            beta = session_learn.slot_path(self.transcript)
        self.assertNotEqual(beta, self.slot)
        self.assertEqual(session_learn.slot_path(self.transcript), self.slot)
        self.assertNotEqual(session_learn.slot_path(self.transcript + '2'), self.slot)

    def test_one_process_runs_every_step_and_a_failure_stops_none(self):
        from imprint import cli_conversations, cli_extract, cli_summarize
        with patch.object(session_learn.time, 'sleep'), \
                patch.object(cli_conversations, 'index_transcript', side_effect=RuntimeError('qdrant down')), \
                patch.object(cli_extract, 'extract', return_value=2) as extract, \
                patch.object(cli_summarize, 'summarize_transcript', return_value={'status': 'disabled'}) as summarize:
            session_learn.learn(self.transcript)
        extract.assert_called_once_with(self.transcript)
        summarize.assert_called_once_with(self.transcript)
        log = (self.data / 'session_learn.log').read_text(encoding='utf-8')
        self.assertIn('conversation failed: RuntimeError: qdrant down', log)
        self.assertIn('extraction: 2', log)
        self.assertIsNone(queue_lock.read_holder())

    def test_each_step_is_reported_while_it_runs_and_nothing_after(self):
        # The progress record is what the dashboard, the desktop app and a
        # waiting command show (ADR 0089).
        from imprint import cli_conversations, cli_extract, cli_summarize, progress
        seen = []

        def indexed(transcript, progress):
            seen.append(_record())
            progress(3, 8)
            seen.append(_record())
            return 8, 0

        def _record():
            record = progress.read_progress()
            return record['phase'], record['processed'], record['total'], record['unit']

        with patch.object(session_learn.time, 'sleep'), \
                patch.object(cli_conversations, 'index_transcript', side_effect=indexed), \
                patch.object(cli_extract, 'extract', side_effect=lambda t: seen.append(_record())), \
                patch.object(cli_summarize, 'summarize_transcript', side_effect=lambda t: seen.append(_record())):
            session_learn.learn(self.transcript)
        self.assertEqual(seen, [('reading', 0, 0, 'chunks'), ('embedding', 3, 8, 'chunks'),
                                ('extracting', 0, 0, 'chunks'), ('summarizing', 0, 0, 'chunks')])
        self.assertIsNone(progress.read_progress())

    def test_the_log_starts_over_past_its_limit(self):
        log = self.data / 'session_learn.log'
        log.write_text('old\n' * 10, encoding='utf-8')
        with patch.object(session_learn, 'LOG_LIMIT', 20), session_learn._log() as stream:
            stream.write('new\n')
        self.assertEqual(log.read_text(encoding='utf-8'), 'new\n')


if __name__ == '__main__':
    unittest.main()
