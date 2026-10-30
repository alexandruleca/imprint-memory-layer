"""URL commands report through the shared progress Task (ADR 0089)."""
import contextlib
import io
import os
import tempfile
import unittest
from unittest.mock import patch

from imprint import cli_ingest_url, cli_refresh_urls, progress


class UrlProgressTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {'IMPRINT_DATA_DIR': temp.name})
        env.start(); self.addCleanup(env.stop)

    def run_main(self, module, argv):
        output = io.StringIO()
        with patch('sys.argv', argv), contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            module.main()
        return output.getvalue()

    def test_refresh_urls_writes_the_record_and_keeps_a_line_per_url(self):
        known = {'https://a.example/': {'project': 'docs', 'etag': 'same'},
                 'https://b.example/': {'project': 'docs', 'etag': 'old'}}
        seen = []

        def ingest(url, project, known, force=False):
            seen.append(progress.read_progress())
            return 3, 'stored'

        with patch.object(cli_refresh_urls.vs, 'get_url_sources', return_value=known), \
                patch.object(cli_refresh_urls.url_ext, 'head_check',
                             side_effect=lambda url: {'etag': 'same' if 'a.' in url else 'new'}), \
                patch.object(cli_refresh_urls, 'ingest_one', side_effect=ingest):
            output = self.run_main(cli_refresh_urls, ['refresh-urls'])
        record = seen[0]
        self.assertEqual((record['command'], record['unit'], record['total']), ('refresh-urls', 'urls', 2))
        self.assertEqual(record['processed'], 1)  # The unchanged URL was counted.
        self.assertIn('= https://a.example/  (unchanged)', output)
        self.assertIn('https://b.example/  (re-indexed, 3 chunks)', output)
        self.assertIn('URL Refresh Complete', output)
        self.assertIn('Updated:   1', output)
        self.assertIsNone(progress.read_progress())

    def test_ingest_url_total_grows_with_discovered_links(self):
        seen = []

        def ingest(url, project, known, force=False):
            seen.append(progress.read_progress())
            return 2, 'stored'

        links = {'https://site.example/': ['https://site.example/a', 'https://site.example/b']}
        with patch.object(cli_ingest_url.vs, 'get_url_sources', return_value={}), \
                patch.object(cli_ingest_url, 'ingest_one', side_effect=ingest), \
                patch.object(cli_ingest_url.url_ext, 'extract_links', side_effect=lambda url: links.get(url, [])), \
                patch('imprint.tag_backlog.report'), \
                patch.object(progress.Task, 'RECORD_EVERY', 0):
            output = self.run_main(cli_ingest_url, ['ingest-url', 'https://site.example/', '--follow',
                                                    '--follow-depth', '1'])
        self.assertEqual([record['total'] for record in seen], [1, 3, 3])
        self.assertEqual(seen[-1]['unit'], 'pages')
        self.assertIn('discovered 2 link(s)', output)
        self.assertIn('https://site.example/b  (2 chunks)', output)
        self.assertIn('Stored:   6 chunks', output)
        self.assertIsNone(progress.read_progress())


if __name__ == '__main__':
    unittest.main()
