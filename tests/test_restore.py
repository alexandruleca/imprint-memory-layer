"""A file gone from disk keeps its snapshot (ADR 0196), and `imprint restore`
writes indexed files back from their snapshots (ADR 0197)."""
import contextlib
import hashlib
import io
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from qdrant_client import QdrantClient, models as qm

from imprint import cli_restore, config, imprint_graph as kg, source_memory as sm, source_snapshots as ss
from imprint import vectorstore as vs

TOKEN = 'ghp_' + 'Zx9' * 12  # A GitHub token as redaction recognises one.
ROADMAP = '# Roadmap — café\r\n\r\n1. Keep the plans.\r\n2. Restore them.\r\n'
KEYS = f'Deploy with {TOKEN} on Fridays.\n'


class Fixture(unittest.TestCase):
    def setUp(self):
        kg._close_all()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / 'data'
        self.folder = Path(self.temp.name) / 'app'
        self.out = Path(self.temp.name) / 'out'
        (self.folder / 'plans').mkdir(parents=True)
        self.data.mkdir()
        env = patch.dict(os.environ, {'IMPRINT_DATA_DIR': str(self.data), 'IMPRINT_WORKSPACE': 'default',
                                      'IMPRINT_LLM_TAGS': '0', 'IMPRINT_REDACT_PRIVACY_FILTER': 'false'})
        env.start(); self.addCleanup(env.stop)
        os.environ.pop('IMPRINT_INGEST_KEEP_MISSING_DAYS', None)
        self.client = QdrantClient(':memory:')
        self.addCleanup(self.client.close)
        for item in (patch.object(vs, '_get_client', return_value=self.client),
                     patch.object(vs, '_wal_log'),
                     patch.object(self.client, 'create_payload_index'),
                     patch.object(config, 'EMBEDDING_DIM', 3),
                     patch('imprint.embeddings.embed_documents_batch', side_effect=lambda xs, **_: [[1., 0., 0.] for _ in xs]),
                     patch('imprint.embeddings.embed_document', return_value=[1., 0., 0.]),
                     patch('imprint.tagger.derive_zero_shot', return_value=[]),
                     patch('imprint.tagger.build_payload_tags', return_value={})):
            item.start(); self.addCleanup(item.stop)
        vs._collections_ready.clear(); vs._id_caches.clear()
        self.addCleanup(vs._collections_ready.clear)
        self.addCleanup(vs._id_caches.clear)
        self.addCleanup(kg._close_all)
        (self.folder / 'plans' / 'roadmap.md').write_bytes(ROADMAP.encode('utf-8'))
        (self.folder / 'plans' / 'keys.md').write_bytes(KEYS.encode('utf-8'))
        (self.folder / 'main.py').write_bytes(b'def main():\n    return 1\n')

    def index(self, command='ingest', **env):
        from imprint import cli_index
        out = io.StringIO()
        with patch('sys.argv', ['index', str(self.folder), f'{self.folder}:app']), \
                patch.dict(os.environ, {key.upper(): value for key, value in env.items()}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            cli_index.main(command=command)
        return out.getvalue()

    def chunks(self, source):
        _, coll = vs._ensure_collection()
        rows, _ = self.client.scroll(coll, scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key='source', match=qm.MatchValue(value=source))]), limit=100)
        return rows

    def head(self, source, project, text, *, path, rel, kind='text', details=None, foreign=False):
        """A snapshot as ingest or sync would leave it, without its chunks."""
        doc = sm.document(text, kind=kind, metadata=details or {})
        rows = sm.records(doc, prefix=f'[{rel}]\n')
        for row in rows:
            row.update(project=project, source=source, source_type='file')
        rows[0].update(source_path=str(path), source_hash=hashlib.sha256(text.encode()).hexdigest(),
                       source_relative_path=rel)
        snapshot = ss.prepare(doc, rows, source, project)
        if foreign:
            snapshot['metadata']['provenance']['source_device'] = {'id': 'peerdevice01', 'name': 'peer', 'os': 'linux'}
        ss.publish(snapshot, imported=foreign)


