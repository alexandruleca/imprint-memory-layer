import concurrent.futures
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from imprint import config, workspace_policy as policy


class WorkspacePolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'IMPRINT_DATA_DIR': str(self.root / 'data'), 'IMPRINT_WORKSPACE': ''})
        self.env.start()
        self.addCleanup(self.env.stop)
        config.register_workspace('alpha')
        config.register_workspace('beta')
        self.a, self.b = self.root / 'project', self.root / 'project-other'
        self.a.mkdir()
        self.b.mkdir()

    def test_first_ingest_binds_then_outside_paths_fail(self):
        policy.ensure_ingest_paths([str(self.a)], 'alpha')
        policy.ensure_ingest_paths([str(self.a / 'file.py')], 'alpha')
        with self.assertRaisesRegex(ValueError, 'outside workspace'):
            policy.ensure_ingest_paths([str(self.b)], 'alpha')
        with self.assertRaisesRegex(ValueError, 'overlaps workspace'):
            policy.ensure_ingest_paths([str(self.a)], 'beta')
        self.assertEqual(policy.roots('beta'), [])

    def test_overlap_requires_confirmation_and_then_is_allowed(self):
        policy.add_root(str(self.a), 'alpha')
        with self.assertRaisesRegex(ValueError, 'alpha'):
            policy.add_root(str(self.a), 'beta')
        result = policy.add_root(str(self.a), 'beta', allow_overlap=True)
        self.assertEqual(result['overlaps'], ['alpha'])
        policy.ensure_ingest_paths([str(self.a / 'file.py')], 'beta')

    def test_no_partial_binding_when_other_ingest_paths_are_outside(self):
        with self.assertRaises(ValueError):
            policy.ensure_ingest_paths([str(self.a), str(self.b)], 'alpha')
        self.assertEqual(policy.roots('alpha'), [])

    def test_concurrent_first_claim_has_one_owner(self):
        def claim(ws):
            try:
                policy.ensure_ingest_paths([str(self.a)], ws)
                return True
            except ValueError:
                return False
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            self.assertEqual(sorted(pool.map(claim, ['alpha', 'beta'])), [False, True])

    def test_symlink_cannot_bypass_workspace_roots(self):
        policy.add_root(str(self.a), 'alpha')
        alias = self.a / 'escape'
        try:
            alias.symlink_to(self.b, target_is_directory=True)
        except OSError as error:
            self.skipTest(str(error))
        with self.assertRaises(ValueError):
            policy.ensure_ingest_paths([str(alias / 'file.py')], 'alpha')

    def test_conversation_scope_uses_recorded_cwd(self):
        policy.add_root(str(self.a), 'alpha')
        transcript = self.root / 'session.jsonl'
        transcript.write_text(json.dumps({'cwd': str(self.a)}))
        self.assertTrue(policy.transcript_allowed(transcript, 'alpha'))
        transcript.write_text('\n'.join(json.dumps({'cwd': str(path)}) for path in [self.a, self.b]))
        self.assertFalse(policy.transcript_allowed(transcript, 'alpha'))
        transcript.write_text(json.dumps({'type': 'message'}))
        self.assertFalse(policy.transcript_allowed(transcript, 'alpha'))
        transcript.write_text(json.dumps({'cwd': '.'}))
        self.assertFalse(policy.transcript_allowed(transcript, 'alpha'))

    def test_unknown_workspaces_and_empty_cleanup_targets_fail(self):
        with self.assertRaises(ValueError):
            policy.add_root(str(self.a), 'unknown')
        for target in ('', '///', '\\\\', '.', '..'):
            with self.subTest(target=target), self.assertRaises(ValueError):
                policy.source_key(target)

    def test_mcp_process_pins_default_until_restart(self):
        import runpy
        from imprint import server
        config.switch_workspace('alpha')
        def serve():
            config.switch_workspace('beta')
            self.assertEqual(config.get_active_workspace(), 'alpha')
            self.assertEqual(server._validate_ws('beta'), ('beta', None))
        with patch.object(server.mcp, 'run', side_effect=serve):
            runpy.run_module('imprint', run_name='__main__')

    def test_queue_preserves_explicit_cli_workspace_after_global_switch(self):
        from imprint import queue
        config.switch_workspace('beta')
        with patch.object(queue, '_notify_dispatcher'):
            job = queue.enqueue('ingest', {'workspace': 'alpha', 'args': [str(self.a)]})
        self.assertEqual(queue.get_job(job)['body']['_workspace'], 'alpha')
        with self.assertRaises(ValueError):
            queue.enqueue('ingest', {'workspace': 'unknown'})
