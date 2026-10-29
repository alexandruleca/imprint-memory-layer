import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from imprint.downloads import expected_digest, verify_archive
from imprint import qdrant_runner


class DownloadTests(unittest.TestCase):
    def test_bundled_uv_version_and_every_packaged_target_have_pinned_checksums(self):
        root = Path(__file__).resolve().parents[1]
        entry = json.loads((root / 'requirements/runtime-manifest.json').read_text())['uv']
        version = entry['version']
        for name in ('Makefile', 'scripts/fetch-uv.sh', 'scripts/fetch-uv.ps1'):
            self.assertIn(version, (root / name).read_text(), name)
        for target, suffix in (
            ('x86_64-unknown-linux-gnu', 'tar.gz'), ('aarch64-unknown-linux-gnu', 'tar.gz'),
            ('aarch64-apple-darwin', 'tar.gz'),
            ('x86_64-pc-windows-msvc', 'zip'), ('aarch64-pc-windows-msvc', 'zip'),
        ):
            digest = expected_digest('uv', version, f'uv-{target}.{suffix}')
            self.assertRegex(digest, r'^[0-9a-f]{64}$')

    def test_corrupted_download_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / 'asset.zip'
            archive.write_bytes(b'valid artifact')
            verify_archive(archive, hashlib.sha256(b'valid artifact').hexdigest())
            archive.write_bytes(b'corrupted artifact')
            with self.assertRaisesRegex(ValueError, 'Checksum mismatch'):
                verify_archive(archive, hashlib.sha256(b'valid artifact').hexdigest())

    def test_unknown_version_never_silently_downloads(self):
        with self.assertRaisesRegex(ValueError, 'No pinned checksum'):
            expected_digest('qdrant', 'unverified', 'asset.tar.gz')

    def test_linux_arm_asset_exists_in_pinned_manifest(self):
        with patch('platform.system', return_value='Linux'), patch('platform.machine', return_value='aarch64'):
            asset = qdrant_runner._platform_asset()
        self.assertIn('musl', asset)
        self.assertEqual(len(expected_digest('qdrant', qdrant_runner.QDRANT_VERSION, asset)), 64)

    def test_intel_mac_has_no_qdrant_asset(self):
        # Intel Macs are not supported (ADR 0111).
        with patch('platform.system', return_value='Darwin'), patch('platform.machine', return_value='x86_64'):
            with self.assertRaisesRegex(RuntimeError, 'Intel Macs are not supported'):
                qdrant_runner._platform_asset()
        with patch('platform.system', return_value='Darwin'), patch('platform.machine', return_value='arm64'):
            asset = qdrant_runner._platform_asset()
        self.assertEqual(len(expected_digest('qdrant', qdrant_runner.QDRANT_VERSION, asset)), 64)