class MissingFileTests(Fixture):
    """ADR 0196."""

    def gone(self):
        self.index('ingest')
        before = ss.get('app/plans/roadmap.md', 'app')['metadata']
        (self.folder / 'plans' / 'roadmap.md').unlink()
        return before, self.index('refresh')

    def test_a_file_gone_from_disk_leaves_search_and_keeps_its_snapshot(self):
        before, output = self.gone()
        self.assertEqual(self.chunks('app/plans/roadmap.md'), [])
        snapshot = ss.get('app/plans/roadmap.md', 'app')
        self.assertEqual(snapshot['text'], ROADMAP)
        meta = snapshot['metadata']
        self.assertAlmostEqual(meta[ss.MISSING], time.time(), delta=120)
        # Neither a new version nor a newer head for sync.
        self.assertEqual((meta['version'], meta['updated_at'], meta['manifest']),
                         (before['version'], before['updated_at'], before['manifest']))
        self.assertEqual([m['updated_at'] for m in ss.list_metadata() if m['source'] == 'app/plans/roadmap.md'],
                         [before['updated_at']])
        self.assertIn('Missing:  1 files gone from disk', output)
        # A head without chunks is not a source to repair: the next refresh
        # neither stores nor marks anything again.
        self.assertNotIn('app/plans/roadmap.md', vs.get_source_states())
        again = self.index('refresh')
        self.assertNotIn('Missing:', again)
        self.assertIn('Stored:   0', again)
        self.assertEqual(ss.get('app/plans/roadmap.md', 'app')['metadata'][ss.MISSING], meta[ss.MISSING])
        self.assertIsNotNone(ss.get('app/plans/keys.md', 'app'))

    def test_mcp_tools_say_the_file_is_missing_rather_than_the_index_broken(self):
        from imprint import server
        self.gone()
        listing = server.list_sources(project='app')
        line = next(line for line in listing.splitlines() if 'app/plans/roadmap.md' in line)
        self.assertIn('0 chunks', line)
        self.assertIn('missing from disk since', line)
        summary = server.file_summary('app/plans/roadmap.md')
        self.assertIn('Chunks: 0', summary)
        self.assertIn('missing from disk since', summary)
        read = json.loads(server.read_source('app/plans/roadmap.md', 'app'))
        self.assertEqual(read['content'], ROADMAP)
        self.assertIn('since', read['missing_from_disk'])
        self.assertFalse(read['index']['complete'])
        self.assertIn('missing from disk', read['index']['expected'])
        self.assertNotIn('missing_from_disk', json.loads(server.read_source('app/main.py', 'app')))

    def test_a_file_that_reappears_is_indexed_again_and_no_longer_missing(self):
        before, _ = self.gone()
        (self.folder / 'plans' / 'roadmap.md').write_bytes(ROADMAP.encode('utf-8'))
        self.index('refresh')
        self.assertTrue(self.chunks('app/plans/roadmap.md'))
        meta = ss.get('app/plans/roadmap.md', 'app')['metadata']
        self.assertNotIn(ss.MISSING, meta)
        self.assertGreater(meta['updated_at'], before['updated_at'])

    def test_a_file_back_on_disk_is_not_missing_even_when_it_cannot_be_read(self):
        self.gone()
        (self.folder / 'plans' / 'roadmap.md').write_bytes(b'\xff not UTF-8')
        with self.assertRaises(SystemExit):
            self.index('refresh')
        snapshot = ss.get('app/plans/roadmap.md', 'app')
        self.assertEqual(snapshot['text'], ROADMAP)  # The last good text stays.
        self.assertNotIn(ss.MISSING, snapshot['metadata'])

    def test_missing_snapshots_expire_after_the_setting_unless_the_file_came_back(self):
        self.gone()
        (self.folder / 'plans' / 'keys.md').unlink()
        self.index('refresh')
        (self.folder / 'plans' / 'keys.md').write_bytes(KEYS.encode('utf-8'))
        later = time.time() + 3 * 86400
        self.assertEqual(ss.expire_missing(5, now=later), [])
        expired = ss.expire_missing(2, now=later)
        self.assertEqual([meta['source'] for meta in expired], ['app/plans/roadmap.md'])
        self.assertIsNone(ss.get('app/plans/roadmap.md', 'app'))
        self.assertIsNotNone(ss.get('app/plans/keys.md', 'app'))  # Back on disk.

    def test_another_devices_head_never_expires_here(self):
        self.head('app/notes.md', 'app', 'notes\n', path='/peer/app/notes.md', rel='notes.md', foreign=True)
        ss.mark_missing('app/notes.md', 'app', now=1.0)  # Only a local refresh marks; still, not ours.
        self.assertEqual(ss.expire_missing(0), [])
        self.assertIsNotNone(ss.get('app/notes.md', 'app'))

    def test_zero_days_removes_the_snapshot_with_the_chunks_as_before(self):
        self.gone()  # Marked under the default.
        (self.folder / 'plans' / 'keys.md').unlink()
        output = self.index('refresh', imprint_ingest_keep_missing_days='0')
        self.assertIsNone(ss.get('app/plans/keys.md', 'app'))
        self.assertIsNone(ss.get('app/plans/roadmap.md', 'app'))  # The earlier mark expires too.
        self.assertIn('Expired:  1 snapshots', output)
        self.assertNotIn('Missing:', output)

    def test_purge_removes_a_missing_head(self):
        from imprint import purge
        self.gone()
        result = purge.purge(str(self.folder / 'plans' / 'roadmap.md'), apply=True)
        self.assertEqual(result['sources'], 1)
        self.assertIsNone(ss.get('app/plans/roadmap.md', 'app'))

    def test_the_mark_stays_on_this_device(self):
        self.gone()
        snapshot = ss.get('app/plans/roadmap.md', 'app')
        # An older release validates and versions a marked head unchanged.
        ss._validate(snapshot['metadata'], snapshot['text'])
        self.assertEqual(ss._version(snapshot['metadata']), snapshot['metadata']['version'])
        frames = list(ss.export_parts('default'))
        sent = [json.loads(zlib_inflate(frames, transfer)) for transfer in {frame['transfer'] for frame in frames}]
        self.assertFalse([s for s in sent if ss.MISSING in s['metadata']])
        config.register_workspace('other')
        importer = ss.Importer('other')
        for frame in frames:
            importer.feed(frame)
        importer.finish()
        received = ss.get('app/plans/roadmap.md', 'app', 'other')['metadata']
        self.assertNotIn(ss.MISSING, received)
        self.assertEqual(received['updated_at'], snapshot['metadata']['updated_at'])
        # A sender that kept the mark does not mark this device's copy.
        ss.publish(snapshot, 'other', imported=True)
        self.assertNotIn(ss.MISSING, ss.get('app/plans/roadmap.md', 'app', 'other')['metadata'])

    def test_scrub_keeps_the_mark(self):
        self.gone()
        snapshot = ss.get('app/plans/roadmap.md', 'app')
        with patch('imprint.redaction.redact_all', side_effect=lambda xs: [x.replace('plans', '[REDACTED:secret:5]') for x in xs]):
            clean = ss.sanitize(snapshot)
        self.assertNotEqual(clean['text'], snapshot['text'])
        self.assertEqual(clean['metadata'][ss.MISSING], snapshot['metadata'][ss.MISSING])


