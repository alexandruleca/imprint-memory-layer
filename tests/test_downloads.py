import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from imprint.downloads import expected_digest, verify_archive
from imprint import qdrant_runner


class DownloadTests(unittest.TestCase):
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
