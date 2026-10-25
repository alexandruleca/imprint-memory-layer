"""The knowledge graph's shared per-workspace SQLite connection."""
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from imprint import config, imprint_graph as kg


class SharedGraphConnectionTests(unittest.TestCase):
    def test_concurrent_fact_reads_on_the_shared_connection_all_succeed(self):
        """Concurrent kg_query steps once returned no facts or raised
        "bad parameter or other API misuse" on the shared connection."""
        subjects = [f'subject-{i}' for i in range(8)]
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(config, 'get_data_dir', return_value=Path(directory)), \
             patch.dict(kg._conns, clear=True):
            try:
                with kg._connection('alpha') as conn, conn:
                    conn.executemany('INSERT INTO facts (subject, predicate, object, valid_from) VALUES (?, ?, ?, ?)',
                                     [(subject, 'uses', 'qdrant', 1.0) for subject in subjects])
                barrier = threading.Barrier(len(subjects))
                outcomes = []

                def read(subject):
                    barrier.wait()
                    for _ in range(40):
                        try:
                            outcomes.append(len(kg.query(subject=subject, workspace='alpha')))
                        except Exception as error:
                            outcomes.append(repr(error))
                threads = [threading.Thread(target=read, args=(subject,)) for subject in subjects]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()
                self.assertEqual(outcomes, [1] * 320)
                self.assertEqual(kg.count(workspace='alpha'), 8)
            finally:
                kg._close_all()


if __name__ == '__main__':
    unittest.main()
