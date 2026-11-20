import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

import httpx

from imprint import browser_capture as browser, browser_worker, config, queue, queue_lock

EXTENSION = 'a' * 32
OTHER = 'b' * 32


def capture(kind='page', **fields):
    return {'project': 'reading', 'documents': [{
        'kind': kind, 'url': 'https://example.com/article', 'title': 'Useful article',
        'text': 'A useful article with enough text for an indexed memory.', **fields}]}


class TemporaryData:
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        env = patch.dict(os.environ, {'IMPRINT_DATA_DIR': temporary.name})
        env.start()
        self.addCleanup(env.stop)
        known = patch.object(config, 'get_known_workspaces', return_value=['default', 'other'])
        known.start()
        self.addCleanup(known.stop)
        active = patch.object(config, 'get_active_workspace', return_value='default')
        active.start()
        self.addCleanup(active.stop)


class PairingTests(TemporaryData, unittest.TestCase):
    def test_pair_rotation_revoke_and_hashed_storage(self):
        token = browser.pair(EXTENSION, 'other')
        self.assertEqual(browser.grant(EXTENSION)['workspace'], 'other')
        self.assertEqual(browser.grant(EXTENSION)['token_hash'], browser.token_hash(token))
        self.assertNotIn(token.encode(), (self.data / 'browser-pairings.sqlite3').read_bytes())
        if os.name != 'nt':
            self.assertEqual((self.data / 'browser-pairings.sqlite3').stat().st_mode & 0o777, 0o600)
        second = browser.pair(EXTENSION, 'default')
        self.assertNotEqual(token, second)
        self.assertEqual(browser.grant(EXTENSION)['token_hash'], browser.token_hash(second))
        browser.revoke(EXTENSION)
        self.assertIsNone(browser.grant(EXTENSION))

    def test_unknown_grant_does_not_create_state(self):
        self.assertIsNone(browser.grant(EXTENSION))
        self.assertFalse((self.data / 'browser-pairings.sqlite3').exists())
        for value in ('*', 'a' * 31, 'q' * 32, EXTENSION + '/page', None):
            with self.assertRaises(ValueError):
                browser.grant(value)
        with self.assertRaises(ValueError):
            browser.pair(EXTENSION, 'unknown')


class IdentityTests(unittest.TestCase):
    def key(self, body):
        return browser.normalize_capture(body)['documents'][0]['source_key']

    def test_page_updates_and_selections_have_separate_identities(self):
        page = capture()
        first = self.key(page)
        page['documents'][0]['text'] = 'New version of this useful article with revised content.'
        self.assertEqual(first, self.key(page))
        selection = capture('selection')
        self.assertNotEqual(first, self.key(selection))
        selected_key = self.key(selection)
        selection['documents'][0]['text'] = 'A different passage selected from the same article.'
        self.assertNotEqual(selected_key, self.key(selection))
        self.assertNotEqual(self.key(capture(url='https://example.com/#/one')),
                            self.key(capture(url='https://example.com/#/two')))

    def test_gmail_keys_bind_account_and_message_independently_of_tab_route(self):
        doc = capture('gmail', url='https://mail.google.com/mail/u/0/#inbox/thread',
                      account='one@example.com', message_id='123abc')
        first = self.key(doc)
        doc['documents'][0]['url'] = 'https://mail.google.com/mail/u/1/#all/other-thread'
        self.assertEqual(first, self.key(doc))
        doc['documents'][0]['account'] = 'two@example.com'
        self.assertNotEqual(first, self.key(doc))
        doc['documents'][0]['account'] = 'ONE@example.com'
        self.assertEqual(first, self.key(doc))
        doc['documents'][0]['message_id'] = 'other-message'
        self.assertNotEqual(first, self.key(doc))

    def test_invalid_and_oversized_content_is_rejected(self):
        for body in ([], {}, capture(text='short'), capture(text='x' * 800001),
                     capture(url='file:///etc/passwd'), capture(url='https://user:pass@example.com/'),
                     capture(url='https://example.com:bad/'), capture(url='https://example.com/\nsecret'),
                     capture('gmail'), capture(url='https://mail.google.com/mail/'),
                     capture('gmail', url='https://mail.google.com/', account='', message_id='abc'),
                     {'project': 'reading', 'documents': [capture()['documents'][0]] * 2},
                     capture(text='日' * 400000)):
            with self.subTest(body_type=type(body)), self.assertRaises(ValueError):
                browser.normalize_capture(body)


