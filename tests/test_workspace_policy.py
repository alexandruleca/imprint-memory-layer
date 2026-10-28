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

    def test_conversation_ownership_uses_recorded_cwd(self):
        transcript = self.root / 'session.jsonl'
        def owner(*cwds):
            transcript.write_text('\n'.join(json.dumps({'cwd': str(cwd)}) for cwd in cwds))
            return [ws for ws in ('alpha', 'beta') if policy.transcript_allowed(transcript, ws)]
        # Learning needs no approved root: no workspace has claimed this project.
        self.assertEqual(owner(self.a), ['alpha', 'beta'])
        policy.add_root(str(self.a), 'alpha')
        self.assertEqual(owner(self.a / 'sub'), ['alpha'])
        self.assertEqual(owner(self.b), ['alpha', 'beta'])
        # Touching another workspace's project keeps the whole conversation out.
        self.assertEqual(owner(self.a, self.b), ['alpha'])
        # A root shared on purpose shares its conversations.
        policy.add_root(str(self.a), 'beta', allow_overlap=True)
        self.assertEqual(owner(self.a), ['alpha', 'beta'])
        for content in (json.dumps({'type': 'message'}), json.dumps({'cwd': '.'})):
            transcript.write_text(content)
            self.assertFalse(policy.transcript_allowed(transcript, 'alpha'))

    def test_desktop_exports_scan_download_folders_without_roots_but_not_other_folders(self):
        import zipfile
        from imprint import cli_desktop_learn as desktop
        config.switch_workspace('alpha')
        downloads, elsewhere = self.root / 'Downloads', self.b / 'exports'
        for folder in (downloads, elsewhere):
            folder.mkdir()
            with zipfile.ZipFile(folder / 'data-2026-10-24-export.zip', 'w') as export:
                export.writestr('conversations.json', json.dumps([{'name': folder.name}]))
        (downloads / 'linked-data-2026-10-24.zip').symlink_to(elsewhere / 'data-2026-10-24-export.zip')
        with patch.object(desktop, '_downloads_roots', return_value=[downloads]), \
                patch.object(desktop, '_ingest_json_file', return_value=1) as ingest:
            self.assertEqual(desktop.scan_once_api()['indexed_zips'], 1)
            self.assertEqual(ingest.call_args.args[2], str((downloads / 'data-2026-10-24-export.zip').resolve()))
            with self.assertRaisesRegex(ValueError, 'outside the workspace roots'):
                desktop.scan_once_api([str(elsewhere)])
            policy.add_root(str(self.b), 'alpha')
            self.assertEqual(desktop.scan_once_api([str(elsewhere)])['indexed_zips'], 1)
        self.assertEqual(policy.roots('alpha'), [str(self.b.resolve())])

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
        # run_module starts a session as well as pinning its environment. Keep
        # its first-tool notice out of later in-process MCP reader tests.
        with patch.object(server.mcp, 'run', side_effect=serve), patch.dict(server._session, {}, clear=True):
            runpy.run_module('imprint', run_name='__main__')

    def test_queue_preserves_explicit_cli_workspace_after_global_switch(self):
        from imprint import queue
        config.switch_workspace('beta')
        with patch.object(queue, '_notify_dispatcher'):
            job = queue.enqueue('ingest', {'workspace': 'alpha', 'args': [str(self.a)]})
        self.assertEqual(queue.get_job(job)['body']['_workspace'], 'alpha')
        with self.assertRaises(ValueError):
            queue.enqueue('ingest', {'workspace': 'unknown'})
