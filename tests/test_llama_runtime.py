import contextlib
import ctypes
import gc
import inspect
import io
import sys
import tempfile
import unittest
import weakref
from pathlib import Path
from unittest.mock import MagicMock, patch

import llama_cpp

from imprint import config, llama_runtime
from imprint.llama_runtime import Gpu

# An RTX 4080 Laptop listed after a GTX 1060, as Vulkan enumerates them on the
# machine ADR 0086 measured.
GPUS = [Gpu(0, 'NVIDIA GeForce GTX 1060 6GB', 6245, device=0x1060), Gpu(1, 'NVIDIA GeForce RTX 4080 Laptop GPU', 11997, device=0x4080)]
# An AMD laptop's integrated GPU, with most of 128 GB of system memory, beside
# a GTX 1060 in an enclosure.
EGPU = [Gpu(0, 'NVIDIA GeForce GTX 1060 6GB', 6245, device=0x1060), Gpu(1, 'AMD Radeon(TM) 8060S Graphics', 98304, True, 0x8060)]


class ChooseTests(unittest.TestCase):
    def test_auto_takes_the_gpu_with_the_most_memory(self):
        self.assertEqual(llama_runtime.choose('auto', GPUS).index, 1)

    def test_an_index_names_a_gpu_or_none(self):
        self.assertEqual(llama_runtime.choose('0', GPUS).index, 0)
        self.assertIsNone(llama_runtime.choose('2', GPUS))
        self.assertIsNone(llama_runtime.choose('auto', []))

    def test_auto_takes_an_integrated_gpu_only_without_a_discrete_one(self):
        self.assertEqual(llama_runtime.choose('auto', EGPU).index, 0)
        self.assertEqual(llama_runtime.choose('auto', EGPU[1:]).index, 1)

    def test_an_index_names_an_integrated_gpu_beside_a_discrete_one(self):
        self.assertEqual(llama_runtime.choose('1', EGPU).name, 'AMD Radeon(TM) 8060S Graphics')


class CpuProfileTests(unittest.TestCase):
    def test_only_the_cpu_install_profile_explains_a_cpu_build(self):
        with tempfile.TemporaryDirectory() as data, patch.object(config, 'get_data_dir', return_value=Path(data)):
            self.assertFalse(llama_runtime.cpu_profile())
            for profile, expected in (('cpu', True), ('auto', False), ('gpu', False)):
                (Path(data) / 'profile.json').write_text(f'{{"profile": "{profile}"}}')
                self.assertEqual(llama_runtime.cpu_profile(), expected, profile)
            (Path(data) / 'profile.json').write_text('not json')
            self.assertFalse(llama_runtime.cpu_profile())


class PlacementTests(unittest.TestCase):
    def test_the_whole_model_goes_on_the_chosen_gpu(self):
        with patch.object(config, 'GPU_DEVICE', 'auto'), patch.object(llama_runtime, 'gpus', return_value=GPUS):
            self.assertEqual(llama_runtime.placement(-1),
                             {'n_gpu_layers': -1, 'split_mode': llama_cpp.LLAMA_SPLIT_MODE_NONE, 'main_gpu': 0,
                              'device': 0x4080})
        with patch.object(config, 'GPU_DEVICE', '1'), patch.object(llama_runtime, 'gpus', return_value=EGPU):
            self.assertEqual(llama_runtime.placement(-1)['device'], 0x8060)

    def test_no_layers_or_no_gpu_means_the_cpu(self):
        with patch.object(config, 'GPU_DEVICE', 'auto'), patch.object(llama_runtime, 'gpus', return_value=[]) as gpus:
            self.assertEqual(llama_runtime.placement(-1), {'n_gpu_layers': 0})
            self.assertEqual(llama_runtime.placement(0), {'n_gpu_layers': 0})
            gpus.assert_called_once()

    def test_the_installed_build_lists_the_gpus_it_offloads_to(self):
        # The CPU build on Windows and Linux; on macOS the Metal build, which
        # lists the Mac's GPU (ADR 0090).
        found = llama_runtime.gpus()
        if not llama_cpp.llama_supports_gpu_offload():
            self.assertEqual(found, [])
        self.assertEqual([gpu.index for gpu in found], list(range(len(found))))
        self.assertTrue(all(gpu.name and gpu.memory_mb > 0 and gpu.device for gpu in found), found)
        kinds = [gpu.integrated for gpu in found]
        self.assertEqual(kinds, sorted(kinds), 'discrete GPUs come first')

    def test_the_chosen_device_is_the_only_one_llama_cpp_loads_onto(self):
        from llama_cpp import llama_cpp as binding
        defaults = binding.llama_model_default_params
        seen = []

        def construct(**options):  # The list lives while the model loads, as llama.cpp copies it then.
            devices = binding.llama_model_default_params().devices
            seen.append(devices and tuple((ctypes.c_void_p * 2).from_address(devices)))
            return MagicMock()

        with patch.object(llama_cpp, 'Llama', side_effect=construct):
            llama_runtime._construct('/models/model.gguf', device=0x8060, n_gpu_layers=-1)
            llama_runtime._construct('/models/model.gguf', n_gpu_layers=0)
        self.assertEqual(seen, [(0x8060, None), None])  # NULL-terminated; no list for the CPU.
        self.assertIs(binding.llama_model_default_params, defaults)

    def test_llama_builds_its_params_from_the_bindings_defaults(self):
        # _offload_to relies on it: Llama does not take llama_model_params.devices.
        self.assertIn('llama_cpp.llama_model_default_params()', inspect.getsource(llama_cpp.Llama.__init__))
        self.assertIn('devices', dict(llama_cpp.llama_model_params._fields_))


