import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('chrome_package', ROOT / 'scripts/package_chrome_extension.py')
package = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(package)


class ChromePackageTests(unittest.TestCase):
    def test_archive_contains_only_runtime_files_and_standalone_install_guide(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            shutil.copytree(ROOT / 'extensions/chrome', source)
            (source / 'settings.json').write_text('{"token":"do-not-distribute"}')
            (source / 'node_modules').mkdir()
            (source / 'node_modules/dependency.js').write_text('development-only')
            output = package.package(source, root / 'extension.zip')
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(set(archive.namelist()), {'imprint-chrome-extension/' + name for name in package.FILES})
                self.assertEqual(json.loads(archive.read('imprint-chrome-extension/manifest.json'))['manifest_version'], 3)
                self.assertIn(b'Load unpacked', archive.read('imprint-chrome-extension/INSTALL.txt'))
                self.assertIsNone(archive.testzip())
            digest = hashlib.sha256(output.read_bytes()).hexdigest()
            self.assertEqual(output.with_suffix('.zip.sha256').read_text(), f'{digest}  extension.zip\n')

    def test_zip_is_reproducible_across_source_timestamps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            shutil.copytree(ROOT / 'extensions/chrome', source)
            first = package.package(source, root / 'first.zip').read_bytes()
            for name in package.FILES:
                os.utime(source / name, (1_700_000_000, 1_700_000_000))
            self.assertEqual(first, package.package(source, root / 'second.zip').read_bytes())

    def test_missing_runtime_file_preserves_previous_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            shutil.copytree(ROOT / 'extensions/chrome', source)
            output = package.package(source, root / 'extension.zip')
            previous = output.read_bytes()
            (source / 'background.js').unlink()
            with self.assertRaisesRegex(ValueError, 'background.js'):
                package.package(source, output)
            self.assertEqual(previous, output.read_bytes())


if __name__ == '__main__':
    unittest.main()
