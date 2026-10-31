import sys

import numpy  # noqa: F401  Imported once: tests swap sys.modules.
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


# The requirements of a release after ADR 0086, as their locks list them.
LOCKS = {
    'base.lock.txt': 'numpy==2.5.4 \\\n    --hash=sha256:00\n    # via -r requirements/base.txt\nprotobuf==7.36.2\n',
    'cpu.lock.txt': 'flatbuffers==25.12.19\nmpmath==1.3.0\nnumpy==2.5.4\nonnxruntime==1.31.0\nsympy==1.14.0\n',
    'llm.lock.txt': 'llama-cpp-python==0.3.36\nnumpy==2.5.4\n',
}
GPU_BUILD = [  # onnxruntime-gpu[cuda,cudnn] as ADR 0038 installed it, with onnx for the CUDA check
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


# The GPU builds' locks shipped beside them since ADR 0122.
GPU_LOCKS = {
    'onnx-cuda.lock.txt': 'onnxruntime-gpu==1.31.0\n' + ''.join(f'{name}==1.0\n' for name, _, _ in NVIDIA),
    'onnx-directml.lock.txt': 'onnxruntime-directml==1.24.4\nsympy==1.14.0\n',
    'onnx-rocm.lock.txt': 'coloredlogs==15.0.1\nhumanfriendly==10.0\n',
}


class UnusedDistributionTests(unittest.TestCase):
    def unused(self, installed, build='cpu', locks=LOCKS):
        with tempfile.TemporaryDirectory() as directory:
            site, requirements = Path(directory, 'site'), Path(directory, 'requirements')
            site.mkdir()
            requirements.mkdir()
            for name, text in locks.items():
                (requirements / name).write_text(text)
            for name, requires, files in installed:
                _distribution(site, name, requires, files)
            from importlib.metadata import distributions
            return onnx_runtime.unused(requirements, list(distributions(path=[str(site)])), build)

    def test_the_cuda_build_goes_with_every_nvidia_library(self):
        # An installation whose embeddings ran on CUDA before ADR 0086. onnx,
        # which only setup's CUDA check used, is required by none of them.
        self.assertEqual(self.unused(SHARED + CPU_BUILD + GPU_BUILD + NVIDIA),
                         sorted(['onnxruntime-gpu'] + NVIDIA_NAMES))

    def test_what_something_else_requires_stays(self):
        user = [('torch', ['nvidia-cublas', 'nvidia-cuda-runtime', 'sympy'], ['torch/__init__.py']),
                ('kreuzberg', [], ['kreuzberg/__init__.py'])]
        unused = self.unused(SHARED + CPU_BUILD + GPU_BUILD + NVIDIA + user)
        for name in ('nvidia-cublas', 'nvidia-cuda-nvrtc', 'nvidia-cuda-runtime', 'torch', 'kreuzberg', 'sympy'):
            self.assertNotIn(name, unused)
        self.assertIn('nvidia-cudnn-cu13', unused)

    def test_the_cpu_build_alone_has_nothing_unused(self):
        self.assertEqual(self.unused(SHARED + CPU_BUILD), [])
        # NVIDIA libraries without an ONNX Runtime build that brought them in
        # belong to something else.
        self.assertEqual(self.unused(SHARED + CPU_BUILD + NVIDIA), [])

    def test_the_shipped_requirements_name_no_cuda_build_for_the_cpu(self):
        names = onnx_runtime._kept_requirements(onnx_runtime.REQUIREMENTS, 'cpu')
        self.assertIn('onnxruntime', names)
        self.assertFalse({name for name in names if name.startswith(('onnxruntime-', 'nvidia-'))})
        cuda = onnx_runtime._kept_requirements(onnx_runtime.REQUIREMENTS, 'cuda')
        self.assertTrue({'onnxruntime-gpu', 'nvidia-cudnn-cu13', 'nvidia-cublas'} <= cuda)
        self.assertNotIn('onnxruntime-directml', cuda)

    def test_the_chosen_gpu_build_and_its_libraries_stay(self):
        locks = {**LOCKS, **GPU_LOCKS}
        installed = SHARED + GPU_BUILD[:1] + NVIDIA + CPU_BUILD[1:]
        self.assertEqual(self.unused(installed, 'cuda', locks), [])
        # Falling back to the CPU build removes the CUDA build and its libraries.
        self.assertEqual(self.unused(installed, 'cpu', locks), sorted(['onnxruntime-gpu'] + NVIDIA_NAMES))
        # Another GPU build chosen: the CUDA one goes too.
        self.assertEqual(self.unused(installed, 'directml', locks), sorted(['onnxruntime-gpu'] + NVIDIA_NAMES))

    def test_the_rocm_build_is_kept_though_no_lock_names_it(self):
        migraphx = [('onnxruntime-migraphx', ['coloredlogs', 'numpy'], ['onnxruntime/__init__.py']),
                    ('coloredlogs', ['humanfriendly'], ['coloredlogs/__init__.py']),
                    ('humanfriendly', [], ['humanfriendly/__init__.py'])]
        locks = {**LOCKS, **GPU_LOCKS}
        self.assertEqual(self.unused(SHARED + migraphx, 'rocm', locks), [])
        self.assertEqual(self.unused(SHARED + migraphx, 'cpu', locks),
                         ['coloredlogs', 'humanfriendly', 'onnxruntime-migraphx'])


class BuildPlanTests(unittest.TestCase):
    """Which ONNX Runtime builds setup tries (ADR 0122). Hardware is faked."""

    def plan(self, system, machine='x86_64', profile='auto', nvidia='', rocm='', agent=False, adapters=()):
        with patch.object(onnx_runtime, 'nvidia_gpu', return_value=nvidia), \
             patch.object(onnx_runtime, 'amd_gpu', return_value=False), \
             patch.object(onnx_runtime, 'rocm_version', return_value=rocm), \
             patch.object(onnx_runtime, 'rocm_gpu', return_value=agent), \
             patch.object(onnx_runtime, 'directx12_gpus', return_value=list(adapters)):
            return [step['name'] for step in onnx_runtime.plan(profile, system, machine)]

    def test_nvidia_tries_cuda_then_the_cpu(self):
        self.assertEqual(self.plan('Linux', nvidia='GPU 0: RTX'), ['cuda', 'cpu'])
        self.assertEqual(self.plan('Windows', 'AMD64', nvidia='GPU 0: RTX', adapters=['RTX']),
                         ['cuda', 'directml', 'cpu'])

    def test_windows_without_nvidia_tries_directml_on_a_real_adapter(self):
        self.assertEqual(self.plan('Windows', 'AMD64', adapters=['AMD Radeon RX 7800 XT']), ['directml', 'cpu'])
        self.assertEqual(self.plan('Windows', 'AMD64'), ['cpu'])

    def test_amd_on_linux_needs_a_pinned_rocm_release_with_a_gpu_agent(self):
        self.assertEqual(self.plan('Linux', rocm='7.2.1', agent=True), ['rocm', 'cpu'])
        self.assertEqual(self.plan('Linux', rocm='7.2.1', agent=False), ['cpu'])
        steps = None
        with patch.object(onnx_runtime, 'nvidia_gpu', return_value=''), \
             patch.object(onnx_runtime, 'rocm_version', return_value='6.4.4'), \
             patch.object(onnx_runtime, 'amd_gpu', return_value=False), \
             patch.object(onnx_runtime, 'rocm_gpu', return_value=True):
            steps = onnx_runtime.plan('auto', 'Linux', 'x86_64')
        self.assertEqual([step['name'] for step in steps], ['cpu'])
        self.assertIn('ROCm 6.4.4 has no pinned ONNX Runtime build', steps[0]['reason'])
        self.assertEqual(self.plan('Linux'), ['cpu'])

    def test_an_amd_gpu_without_rocm_says_how_to_use_it(self):
        with patch.object(onnx_runtime, 'nvidia_gpu', return_value=''), \
             patch.object(onnx_runtime, 'rocm_version', return_value=''), \
             patch.object(onnx_runtime, 'amd_gpu', return_value=True):
            (step,) = onnx_runtime.plan('auto', 'Linux', 'x86_64')
        self.assertEqual(step['name'], 'cpu')
        self.assertIn('an AMD GPU needs ROCm 7.2.0 or', step['reason'])
        with tempfile.TemporaryDirectory() as directory:
            card = Path(directory, 'card0', 'device')
            card.mkdir(parents=True)
            (card / 'vendor').write_text('0x10de\n')
            self.assertFalse(onnx_runtime.amd_gpu(Path(directory)))
            (card / 'vendor').write_text('0x1002\n')
            self.assertTrue(onnx_runtime.amd_gpu(Path(directory)))

    def test_the_rocm_step_carries_its_pinned_wheel(self):
        with patch.object(onnx_runtime, 'nvidia_gpu', return_value=''), \
             patch.object(onnx_runtime, 'rocm_version', return_value='7.2.4'), \
             patch.object(onnx_runtime, 'rocm_gpu', return_value=True):
            wheel = onnx_runtime.plan('auto', 'Linux', 'x86_64')[0]['wheel']
        self.assertTrue(wheel['url'].startswith('https://repo.radeon.com/rocm/manylinux/rocm-rel-7.2.4/'))
        self.assertRegex(wheel['sha256'], '^[0-9a-f]{64}$')

    def test_the_cpu_profile_macs_and_arm_use_the_cpu_build(self):
        self.assertEqual(self.plan('Linux', profile='cpu', nvidia='GPU 0: RTX'), ['cpu'])
        self.assertEqual(self.plan('Darwin', 'arm64'), ['cpu'])  # Core ML is in the CPU wheel.
        self.assertEqual(self.plan('Linux', 'aarch64', nvidia='GPU 0: Orin'), ['cpu'])

    def test_the_rocm_release_comes_from_its_version_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(onnx_runtime.rocm_version(root), '')
            (root / '.info').mkdir()
            (root / '.info' / 'version').write_text('7.2.1-81\n')
            self.assertEqual(onnx_runtime.rocm_version(root), '7.2.1')
            (root / '.info' / 'version').write_text('7.2\n')
            self.assertEqual(onnx_runtime.rocm_version(root), '7.2.0')

    def test_a_rocm_gpu_agent_is_read_from_rocminfo(self):
        agents = 'Agent 1\n  Device Type:             CPU\nAgent 2\n  Device Type:             GPU\n'
        with patch.object(onnx_runtime, '_run', return_value=agents):
            self.assertTrue(onnx_runtime.rocm_gpu(Path('/opt/rocm')))
        with patch.object(onnx_runtime, '_run', return_value=agents.replace('GPU', 'CPU')):
            self.assertFalse(onnx_runtime.rocm_gpu(Path('/opt/rocm')))

    def test_software_display_adapters_do_not_count(self):
        names = 'Microsoft Basic Render Driver\nIntel(R) Arc(TM) Graphics\nMicrosoft Remote Display Adapter\n'
        with patch.object(onnx_runtime, '_run', return_value=names):
            self.assertEqual(onnx_runtime.directx12_gpus(), ['Intel(R) Arc(TM) Graphics'])

    def test_every_shipped_rocm_wheel_is_pinned_by_digest(self):
        wheels = onnx_runtime.rocm_wheels()
        self.assertTrue(wheels)
        for version, wheel in wheels.items():
            self.assertRegex(version, r'^\d+\.\d+\.\d+$')
            self.assertRegex(wheel['sha256'], '^[0-9a-f]{64}$')
            self.assertIn(f"/rocm-rel-{version.removesuffix('.0') if version.endswith('.0') else version}/",
                          wheel['url'])
            self.assertIn('onnxruntime_migraphx-', wheel['url'])


class BuildCheckTests(unittest.TestCase):
    """setup's check that the installed build runs its GPU provider."""

    def check(self, build, installed, available, first):
        import types
        made = []

        class Session:
            def __init__(self, model, sess_options=None, providers=()):
                made.append(providers)

            def get_providers(self):
                return [first, 'CPUExecutionProvider']

            def run(self, names, inputs):
                return [inputs['x'] * inputs['x']]
        ort = types.SimpleNamespace(get_available_providers=lambda: available, InferenceSession=Session,
                                    SessionOptions=lambda: types.SimpleNamespace(), preload_dlls=lambda: None)
        with patch.object(onnx_runtime, 'providers', return_value=installed), \
             patch.dict(sys.modules, {'onnxruntime': ort}):
            return onnx_runtime.check(build), made

    def test_a_gpu_build_must_load_its_provider(self):
        ok, made = self.check('cuda', {'onnxruntime-gpu': True}, ['CUDAExecutionProvider', 'CPUExecutionProvider'],
                              'CUDAExecutionProvider')
        self.assertTrue(ok['ok'])
        self.assertEqual(made[0][0], 'CUDAExecutionProvider')
        failed, _ = self.check('cuda', {'onnxruntime-gpu': True}, ['CUDAExecutionProvider', 'CPUExecutionProvider'],
                               'CPUExecutionProvider')
        self.assertEqual(failed, {'ok': False, 'detail': 'CUDAExecutionProvider did not load'})
        missing, _ = self.check('directml', {'onnxruntime-directml': True}, ['CPUExecutionProvider'], '')
        self.assertFalse(missing['ok'])

    def test_the_build_must_be_alone_and_complete(self):
        both, _ = self.check('cuda', {'onnxruntime': True, 'onnxruntime-gpu': True}, [], '')
        self.assertFalse(both['ok'])
        broken, _ = self.check('rocm', {'onnxruntime-migraphx': False}, [], '')
        self.assertFalse(broken['ok'])

    def test_the_tiny_model_is_a_valid_onnx_model(self):
        try:
            import onnxruntime as ort
        except ImportError:
            self.skipTest('onnxruntime is not installed')
        import numpy as np
        session = ort.InferenceSession(onnx_runtime._TINY_MODEL, providers=['CPUExecutionProvider'])
        x = np.array([[1, 2, 3, 4]], dtype=np.float32)
        self.assertTrue(np.allclose(session.run(None, {'x': x})[0], x * x))


if __name__ == '__main__':
    unittest.main()