class BrowserAPITests(TemporaryData, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.token = browser.pair(EXTENSION, 'other')
        self.headers = {'Origin': browser.extension_origin(EXTENSION),
                        'X-Imprint-Extension-Id': EXTENSION, 'Authorization': 'Bearer ' + self.token}

    async def request(self, path='/api/browser/status', method='GET', headers=None, peer=('127.0.0.1', 1234), **kwargs):
        from imprint.api import app
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=peer),
                                     base_url='http://127.0.0.1:8420') as client:
            return await client.request(method, path, headers=self.headers if headers is None else headers, **kwargs)

    async def test_credentials_origin_peer_host_and_scope_boundaries(self):
        self.assertEqual((await self.request()).json()['workspace'], 'other')
        # Chrome can omit Origin on extension GETs; credentials + ID remain mandatory.
        self.assertEqual((await self.request(headers={k: v for k, v in self.headers.items() if k != 'Origin'})).status_code, 200)
        for headers in ({}, {**self.headers, 'Authorization': 'Bearer invalid'},
                        {**self.headers, 'Origin': 'https://evil.example'},
                        {**self.headers, 'Origin': 'null'},
                        {**self.headers, 'Origin': browser.extension_origin(OTHER)},
                        {**self.headers, 'Host': 'evil.example'}):
            self.assertIn((await self.request(headers=headers)).status_code, (401, 403))
        self.assertEqual((await self.request(peer=('192.168.1.2', 4))).status_code, 403)
        for path in ('/api/config', '/api/workspaces', '/api/commands/wipe'):
            self.assertEqual((await self.request(path, method='POST' if 'commands' in path else 'GET')).status_code, 403)
        browser.pair(EXTENSION, 'other')
        self.assertEqual((await self.request()).status_code, 401)

    async def test_preflight_only_for_exact_paired_origins_and_headers(self):
        headers = {'Origin': browser.extension_origin(EXTENSION), 'Access-Control-Request-Method': 'POST',
                   'Access-Control-Request-Headers': 'authorization,content-type,x-imprint-extension-id'}
        response = await self.request(method='OPTIONS', headers=headers)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers['access-control-allow-origin'], headers['Origin'])
        for change in ({'Origin': browser.extension_origin(OTHER)},
                       {'Access-Control-Request-Method': 'DELETE'},
                       {'Access-Control-Request-Headers': 'x-arbitrary-header'}):
            self.assertEqual((await self.request(method='OPTIONS', headers={**headers, **change})).status_code, 403)

    async def test_enqueue_pins_workspace_and_jobs_do_not_expose_capture_text(self):
        response = await self.request('/api/browser/captures', method='POST', json=capture())
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.headers['access-control-allow-origin'], self.headers['Origin'])
        job_id = response.json()['job_id']
        job = queue.get_job(job_id)
        self.assertEqual(job['body']['_workspace'], 'other')
        self.assertEqual(job['command'], 'browser-capture')
        public = await self.request('/api/browser/jobs/' + job_id)
        self.assertEqual(public.status_code, 200)
        self.assertNotIn('body', public.json())
        browser.pair(OTHER, 'other')
        outsider = {'Origin': browser.extension_origin(OTHER), 'X-Imprint-Extension-Id': OTHER,
                    'Authorization': 'Bearer ' + browser.pair(OTHER, 'other')}
        self.assertEqual((await self.request('/api/browser/jobs/' + job_id, headers=outsider)).status_code, 404)
        unrelated = queue.enqueue('status', {'workspace': 'other'})
        self.assertEqual((await self.request('/api/browser/jobs/' + unrelated)).status_code, 404)
        response = await self.request('/api/browser/captures', method='POST', json={**capture(), 'workspace': 'default'})
        self.assertEqual(response.status_code, 400)

    async def test_request_validation_happens_before_queue_submission(self):
        with patch.object(queue, 'enqueue') as enqueue:
            for content, content_type, code in ((b'{', 'application/json', 400),
                                               (b'[]', 'application/json', 400),
                                               (b'x' * 1000001, 'application/json', 413),
                                               (b'{}', 'text/plain', 415)):
                response = await self.request('/api/browser/captures', method='POST',
                    content=content, headers={**self.headers, 'Content-Type': content_type})
                self.assertEqual(response.status_code, code, response.text)
            enqueue.assert_not_called()

    async def test_local_discovery_and_captures_need_no_pairing_and_pin_the_selected_workspace(self):
        browser.revoke(EXTENSION)
        prefix = '/api/browser/local/'
        headers = {'X-Imprint-Extension-Id': EXTENSION}
        result = await self.request(prefix + 'status', headers=headers)
        self.assertEqual(result.json()['workspaces'], ['default', 'other'])
        self.assertEqual(result.json()['active'], 'default')
        headers['Origin'] = browser.extension_origin(EXTENSION)
        for content in (capture(), {'url': 'https://example.com/article', 'project': 'reading'}):
            result = await self.request(prefix + 'captures', method='POST', headers=headers,
                                        json={**content, 'workspace': 'other'})
            self.assertEqual(result.status_code, 202, result.text)
            job_id = result.json()['job_id']
            body = queue.get_job(job_id)['body']
            self.assertEqual(body['_workspace'], 'other')
            self.assertNotIn('_browser_pairing_hash', body)
            self.assertEqual(config.get_active_workspace(), 'default')
            result = await self.request(prefix + 'jobs/' + job_id, headers=headers)
            self.assertEqual(result.status_code, 200)
            self.assertNotIn('body', result.json())
            other = {'X-Imprint-Extension-Id': OTHER, 'Origin': browser.extension_origin(OTHER)}
            self.assertEqual((await self.request(prefix + 'jobs/' + job_id, headers=other)).status_code, 404)
        for body in (capture(), {**capture(), 'workspace': 'missing'},
                     {'workspace': 'other', 'url': 'https://user:secret@example.com/'},
                     {**capture(), 'workspace': 'other', 'url': 'https://example.com'}):
            result = await self.request(prefix + 'captures', method='POST', headers=headers, json=body)
            self.assertEqual(result.status_code, 400)

    async def test_firefox_sends_its_per_install_uuid_and_matching_moz_extension_origin(self):
        firefox, other = '6a8a5c4e-1b2d-4c3e-9f00-123456789abc', '00000000-0000-4000-8000-000000000000'
        prefix = '/api/browser/local/'
        headers = {'X-Imprint-Extension-Id': firefox, 'Origin': 'moz-extension://' + firefox}
        self.assertEqual((await self.request(prefix + 'status', headers={'X-Imprint-Extension-Id': firefox})).status_code, 200)
        result = await self.request(prefix + 'captures', method='POST', headers=headers, json={**capture(), 'workspace': 'other'})
        self.assertEqual(result.status_code, 202, result.text)
        job = prefix + 'jobs/' + result.json()['job_id']
        self.assertEqual((await self.request(job, headers=headers)).status_code, 200)
        self.assertEqual((await self.request(job, headers={'X-Imprint-Extension-Id': other})).status_code, 404)
        preflight = await self.request(prefix + 'captures', method='OPTIONS', headers={
            'Origin': 'moz-extension://' + firefox, 'Access-Control-Request-Method': 'POST',
            'Access-Control-Request-Headers': 'content-type, x-imprint-extension-id'})
        self.assertEqual(preflight.status_code, 204)
        self.assertEqual(preflight.headers['access-control-allow-origin'], 'moz-extension://' + firefox)
        # The origin must be the one this UUID names, in Firefox's scheme.
        for origin in ('moz-extension://' + other, 'chrome-extension://' + firefox, 'moz-extension://' + EXTENSION,
                       'moz-extension://' + firefox.upper()):
            result = await self.request(prefix + 'captures', method='POST', json={**capture(), 'workspace': 'other'},
                                        headers={**headers, 'Origin': origin})
            self.assertEqual(result.status_code, 403, origin)
        for bad in (firefox.upper(), firefox + '0', 'capture@imprintmcp.alexandruleca.com'):
            result = await self.request(prefix + 'status', headers={'X-Imprint-Extension-Id': bad})
            self.assertEqual(result.status_code, 403, bad)

    async def test_local_routes_reject_website_origins_and_container_peers(self):
        from imprint.api import app
        path = '/api/browser/local/captures'
        for origin in (None, 'https://example.com', 'null', 'http://127.0.0.1:8420', browser.extension_origin(OTHER)):
            headers = {'X-Imprint-Extension-Id': EXTENSION}
            if origin is not None:
                headers['Origin'] = origin
            result = await self.request(path, method='POST', headers=headers, json={**capture(), 'workspace': 'other'})
            self.assertEqual(result.status_code, 403)
        with patch.object(app.state, 'container_bind', True):
            self.assertEqual((await self.request('/api/browser/local/status', peer=('172.17.0.1', 80))).status_code, 403)

    async def test_binary_upload_is_bounded_and_can_be_retried_without_reuploading(self):
        path = '/api/browser/local/files'
        metadata = {'filename': 'résumé.txt', 'project': 'reading', 'workspace': 'other'}
        headers = {'X-Imprint-Extension-Id': EXTENSION, 'Origin': browser.extension_origin(EXTENSION),
                   'Content-Type': 'application/octet-stream', 'X-Imprint-File': quote(json.dumps(metadata))}
        payload = b'This attachment contains private notes for indexing.'
        with patch.object(browser, 'file_limit', return_value=10):
            async def chunks():
                yield payload[:8]
                yield payload[8:]
            self.assertEqual((await self.request(path, method='POST', headers=headers, content=chunks())).status_code, 413)
        for change in ({'filename': '../private.txt'}, {'workspace': 'unknown'}, {'filename': 'script.exe'}):
            invalid = {**headers, 'X-Imprint-File': quote(json.dumps({**metadata, **change}))}
            self.assertEqual((await self.request(path, method='POST', headers=invalid, content=payload)).status_code, 400)
        wrong_origin = {**headers, 'Origin': 'https://example.com'}
        self.assertEqual((await self.request(path, method='POST', headers=wrong_origin, content=payload)).status_code, 403)
        response = await self.request(path, method='POST', headers=headers, content=payload)
        self.assertEqual(response.status_code, 202, response.text)
        job_id = response.json()['job_id']
        body = queue.get_job(job_id)['body']
        self.assertEqual(body['_workspace'], 'other')
        self.assertEqual(body['filename'], 'résumé.txt')
        self.assertNotIn(payload.decode(), str(body))
        self.assertEqual(queue.read_browser_file(body['file_digest']), payload)
        with queue._connect() as conn:
            conn.execute("UPDATE jobs SET status='failed' WHERE id=?", (job_id,))
        retry = queue.resubmit(job_id)
        self.assertEqual(queue.get_job(retry['job_id'])['body']['file_digest'], body['file_digest'])


