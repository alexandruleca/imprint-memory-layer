import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from imprint import config, config_schema, embeddings, local_llm

TOKEN = 'hf_storedtoken0000wxyz'


class Stop(Exception):
    pass


class HubTokenTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data = Path(directory.name)
        environment = patch.dict(os.environ, {'IMPRINT_DATA_DIR': directory.name})
        environment.start()
        self.addCleanup(environment.stop)
        for name in ('HF_TOKEN', 'HUGGING_FACE_HUB_TOKEN'):
            os.environ.pop(name, None)

    def test_stored_token_applies_until_reset_and_environment_wins(self):
        self.assertIsNone(config.hub_token())
        config_schema.set_value('huggingface.token', TOKEN)
        self.assertEqual(config.hub_token(), TOKEN)
        with patch.dict(os.environ, {'HF_TOKEN': 'hf_fromenvironment'}):
            self.assertEqual(config.hub_token(), 'hf_fromenvironment')
            self.assertEqual(config_schema.resolve('huggingface.token')[1], 'env')
        self.assertTrue(config_schema.reset_value('huggingface.token'))
        self.assertIsNone(config.hub_token())

    def test_cli_output_masks_the_token(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            config_schema.cli_set('huggingface.token', TOKEN)
            config_schema.cli_get('huggingface.token')
            config_schema.cli_list()
            config_schema.cli_status_overrides()
        output = stdout.getvalue()
        self.assertNotIn(TOKEN, output)
        self.assertIn('****wxyz', output)
        self.assertEqual(json.loads((self.data / 'config.json').read_text())['huggingface']['token'], TOKEN)

    def test_short_values_reveal_nothing(self):
        setting = config_schema.SETTINGS_BY_KEY['huggingface.token']
        self.assertEqual(config_schema.display_value(setting, 'hf_short'), '****')
        self.assertEqual(config_schema.display_value(setting, ''), '')

    def test_embedding_download_sends_the_token(self):
        config_schema.set_value('huggingface.token', TOKEN)
        with patch.object(embeddings, '_session', None), \
             patch.object(embeddings, 'hf_hub_download', side_effect=Stop) as download, \
             self.assertRaises(Stop):
            embeddings._load_locked()
        self.assertEqual(download.call_args.kwargs['token'], TOKEN)

    def test_gguf_download_sends_the_token(self):
        config_schema.set_value('huggingface.token', TOKEN)
        with patch.object(httpx, 'stream', side_effect=Stop) as stream, self.assertRaises(Stop):
            local_llm.download('owner/repo', 'model.gguf', self.data / 'models' / 'model.gguf')
        self.assertEqual(stream.call_args.kwargs['headers']['authorization'], 'Bearer ' + TOKEN)


class HubTokenAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data = Path(directory.name)
        environment = patch.dict(os.environ, {'IMPRINT_DATA_DIR': directory.name})
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop('HF_TOKEN', None)

    async def request(self, method, path, **kwargs):
        from imprint.api import app
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('127.0.0.1', 1234)),
                                     base_url='http://127.0.0.1:8420') as client:
            response = await client.request(method, path, **kwargs)
        self.assertEqual(response.status_code, 200, response.text)
        return response

    async def token_setting(self):
        settings = (await self.request('GET', '/api/config')).json()['settings']
        return next(item for item in settings if item['key'] == 'huggingface.token')

    async def test_dashboard_sets_masks_and_removes_the_token(self):
        self.assertEqual(await self.token_setting(), {
            'key': 'huggingface.token', 'value': '', 'source': 'default', 'default': '', 'type': 'str',
            'env': 'HF_TOKEN', 'desc': config_schema.SETTINGS_BY_KEY['huggingface.token'].desc, 'secret': True})
        saved = await self.request('PUT', '/api/config/huggingface.token', json={'value': TOKEN})
        self.assertNotIn(TOKEN, saved.text)
        setting = await self.token_setting()
        self.assertEqual((setting['value'], setting['source']), ('****wxyz', 'config'))
        self.assertNotIn(TOKEN, (await self.request('GET', '/api/config')).text)
        self.assertEqual(config.hub_token(), TOKEN)

        removed = await self.request('DELETE', '/api/config/huggingface.token')
        self.assertTrue(removed.json()['removed'])
        self.assertEqual((await self.token_setting())['source'], 'default')
        self.assertIsNone(config.hub_token())


if __name__ == '__main__':
    unittest.main()
