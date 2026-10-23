import concurrent.futures
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from imprint import config, queue, qdrant_runner, server, tagger
from imprint.doctor import report


class WorkspaceTests(unittest.TestCase):
    def test_queued_job_retains_workspace_after_switch(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {'IMPRINT_DATA_DIR': directory, 'IMPRINT_WORKSPACE': ''}), \
             patch.object(queue, '_notify_dispatcher'):
            config.switch_workspace('original')
            body = {'directory': directory}
            job = queue.enqueue('ingest', body)
            config.switch_workspace('other')
            self.assertEqual(queue.get_job(job)['body']['_workspace'], 'original')
            self.assertNotIn('_workspace', body)
            with patch.dict(os.environ, {'IMPRINT_WORKSPACE': 'original'}):
                self.assertEqual(config.collection_name(), 'memories_original')
            self.assertEqual(config.get_active_workspace(), 'other')
            with patch.dict(os.environ, {'IMPRINT_WORKSPACE': '../invalid'}):
                with self.assertRaises(ValueError):
                    config.get_active_workspace()

    def test_mcp_resolves_workspace_at_call_boundary(self):
        with patch.object(config, 'get_active_workspace', return_value='chosen'), \
             patch.object(config, 'get_known_workspaces', return_value=['chosen']), \
             patch.object(server.vs, 'search', return_value=[] ) as search, \
             patch.object(server, 'wake_up', return_value='context') as wake:
            server._woken_workspaces.discard('chosen')
            self.addCleanup(server._woken_workspaces.discard, 'chosen')
            server.search('a valid query', detail='full')
            server.search('another valid query', detail='full')
            self.assertEqual(search.call_args.kwargs['workspace'], 'chosen')
            wake.assert_called_once_with(workspace='chosen')

    def test_optional_llm_is_never_enabled_by_provider(self):
        with patch('imprint.config_schema.resolve', return_value=(False, 'default')), \
             patch.object(tagger, 'derive_llm_classify') as classify:
            tagger.build_payload_tags('retry connection', llm=False, zero_shot=False)
            tagger.build_payload_tags('retry connection', llm=None, zero_shot=False)
            classify.assert_not_called()


class DiagnosticsTests(unittest.TestCase):
    def test_doctor_is_read_only_and_does_not_spawn_storage(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {'CODEX_HOME': directory}), \
             patch.object(qdrant_runner, 'is_running', return_value=False), \
             patch.object(qdrant_runner, 'ensure_running') as spawn:
            result = report(Path(directory))
            self.assertFalse(result['ok'])  # No dashboard in this fixture.
            self.assertEqual(list(Path(directory).iterdir()), [])
            spawn.assert_not_called()
            self.assertIn('not-running', [item['status'] for item in result['checks']])

    def test_concurrent_qdrant_start_spawns_once(self):
        ready = threading.Event()
        def spawn(_binary):
            time.sleep(.15)
            ready.set()
            return 123
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(config, 'get_data_dir', return_value=Path(directory)), \
             patch.object(qdrant_runner, 'is_running', side_effect=ready.is_set), \
             patch.object(qdrant_runner, 'DISABLE_SPAWN', False), \
             patch.object(qdrant_runner, '_find_or_download_binary', return_value=Path(directory) / 'qdrant'), \
             patch.object(qdrant_runner, '_spawn', side_effect=spawn) as start, \
             patch.object(qdrant_runner, '_wait_until_ready', return_value=True), \
             concurrent.futures.ThreadPoolExecutor(2) as workers:
            results = list(workers.map(lambda _: qdrant_runner.ensure_running(), range(2)))
            self.assertEqual(results[0], results[1])
            start.assert_called_once()
