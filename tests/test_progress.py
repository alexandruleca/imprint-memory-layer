"""The running job's progress record, which every surface reads (ADR 0089)."""
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from imprint import progress


class ProgressRecordTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data = Path(directory.name)
        environment = patch.dict(os.environ, {'IMPRINT_DATA_DIR': str(self.data)})
        environment.start()
        self.addCleanup(environment.stop)

    def test_a_job_says_what_it_counts_and_the_record_goes_when_it_ends(self):
        progress.write_progress('learn', 3, 112, 40, 0, time.time(), [], phase='reading', unit='conversations')
        record = progress.read_progress()
        self.assertEqual((record['command'], record['processed'], record['total'], record['unit']), ('learn', 3, 112, 'conversations'))
        self.assertTrue((self.data / 'progress.json').is_file())
        progress.clear_progress()
        self.assertIsNone(progress.read_progress())

    def test_the_record_of_an_ended_writer_is_not_read(self):
        (self.data / 'progress.json').write_text(json.dumps({'pid': 1 << 30, 'command': 'setup', 'phase': 'installing llama.cpp',
                                                              'processed': 1, 'total': 3, 'unit': 'steps'}))
        self.assertIsNone(progress.read_progress())

    def test_the_queue_reports_the_unit_and_files_for_a_record_without_one(self):
        # Go setup writes the same record; older Python writers had no unit.
        from imprint import api
        record = {'pid': os.getpid(), 'command': 'setup', 'phase': 'installing llama.cpp', 'processed': 1, 'total': 3,
                  'unit': 'steps', 'stored': 0, 'skipped': 0, 'started_at': time.time() - 10, 'projects': []}
        merged = api._merge_progress({'id': 'cli-1', 'command': 'setup'}, record)
        self.assertEqual((merged['phase'], merged['processed'], merged['total'], merged['unit']), ('installing llama.cpp', 1, 3, 'steps'))
        del record['unit']
        self.assertEqual(api._merge_progress({'id': 'cli-1', 'command': 'ingest'}, record)['unit'], 'files')



class Terminal(io.StringIO):
    def isatty(self):
        return True


class TaskTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data = Path(directory.name)
        environment = patch.dict(os.environ, {'IMPRINT_DATA_DIR': str(self.data)})
        environment.start()
        self.addCleanup(environment.stop)

    def test_without_a_terminal_a_line_per_phase_and_tenth_not_per_item(self):
        log = io.StringIO()
        with progress.Task('retag', phase='llm_tagging', unit='memories', total=1000, stream=log) as task:
            for _ in range(1000):
                task.advance(stored=1)
            record = progress.read_progress()
            task.update(0, total=10, phase='embedding', unit='chunks')
            task.finish('retag: 1,000 memories tagged')
        lines = log.getvalue().splitlines()
        self.assertNotIn('\r', log.getvalue())
        self.assertLessEqual(len(lines), 14, lines)  # start, ten tenths, the new phase, the summary
        self.assertEqual(lines[0], '  retag: tagging · 0/1,000 memories · 0% · 0s')
        self.assertIn('  retag: tagging · 1,000/1,000 memories · 100%', log.getvalue())
        self.assertIn('  retag: embedding · 0/10 chunks · 0%', log.getvalue())
        self.assertTrue(lines[-1].startswith('  retag: 1,000 memories tagged ('))
        self.assertEqual((record['phase'], record['unit'], record['total']), ('llm_tagging', 'memories', 1000))
        self.assertIsNone(progress.read_progress())  # the record goes when the work ends

    def test_on_a_terminal_one_line_redrawn_in_place_then_a_summary(self):
        screen = Terminal()
        with patch.object(progress.Task, 'REDRAW', 0), \
             progress.Task('index', phase='reading', unit='files', total=4, stream=screen) as task:
            task.advance(detail='notes.md')
            task.finish()
        out = screen.getvalue()
        self.assertIn('\r\x1b[K  index: reading  ', out)
        self.assertIn('█', out)
        self.assertEqual(out.count('\n'), 1)  # only the summary ends a line
        self.assertTrue(out.rstrip('\n').endswith(')'))
        self.assertIn('  index: 1/4 files (', out)

    def test_a_command_without_the_queue_lock_writes_no_record(self):
        with progress.Task('migrate', unit='memories', total=5, record=False, stream=io.StringIO()) as task:
            task.advance(5)
            self.assertFalse((self.data / 'progress.json').exists())

    def test_an_error_inside_the_work_still_drops_the_record_and_the_line(self):
        screen = Terminal()
        with self.assertRaises(RuntimeError):
            with progress.Task('scrub', unit='memories', total=3, stream=screen):
                raise RuntimeError('disk full')
        self.assertIsNone(progress.read_progress())
        self.assertTrue(screen.getvalue().endswith('\r\x1b[K'))

    def test_work_of_unknown_size_counts_without_a_percentage(self):
        log = io.StringIO()
        with progress.Task('ingest-url', unit='pages', stream=log) as task:
            task.advance(3)
            self.assertIn('ingest-url · 3 pages · ', task.line())
            self.assertNotIn('%', task.line())


    def test_each_phase_logs_its_own_tenths(self):
        log = io.StringIO()
        with progress.Task('ingest', phase='comparing', unit='files', total=2, record=False, stream=log) as task:
            task.update(2)
            task.update(0, total=10, phase='indexing')
            task.update(5)
        self.assertIn('ingest: indexing · 5/10 files · 50%', log.getvalue())


if __name__ == '__main__':
    unittest.main()
