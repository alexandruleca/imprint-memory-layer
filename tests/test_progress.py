"""The running job's progress record, which every surface reads (ADR 0089)."""
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


if __name__ == '__main__':
    unittest.main()
