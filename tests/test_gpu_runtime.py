"""Which ONNX Runtime build embeddings need (ADR 0062)."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from imprint import config_schema, cuda_devices, gpu_runtime, queue

RTX = cuda_devices.Device(0, 'NVIDIA GeForce RTX 4080 Laptop GPU', (8, 9), 12281)
GTX = cuda_devices.Device(1, 'NVIDIA GeForce GTX 1060 6GB', (6, 1), 6143)
PASCAL_FIRST = [cuda_devices.Device(0, GTX.name, GTX.compute, GTX.memory_mb),
                cuda_devices.Device(1, RTX.name, RTX.compute, RTX.memory_mb)]
LINUX, WINDOWS = ('linux', 'x86_64'), ('win32', 'amd64')


def decide(requested, gpu_setting='auto', platform_key=LINUX, cuda_major=13, cuda_version=(13, 0),
           devices=(RTX, GTX), failure=lambda device: ''):
    return gpu_runtime.decide(requested, gpu_setting, platform_key=platform_key, cuda_major=cuda_major,
                              cuda_version=cuda_version, devices=list(devices), failure=failure)


@contextlib.contextmanager
def machine(devices=(RTX, GTX), cuda_version=(13, 0), platform_key=LINUX, providers=None, major=13):
    """This machine's GPUs, driver, platform and installed ONNX Runtime."""
    with patch.multiple(cuda_devices, devices=lambda: list(devices), cuda_version=lambda: cuda_version,
                        driver_version=lambda: '581.80', runtime_major=lambda *a: major), \
         patch.object(gpu_runtime, 'platform_key', return_value=platform_key), \
         patch('imprint.onnx_runtime.providers', return_value=providers or {'onnxruntime': True}):
        yield


class DataDirTestCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data = Path(directory.name)
        environment = patch.dict(os.environ, {'IMPRINT_DATA_DIR': directory.name})
        environment.start()
        self.addCleanup(environment.stop)
        for name in ('IMPRINT_DEVICE', 'IMPRINT_GPU_DEVICE'):
            os.environ.pop(name, None)


class DecisionTests(unittest.TestCase):
    def test_the_cpu_device_never_needs_cuda(self):
        plan = decide('cpu')
        self.assertEqual((plan.runtime, plan.reason), ('cpu', 'model.device is cpu'))
        # An unvalidated environment value embeds on the CPU, so it needs no CUDA either.
        self.assertEqual(decide('cuda').runtime, 'cpu')

    def test_gpu_and_auto_need_cuda_for_a_supported_nvidia_gpu(self):
        for requested in ('gpu', 'auto'):
            for platform_key in (LINUX, WINDOWS, ('linux', 'aarch64')):
                plan = decide(requested, platform_key=platform_key)
                self.assertEqual((plan.runtime, plan.device, plan.gpu), ('gpu', 0, RTX.name), (requested, platform_key))
                self.assertEqual(plan.reason, f'{RTX.name}, CUDA 13')

    def test_machines_without_a_usable_nvidia_gpu_stay_on_the_cpu(self):
        cases = {
            # AMD and Intel GPUs have no CUDA driver: the driver API finds nothing.
            'no NVIDIA GPU found': dict(cuda_version=None, devices=()),
            'Imprint has no CUDA build for macOS on arm64': dict(platform_key=('darwin', 'arm64'), cuda_version=None, devices=()),
            'Imprint has no CUDA build for Windows on arm64': dict(platform_key=('win32', 'arm64')),
            'the NVIDIA driver supports CUDA 12.8 and the GPU runtime needs CUDA 13: update the driver': dict(cuda_version=(12, 8)),
            f'CUDA 13 needs compute capability 7.5 or newer; {GTX.name} has 6.1': dict(devices=(GTX,)),
            "the GPU runtime's requirements are missing from this installation": dict(cuda_major=None),
        }
        for reason, facts in cases.items():
            for requested in ('gpu', 'auto'):
                plan = decide(requested, **facts)
                self.assertEqual((plan.runtime, plan.reason, plan.device), ('cpu', reason, None), facts)

    def test_auto_skips_an_unsupported_gpu_enumerated_first(self):
        plan = decide('auto', devices=PASCAL_FIRST)
        self.assertEqual((plan.runtime, plan.device), ('gpu', 1))

    def test_an_explicit_gpu_device_decides_alone(self):
        self.assertEqual(decide('gpu', '0').device, 0)
        unsupported = decide('gpu', '1')
        self.assertEqual((unsupported.runtime, unsupported.reason),
                         ('cpu', f'CUDA 13 needs compute capability 7.5 or newer; model.gpu_device 1, {GTX.name} has 6.1'))
        missing = decide('gpu', '3')
        self.assertEqual((missing.runtime, missing.reason), ('cpu', 'model.gpu_device is 3 and there is no CUDA device 3'))

    def test_an_unknown_cuda_major_trusts_every_gpu(self):
        self.assertEqual(decide('gpu', devices=(GTX,), cuda_major=14, cuda_version=(14, 0)).device, 1)

    def test_a_recorded_failure_keeps_the_cpu_until_a_retry(self):
        plan = decide('gpu', failure=lambda device: f'cuDNN missing on {device.name}')
        self.assertEqual(plan.runtime, 'cpu')
        self.assertEqual(plan.reason, f'CUDA failed on this machine before (cuDNN missing on {RTX.name}); '
                                      'imprint setup --retry-gpu tries again')