def zlib_inflate(frames, transfer):
    import base64
    import zlib
    parts = sorted((frame for frame in frames if frame['transfer'] == transfer), key=lambda frame: frame['part'])
    return zlib.decompress(b''.join(base64.b64decode(frame['data']) for frame in parts)).decode('utf-8')


class RestoreTests(Fixture):
    """ADR 0197."""

    def setUp(self):
        super().setUp()
        self.index('ingest')
        self.mtime = os.stat(self.folder / 'plans' / 'roadmap.md').st_mtime
        shutil.rmtree(self.folder / 'plans')
        self.index('refresh')

    def restore(self, **options):
        return cli_restore.restore('default', **options)

    def statuses(self, result):
        return {entry['path']: (entry['status'], entry['quality']) for entry in result['entries']}

    def test_a_preview_writes_nothing_and_apply_restores_exactly_or_says_what_was_redacted(self):
        root = str(self.folder / 'plans')
        preview = self.restore(root=root)
        self.assertEqual(self.statuses(preview), {'roadmap.md': ('write', 'exact'), 'keys.md': ('write', 'redacted')})
        self.assertFalse((self.folder / 'plans').exists())
        keys = next(entry for entry in preview['entries'] if entry['path'] == 'keys.md')
        self.assertIn('1 redacted secret', keys['detail'])

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(cli_restore.main([root, '--apply']), 0)
        self.assertIn('Wrote 2 files (1 exact, 1 with redacted secrets, 0 differing)', output.getvalue())
        self.assertEqual((self.folder / 'plans' / 'roadmap.md').read_bytes(), ROADMAP.encode('utf-8'))
        self.assertAlmostEqual(os.stat(self.folder / 'plans' / 'roadmap.md').st_mtime, self.mtime, delta=1)
        restored_keys = (self.folder / 'plans' / 'keys.md').read_text(encoding='utf-8')
        self.assertNotIn(TOKEN, restored_keys)
        self.assertIn('[REDACTED:github-token:40]', restored_keys)
        # No temporary file is left behind.
        self.assertEqual(sorted(p.name for p in (self.folder / 'plans').iterdir()), ['keys.md', 'roadmap.md'])

    def test_existing_files_are_compared_only_with_all_and_replaced_only_with_overwrite(self):
        root = str(self.folder / 'plans')
        self.restore(root=root, apply=True)
        edited = b'my newer roadmap\n'
        (self.folder / 'plans' / 'roadmap.md').write_bytes(edited)
        self.assertEqual(self.statuses(self.restore(root=root, apply=True))['roadmap.md'][0], 'present')
        compared = self.restore(root=root, every=True, apply=True)
        self.assertEqual({path: status for path, (status, _) in self.statuses(compared).items()},
                         {'roadmap.md': 'keep', 'keys.md': 'same'})
        self.assertEqual((self.folder / 'plans' / 'roadmap.md').read_bytes(), edited)
        replaced = self.restore(root=root, every=True, overwrite=True, apply=True)
        self.assertEqual(self.statuses(replaced)['roadmap.md'], ('replace', 'exact'))
        self.assertEqual((self.folder / 'plans' / 'roadmap.md').read_bytes(), ROADMAP.encode('utf-8'))

    def test_to_writes_the_files_under_another_folder_at_the_same_paths(self):
        result = self.restore(root=str(self.folder), to=str(self.out), apply=True)
        self.assertEqual(self.statuses(result)['main.py'], ('write', 'exact'))  # The destination lacked it.
        self.assertEqual((self.out / 'plans' / 'roadmap.md').read_bytes(), ROADMAP.encode('utf-8'))
        self.assertEqual((self.out / 'main.py').read_bytes(), b'def main():\n    return 1\n')
        self.assertFalse((self.folder / 'plans').exists())

    def test_a_project_restores_the_files_of_every_device_under_to(self):
        self.head('app/docs/notes.txt', 'app', 'Written on the laptop.\n', path='/home/peer/app/docs/notes.txt',
                  rel='docs/notes.txt', foreign=True)
        # By folder, only this device's files: the laptop's path means nothing here.
        self.assertNotIn('docs/notes.txt', self.statuses(self.restore(root=str(self.folder), to=str(self.out))))
        result = self.restore(project='app', to=str(self.out), apply=True)
        self.assertEqual(self.statuses(result)['docs/notes.txt'], ('write', 'exact'))
        self.assertEqual((self.out / 'docs' / 'notes.txt').read_text(), 'Written on the laptop.\n')
        self.assertEqual((self.out / 'plans' / 'roadmap.md').read_bytes(), ROADMAP.encode('utf-8'))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli_restore.main(['--project', 'app'])  # Another device's files need a destination.

    def test_paths_from_synced_snapshots_cannot_leave_the_destination(self):
        for index, rel in enumerate(('../../escape.txt', '/etc/escape.txt', 'C:/escape.txt')):
            self.head(f'app/bad{index}', 'app', 'not here\n', path=f'/peer/bad{index}', rel=rel, foreign=True)
        result = self.restore(project='app', to=str(self.out), apply=True)
        refused = [entry for entry in result['entries'] if entry['status'] == 'refused']
        self.assertEqual(len(refused), 3)
        self.assertEqual(result['errors'], 3)
        self.assertFalse(list(Path(self.temp.name).rglob('escape.txt')))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli_restore.main(['--project', 'app', '--to', str(self.out)]), 1)

    def test_a_link_inside_the_destination_cannot_lead_outside_it(self):
        outside = Path(self.temp.name) / 'outside'
        outside.mkdir()
        self.out.mkdir()
        try:
            os.symlink(outside, self.out / 'plans', target_is_directory=True)
        except (OSError, NotImplementedError):
            if os.name != 'nt':
                self.skipTest('this account cannot create symbolic links')
            import _winapi  # A junction needs no privilege.
            _winapi.CreateJunction(str(outside), str(self.out / 'plans'))
        result = self.restore(root=str(self.folder), to=str(self.out), apply=True)
        self.assertEqual(self.statuses(result)['plans/roadmap.md'][0], 'refused')
        self.assertEqual(list(outside.iterdir()), [])

    def test_documents_restore_only_their_extracted_text_beside_their_name(self):
        docs = self.folder / 'docs'
        self.head('app/docs/brief.pdf', 'app', 'The brief, as text.\n', kind='extracted', details={'parts': []},
                  path=docs / 'brief.pdf', rel='docs/brief.pdf')
        self.head('app/docs/budget.xlsx', 'app', '| a | b |\n', details={'data_file': {'format': 'xlsx', 'reason': 'large'}},
                  path=docs / 'budget.xlsx', rel='docs/budget.xlsx')
        self.head('app/chat', 'app', 'a conversation\n', kind='conversation', path=docs / 'chat.jsonl', rel='docs/chat.jsonl')
        result = self.restore(root=str(docs), apply=True)
        self.assertEqual(self.statuses(result), {'brief.pdf.extracted.txt': ('extract', 'extracted'),
                                                 'budget.xlsx.extracted.txt': ('extract', 'extracted')})
        self.assertEqual(result['counts']['not_files'], 1)
        self.assertEqual((docs / 'brief.pdf.extracted.txt').read_text(), 'The brief, as text.\n')
        self.assertFalse((docs / 'brief.pdf').exists())
        self.assertFalse((docs / 'budget.xlsx').exists())
        (docs / 'brief.pdf').write_bytes(b'%PDF-1.7')
        (docs / 'brief.pdf.extracted.txt').unlink()
        again = self.restore(root=str(docs), apply=True)
        self.assertEqual(self.statuses(again)['brief.pdf.extracted.txt'][0], 'present')  # The document is back.
        self.assertFalse((docs / 'brief.pdf.extracted.txt').exists())

    def test_two_snapshots_of_one_path_restore_the_newer(self):
        # The same file indexed again later under another project name.
        self.head('renamed/plans/roadmap.md', 'renamed', 'the later plan\n', path=self.folder / 'plans' / 'roadmap.md',
                  rel='plans/roadmap.md')
        result = self.restore(root=str(self.folder / 'plans'), apply=True)
        sources = {entry['source']: entry['status'] for entry in result['entries']}
        self.assertEqual(sources['renamed/plans/roadmap.md'], 'write')
        self.assertEqual(sources['app/plans/roadmap.md'], 'older')
        self.assertEqual((self.folder / 'plans' / 'roadmap.md').read_text(), 'the later plan\n')
        self.assertEqual(result['errors'], 0)


if __name__ == '__main__':
    unittest.main()
