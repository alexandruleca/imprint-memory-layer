import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from imprint import onnx_runtime


def _install(site: Path, distribution: str, files: list[str]) -> None:
    info = site / f"{distribution.replace('-', '_')}-1.31.0.dist-info"
    info.mkdir()
    (info / 'METADATA').write_text(f'Metadata-Version: 2.1\nName: {distribution}\nVersion: 1.31.0\n')
    (info / 'top_level.txt').write_text('onnxruntime\n')
    (info / 'RECORD').write_text(''.join(f'{path},,\n' for path in files + [f'{info.name}/RECORD']))


class OnnxRuntimeProviderTests(unittest.TestCase):
    def test_reports_each_provider_and_detects_deleted_shared_files(self):
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory)
            (site / 'onnxruntime' / 'capi').mkdir(parents=True)
            (site / 'onnxruntime' / '__init__.py').write_text('')
            (site / 'onnxruntime' / 'capi' / 'providers_cuda.so').write_text('')
            _install(site, 'onnxruntime', ['onnxruntime/__init__.py'])
            # The state left after uninstalling the CPU build over the GPU
            # build: shared files are gone, GPU-only files remain.
            _install(site, 'onnxruntime-gpu', ['onnxruntime/__init__.py', 'onnxruntime/capi/providers_cuda.so'])
            (site / 'onnxruntime' / '__init__.py').unlink()
            with patch.object(sys, 'path', [str(site)]):
                self.assertEqual(onnx_runtime.providers(), {'onnxruntime': False, 'onnxruntime-gpu': False})
                (site / 'onnxruntime' / '__init__.py').write_text('')
                self.assertEqual(onnx_runtime.providers(), {'onnxruntime': True, 'onnxruntime-gpu': True})

    def test_no_provider(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(sys, 'path', [directory]):
            self.assertEqual(onnx_runtime.providers(), {})


if __name__ == '__main__':
    unittest.main()
