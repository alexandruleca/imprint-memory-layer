import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from imprint import config_schema, cuda_devices, embeddings

RTX = cuda_devices.Device(0, 'NVIDIA GeForce RTX 4080 Laptop GPU', (8, 9), 12281)
GTX = cuda_devices.Device(1, 'NVIDIA GeForce GTX 1060 6GB', (6, 1), 6143)
# The same Pascal card enumerated first, ahead of a supported one.
PASCAL_FIRST = [cuda_devices.Device(0, GTX.name, GTX.compute, GTX.memory_mb),
                cuda_devices.Device(1, RTX.name, RTX.compute, RTX.memory_mb)]


def host(devices, major):
    return patch.multiple(cuda_devices, devices=lambda: devices, runtime_major=lambda: major)


class DeviceSelectionTests(unittest.TestCase):
    def test_auto_skips_devices_the_runtime_has_no_code_for(self):
        with host(PASCAL_FIRST, 13):
            self.assertEqual(cuda_devices.resolve('auto'), 1)
        with host(PASCAL_FIRST, 12):
            self.assertEqual(cuda_devices.resolve('auto'), 0)
        with host(PASCAL_FIRST[:1], 13):
            self.assertIsNone(cuda_devices.resolve('auto'))

    def test_auto_trusts_devices_when_the_runtime_is_unknown(self):
        with host(PASCAL_FIRST, None):
            self.assertEqual(cuda_devices.resolve('auto'), 0)
        with host([], 13):
            self.assertIsNone(cuda_devices.resolve('auto'))

    def test_an_explicit_index_is_used_as_given(self):
        with host(PASCAL_FIRST, 13):
            self.assertEqual(cuda_devices.resolve('0'), 0)

    def test_choices_name_each_device_and_why_one_cannot_be_used(self):
        with host([RTX, GTX], 13):
            choices = config_schema.SETTINGS_BY_KEY['model.gpu_device'].options()
        self.assertEqual([c.value for c in choices], ['auto', '0', '1'])
        self.assertEqual(choices[1].label, '0: NVIDIA GeForce RTX 4080 Laptop GPU')
        self.assertEqual(choices[1].detail, '12 GB, compute 8.9')
        self.assertEqual(choices[1].unavailable, '')
        self.assertEqual(choices[2].unavailable, 'CUDA 13 needs compute capability 7.5+, this GPU has 6.1')


class DeviceProviderTests(unittest.TestCase):
    def providers(self, setting):
        with patch.object(embeddings.config, 'DEVICE', 'gpu'), \
             patch.object(embeddings.config, 'GPU_DEVICE', setting), \
             patch.object(embeddings, '_preload_cuda_libs'), \
             patch.object(embeddings.ort, 'get_available_providers',
                          return_value=['CUDAExecutionProvider', 'CPUExecutionProvider']):
            return embeddings._resolve_providers()

    def test_cuda_runs_on_the_resolved_device(self):
        with host(PASCAL_FIRST, 13):
            cuda, cpu = self.providers('auto')
        self.assertEqual((cuda[0], cuda[1]['device_id'], cpu), ('CUDAExecutionProvider', 1, 'CPUExecutionProvider'))

    def test_no_supported_device_embeds_on_cpu(self):
        with host(PASCAL_FIRST[:1], 13), patch('sys.stderr'):
            self.assertEqual(self.providers('auto'), ['CPUExecutionProvider'])


class SettingValueTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        environment = patch.dict(os.environ, {'IMPRINT_DATA_DIR': directory.name})
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop('IMPRINT_GPU_DEVICE', None)
        os.environ.pop('IMPRINT_DEVICE', None)

    def test_gpu_device_is_auto_or_an_index(self):
        self.assertEqual(config_schema.resolve('model.gpu_device'), ('auto', 'default'))
        self.assertEqual(config_schema.set_value('model.gpu_device', ' 01 '), '1')
        self.assertEqual(config_schema.resolve('model.gpu_device'), ('1', 'config'))
        for value in ('-1', 'cuda:0', ''):
            with self.assertRaises(ValueError):
                config_schema.set_value('model.gpu_device', value)

    def test_an_index_stored_as_a_number_still_resolves(self):
        config_schema.write_config({'model': {'gpu_device': 1}})
        self.assertEqual(config_schema.resolve('model.gpu_device'), ('1', 'config'))

    def test_closed_choices_reject_other_values_and_open_ones_accept_them(self):
        with self.assertRaises(ValueError):
            config_schema.set_value('model.device', 'tpu')
        self.assertEqual(config_schema.set_value('model.device', 'gpu'), 'gpu')
        self.assertEqual(config_schema.set_value('chat.n_gpu_layers', '20'), 20)


class SettingAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data = Path(directory.name)
        environment = patch.dict(os.environ, {'IMPRINT_DATA_DIR': directory.name})
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop('IMPRINT_DEVICE', None)

    async def request(self, method, path, **kwargs):
        from imprint.api import app
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('127.0.0.1', 1234)),
                                     base_url='http://127.0.0.1:8420') as client:
            return await client.request(method, path, **kwargs)

    async def test_settings_carry_their_choices(self):
        with host([RTX], 13):
            settings = {s['key']: s for s in (await self.request('GET', '/api/config')).json()['settings']}
        self.assertEqual(settings['model.gpu_device']['choices'][1],
                         {'value': '0', 'label': '0: NVIDIA GeForce RTX 4080 Laptop GPU',
                          'detail': '12 GB, compute 8.9', 'unavailable': ''})
        self.assertTrue(settings['model.gpu_device']['custom'])
        self.assertEqual([c['value'] for c in settings['model.device']['choices']], ['auto', 'cpu', 'gpu'])
        self.assertFalse(settings['model.device']['custom'])

    async def test_a_refused_value_is_an_error_response(self):
        response = await self.request('PUT', '/api/config/model.device', json={'value': 'tpu'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('expected one of auto, cpu, gpu', response.json()['error'])
        self.assertEqual((await self.request('PUT', '/api/config/model.nope', json={'value': '1'})).status_code, 404)
        self.assertEqual(config_schema.resolve('model.device')[1], 'default')


if __name__ == '__main__':
    unittest.main()
