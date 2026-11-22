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
                manifest = json.loads(archive.read('imprint-chrome-extension/manifest.json'))
                self.assertEqual(manifest['manifest_version'], 3)
                self.assertEqual(set(manifest['icons']), {'16', '32', '48', '128'})
                icons = {**manifest['icons'], **manifest['action']['default_icon']}
                for size, name in icons.items():
                    icon = archive.read('imprint-chrome-extension/' + name)
                    self.assertEqual(icon[:8], b'\x89PNG\r\n\x1a\n')
                    self.assertEqual(int.from_bytes(icon[16:20], 'big'), int(size))
                self.assertIn(b'Load unpacked', archive.read('imprint-chrome-extension/INSTALL.txt'))
                self.assertEqual(archive.read('imprint-chrome-extension/LICENSE'), (ROOT / 'LICENSE').read_bytes())
                self.assertIsNone(archive.testzip())
            digest = hashlib.sha256(output.read_bytes()).hexdigest()
            self.assertEqual(output.with_suffix('.zip.sha256').read_text(), f'{digest}  extension.zip\n')

    def test_firefox_addon_has_its_own_manifest_and_files_at_the_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = package.package(ROOT / 'extensions/chrome', root / 'addon.xpi', browser='firefox', version='v0.57.1')
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(set(archive.namelist()), set(package.FIREFOX_FILES))
                self.assertNotIn('INSTALL.txt', archive.namelist())
                manifest = json.loads(archive.read('manifest.json'))
            source = json.loads((ROOT / 'extensions/chrome/manifest.json').read_text())
            self.assertEqual(manifest['background'], {'scripts': ['background.js'], 'type': 'module'})
            self.assertEqual(manifest['options_ui'], {'page': 'options.html', 'open_in_tab': True})
            gecko = manifest['browser_specific_settings']['gecko']
            self.assertEqual((gecko['id'], gecko['strict_min_version']), (package.GECKO_ID, '140.0'))
            self.assertEqual(gecko['data_collection_permissions'], {'required': ['none']})
            self.assertEqual(manifest['browser_specific_settings']['gecko_android'], {'strict_min_version': '142.0'})
            for key in ('minimum_chrome_version', 'options_page', 'version_name'):
                self.assertNotIn(key, manifest)
            self.assertEqual(manifest['version'], '0.57.1')
            # Everything else, permissions included, is the Chrome extension's.
            for key in ('permissions', 'host_permissions', 'content_security_policy', 'action', 'commands', 'icons'):
                self.assertEqual(manifest[key], source[key])
            self.assertEqual(output.with_name('addon.xpi.sha256').read_text().split()[0],
                             hashlib.sha256(output.read_bytes()).hexdigest())

    def test_a_release_tag_becomes_the_version_both_browsers_show(self):
        self.assertEqual(package.release_version('v0.57.1'), ('0.57.1', '0.57.1'))
        self.assertEqual(package.release_version('v0.57.1-dev.2'), ('0.57.1.2', '0.57.1-dev.2'))
        self.assertEqual(package.release_version('v0.0.0-test'), ('0.0.0', '0.0.0-test'))
        self.assertEqual(package.release_version('v1.02.3'), ('1.2.3', '1.02.3'))
        for tag in ('', 'latest', 'v1.2', 'v1.2.3.4', 'v1.2.3-dev.x'):
            with self.assertRaises(ValueError):
                package.release_version(tag)
        with tempfile.TemporaryDirectory() as directory:
            output = package.package(ROOT / 'extensions/chrome', Path(directory) / 'chrome.zip', version='v0.57.1-dev.2')
            with zipfile.ZipFile(output) as archive:
                manifest = json.loads(archive.read('imprint-chrome-extension/manifest.json'))
            self.assertEqual((manifest['version'], manifest['version_name']), ('0.57.1.2', '0.57.1-dev.2'))
            self.assertEqual(manifest['background'], {'service_worker': 'background.js', 'type': 'module'})

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