class LoadTests(unittest.TestCase):
    def test_a_gpu_that_cannot_hold_the_model_leaves_it_on_the_cpu(self):
        model = MagicMock(verbose=True)
        constructor = MagicMock(side_effect=[ValueError('Failed to load model from file'), model])
        err = io.StringIO()
        with patch.object(llama_runtime, 'placement', return_value={'n_gpu_layers': -1, 'main_gpu': 1}), \
                patch.object(llama_cpp, 'Llama', constructor), contextlib.redirect_stderr(err):
            self.assertEqual(llama_runtime.load('/models/model.gguf', -1, n_ctx=8), (model, False))
        self.assertEqual(constructor.call_args.kwargs, {'model_path': '/models/model.gguf', 'verbose': True, 'n_ctx': 8, 'n_gpu_layers': 0})
        self.assertIn('model.gguf runs on the CPU', err.getvalue())
        self.assertFalse(model.verbose)

    def test_loading_prints_nothing_unless_it_fails(self):
        def chatty(**options):
            print('Model metadata: {...}\nUsing chat eos_token: <|endoftext|>', file=sys.stderr)
            if options['n_gpu_layers']:
                print('ggml_vulkan: failed to allocate 639 MB', file=sys.stderr)
                raise ValueError('Failed to create llama_context')
            return MagicMock()
        out, err = io.StringIO(), io.StringIO()
        with patch.object(llama_runtime, 'placement', return_value={'n_gpu_layers': -1}), \
                patch.object(llama_cpp, 'Llama', side_effect=chatty), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            llama_runtime.load('/models/model.gguf', -1)
        self.assertEqual(out.getvalue(), '')
        self.assertEqual(err.getvalue().count('Model metadata'), 1)  # From the failed load only.
        self.assertIn('failed to allocate 639 MB', err.getvalue())

    def test_models_still_loaded_at_exit_are_freed(self):
        # ggml's Metal device aborts the process at exit while a model holds
        # GPU memory, and Python does not free module-level models.
        with patch.object(llama_runtime, 'placement', return_value={'n_gpu_layers': 0}), \
                patch.object(llama_cpp, 'Llama', MagicMock(side_effect=lambda **options: MagicMock())), \
                patch.object(llama_runtime, '_loaded', weakref.WeakSet()):
            kept, _ = llama_runtime.load('/models/kept.gguf', 0)
            dropped_ref = weakref.ref(llama_runtime.load('/models/dropped.gguf', 0)[0])
            gc.collect()
            self.assertIsNone(dropped_ref(), 'freeing at exit must not keep a dropped model alive')
            llama_runtime._free_loaded()
        kept.close.assert_called_once_with()

    def test_a_model_the_cpu_cannot_load_fails(self):
        with patch.object(llama_runtime, 'placement', return_value={'n_gpu_layers': 0}), \
                patch.object(llama_cpp, 'Llama', MagicMock(side_effect=ValueError('corrupt'))):
            with self.assertRaisesRegex(ValueError, 'corrupt'):
                llama_runtime.load('/models/model.gguf', 0)

    def test_llama_cpp_logs_only_errors_and_only_to_stderr(self):
        llama_runtime._quiet_logs()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            llama_runtime._log_callback(1, b'ggml_vulkan: Found 3 Vulkan devices\n', None)
            llama_runtime._log_callback(3, b'failed to allocate buffer\n', None)
        self.assertEqual(out.getvalue(), '')
        self.assertEqual(err.getvalue(), 'failed to allocate buffer\n')


if __name__ == '__main__':
    unittest.main()
