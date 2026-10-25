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


def _distribution(site: Path, name: str, requires=(), files=()):
    """An installed distribution with these requirements; its RECORD lists
    files, from which its package directories follow."""
    info = site / f"{name.replace('-', '_')}-1.0.dist-info"
    info.mkdir()
    lines = [f'Metadata-Version: 2.1', f'Name: {name}', 'Version: 1.0'] + [f'Requires-Dist: {r}' for r in requires]
    (info / 'METADATA').write_text('\n'.join(lines) + '\n')
    (info / 'RECORD').write_text(''.join(f'{path},,\n' for path in [*files, f'{info.name}/RECORD']))


# The requirements of a release after ADR 0062, as their locks list them.
LOCKS = {
    'base.lock.txt': 'numpy==2.5.4 \\\n    --hash=sha256:00\n    # via -r requirements/base.txt\nprotobuf==7.36.2\n',
    'cpu.lock.txt': 'flatbuffers==25.12.19\nmpmath==1.3.0\nnumpy==2.5.4\nonnxruntime==1.31.0\nsympy==1.14.0\n',
    'gpu.lock.txt': ('flatbuffers==25.12.19\nml-dtypes==0.6.0\nnumpy==2.5.4\nnvidia-cublas==13.8.0.4\nnvidia-cuda-nvrtc==13.4.92\n'
                     'nvidia-cuda-runtime==13.4.92\nnvidia-cudnn-cu13==9.27.0.42\n'
                     "nvidia-curand==10.4.4.72 ; sys_platform == 'linux'\nonnx==1.23.2\nonnxruntime-gpu==1.31.0\n"),
    'llm.lock.txt': 'llama-cpp-python==0.3.36\nnumpy==2.5.4\n',
}
GPU_BUILD = [  # onnxruntime-gpu[cuda,cudnn] as ADR 0038 installed it
    ('onnxruntime-gpu', ['flatbuffers', 'numpy>=1.21.6', 'protobuf', 'sympy; extra == "symbolic"',
                         'nvidia-cuda-runtime~=13.0; extra == "cuda"', 'nvidia-cufft~=12.0; extra == "cuda"',
                         'nvidia-curand~=10.0; extra == "cuda"', 'nvidia-cuda-nvrtc~=13.0; extra == "cuda"',
                         'nvidia-cudnn-cu13~=9.0; extra == "cudnn"'], ['onnxruntime/__init__.py']),
    ('onnx', ['numpy', 'protobuf', 'ml_dtypes'], ['onnx/__init__.py']),
    ('ml-dtypes', ['numpy'], ['ml_dtypes/__init__.py']),
]
NVIDIA = [
    ('nvidia-cublas', ['nvidia-cuda-nvrtc'], ['nvidia/cu13/lib/libcublas.so.13']),
    ('nvidia-cuda-nvrtc', [], ['nvidia/cu13/lib/libnvrtc.so.13']),
    ('nvidia-cuda-runtime', [], ['nvidia/cu13/lib/libcudart.so.13']),
    ('nvidia-cudnn-cu13', ['nvidia-cublas'], ['nvidia/cudnn/lib/libcudnn.so.9']),
    ('nvidia-cufft', ['nvidia-nvjitlink'], ['nvidia/cu13/lib/libcufft.so.12']),
    ('nvidia-curand', [], ['nvidia/cu13/lib/libcurand.so.10']),
    ('nvidia-nvjitlink', [], ['nvidia/cu13/lib/libnvJitLink.so.13']),
]
CPU_BUILD = [
    ('onnxruntime', ['flatbuffers', 'numpy>=1.21.6', 'protobuf', 'sympy; extra == "symbolic"'], ['onnxruntime/__init__.py']),
    ('sympy', ['mpmath'], ['sympy/__init__.py']),
    ('mpmath', [], ['mpmath/__init__.py']),
]
SHARED = [('numpy', [], ['numpy/__init__.py']), ('protobuf', [], ['google/protobuf/__init__.py']),
          ('flatbuffers', [], ['flatbuffers/__init__.py'])]
NVIDIA_NAMES = [name for name, _, _ in NVIDIA]


class UnusedDistributionTests(unittest.TestCase):
    def unused(self, selected, installed):
        with tempfile.TemporaryDirectory() as directory:
            site, requirements = Path(directory, 'site'), Path(directory, 'requirements')
            site.mkdir()
            requirements.mkdir()
            for name, text in LOCKS.items():
                (requirements / name).write_text(text)
            for name, requires, files in installed:
                _distribution(site, name, requires, files)
            from importlib.metadata import distributions
            return onnx_runtime.unused(selected, requirements, list(distributions(path=[str(site)])))

    def test_the_cpu_runtime_drops_the_gpu_build_and_every_nvidia_library(self):
        # The installation ADR 0038's gpu profile left with model.device=cpu.
        self.assertEqual(self.unused('cpu', SHARED + CPU_BUILD + GPU_BUILD + NVIDIA),
                         sorted(['ml-dtypes', 'onnx', 'onnxruntime-gpu'] + NVIDIA_NAMES))

    def test_libraries_an_older_lock_installed_go_after_their_build(self):
        # A CPU runtime that kept CUDA after setup switched away from the GPU
        # build: nothing requires cuFFT any more, but it shares nvidia/.
        self.assertEqual(self.unused('cpu', SHARED + CPU_BUILD + NVIDIA), NVIDIA_NAMES)

    def test_the_gpu_runtime_drops_what_its_lock_no_longer_names(self):
        installed = SHARED + GPU_BUILD + NVIDIA
        self.assertEqual(self.unused('gpu', installed), ['nvidia-cufft', 'nvidia-nvjitlink'])

    def test_the_gpu_runtime_drops_the_cpu_build(self):
        self.assertEqual(self.unused('gpu', SHARED + CPU_BUILD), ['mpmath', 'onnxruntime', 'sympy'])

    def test_what_something_else_requires_stays(self):
        user = [('torch', ['nvidia-cublas', 'nvidia-cuda-runtime', 'sympy'], ['torch/__init__.py']),
                ('kreuzberg', [], ['kreuzberg/__init__.py'])]
        unused = self.unused('cpu', SHARED + CPU_BUILD + GPU_BUILD + NVIDIA + user)
        for name in ('nvidia-cublas', 'nvidia-cuda-nvrtc', 'nvidia-cuda-runtime', 'torch', 'kreuzberg', 'sympy'):
            self.assertNotIn(name, unused)
        self.assertIn('nvidia-cudnn-cu13', unused)

    def test_a_matching_environment_has_nothing_unused(self):
        self.assertEqual(self.unused('cpu', SHARED + CPU_BUILD), [])
        self.assertEqual(self.unused('gpu', SHARED + GPU_BUILD + [d for d in NVIDIA if d[0] not in ('nvidia-cufft', 'nvidia-nvjitlink')]), [])

    def test_the_shipped_locks_name_the_trimmed_cuda_libraries(self):
        names = onnx_runtime.requirement_names(onnx_runtime.REQUIREMENTS / 'gpu.lock.txt')
        self.assertTrue({'onnxruntime-gpu', 'nvidia-cudnn-cu13', 'nvidia-cublas', 'nvidia-cuda-runtime', 'nvidia-curand'} <= names)
        # The CUDA execution provider loads neither for an embedding model (ADR 0062).
        self.assertFalse({'nvidia-cufft', 'nvidia-nvjitlink'} & names)


if __name__ == '__main__':
    unittest.main()
