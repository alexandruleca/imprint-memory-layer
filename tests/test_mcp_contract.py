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
