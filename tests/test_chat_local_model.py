"""chat.local_model: the default, the pinned Qwen3.5 MoE, or a custom GGUF (ADR 0153)."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from imprint import chat, config_schema as schema, local_llm, model_profiles as profiles


class ValueTests(unittest.TestCase):
    def test_named_choices_paths_and_repository_files_are_accepted(self):
        for value in ('default', 'qwen3.5-35b-a3b', '/models/mine.gguf', 'C:\\models\\mine.gguf',
                      'bartowski/Qwen3-14B-GGUF/Qwen3-14B-Q4_K_M.gguf', 'owner/repo/Q4/split.gguf'):
            self.assertEqual(schema.local_chat_model(value), value)

    def test_a_hugging_face_file_url_is_stored_as_its_repository_file(self):
        for url in ('https://huggingface.co/unsloth/Qwen3-8B-GGUF/blob/main/Qwen3-8B-Q4_K_M.gguf',
                    'https://huggingface.co/unsloth/Qwen3-8B-GGUF/resolve/main/Qwen3-8B-Q4_K_M.gguf?download=true',
                    'https://hf.co/unsloth/Qwen3-8B-GGUF/resolve/abc123/Qwen3-8B-Q4_K_M.gguf'):
            self.assertEqual(schema.local_chat_model(url), 'unsloth/Qwen3-8B-GGUF/Qwen3-8B-Q4_K_M.gguf')

    def test_anything_else_is_refused_with_the_accepted_forms(self):
        for value in ('qwen', 'owner/file.gguf', 'owner//file.gguf', 'owner/repo/../x.gguf', 'owner/repo/model.bin', ''):
            with self.assertRaises(ValueError, msg=value):
                schema.local_chat_model(value)

    def test_the_setting_defaults_to_the_current_model_and_offers_the_moe(self):
        setting = schema.SETTINGS_BY_KEY['chat.local_model']
        self.assertEqual(setting.default, 'default')
        self.assertTrue(setting.custom)
        with patch.object(schema, 'total_memory', return_value=64 * 1024 ** 3):
            values = [(c.value, c.unavailable) for c in setting.options()]
        self.assertEqual(values, [('default', ''), ('qwen3.5-35b-a3b', '')])

    def test_the_moe_is_unavailable_below_its_memory_and_says_why(self):
        setting = schema.SETTINGS_BY_KEY['chat.local_model']
        with patch.object(schema, 'total_memory', return_value=16 * 1024 ** 3):
            moe = setting.options()[1]
        self.assertEqual(moe.unavailable, 'Needs 32 GB of memory; this computer has 16 GB')
        with patch.object(schema, 'total_memory', return_value=None):
            self.assertEqual(setting.options()[1].unavailable, '')

    def test_total_memory_reads_this_computer(self):
        memory = schema.total_memory()
        self.assertTrue(memory is None or memory > 256 * 1024 ** 2)


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        patcher = patch.object(chat, '_models_dir', return_value=self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)

    def config(self, **values):
        defaults = {'chat.local_model': 'default', 'chat.model_repo': profiles.HELPER_REPO,
                    'chat.model_file': profiles.HELPER_FILE, 'chat.model_path': ''}
        defaults.update(values)
        return patch.object(chat, '_cfg', side_effect=lambda key: defaults[key])

    def test_default_keeps_the_existing_repo_file_and_path_settings(self):
        with self.config():
            self.assertEqual(chat._resolve_local_model_path(), (self.tmp / profiles.HELPER_FILE, None))
        with self.config(**{'chat.model_repo': 'me/other', 'chat.model_file': 'other.gguf'}):
            self.assertEqual(chat._model_source()['repo'], 'me/other')
            self.assertEqual(chat._resolve_local_model_path(), (self.tmp / 'other.gguf', None))
        with self.config(**{'chat.model_path': '/opt/own.gguf'}):
            self.assertEqual(chat._resolve_local_model_path(), (Path('/opt/own.gguf'), None))

    def test_the_moe_is_a_pinned_download_whatever_the_repo_settings_say(self):
        with self.config(**{'chat.local_model': 'qwen3.5-35b-a3b', 'chat.model_path': '/opt/own.gguf'}):
            source = chat._model_source()
            path, _ = chat._resolve_local_model_path()
        self.assertEqual((source['repo'], source['file']), (profiles.CHAT_MOE_REPO, profiles.CHAT_MOE_FILE))
        self.assertEqual(path, self.tmp / profiles.CHAT_MOE_FILE)
        self.assertEqual(profiles.pinned(source['repo'], source['file']),
                         (profiles.CHAT_MOE_REVISION, profiles.CHAT_MOE_SHA256))

    def test_a_custom_repository_file_downloads_into_its_own_folder_unpinned(self):
        with self.config(**{'chat.local_model': 'bartowski/Qwen3-14B-GGUF/Qwen3-14B-Q4_K_M.gguf'}):
            source = chat._model_source()
            path, _ = chat._resolve_local_model_path()
        self.assertEqual((source['repo'], source['file']), ('bartowski/Qwen3-14B-GGUF', 'Qwen3-14B-Q4_K_M.gguf'))
        self.assertEqual(path, self.tmp / 'bartowski--Qwen3-14B-GGUF' / 'Qwen3-14B-Q4_K_M.gguf')
        self.assertIsNone(profiles.pinned(source['repo'], source['file']))

    def test_a_custom_path_is_the_users_file_and_never_downloaded(self):
        own = self.tmp / 'own.gguf'
        with self.config(**{'chat.local_model': str(own)}), patch('imprint.local_llm.download') as download:
            self.assertEqual(chat._resolve_local_model_path(), (own, None))
            self.assertFalse(chat._model_missing())
            self.assertEqual(chat.download_model(), (None, f'model_path does not exist: {own}'))
        download.assert_not_called()

    def test_the_moe_downloads_from_its_pinned_revision(self):
        with self.config(**{'chat.local_model': 'qwen3.5-35b-a3b'}), \
             patch('imprint.local_llm.download', return_value=self.tmp / 'x') as download:
            chat.download_model()
        download.assert_called_once_with(profiles.CHAT_MOE_REPO, profiles.CHAT_MOE_FILE,
                                         self.tmp / profiles.CHAT_MOE_FILE, None)


class SwitchTests(unittest.TestCase):
    """Another choice releases the loaded chat model and loads the new one."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.first, self.second = self.tmp / 'a.gguf', self.tmp / 'b.gguf'
        self.wanted = self.first
        for name, value in {'_llm': None, '_llm_path': None, '_llama': MagicMock(return_value=object()),
                            '_resolve_local_model_path': MagicMock(side_effect=lambda: (self.wanted, None)),
                            '_resolve_model_path': MagicMock(side_effect=lambda: (self.wanted, None)),
                            '_model_source': MagicMock(return_value={'path': str(self.tmp), 'repo': '', 'file': ''}),
                            '_cfg': MagicMock(side_effect=lambda key: {'chat.n_ctx': 8192, 'chat.n_gpu_layers': 0}[key])}.items():
            patcher = patch.object(chat, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.load = patch.object(local_llm, 'load', side_effect=lambda path, *a, **k: f'model:{path.name}')
        self.load.start()
        self.addCleanup(self.load.stop)

    def test_the_same_choice_reuses_the_model_and_another_releases_it(self):
        with patch.object(local_llm, 'release') as release, \
             patch('imprint.tagger._resolve_tagger_model_path', return_value=(self.tmp / 'tagger.gguf', None)):
            self.assertEqual(chat.load_model(), ('model:a.gguf', None))
            self.assertEqual(chat.load_model(), ('model:a.gguf', None))
            release.assert_not_called()
            self.wanted = self.second
            self.assertEqual(chat.load_model(), ('model:b.gguf', None))
        release.assert_called_once_with(self.first)

    def test_a_model_the_tagger_shares_is_not_released(self):
        with patch.object(local_llm, 'release') as release, \
             patch('imprint.tagger._resolve_tagger_model_path', return_value=(self.first, None)):
            chat.load_model()
            self.wanted = self.second
            chat.load_model()
        release.assert_not_called()

    def test_release_forgets_every_configuration_of_a_file(self):
        path = str(self.first.resolve())
        with patch.dict(local_llm._models, {(path, 8192, 0): 'x', (path, 4096, -1): 'y', ('other', 8192, 0): 'z'}, clear=True):
            local_llm.release(self.first)
            self.assertEqual(list(local_llm._models), [('other', 8192, 0)])


class WslTests(unittest.TestCase):
    def test_a_linux_path_for_the_chat_model_is_translated(self):
        from imprint import wsl_bridge
        import inspect
        source = inspect.getsource(wsl_bridge)
        self.assertIn("'chat.local_model'", source)


if __name__ == '__main__':
    unittest.main()
