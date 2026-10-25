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
        self.queued = []
        self.jobs = {'active': None, 'queued': [], 'recent': []}
        patches = [patch.object(queue, 'enqueue', side_effect=self.enqueue),
                   patch.object(queue, 'list_queue', side_effect=lambda recent_limit=20: self.jobs)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def enqueue(self, command, body):
        self.queued.append(command)
        return f'job-{len(self.queued)}'

    async def request(self, method, path, **kwargs):
        from imprint.api import app
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('127.0.0.1', 1234)),
                                     base_url='http://127.0.0.1:8420') as client:
            return await client.request(method, path, **kwargs)

    async def test_choosing_the_gpu_queues_bootstrap(self):
        with machine():
            response = await self.request('PUT', '/api/config/model.device', json={'value': 'gpu'})
        self.assertEqual(response.json()['provisioning'], 'job-1')
        self.assertEqual(self.queued, ['bootstrap'])

    async def test_a_change_the_runtime_already_matches_queues_nothing(self):
        with machine(devices=()):
            response = await self.request('PUT', '/api/config/model.device', json={'value': 'gpu'})
        self.assertIsNone(response.json()['provisioning'])
        with machine():
            response = await self.request('PUT', '/api/config/tagger.llm', json={'value': 'true'})
        self.assertIsNone(response.json()['provisioning'])
        self.assertEqual(self.queued, [])

    async def test_resetting_the_device_removes_the_cuda_runtime(self):
        config_schema.set_value('model.device', 'gpu')
        with machine(providers={'onnxruntime-gpu': True}):
            response = await self.request('DELETE', '/api/config/model.device')
        self.assertEqual((response.json()['removed'], response.json()['provisioning']), (True, 'job-1'))

    async def test_one_queued_bootstrap_serves_every_change(self):
        self.jobs['queued'] = [{'id': 'queued-1', 'command': 'bootstrap'}]
        with machine():
            response = await self.request('PUT', '/api/config/model.device', json={'value': 'gpu'})
        self.assertEqual(response.json()['provisioning'], 'queued-1')
        self.assertEqual(self.queued, [])

    async def test_a_change_during_a_running_bootstrap_queues_another(self):
        self.jobs['active'] = {'id': 'running-1', 'command': 'bootstrap'}
        with machine(devices=()):  # Nothing pending by now, but the job may have planned before.
            response = await self.request('PUT', '/api/config/model.device', json={'value': 'cpu'})
        self.assertEqual(response.json()['provisioning'], 'job-1')


class RestartTests(unittest.TestCase):
    def test_the_server_restarts_only_into_another_runtime(self):
        from imprint import api

        class Server:
            restarted = False

            def restart(self):
                self.restarted = True

        for installed, server_runs, want in [('gpu', True, True), (api._LOADED_RUNTIME, True, False), ('gpu', False, False)]:
            server = Server()
            with patch.object(api.app.state, 'server', server if server_runs else None), \
                 patch.object(gpu_runtime, 'installed', return_value=installed), \
                 patch.object(api, '_LOADED_RUNTIME', 'cpu' if installed == 'gpu' else installed):
                api.restart_into_runtime()
            self.assertEqual(server.restarted, want, (installed, server_runs))

    def test_bootstrap_jobs_never_prompt(self):
        self.assertEqual(queue.build_command_args('bootstrap', {}), ['--non-interactive'])

    def test_only_a_finished_bootstrap_restarts_the_server(self):
        from imprint import api
        with patch.object(api, 'restart_into_runtime') as restart, patch.object(api, '_reset_after_wipe') as reset:
            api._after_job('bootstrap', 'failed')
            api._after_job('ingest', 'done')
            restart.assert_not_called()
            api._after_job('bootstrap', 'done')
            restart.assert_called_once_with()
            api._after_job('wipe', 'done')
            reset.assert_called_once_with()

    def test_the_dispatcher_reports_each_job_to_the_server_running_it(self):
        # The server hands in the callback: `python -m imprint.api` serves from
        # __main__, where importing imprint.api would reach another copy.
        import asyncio
        import sys
        with tempfile.TemporaryDirectory() as data, patch.dict(os.environ, {'IMPRINT_DATA_DIR': data}), \
                patch.object(queue, '_build_argv', lambda command, body: [sys.executable, '-c', 'pass']):
            job_id = queue.enqueue('bootstrap', {})
            seen = []
            asyncio.run(queue._run_job(queue.get_job(job_id), lambda command, status: seen.append((command, status))))
        self.assertEqual(seen, [('bootstrap', 'done')])


if __name__ == '__main__':
    unittest.main()
