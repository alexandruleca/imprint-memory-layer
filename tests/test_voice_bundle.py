"""Compiler-free setup must retain the same source/patch/binary checks."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from imprint import voice_bundle as bundle, voice_runtime as runtime, voice_setup as setup
from imprint.voice_memory import VoiceMemoryError


class VoiceBundleTests(unittest.TestCase):
    def fixture(self, root):
        archive = root / 'live-source.tar.gz'
        with tarfile.open(archive, 'w:gz') as output:
            item = tarfile.TarInfo(f'llama.cpp-{runtime.SOURCE_REVISION}/tools/liquid-audio/server.cpp')
            item.size = len(b'original')
            output.addfile(item, io.BytesIO(b'original'))
        binary = runtime.native_binary(root, 'linux-x64')
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b'native fixture'); binary.chmod(0o700)
        (root / 'live-build.json').write_text(json.dumps(dict(
            source_revision=runtime.SOURCE_REVISION, patch_version=runtime.PATCH_VERSION,
            build_version=runtime.BUILD_VERSION, target='linux-x64',
            binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
            patch_sha256=hashlib.sha256(b'patched').hexdigest())))
        for mock in [patch.object(runtime, 'target', return_value='linux-x64'),
                     patch.object(runtime, 'SOURCE_SHA256', hashlib.sha256(archive.read_bytes()).hexdigest()),
                     patch.object(runtime, 'patched_server', return_value='patched')]:
            mock.start(); self.addCleanup(mock.stop)
        return binary

    def test_export_has_no_weights_and_installs_with_no_tools_or_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); cache = root / 'builder'; cache.mkdir()
            binary = self.fixture(cache)
            (cache / 'private.gguf').write_bytes(b'not for distribution')
            (cache / 'live-build.json').chmod(0o600)
            shipped = root / 'bundle'; bundle.export_engine(cache, shipped)
            self.assertTrue(all(p.stat().st_mode & 0o004 for p in shipped.rglob('*') if p.is_file()))
            self.assertFalse(list(shipped.rglob('*.gguf')))
            installed = root / 'fresh'; installed.mkdir(); events = []
            with patch.object(bundle, 'bundled_root', return_value=shipped), \
                    patch.object(bundle.lfm, 'install_weights') as weights, \
                    patch.object(runtime.lfm, 'verify_weights'), \
                    patch.object(runtime.subprocess, 'run') as compile_process, \
                    patch.object(runtime.urllib.request, 'urlopen') as network, \
                    patch.object(setup.shutil, 'which', return_value=None):
                self.assertIsNone(setup.prerequisites(installed))
                bundle.install(installed, progress=events.append, download_prefix='.voice-test-')
                weights.assert_called_once(); compile_process.assert_not_called(); network.assert_not_called()
                self.assertEqual(runtime.verify(installed).read_bytes(), binary.read_bytes())
                self.assertTrue(runtime.installed(installed))
            self.assertTrue((installed / 'NOTICE.md').is_file())
            self.assertTrue((installed / 'licenses/gcc/COPYING.RUNTIME').is_file())
            self.assertFalse(list(installed.glob('*.part')))
            self.assertEqual([e['stage'] for e in events], ['preparing', 'finishing'])

    def test_wrong_target_and_tamper_fail_before_model_download(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); self.fixture(root)
            shipped = root / 'shipped'; bundle.export_engine(root, shipped)
            with patch.object(bundle, 'bundled_root', return_value=shipped), \
                    patch.object(bundle.lfm, 'install_weights') as weights:
                runtime.native_binary(shipped).write_bytes(b'tampered')
                with self.assertRaises(VoiceMemoryError): bundle.install(root / 'fresh')
                weights.assert_not_called()
            with self.assertRaises(VoiceMemoryError): runtime.verify_engine(root, 'macos-arm64')
            metadata = json.loads((root / 'live-build.json').read_text())
            metadata['build_version'] -= 1
            (root / 'live-build.json').write_text(json.dumps(metadata))
            self.assertFalse(runtime.installed(root))
            (shipped / 'live-build.json').unlink()
            with patch.object(bundle, 'bundled_root', return_value=shipped):
                self.assertTrue(bundle.available())
                with self.assertRaises(VoiceMemoryError): setup.prerequisites(root)


if __name__ == '__main__':
    unittest.main()