class PlanTests(DataDirTestCase):
    def test_the_cpu_device_probes_no_hardware(self):
        with patch.object(cuda_devices, 'devices', side_effect=AssertionError('probed')), \
             patch.object(cuda_devices, 'cuda_version', side_effect=AssertionError('probed')):
            self.assertEqual(gpu_runtime.plan().runtime, 'cpu')

    def test_plan_reads_the_settings(self):
        config_schema.set_value('model.gpu_device', '1')
        with machine():
            config_schema.set_value('model.device', 'gpu')
            self.assertEqual(gpu_runtime.plan().runtime, 'cpu')
            config_schema.set_value('model.gpu_device', 'auto')
            self.assertEqual(gpu_runtime.plan().device, 0)

    def test_installed_names_the_single_intact_runtime(self):
        cases = [({'onnxruntime': True}, 'cpu'), ({'onnxruntime-gpu': True}, 'gpu'), ({}, ''),
                 ({'onnxruntime-gpu': False}, ''), ({'onnxruntime': True, 'onnxruntime-gpu': True}, '')]
        for providers, want in cases:
            with patch('imprint.onnx_runtime.providers', return_value=providers):
                self.assertEqual(gpu_runtime.installed(), want, providers)

    def test_pending_says_what_setup_would_install(self):
        gpu = decide('gpu')
        cpu = decide('cpu')
        cases = [(gpu, {'onnxruntime': True}, f'the CUDA runtime (more than 1 GB) for {RTX.name}'),
                 (gpu, {'onnxruntime-gpu': True}, ''),
                 (cpu, {'onnxruntime-gpu': True}, 'the CPU runtime, removing the CUDA libraries'),
                 (cpu, {'onnxruntime': True}, ''),
                 (cpu, {}, 'the CPU runtime')]
        for plan, providers, want in cases:
            with patch('imprint.onnx_runtime.providers', return_value=providers):
                self.assertEqual(gpu_runtime.pending(plan), want, (plan.runtime, providers))

    def test_plan_command_prints_json_and_retry_forgets_a_failure(self):
        (self.data / 'gpu_state.json').write_text(json.dumps({'ort_gpu': 'broken', 'ort_gpu_reason': 'x'}))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(gpu_runtime.main(['plan', '--retry']), 0)
        self.assertEqual(json.loads(out.getvalue())['runtime'], 'cpu')
        self.assertFalse((self.data / 'gpu_state.json').exists())


