"""Meeting recordings on paired devices that both turned it on (ADR 0177):
the manifest, pieces of audio that resume, and deletions."""
import contextvars
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from imprint import config, recording_sync, recordings, sync_deletions


class RecordingSyncTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.side = contextvars.ContextVar('device', default='a')
        self.dirs = {side: Path(directory.name) / side for side in 'ab'}
        for patcher in (patch.object(config, 'get_data_dir', side_effect=lambda: self.dirs[self.side.get()]),
                        patch.dict(os.environ, {'IMPRINT_SYNC_RECORDINGS': '1'}),
                        patch.object(recording_sync, 'PIECE', 4)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def on(self, side, fn, *args):
        token = self.side.set(side)
        try:
            return fn(*args)
        finally:
            self.side.reset(token)

    def recorded(self, audio=b'0123456789', transcript='# Standup\n\nYou: hello\n'):
        meta = recordings.start('Standup', consent=True)
        recordings.append(meta['id'], audio)
        recordings.stop(meta['id'], duration=3)
        (recordings.root() / meta['id'] / 'transcript.md').write_text(transcript, encoding='utf-8')
        recordings.update(meta['id'], status='ready', transcribed_at=time.time())
        return meta['id']

    def send(self, keys=None):
        """Plan and export on a, from b's manifest, as a sync does."""
        remote = self.on('b', recording_sync.manifest)
        local = self.on('a', recording_sync.manifest)
        wanted = keys if keys is not None else [k for k, v in local.items() if k not in remote or v > remote[k]]
        planned = self.on('a', recording_sync.plan, wanted, remote)
        return planned, list(self.on('a', lambda: list(recording_sync.export(planned))))

    def test_the_manifest_lists_finished_recordings_their_audio_and_downloads_only_when_on(self):
        rid = self.on('a', self.recorded)
        live = self.on('a', recordings.start, 'Live', 'meetings', True, True)['id']
        listed = self.on('a', recording_sync.manifest)
        self.assertEqual(set(listed), {rid, rid + '#audio'})  # one still recording is not offered
        self.assertEqual(listed[rid + '#audio'], 10.0)
        self.assertNotIn(live, listed)
        with patch.dict(os.environ, {'IMPRINT_SYNC_RECORDINGS': '0'}):
            self.assertEqual(self.on('a', recording_sync.manifest), {})

    def test_a_recording_arrives_whole_in_pieces_and_an_interrupted_download_resumes(self):
        rid = self.on('a', self.recorded)
        planned, records = self.send()
        self.assertEqual(planned, [f'{rid}@0'])
        self.assertEqual([r['offset'] for r in records], [0, 4, 8])
        self.assertEqual([r['final'] for r in records], [False, False, True])
        # The connection drops after two pieces.
        self.assertEqual(self.on('b', recording_sync.import_records, records[:2]), {'completed': 0, 'skipped': 0})
        self.assertEqual(self.on('b', recording_sync.manifest), {rid + '#part': 8.0})
        planned, records = self.send()
        self.assertEqual(planned, [f'{rid}@8'])
        self.assertEqual(self.on('b', recording_sync.import_records, records), {'completed': 1, 'skipped': 0})
        held = self.on('b', recordings.get, rid)
        self.assertEqual((held['status'], held['title'], held['audio_bytes'], held['transcript']), ('ready', 'Standup', 10, True))
        self.assertEqual((self.dirs['b'] / 'recordings' / rid / 'audio.webm').read_bytes(), b'0123456789')
        self.assertEqual(self.on('b', recording_sync.manifest), self.on('a', recording_sync.manifest))

    def test_audio_the_other_device_holds_is_not_sent_again(self):
        rid = self.on('a', self.recorded)
        self.on('b', recording_sync.import_records, self.send()[1])
        self.on('a', lambda: recordings.update(rid, transcribed_at=time.time() + 5))  # transcribed again
        (self.dirs['a'] / 'recordings' / rid / 'transcript.md').write_text('# Standup\n\nYou: hello again\n')
        planned, records = self.send()
        self.assertEqual(planned, [f'{rid}@10'])
        self.assertEqual([(r['data'], r['final']) for r in records], [('', True)])
        self.on('b', recording_sync.import_records, records)
        self.assertIn('hello again', (self.dirs['b'] / 'recordings' / rid / 'transcript.md').read_text())
        self.assertEqual(self.on('b', recordings.get, rid)['audio_bytes'], 10)

    def test_speakers_and_their_parts_travel_and_a_rename_reaches_the_other_device(self):
        import json
        rid = self.on('a', self.recorded)
        parts = {'model': 'large-v3', 'echoes': 0, 'voices': True,
                 'entries': [{'speaker': 'call-1', 'start': 1.0, 'end': 2.0, 'text': 'hello'}]}
        (self.dirs['a'] / 'recordings' / rid / 'segments.json').write_text(json.dumps(parts), encoding='utf-8')
        self.on('a', lambda: recordings.update(rid, speakers={'mic-1': 'You', 'call-1': 'Speaker 1'}))
        self.on('b', recording_sync.import_records, self.send()[1])
        self.assertEqual(self.on('b', recordings.get, rid)['speakers'], {'mic-1': 'You', 'call-1': 'Speaker 1'})
        self.assertEqual(json.loads((self.dirs['b'] / 'recordings' / rid / 'segments.json').read_text()), parts)
        # A rename is a newer version: the details come again, not the audio.
        self.on('a', lambda: recordings.update(rid, speakers={'mic-1': 'You', 'call-1': 'Ana'}))
        planned, records = self.send()
        self.assertEqual(planned, [f'{rid}@10'])
        self.on('b', recording_sync.import_records, records)
        self.assertEqual(self.on('b', recordings.get, rid)['speakers']['call-1'], 'Ana')

    def test_speaker_names_and_parts_from_another_device_are_checked(self):
        rid = self.on('a', self.recorded)
        records = self.send()[1]
        odd = {'mic-1': '**Ana**', 'call-2': '  ', '../x': 'Bob', 'call-3': ['not', 'a', 'name']}
        final = [dict(r, meta=dict(r['meta'], speakers=odd), segments='{"entries": [{"speaker": 3}]}')
                 if r['final'] else r for r in records]
        self.on('b', recording_sync.import_records, final)
        self.assertEqual(self.on('b', recordings.get, rid)['speakers'], {'mic-1': 'Ana'})
        self.assertFalse((self.dirs['b'] / 'recordings' / rid / 'segments.json').exists())

    def test_audio_deleted_on_one_device_is_deleted_on_the_other(self):
        rid = self.on('a', self.recorded)
        self.on('b', recording_sync.import_records, self.send()[1])
        self.on('a', recordings.remove_audio, rid)
        planned, records = self.send()
        self.assertEqual([(r['size'], r['final']) for r in records], [(0, True)])
        self.on('b', recording_sync.import_records, records)
        held = self.on('b', recordings.get, rid)
        self.assertEqual((held['audio_bytes'], held['transcript']), (0, True))

    def test_a_deleted_recording_goes_on_the_other_device_and_does_not_come_back(self):
        rid = self.on('a', self.recorded)
        records = self.send()[1]
        self.on('b', recording_sync.import_records, records)
        self.on('a', recordings.delete, rid)
        asked = self.on('a', sync_deletions.doomed, 'default', {'recordings': self.on('a', recording_sync.manifest)},
                        {'recordings': self.on('b', recording_sync.manifest)})
        self.assertEqual(set(asked['recordings']), {rid})
        gone = self.on('b', sync_deletions.apply, 'default', asked, {'recordings': self.on('b', recording_sync.manifest)})
        self.assertEqual(gone['recordings'], [rid])
        self.assertFalse((self.dirs['b'] / 'recordings' / rid).exists())
        # Sent again by a device that still held it, it is not stored.
        self.assertEqual(self.on('b', recording_sync.import_records, records), {'completed': 0, 'skipped': 3})

    def test_pieces_travel_as_sync_streams_counted_in_bytes_and_are_stored_on_arrival(self):
        import json
        from imprint import sync_delta, sync_ws
        rid = self.on('a', self.recorded)
        planned = self.send()[0]
        streams = self.on('a', lambda: list(sync_delta.export_chunks('default', {'recordings': planned})))
        self.assertEqual([json.loads(lines[0])['datasets']['recordings'] for lines in streams], [4, 4, 2])
        received = {}
        for lines in streams:
            frames = [json.loads(line) for line in lines]
            self.assertTrue(all(sync_ws._stream_line_ok(frame, at > 0) for at, frame in enumerate(frames)))
            buffer = Path(tempfile.mkdtemp()) / 'stream.jsonl'
            buffer.write_text('\n'.join(lines))
            stats = self.on('b', sync_ws.process_import_buffer, str(buffer), 'default')
            for key, value in stats.get('recordings', {}).items():
                received[key] = received.get(key, 0) + value
        self.assertEqual(received, {'completed': 1, 'skipped': 0})
        self.assertEqual((self.dirs['b'] / 'recordings' / rid / 'audio.webm').read_bytes(), b'0123456789')

    def test_corrupt_or_unsafe_pieces_are_refused_without_failing_the_sync(self):
        rid = self.on('a', self.recorded)
        records = self.send()[1]
        corrupt = [dict(r, sha256='0' * 64) if r['final'] else r for r in records]
        self.assertEqual(self.on('b', recording_sync.import_records, corrupt), {'completed': 0, 'skipped': 1})
        self.assertFalse((self.dirs['b'] / 'recordings' / rid / recording_sync.PART).exists())
        unsafe = [dict(records[0], id='../../etc'), dict(records[0], meta='not details'), dict(records[1])]
        self.assertEqual(self.on('b', recording_sync.import_records, unsafe), {'completed': 0, 'skipped': 3})
        # Details are checked: a status that cannot arrive becomes "stopped".
        busy = [dict(r, meta=dict(r['meta'], status='transcribing')) for r in records]
        self.assertEqual(self.on('b', recording_sync.import_records, busy), {'completed': 1, 'skipped': 0})
        self.assertEqual(self.on('b', recordings.get, rid)['status'], 'stopped')
        with patch.dict(os.environ, {'IMPRINT_SYNC_RECORDINGS': '0'}):
            self.assertEqual(self.on('b', recording_sync.import_records, records), {'completed': 0, 'skipped': 3})


if __name__ == '__main__':
    unittest.main()
