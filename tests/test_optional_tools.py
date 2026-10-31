"""Optional ingestion tools install when their setting is on (ADR 0121)."""
import hashlib
import io
import subprocess
import sys
import tarfile
import tempfile
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from imprint import optional_tools as tools
from imprint.downloads import expected_digest
from imprint.extractors import ExtractedDoc, ExtractionError, ExtractorUnavailable


def done(code=0, out='', err=''):
    return subprocess.CompletedProcess([], code, out, err)


def release(*members):
    """A gzipped tar holding each (name, text) member."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as tar:
        for name, text in members:
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o755
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class ToolTestCase(unittest.TestCase):
    def setUp(self):
        tools._attempted.clear()
        tools._state.clear()
        self.addCleanup(tools._attempted.clear)
        self.addCleanup(tools._state.clear)


class KreuzbergInstallTests(ToolTestCase):
    def install(self, *results, uv='/opt/imprint/bin/uv'):
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            return results[len(calls) - 1]
        with patch.object(tools, '_kreuzberg_installed', return_value=False), \
                patch.object(tools, '_uv', return_value=uv), \
                patch('platform.system', return_value='Linux'), patch('platform.machine', return_value='x86_64'), \
                patch('subprocess.run', side_effect=run):
            try:
                tools.ensure('kreuzberg')
            except tools.InstallFailed as error:
                return calls, str(error)
        return calls, ''

    def test_installs_the_hashed_lock_into_this_python_with_wheels_only(self):
        calls, error = self.install(done(), done())
        self.assertEqual(error, '')
        install, check = calls
        self.assertEqual(install[:4], ['/opt/imprint/bin/uv', 'pip', 'install', '--python'])
        self.assertEqual(install[4], sys.executable)
        for flag in ('--require-hashes', '--only-binary', ':all:'):
            self.assertIn(flag, install)
        self.assertTrue(install[-1].endswith('kreuzberg.lock.txt'))
        self.assertEqual(check, [sys.executable, '-c', 'import kreuzberg'])

    def test_without_uv_the_venvs_pip_installs(self):
        calls, _ = self.install(done(), done(), uv=None)
        self.assertEqual(calls[0][:4], [sys.executable, '-m', 'pip', 'install'])

    def test_a_failed_install_says_why_and_how_to_install_by_hand(self):
        calls, error = self.install(done(1, err='No solution found\nno wheel for this platform'))
        self.assertEqual(len(calls), 1)
        self.assertIn('no wheel for this platform', error)
        self.assertIn('imprint bootstrap --non-interactive', error)
        self.assertEqual(tools.status('kreuzberg'), error)

    def test_an_install_that_does_not_load_fails(self):
        _, error = self.install(done(), done(1, err='ImportError: libfoo.so'))
        self.assertIn('does not load', error)

    def test_platforms_without_a_wheel_are_not_built_from_source(self):
        with patch.object(tools, '_kreuzberg_installed', return_value=False), \
                patch('platform.system', return_value='Linux'), patch('platform.machine', return_value='riscv64'), \
                patch('subprocess.run') as run:
            with self.assertRaisesRegex(tools.InstallFailed, 'no build for Linux riscv64'):
                tools.ensure('kreuzberg')
        run.assert_not_called()

    def test_the_lock_pins_plain_kreuzberg_with_hashes(self):
        lock = (tools.REQUIREMENTS / 'kreuzberg.lock.txt').read_text()
        self.assertIn('kreuzberg==4.10.4', lock)
        self.assertNotIn('torch', lock)
        self.assertIn('--hash=sha256:', lock)


@unittest.skipIf(sys.platform == 'win32', 'the fake obscura is a shell script')
class ObscuraInstallTests(ToolTestCase):
    def setUp(self):
        super().setUp()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.target = Path(directory.name) / 'obscura-bin'
        for patcher in (patch.object(tools, 'obscura_dir', return_value=self.target),
                        patch('shutil.which', return_value=None),
                        patch('platform.system', return_value='Linux'), patch('platform.machine', return_value='x86_64')):
            patcher.start()
            self.addCleanup(patcher.stop)

    def install(self, archive, digest=None):
        digest = digest or hashlib.sha256(archive).hexdigest()
        with patch.object(tools, 'expected_digest', return_value=digest), \
                patch('urllib.request.urlopen', return_value=io.BytesIO(archive)) as urlopen, \
                redirect_stderr(io.StringIO()):
            tools.ensure('obscura')
        return urlopen.call_args[0][0]

    def test_downloads_verifies_and_installs_the_cli_and_its_worker(self):
        url = self.install(release(('obscura', '#!/bin/sh\necho obscura 0.2.5\n'),
                                   ('obscura-worker', '#!/bin/sh\n'), ('README', 'not installed')))
        self.assertEqual(url, 'https://github.com/h4ckf0r0day/obscura/releases/download/v0.2.5/obscura-x86_64-linux.tar.gz')
        self.assertEqual(sorted(p.name for p in self.target.iterdir()), ['obscura', 'obscura-worker'])
        self.assertEqual(tools.obscura_path(), str(self.target / 'obscura'))
        self.assertEqual(tools.status('obscura'), '')

    def test_a_corrupted_download_installs_nothing(self):
        with self.assertRaisesRegex(tools.InstallFailed, 'Checksum mismatch'):
            self.install(release(('obscura', '#!/bin/sh\necho obscura\n'), ('obscura-worker', '')), digest='0' * 64)
        self.assertIsNone(tools.obscura_path())

    def test_a_binary_that_does_not_run_here_is_not_installed(self):
        with self.assertRaisesRegex(tools.InstallFailed, 'does not run here'):
            self.install(release(('obscura', '#!/bin/sh\nexit 1\n'), ('obscura-worker', '')))
        self.assertIsNone(tools.obscura_path())

    def test_an_archive_without_the_worker_is_rejected(self):
        with self.assertRaisesRegex(tools.InstallFailed, 'obscura-worker not found'):
            self.install(release(('obscura', '#!/bin/sh\necho obscura\n')))

    def test_every_supported_platform_has_a_pinned_asset(self):
        for asset in tools._OBSCURA_ASSETS.values():
            self.assertRegex(expected_digest('obscura', tools.OBSCURA_VERSION, asset), '^[0-9a-f]{64}$')
        with patch('platform.system', return_value='Darwin'), patch('platform.machine', return_value='x86_64'):
            self.assertIn('no build', tools.unsupported('obscura'))


class RepairOnceTests(ToolTestCase):
    def test_a_missing_tool_is_installed_once_per_process_and_failure_is_reported(self):
        with patch.object(tools, 'installed', return_value=False), \
                patch.object(tools, 'ensure', side_effect=tools.InstallFailed('offline')) as ensure:
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertFalse(tools.ensure_once('obscura'))
                self.assertFalse(tools.ensure_once('obscura'))
        ensure.assert_called_once_with('obscura')
        self.assertIn('Warning: offline', err.getvalue())

    def test_an_installed_tool_needs_nothing(self):
        with patch.object(tools, 'installed', return_value=True), patch.object(tools, 'ensure') as ensure:
            self.assertTrue(tools.ensure_once('kreuzberg'))
        ensure.assert_not_called()

    def test_setup_reports_each_enabled_tool(self):
        def ensure(tool):
            if tool == 'obscura':
                raise tools.InstallFailed('offline')
        out = io.StringIO()
        with patch.object(tools, 'enabled', return_value=True), patch.object(tools, 'ensure', side_effect=ensure), \
                redirect_stdout(out):
            self.assertEqual(tools.main(), 0)
        self.assertEqual(out.getvalue().splitlines(),
                         ['kreuzberg: enabled (installed)', 'obscura: enabled BUT offline'])


class SettingTests(ToolTestCase):
    def test_the_on_choice_says_why_the_tool_is_not_in_use(self):
        from imprint.config_schema import SETTINGS_BY_KEY
        with patch.object(tools, 'installed', return_value=False), patch.object(tools, 'unsupported', return_value=''), \
                patch.object(tools, 'enabled', return_value=True):
            on = SETTINGS_BY_KEY['ingest.use_kreuzberg'].options()[1]
            self.assertIn('not installed yet', on.unavailable)
            tools._state['kreuzberg'] = 'installing'
            self.assertIn('Installing kreuzberg', SETTINGS_BY_KEY['ingest.use_kreuzberg'].options()[1].unavailable)
        with patch.object(tools, 'installed', return_value=True), patch.object(tools, 'enabled', return_value=True):
            self.assertEqual(SETTINGS_BY_KEY['ingest.use_obscura'].options()[1].unavailable, '')

    def test_a_tool_that_is_off_and_missing_can_still_be_turned_on(self):
        # The dashboard disables an unavailable choice that is not the current value.
        from imprint.config_schema import SETTINGS_BY_KEY
        with patch.object(tools, 'installed', return_value=False), patch.object(tools, 'unsupported', return_value=''), \
                patch.object(tools, 'enabled', return_value=False):
            on = SETTINGS_BY_KEY['ingest.use_kreuzberg'].options()[1]
            self.assertEqual(on.unavailable, '')
            self.assertIn('Installs kreuzberg', on.detail)
        with patch.object(tools, 'unsupported', return_value='kreuzberg publishes no build for Darwin x86_64'), \
                patch.object(tools, 'enabled', return_value=False):
            self.assertIn('no build', SETTINGS_BY_KEY['ingest.use_kreuzberg'].options()[1].unavailable)

    def test_config_set_installs_the_tool_it_turns_on(self):
        from imprint import config_schema
        out = io.StringIO()
        with patch.object(config_schema, 'set_value', return_value=True), \
                patch.object(tools, 'installed', side_effect=[False, True]), \
                patch.object(tools, 'ensure') as ensure, redirect_stdout(out):
            config_schema.cli_set('ingest.use_obscura', 'true')
        ensure.assert_called_once_with('obscura')
        self.assertIn('obscura installed', out.getvalue())

    def test_config_set_off_installs_nothing(self):
        from imprint import config_schema
        with patch.object(config_schema, 'set_value', return_value=False), patch.object(tools, 'ensure') as ensure, \
                redirect_stdout(io.StringIO()):
            config_schema.cli_set('ingest.use_kreuzberg', 'false')
        ensure.assert_not_called()

    def test_the_dashboard_installs_in_the_background_when_turned_on(self):
        from imprint import api
        with patch.object(tools, 'start_background') as start:
            api._optional_tool_changed('ingest.use_kreuzberg', True)
            api._optional_tool_changed('ingest.use_obscura', False)
            api._optional_tool_changed('ingest.url_follow', True)
        start.assert_called_once_with('kreuzberg')

    def test_a_background_install_records_its_failure(self):
        with patch.object(tools, 'installed', return_value=False), \
                patch.object(tools, 'unsupported', return_value=''), \
                patch.object(tools, '_install_obscura', side_effect=tools.InstallFailed('offline')), \
                patch('threading.Thread') as thread:
            tools.start_background('obscura')
            self.assertIn('Installing obscura', tools.status('obscura'))
            thread.call_args.kwargs['target']()
            self.assertIn('offline', tools.status('obscura'))
            self.assertIn('imprint bootstrap', tools.status('obscura'))


class ExtractorTests(ToolTestCase):
    def test_kreuzberg_falls_back_to_the_native_extractor(self):
        from imprint.extractors import kreuzberg
        native = lambda path: ExtractedDoc(text='native', mime='', metadata={})
        for error in (ExtractorUnavailable('missing'), ExtractionError('Tesseract has no language data')):
            def extract(path, error=error):
                raise error
            self.assertEqual(kreuzberg._or_native(extract, native)('a.png').text, 'native')
        only = lambda path: (_ for _ in ()).throw(ExtractorUnavailable('missing'))
        with self.assertRaises(ExtractorUnavailable):
            kreuzberg._or_native(only, None)('a.zip')

    def test_kreuzberg_bytes_extraction_passes_the_mime_type(self):
        from imprint.extractors import kreuzberg
        calls = []
        result = types.SimpleNamespace(content='text', tables=[], metadata={'title': 'T'}, mime_type='text/html')
        fake = types.SimpleNamespace(extract_bytes_sync=lambda data, mime: calls.append(mime) or result)
        with patch.dict(sys.modules, {'kreuzberg': fake}):
            doc = kreuzberg._extract_bytes('text/html', b'<p>text</p>', 'https://example.com')
        self.assertEqual(calls, ['text/html'])
        self.assertEqual(doc.metadata['title'], 'T')

    def test_a_missing_kreuzberg_is_installed_once_then_reported(self):
        from imprint.extractors import kreuzberg
        with patch.dict(sys.modules, {'kreuzberg': None}), \
                patch.object(tools, 'ensure_once', return_value=False) as ensure_once, \
                patch.object(tools, 'status', return_value='offline'):
            with self.assertRaisesRegex(ExtractorUnavailable, 'offline'):
                kreuzberg._extract_file('a.zip')
        ensure_once.assert_called_once_with('kreuzberg')

    def test_obscura_installs_when_missing(self):
        from imprint.extractors import obscura
        paths = iter([None, '/data/obscura-bin/obscura'])
        with patch.object(tools, 'obscura_path', side_effect=lambda: next(paths)), \
                patch.object(tools, 'ensure_once', return_value=True) as ensure_once:
            self.assertEqual(obscura._obscura_bin(), '/data/obscura-bin/obscura')
        ensure_once.assert_called_once_with('obscura')


if __name__ == '__main__':
    unittest.main()