class VerdictTests(DataDirTestCase):
    def setUp(self):
        super().setUp()
        config_schema.set_value('model.device', 'gpu')

    def check(self, smoke_result):
        with machine(), patch.object(gpu_runtime, '_smoke', return_value=smoke_result) as smoke:
            result = gpu_runtime.check()
        return result, smoke

    def test_a_passing_check_records_ok_on_the_planned_gpu(self):
        (ok, detail), smoke = self.check('')
        self.assertEqual((ok, detail), (True, RTX.name))
        smoke.assert_called_once_with(0)
        state = json.loads((self.data / 'gpu_state.json').read_text())
        self.assertEqual(state['ort_gpu'], 'ok')
        self.assertEqual({k: state['ort_gpu_env'][k] for k in ('gpu', 'driver')}, {'gpu': RTX.name, 'driver': '581.80'})

    def test_a_failed_check_moves_later_plans_to_the_cpu(self):
        (ok, detail), _ = self.check('cuDNN is unavailable')
        self.assertEqual((ok, detail), (False, 'cuDNN is unavailable'))
        with machine():
            plan = gpu_runtime.plan()
        self.assertEqual(plan.runtime, 'cpu')
        self.assertIn('cuDNN is unavailable', plan.reason)

    def test_an_exception_is_a_failed_check(self):
        with machine(), patch.object(gpu_runtime, '_smoke', side_effect=OSError('cudnn64_9.dll missing\nmore')):
            self.assertEqual(gpu_runtime.check(), (False, 'cudnn64_9.dll missing'))

    def test_a_verdict_holds_for_its_environment_only(self):
        env = {'gpu': RTX.name, 'driver': '581.80', 'python': '3.12', 'runtime': 'a'}
        self.assertTrue(gpu_runtime.same_environment(env, dict(env)))
        self.assertFalse(gpu_runtime.same_environment(env, {**env, 'driver': '582.00'}))
        self.assertFalse(gpu_runtime.same_environment(env, {**env, 'runtime': 'b'}))
        self.assertFalse(gpu_runtime.same_environment({**env, 'runtime': ''}, env))
        self.assertTrue(gpu_runtime.same_environment({'runtime': 'a'}, env))


class RuntimeMajorTests(unittest.TestCase):
    def test_the_shipped_gpu_lock_is_cuda_13(self):
        self.assertEqual(cuda_devices.runtime_major(), 13)

    def test_an_unlocked_requirement_names_the_major_too(self):
        with tempfile.TemporaryDirectory() as directory:
            requirements = Path(directory)
            self.assertIsNone(cuda_devices.runtime_major(requirements))
            (requirements / 'gpu.txt').write_text('onnxruntime-gpu>=1.20.0\nnvidia-cuda-runtime~=13.0\n')
            self.assertEqual(cuda_devices.runtime_major(requirements), 13)


class DeviceSettingTests(DataDirTestCase):
    def choices(self, **facts):
        with machine(**facts):
            return {c.value: c for c in config_schema.SETTINGS_BY_KEY['model.device'].options()}

    def test_gpu_is_unavailable_where_cuda_cannot_help(self):
        choices = self.choices(devices=(), cuda_version=None)
        self.assertEqual(choices['gpu'].unavailable, 'No NVIDIA GPU found')
        self.assertEqual(choices['auto'].detail, 'CPU here: no NVIDIA GPU found')
        self.assertEqual(choices['cpu'].unavailable, '')

    def test_gpu_names_the_card_and_the_download(self):
        choices = self.choices()
        self.assertEqual(choices['gpu'].unavailable, '')
        self.assertEqual(choices['gpu'].detail, f'{RTX.name}, CUDA 13; installs the CUDA runtime, more than 1 GB')
        installed = self.choices(providers={'onnxruntime-gpu': True})
        self.assertEqual(installed['gpu'].detail, f'{RTX.name}, CUDA 13')

    def test_config_set_says_how_to_apply_a_runtime_change(self):
        out = io.StringIO()
        with machine(), contextlib.redirect_stdout(out):
            config_schema.cli_set('model.device', 'gpu')
        self.assertIn(f'Run imprint setup to install the CUDA runtime (more than 1 GB) for {RTX.name}', out.getvalue())
        out = io.StringIO()
        with machine(devices=(GTX,)), contextlib.redirect_stdout(out):
            config_schema.cli_set('model.device', 'gpu')
        self.assertIn(f'CUDA 13 needs compute capability 7.5 or newer; {GTX.name} has 6.1', out.getvalue())
        self.assertNotIn('imprint setup', out.getvalue())


class ProvisioningAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        environment = patch.dict(os.environ, {'IMPRINT_DATA_DIR': directory.name})
        environment.start()
        self.addCleanup(environment.stop)
        for name in ('IMPRINT_DEVICE', 'IMPRINT_GPU_DEVICE'):
            os.environ.pop(name, None)
        self.data = Path(directory.name)
        self.started = 0
        self.running = False
        from imprint import api
        patches = [patch.object(api, '_start_bootstrap', side_effect=self.start),
                   patch.object(api, '_bootstrap_running', side_effect=lambda: self.running)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def start(self):
        self.started += 1

    async def request(self, method, path, **kwargs):
        from imprint.api import app
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('127.0.0.1', 1234)),
                                     base_url='http://127.0.0.1:8420') as client:
            return await client.request(method, path, **kwargs)

    async def test_choosing_the_gpu_starts_bootstrap(self):
        with machine():
            response = await self.request('PUT', '/api/config/model.device', json={'value': 'gpu'})
        self.assertIs(response.json()['provisioning'], True)
        self.assertEqual(self.started, 1)

    async def test_a_change_the_runtime_already_matches_starts_nothing(self):
        with machine(devices=()):
            response = await self.request('PUT', '/api/config/model.device', json={'value': 'gpu'})
        self.assertIs(response.json()['provisioning'], False)
        with machine():
            response = await self.request('PUT', '/api/config/tagger.llm', json={'value': 'true'})
        self.assertIs(response.json()['provisioning'], False)
        self.assertEqual(self.started, 0)

    async def test_resetting_the_device_removes_the_cuda_runtime(self):
        config_schema.set_value('model.device', 'gpu')
        with machine(providers={'onnxruntime-gpu': True}):
            response = await self.request('DELETE', '/api/config/model.device')
        self.assertEqual((response.json()['removed'], response.json()['provisioning']), (True, True))

    async def test_a_change_during_a_running_bootstrap_starts_another(self):
        self.running = True
        with machine(devices=()):  # Nothing pending by now, but that one may have planned before.
            response = await self.request('PUT', '/api/config/model.device', json={'value': 'cpu'})
        self.assertIs(response.json()['provisioning'], True)
        self.assertEqual(self.started, 1)

    async def test_settings_show_a_runtime_setup_did_not_install(self):
        config_schema.set_value('model.device', 'gpu')
        (self.data / 'bootstrap.log').write_text('[*] Installing dependencies for the gpu runtime\n'
                                                 '[x] Installing dependencies for the gpu runtime failed\n')
        with machine():
            runtime = (await self.request('GET', '/api/config')).json()['runtime']
            self.assertIn('the CUDA runtime', runtime['pending'])
            self.assertEqual(runtime['log'][-1], '[x] Installing dependencies for the gpu runtime failed')
            await self.request('POST', '/api/runtime/provision')
            self.assertEqual(self.started, 1)
            self.running = True
            await self.request('POST', '/api/runtime/provision')
            self.assertEqual(self.started, 1)
        with machine(providers={'onnxruntime-gpu': True}):
            runtime = (await self.request('GET', '/api/config')).json()['runtime']
        self.assertEqual((runtime['pending'], runtime['log']), ('', []))


class BootstrapProcessTests(DataDirTestCase):
    def test_bootstrap_runs_outside_the_server_and_logs_to_a_file(self):
        import sys
        from imprint import api
        # Python stands in for the CLI: it reports that it found no `bootstrap` script.
        with patch.object(queue, 'find_imprint_binary', return_value=sys.executable), \
             patch.object(api, '_bootstrap', None):
            with patch.object(api.subprocess, 'Popen', wraps=api.subprocess.Popen) as popen:
                api._start_bootstrap()
            argv = popen.call_args.args[0]
            self.assertEqual(argv[1:], ['bootstrap', '--non-interactive'])
            api._bootstrap.wait(30)
            self.assertFalse(api._bootstrap_running())
        detach = popen.call_args.kwargs
        self.assertTrue(detach.get('start_new_session') or detach.get('creationflags'))
        self.assertIn('bootstrap', (self.data / 'bootstrap.log').read_text())


class AfterJobTests(unittest.TestCase):
    def test_only_jobs_that_changed_collections_reset_the_server(self):
        from imprint import api
        with patch.object(api, '_reset_after_wipe') as reset:
            api._after_job('wipe', 'failed')
            api._after_job('ingest', 'done')
            reset.assert_not_called()
            api._after_job('wipe', 'done')
            reset.assert_called_once_with()

    def test_the_dispatcher_reports_each_job_to_the_server_running_it(self):
        # The server hands in the callback: `python -m imprint.api` serves from
        # __main__, where importing imprint.api would reach another copy.
        import asyncio
        import sys
        with tempfile.TemporaryDirectory() as data, patch.dict(os.environ, {'IMPRINT_DATA_DIR': data}), \
                patch.object(queue, '_build_argv', lambda command, body: [sys.executable, '-c', 'pass']):
            job_id = queue.enqueue('wipe', {})
            seen = []
            asyncio.run(queue._run_job(queue.get_job(job_id), lambda command, status: seen.append((command, status))))
        self.assertEqual(seen, [('wipe', 'done')])


if __name__ == '__main__':
    unittest.main()