class FileRetentionTests(TemporaryData, unittest.TestCase):
    def submit(self, data, status, ended_days_ago=0.0):
        job_id = queue.submit_browser_file({'filename': 'notes.txt', 'workspace': 'other',
                                            '_browser_extension_id': EXTENSION}, data)
        ended = None if status in ('queued', 'running') else time.time() - ended_days_ago * 86400
        with queue._connect() as conn:
            conn.execute('UPDATE jobs SET status=?, ended_at=? WHERE id=?', (status, ended, job_id))
        return job_id, queue.get_job(job_id)['body']['file_digest']

    def stored(self):
        with queue._connect() as conn:
            return {row['digest'] for row in conn.execute('SELECT digest FROM browser_files')}

    def on_disk(self):
        return b''.join(path.read_bytes() for path in self.data.glob('queue.sqlite3*'))

    def test_files_stay_only_while_a_job_can_still_use_them(self):
        indexed_bytes = b'indexed private notes ' * 40
        indexed, _ = self.submit(indexed_bytes, 'done')
        failed, failed_digest = self.submit(b'failed notes', 'failed', 1)
        expired, _ = self.submit(b'expired notes', 'cancelled', 8)
        _, waiting_digest = self.submit(b'waiting notes', 'queued')
        _, shared_digest = self.submit(b'shared notes', 'done')
        self.submit(b'shared notes', 'running')
        with queue._connect() as conn:
            conn.execute('INSERT INTO browser_files VALUES (?, ?)', ('0' * 64, b'orphan'))
        self.assertTrue(indexed_bytes in self.on_disk())
        self.assertEqual(queue.collect_browser_files(), 3)
        self.assertEqual(self.stored(), {failed_digest, waiting_digest, shared_digest})
        self.assertFalse(indexed_bytes[:40] in self.on_disk(), 'deleted file bytes remain on disk')
        for job_id in (indexed, expired):
            with self.assertRaisesRegex(queue.NotRetryable, 'capture it again'):
                queue.resubmit(job_id)
        # A retry names the file, so it outlives the failed job's window.
        queue.resubmit(failed)
        with queue._connect() as conn:
            conn.execute('UPDATE jobs SET ended_at=? WHERE id=?', (time.time() - 30 * 86400, failed))
        self.assertEqual(queue.collect_browser_files(), 0)
        self.assertIn(failed_digest, self.stored())

    def test_dispatcher_and_startup_collect(self):
        self.submit(b'indexed notes', 'done')
        job_id, digest = self.submit(b'unreadable', 'queued')
        asyncio.run(queue._run_job(queue.get_job(job_id)))
        self.assertEqual(queue.get_job(job_id)['status'], 'failed')
        self.assertEqual(self.stored(), {digest})
        with queue._connect() as conn:
            conn.execute('UPDATE jobs SET ended_at=? WHERE id=?', (time.time() - 8 * 86400, job_id))
        queue.recover_on_startup()
        self.assertEqual(self.stored(), set())


