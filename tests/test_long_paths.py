"""Paths beyond Windows' MAX_PATH are read through extended-length spellings (ADR 0147)."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from imprint import long_paths
from imprint.long_paths import os_path, plain

UNC = '\\\\wsl.localhost\\Ubuntu-26.04\\home\\alexa\\code\\deepstash\\mobile-apps\\apps\\deepstash\\components\\' \
      'progress-list\\components\\progress-list-suggest-achievement-item\\components\\' \
      'progress-list-suggest-achievement-item-button\\components\\progress-list-suggest-achievement-item-button-with-icon'


class SpellingTests(unittest.TestCase):
    def test_a_long_wsl_path_gets_the_unc_prefix_and_loses_it_again(self):
        self.assertEqual(len(UNC), 280)
        spelled = os_path(UNC, platform='win32')
        self.assertEqual(spelled, '\\\\?\\UNC\\' + UNC[2:])
        self.assertEqual(plain(spelled), UNC)

    def test_a_long_drive_path_gets_the_verbatim_prefix_normalised(self):
        path = 'C:\\' + '\\'.join(['segment'] * 40) + '\\.\\x.py'
        spelled = os_path(path.replace('\\', '/'), platform='win32')
        self.assertTrue(spelled.startswith('\\\\?\\C:\\segment\\'))
        self.assertNotIn('/', spelled)
        self.assertNotIn('\\.\\', spelled[4:])
        self.assertEqual(plain(spelled), 'C:\\' + '\\'.join(['segment'] * 40) + '\\x.py')

    def test_short_paths_prefixed_paths_and_other_platforms_are_unchanged(self):
        short = '\\\\wsl.localhost\\Ubuntu\\home\\a.py'
        self.assertEqual(os_path(short, platform='win32'), short)
        verbatim = '\\\\?\\UNC\\' + UNC[2:]
        self.assertEqual(os_path(verbatim, platform='win32'), verbatim)
        posix = '/home/' + 'a' * 300
        self.assertEqual(os_path(posix, platform='linux'), posix)
        self.assertEqual(os_path(Path(posix), platform='darwin'), posix)
        self.assertEqual(plain(posix), posix)


class _Mapped:
    """Stands in for Windows: the usual spelling under ``logical`` cannot be
    opened, only its spelling under ``real`` can, as a long path is only
    reachable through its \\\\?\\ form."""

    def __init__(self, logical, real):
        self.logical, self.real = logical, real

    def os_path(self, path, **_):
        text = os.fspath(path)
        return self.real + text[len(self.logical):] if text.startswith(self.logical) else text

    def plain(self, path):
        text = os.fspath(path)
        return self.logical + text[len(self.real):] if text.startswith(self.real) else text


class ReadingTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.real = os.path.realpath(directory.name)
        self.logical = '/unreachable-' + os.urandom(4).hex()
        self.mapped = _Mapped(self.logical, self.real)
        os.makedirs(os.path.join(self.real, 'deep', 'er'))
        Path(self.real, 'deep', 'er', 'app.py').write_text('x = 1\n')
        for target, name in ((long_paths, 'os_path'), (long_paths, 'plain')):
            patcher = patch.object(target, name, getattr(self.mapped, name))
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_scan_lists_through_the_long_spelling_and_returns_the_usual_one(self):
        from imprint import cli_index
        with patch.object(cli_index, 'os_path', self.mapped.os_path):
            files = cli_index.scan_dir(self.logical)
        self.assertEqual(files, [('deep/er/app.py', os.path.join(self.logical, 'deep', 'er', 'app.py'))])

    def test_fingerprint_reads_through_the_long_spelling_and_records_the_usual_one(self):
        from imprint import source_state
        path = os.path.join(self.logical, 'deep', 'er', 'app.py')
        with patch.object(source_state, 'os_path', self.mapped.os_path):
            state = source_state.fingerprint(path)
            self.assertEqual(state['source_path'], path)
            self.assertEqual(source_state.missing_sources({'kept': state}, [self.logical]), [])
            os.remove(os.path.join(self.real, 'deep', 'er', 'app.py'))
            self.assertEqual(source_state.missing_sources({'gone': state}, [self.logical]), ['gone'])


if __name__ == '__main__':
    unittest.main()
