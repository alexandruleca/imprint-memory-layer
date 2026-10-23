import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from imprint import queue


def executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('')
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def same_path(a: str, b: Path) -> bool:
    # Windows paths are case-insensitive; PATH lookups report PATHEXT's case.
    return os.path.normcase(a) == os.path.normcase(str(b))


class ImprintBinaryTests(unittest.TestCase):
    def test_release_layout_wins_over_another_install_on_path(self):
        ext = '.exe' if os.name == 'nt' else ''
        with tempfile.TemporaryDirectory() as install, tempfile.TemporaryDirectory() as other:
            own = executable(Path(install) / 'bin' / f'imprint{ext}')
            executable(Path(other) / f'imprint{ext}')
            with patch.dict(os.environ, {'PATH': other}):
                self.assertTrue(same_path(queue.find_imprint_binary(Path(install)), own))

    def test_path_is_the_fallback_when_the_install_has_no_cli(self):
        ext = '.exe' if os.name == 'nt' else ''
        with tempfile.TemporaryDirectory() as install, tempfile.TemporaryDirectory() as other:
            on_path = executable(Path(other) / f'imprint{ext}')
            with patch.dict(os.environ, {'PATH': other}):
                self.assertTrue(same_path(queue.find_imprint_binary(Path(install)), on_path))


class VersionTests(unittest.TestCase):
    def setUp(self):
        from imprint import api
        self.api = api
        api._CACHED_VERSION = None
        self.addCleanup(setattr, api, '_CACHED_VERSION', None)

    def cli(self, **behaviour):
        lookup = patch.object(self.api.job_queue, 'find_imprint_binary', return_value='/install/bin/imprint')
        run = patch.object(self.api.subprocess, 'check_output', **behaviour)
        lookup.start()
        self.addCleanup(lookup.stop)
        return run

    def test_reports_the_installed_cli_version(self):
        with self.cli(return_value='imprint v9.9.9\n') as run:
            self.assertEqual(self.api.api_version(), {'version': 'v9.9.9'})
            self.assertEqual(self.api.api_version(), {'version': 'v9.9.9'})
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0], ['/install/bin/imprint', 'version'])

    def test_a_failed_lookup_is_not_cached(self):
        with self.cli(side_effect=FileNotFoundError('imprint')):
            self.assertEqual(self.api.api_version(), {'version': 'unknown'})
        with self.cli(return_value='imprint v9.9.9\n'):
            self.assertEqual(self.api.api_version(), {'version': 'v9.9.9'})


if __name__ == '__main__':
    unittest.main()