class WorkerTests(TemporaryData, unittest.TestCase):
    def make_job(self):
        browser.pair(EXTENSION, 'other')
        pairing = browser.grant(EXTENSION)
        job_id = queue.enqueue('browser-capture', {**capture(), 'workspace': 'other',
            '_browser_extension_id': EXTENSION, '_browser_pairing_hash': pairing['token_hash']})
        with queue._connect() as conn:
            conn.execute("UPDATE jobs SET status='running' WHERE id=?", (job_id,))
        return job_id

    def test_token_free_jobs_use_the_existing_ingestion_modes_and_report_url_failures(self):
        def running(content):
            job_id = queue.enqueue('browser-capture', {**content, 'workspace': 'other',
                                                      '_browser_extension_id': EXTENSION})
            with queue._connect() as conn:
                conn.execute("UPDATE jobs SET status='running' WHERE id=?", (job_id,))
            return job_id

        job_id = running(capture())
        with patch('imprint.ingest_core.store_inline_content', return_value=(1, 'stored')) as store:
            browser_worker.run(job_id)
        self.assertEqual(store.call_args.kwargs['workspace'], 'other')
        job_id = running({'url': 'https://example.com/article', 'project': 'reading'})
        with patch('imprint.vectorstore.get_url_sources', return_value={}) as sources, \
                patch('imprint.cli_ingest_url.ingest_one', return_value=(0, 'error: HTTP 401')) as ingest:
            with self.assertRaisesRegex(RuntimeError, 'HTTP 401'):
                browser_worker.run(job_id)
            sources.assert_called_once_with('other')
            ingest.assert_called_once_with('https://example.com/article', 'reading', {}, workspace='other')

    def test_file_jobs_extract_spreadsheet_rows_and_keep_the_original_source(self):
        from io import BytesIO
        from openpyxl import Workbook

        workbook = Workbook()
        workbook.active.title = 'Budget'
        workbook.active.append(['Quarter', 'Budget'])
        workbook.active.append(['Q1', 1200])
        buffer = BytesIO()
        workbook.save(buffer)
        url = 'https://docs.google.com/spreadsheets/d/fixture/edit'
        job_id = queue.submit_browser_file(
            {'filename': 'Cost.xlsx', 'workspace': 'other', 'kind': 'google-sheet',
             'source_url': url, '_browser_extension_id': EXTENSION}, buffer.getvalue())
        with queue._connect() as conn:
            conn.execute("UPDATE jobs SET status='running' WHERE id=?", (job_id,))
        with patch('imprint.ingest_core.store_inline_content', return_value=(1, 'stored')) as store:
            browser_worker.run(job_id)
        self.assertIn('Quarter: Q1 | Budget: 1200', store.call_args.args[0])
        self.assertEqual(store.call_args.kwargs['workspace'], 'other')
        self.assertEqual(store.call_args.kwargs['extra_fields']['source_url'], url)

    def test_worker_uses_shared_pipeline_lock_and_provenance(self):
        job_id = self.make_job()
        with patch('imprint.ingest_core.store_inline_content', return_value=(2, 'stored')) as store:
            browser_worker.run(job_id)
        args, kwargs = store.call_args
        self.assertEqual(kwargs['workspace'], 'other')
        self.assertEqual(kwargs['source_type'], 'browser')
        self.assertEqual(kwargs['extra_fields']['source_url'], 'https://example.com/article')
        self.assertIn('Useful article', args[0])
        self.assertEqual(kwargs['extra_fields']['doc_metadata']['capture_kind'], 'page')
        descriptor = queue_lock.acquire('test', 'test')
        self.assertIsNotNone(descriptor)
        queue_lock.release(descriptor)
        argv = queue._build_argv('browser-capture', queue.get_job(job_id)['body'], job_id=job_id)
        self.assertEqual(argv[-2:], ['imprint.browser_worker', job_id])
        self.assertNotIn('Useful article', ' '.join(argv))

    def test_short_inline_notes_preserve_leading_whitespace(self):
        from imprint.ingest_core import store_inline_content
        with patch('imprint.tagger.build_payload_tags', return_value={}), \
                patch('imprint.vectorstore.replace_source', return_value=(1, 0)) as store:
            self.assertEqual(store_inline_content(' ' * 120 + 'A short useful note.', 'short-note'), (1, 'stored'))
            self.assertEqual(store.call_args.args[0][0]['content'], '[short-note]\n' + ' ' * 120 + 'A short useful note.')

    def test_revoked_queued_pairing_is_not_indexed(self):
        job_id = self.make_job()
        browser.revoke(EXTENSION)
        with patch('imprint.ingest_core.store_inline_content') as store:
            with self.assertRaisesRegex(ValueError, 'revoked'):
                browser_worker.run(job_id)
            store.assert_not_called()

    def test_failed_pipeline_releases_lock_and_dispatcher_records_failure(self):
        job_id = self.make_job()
        with patch('imprint.ingest_core.store_inline_content', return_value=(0, 'error: embed failed')):
            with self.assertRaisesRegex(RuntimeError, 'embed failed'):
                browser_worker.run(job_id)
        descriptor = queue_lock.acquire('test', 'test')
        self.assertIsNotNone(descriptor)
        queue_lock.release(descriptor)
        # Exercise the real dispatcher and subprocess without loading models:
        # a revoked pairing must exit nonzero and be marked failed.
        browser.revoke(EXTENSION)
        with queue._connect() as conn:
            conn.execute("UPDATE jobs SET status='queued' WHERE id=?", (job_id,))
        asyncio.run(queue._run_job(queue.get_job(job_id)))
        self.assertEqual(queue.get_job(job_id)['status'], 'failed')


    def test_capture_replacement_keeps_provenance_other_workspaces_and_failed_old_source(self):
        from qdrant_client import QdrantClient
        from imprint import vectorstore as vs
        client = QdrantClient(':memory:')
        self.addCleanup(client.close)
        vs._collections_ready.clear()
        vs._id_caches.clear()
        self.addCleanup(vs._collections_ready.clear)
        self.addCleanup(vs._id_caches.clear)
        job_id = self.make_job()
        with patch.object(vs, '_get_client', return_value=client), \
                patch.object(config, 'EMBEDDING_DIM', 3), \
                patch.object(client, 'create_payload_index'), \
                patch('imprint.embeddings.embed_documents_batch', side_effect=lambda texts: [[1., 0., 0.] for _ in texts]) as embed, \
                patch('imprint.tagger.build_payload_tags', return_value={}), \
                patch('imprint.tagger.derive_zero_shot', return_value=[]):
            # This short capture must survive the ordinary file chunker's 100-char floor.
            browser_worker.run(job_id)
            rows, _ = client.scroll(config.collection_name('other'), with_payload=True)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].payload['source_type'], 'browser')
            self.assertEqual(rows[0].payload['doc_metadata']['title'], 'Useful article')
            self.assertEqual(rows[0].payload['source_url'], 'https://example.com/article')
            self.assertEqual(rows[0].payload['source_size'], len(('Useful article\n\n' + capture()['documents'][0]['text']).encode()))
            # Re-capture updates this source; it must not append obsolete chunks.
            body = queue.get_job(job_id)['body']
            body['documents'][0]['text'] = 'Revised article with different but still short content.'
            with queue._connect() as conn:
                conn.execute('UPDATE jobs SET body_json=? WHERE id=?', (json.dumps(body), job_id))
            browser_worker.run(job_id)
            rows, _ = client.scroll(config.collection_name('other'), with_payload=True)
            self.assertEqual(len(rows), 1)
            self.assertIn('Revised article', rows[0].payload['content'])
            previous_id = rows[0].id
            body['documents'][0]['text'] = 'A third version must not erase the previous index on failure.'
            with queue._connect() as conn:
                conn.execute('UPDATE jobs SET body_json=? WHERE id=?', (json.dumps(body), job_id))
            embed.side_effect = RuntimeError('embedding offline')
            with self.assertRaisesRegex(RuntimeError, 'embedding offline'):
                browser_worker.run(job_id)
            rows, _ = client.scroll(config.collection_name('other'), with_payload=True)
            self.assertEqual([row.id for row in rows], [previous_id])
            self.assertNotIn(config.collection_name('default'), [collection.name for collection in client.get_collections().collections])


if __name__ == '__main__':
    unittest.main()
