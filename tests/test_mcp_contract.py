import json
import unittest
from unittest.mock import patch

from imprint import server
from fastmcp import Client


class MCPContractTests(unittest.TestCase):
    def test_store_is_durable_and_preserves_explicit_tags(self):
        with patch.object(server.vs, 'store', return_value='abc123') as persist, \
             patch('imprint.tagger.build_payload_tags', return_value={'domain': ['infra']}) as classify:
            response = server.store('We use native runners because installers need native validation.', type='decision', tags='release,infra')
            self.assertEqual(response, 'Stored [abc123]')
            self.assertEqual(persist.call_args.kwargs['tags']['domain'], ['infra', 'release'])
            self.assertFalse(classify.call_args.kwargs['llm'])

    def test_store_failure_is_not_reported_as_queued_success(self):
        with patch.object(server.vs, 'store', side_effect=RuntimeError('storage offline')), \
             patch('imprint.tagger.build_payload_tags', return_value={}):
            with self.assertRaisesRegex(RuntimeError, 'storage offline'):
                server.store('A decision that must not disappear')

    def test_blank_type_uses_local_classifier_and_preferences_are_supported(self):
        with patch.object(server.vs, 'store', return_value='id') as persist, \
             patch('imprint.tagger.build_payload_tags', return_value={}):
            server.store('We decided to use native runners because they exercise installers.')
            self.assertEqual(persist.call_args.kwargs['type'], 'decision')
            server.store('I prefer clear error messages.', type='preference')
            self.assertEqual(persist.call_args.kwargs['type'], 'preference')

    def test_store_keeps_a_title_and_evidence_in_the_note(self):
        with patch.object(server.vs, 'store', return_value='id') as persist, \
             patch('imprint.tagger.build_payload_tags', return_value={}):
            self.assertEqual(server.store('CI pins actions by SHA because tags move.', type='decision',
                                          title='Audit-gate CI pins', evidence='.github/workflows/ci.yml'), 'Stored [id]')
            self.assertEqual(persist.call_args.kwargs['content'],
                             'Audit-gate CI pins\n\nCI pins actions by SHA because tags move.\n\nEvidence: .github/workflows/ci.yml')

    def test_store_over_mcp_accepts_title_and_evidence(self):
        import asyncio

        async def call():
            async with Client(server.mcp) as client:
                return await client.call_tool('store', {'content': 'A decision.', 'title': 'Headline', 'evidence': 'a.py'})
        with patch.object(server.vs, 'store', return_value='id'), \
             patch('imprint.tagger.build_payload_tags', return_value={}):
            self.assertIn('Stored [id]', str(asyncio.run(call()).content))

    def test_arguments_a_tool_does_not_take_are_ignored_and_named_instead_of_failing_the_call(self):
        # Cursor's agent added `importance`; an older server also met `title` this way.
        import asyncio

        async def call(name, arguments):
            async with Client(server.mcp) as client:
                return await client.call_tool(name, arguments)
        with patch.object(server.vs, 'store', return_value='id') as persist, \
             patch('imprint.tagger.build_payload_tags', return_value={}):
            result = asyncio.run(call('store', {'title': 'PAG Datadog cohort monitors PR #64', 'type': 'finding',
                                                'project': 'SwagProductAnalyticsGateway', 'importance': 70,
                                                'content': 'Separate draft PR #64 adds Datadog monitors.'}))
        text = str(result.content)
        self.assertIn('Stored [id]', text)
        self.assertIn('Ignored arguments store does not take: importance.', text)
        self.assertTrue(persist.call_args.kwargs['content'].startswith('PAG Datadog cohort monitors PR #64'))
        # Text an agent puts beside the memory stays in it; tags may come as a list.
        with patch.object(server.vs, 'store', return_value='id') as persist, \
             patch('imprint.tagger.build_payload_tags', return_value={}):
            result = asyncio.run(call('store', {'title': 'st stubs', 'type': 'decision', 'project': 'service-devtool',
                                                'content': 'SBP is a shopware-adapter Double.', 'evidence': 'shopware.go',
                                                'reason': 'Adaptation guidance for the PAG rebase', 'importance': 70,
                                                'tags': ['st', 'nps', 'local-dev']}))
        text = str(result.content)
        self.assertIn('Kept in the note as text, since store does not take them: reason.', text)
        self.assertIn('Ignored arguments store does not take: importance.', text)
        self.assertEqual(persist.call_args.kwargs['content'],
                         'st stubs\n\nSBP is a shopware-adapter Double.\n\nReason: Adaptation guidance for the PAG rebase'
                         '\n\nEvidence: shopware.go')
        self.assertEqual(sorted(persist.call_args.kwargs['tags']['domain']), ['local-dev', 'nps', 'st'])
        # A validation alias still counts as an argument the tool takes.
        self.assertEqual(server._accepted_arguments(asyncio.run(server.mcp.get_tool('get_memory'))),
                         {'memory_id', 'id', 'start', 'max_chars', 'workspace'})

    def test_a_server_running_older_code_than_installed_says_so_once(self):
        import asyncio

        async def call():
            async with Client(server.mcp) as client:
                return str((await client.call_tool('store', {'content': 'A decision.'})).content)
        notice = [middleware for middleware in server.mcp.middleware if isinstance(middleware, server._StaleNotice)][0]
        self.addCleanup(setattr, notice, 'told', False)
        with patch.object(server.vs, 'store', return_value='id'), patch('imprint.tagger.build_payload_tags', return_value={}):
            self.assertNotIn('was updated', asyncio.run(call()))
            with patch.object(server, '_code_stamp', return_value=(1, 1)):
                self.assertIn('Imprint was updated after this server started', asyncio.run(call()))
                self.assertNotIn('was updated', asyncio.run(call()), 'said once')

    def test_invalid_search_does_not_initialize_models(self):
        with patch.object(server.vs, 'search') as search:
            self.assertIn('Error', server.search('   '))
            self.assertIn('Error', server.search('x' * 4001))
            search.assert_not_called()

    def test_store_rejects_blank_content(self):
        with patch.object(server.vs, 'store') as persist:
            self.assertIn('Error', server.store('   '))
            persist.assert_not_called()

    def test_url_ingest_receives_captured_workspace(self):
        with patch.object(server, '_validate_ws', return_value=('chosen', None)), \
             patch.object(server.vs, 'get_url_sources', return_value={}), \
             patch('imprint.cli_ingest_url.ingest_one', return_value=(1, 'stored')) as ingest:
            server.ingest_url('https://example.com/doc', workspace='chosen')
            self.assertEqual(ingest.call_args.kwargs['workspace'], 'chosen')

    def test_file_chunks_are_bounded_with_continuation(self):
        chunks = [{'chunk_index': i, 'content': str(i) * 7000} for i in range(3)]
        with patch.object(server.vs, 'get_chunks_by_source', return_value=chunks) as read:
            response = server.file_chunks('project/large.py')
            self.assertLess(len(response), 12500)
            self.assertIn('start=1', response)
            self.assertEqual(read.call_args.kwargs['end'], 19)


class MemoryArgumentContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_cursor_id_call_and_canonical_call_read_the_same_bounded_record(self):
        # Exercise MCP validation itself: direct Python calls bypass aliases.
        memory_id = 'b93a69214464b791'
        with patch.object(server, '_validate_ws', return_value=('chosen', None)) as workspace, \
                patch.dict(server._session, {'notice': ''}), \
                patch.object(server, 'memory_row', side_effect=lambda *a, **kw: {
                    'id': memory_id, 'content': 'detail ' * 1500}) as read:
            async with Client(server.mcp) as client:
                tools = {tool.name: tool for tool in await client.list_tools()}
                tool = tools['get_memory']
                self.assertEqual(tool.input_schema['required'], ['memory_id'])
                self.assertNotIn('id', tool.input_schema['properties'])
                self.assertTrue(tool.annotations.read_only_hint)
                args = {'workspace': 'chosen', 'start': 100, 'max_chars': 200}
                canonical = await client.call_tool('get_memory', {'memory_id': memory_id, **args})
                cursor = await client.call_tool('get_memory', {'id': memory_id, **args})
                self.assertEqual(canonical.content, cursor.content)
                record = json.loads(cursor.content[0].text)
                self.assertEqual((record['id'], record['start'], len(record['content']), record['next_start']),
                                 (memory_id, 100, 200, 300))
                self.assertEqual(read.call_count, 2)
                read.assert_called_with(memory_id, workspace='chosen')
                workspace.assert_called_with('chosen')

    async def test_missing_and_ambiguous_identifiers_fail_before_reading(self):
        with patch.object(server, 'memory_row') as read, patch.dict(server._session, {'notice': ''}):
            async with Client(server.mcp) as client:
                for args in ({}, {'memory_id': 'one', 'id': 'two'}):
                    with self.subTest(args=args):
                        result = await client.call_tool('get_memory', args, raise_on_error=False)
                        self.assertTrue(result.is_error)
            read.assert_not_called()
