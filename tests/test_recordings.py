"""Meeting recordings: stored raw, split by channel, transcribed locally (ADR 0131)."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from imprint import config, queue as job_queue, recordings


def webm(path: Path, left: np.ndarray, right: np.ndarray, rate: int = 48000):
    """A stereo WebM/Opus file, the format the dashboard's recorder makes."""
    import av
    with av.open(str(path), 'w', format='webm') as container:
        stream = container.add_stream('libopus', rate=rate, layout='stereo')
        frame = av.AudioFrame.from_ndarray(np.stack([left, right]).astype(np.float32), format='fltp', layout='stereo')
        frame.sample_rate = rate
        for packet in [*stream.encode(frame), *stream.encode(None)]:
            container.mux(packet)


def tone(seconds: float, hz: float = 440, rate: int = 48000) -> np.ndarray:
    t = np.arange(int(seconds * rate)) / rate
    return (0.3 * np.sin(2 * np.pi * hz * t)).astype(np.float32)


class RecordingTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data = Path(directory.name)
        patcher = patch.object(config, 'get_data_dir', return_value=self.data)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_recording_needs_consent_and_keeps_its_pieces_until_stopped(self):
        with self.assertRaisesRegex(ValueError, 'knows it is being recorded'):
            recordings.start('Standup')
        meta = recordings.start('  Weekly   sync ', project='checkout-service', others=False, consent=True)
        self.assertEqual((meta['title'], meta['project'], meta['others'], meta['status']),
                         ('Weekly sync', 'checkout-service', False, 'recording'))
        self.assertTrue(recordings.start('', consent=True)['title'].startswith('Meeting '))
        recordings.append(meta['id'], b'abc')
        self.assertEqual(recordings.append(meta['id'], b'de'), {'audio_bytes': 5})
        # The recorder's duration counts, within the time that has passed.
        recordings.update(meta['id'], started_at=meta['started_at'] - 60)
        stopped = recordings.stop(meta['id'], duration=12.34)
        self.assertEqual((stopped['status'], stopped['duration'], stopped['audio_bytes']), ('stopped', 12.3, 5))
        self.assertEqual(recordings.stop(meta['id'])['duration'], 12.3)  # Stopping again changes nothing.
        late = recordings.start('Too long', consent=True)
        self.assertLess(recordings.stop(late['id'], duration=3600)['duration'], 10)
        with self.assertRaisesRegex(ValueError, 'stopped'):
            recordings.append(meta['id'], b'late')
        with self.assertRaises(KeyError):
            recordings.get('../../etc')
        # Newest first. Windows' clock can give recordings started together
        # one time, so the newest one is moved clearly ahead.
        recordings.update(late['id'], started_at=late['started_at'] + 60)
        titles = [m['title'] for m in recordings.list_all()]
        self.assertEqual((titles[0], titles[-1], len(titles)), ('Too long', 'Weekly sync', 3))

    def test_audio_stops_at_the_size_limit(self):
        meta = recordings.start('Long call', consent=True)
        with patch.dict(os.environ, {'IMPRINT_AUDIO_MAX_FILE_MB': '1'}):
            recordings.append(meta['id'], b'x' * (1024 * 1024 - 1))
            with self.assertRaisesRegex(ValueError, 'audio.max_file_size_mb'):
                recordings.append(meta['id'], b'xx')

    def test_channels_split_into_you_and_others_and_a_silent_side_is_skipped(self):
        audio = self.data / 'both.webm'
        webm(audio, tone(1.0, 440), tone(1.0, 660))
        channels = recordings.split_channels(audio, self.data)
        self.assertEqual([name for name, _ in channels], ['You', 'Others'])
        import wave
        with wave.open(str(channels[1][1])) as others:
            self.assertEqual((others.getnchannels(), others.getframerate()), (1, 16000))
            self.assertAlmostEqual(others.getnframes() / 16000, 1.0, delta=0.1)
        webm(audio, tone(1.0), np.zeros(48000, dtype=np.float32))
        self.assertEqual([name for name, _ in recordings.split_channels(audio, self.data)], ['You'])

    def test_the_transcript_names_speakers_in_time_order(self):
        meta = {'title': 'Planning', 'started_at': 0, 'duration': 95}
        spoken = [('Others', {'start': 5.0, 'end': 7.0, 'text': 'Can you take the booking bug?'}),
                  ('You', {'start': 1.0, 'end': 3.0, 'text': 'Morning.'}),
                  ('You', {'start': 8.0, 'end': 9.5, 'text': 'Yes,'}),
                  ('You', {'start': 10.0, 'end': 11.0, 'text': 'today.'}),
                  ('Others', {'start': 3700.0, 'end': 3701.0, 'text': 'Thanks.'})]
        text = recordings.transcript(meta, spoken, 'faster-whisper-large-v3-turbo')
        lines = text.splitlines()
        self.assertEqual(lines[0], '# Planning')
        self.assertIn('about 2 min, transcribed on this computer with faster-whisper-large-v3-turbo', lines[2])
        self.assertEqual(lines[4:], ['**You** (00:00:01): Morning.', '**Others** (00:00:05): Can you take the booking bug?',
                                     '**You** (00:00:08): Yes, today.', '**Others** (01:01:40): Thanks.'])
        self.assertIn('No speech was recognized.', recordings.transcript(meta, [], 'model'))

    def test_transcribing_stores_the_transcript_as_a_replaceable_source(self):
        meta = recordings.start('Sync', project='meetings', consent=True)
        audio = recordings._folder(meta['id']) / 'audio.webm'
        webm(audio, tone(1.0, 440), tone(1.0, 660))
        recordings.stop(meta['id'], duration=60)

        def transcribe(path, force=False):
            speaker = Path(path).stem
            return [{'text': f'{speaker} speaking', 'start': 0.5 if speaker == 'you' else 2.0, 'end': 1.0 if speaker == 'you' else 3.0,
                     'language': 'en'}], {'transcription_model': 'Systran/faster-whisper-small'}

        with patch('imprint.audio_ingest.transcribe', side_effect=transcribe), \
                patch('imprint.ingest_core.store_inline_content', return_value=(2, 'stored')) as store:
            done = recordings.transcribe(meta['id'])
        self.assertEqual((done['status'], done['memories'], done['transcript']), ('ready', 2, True))
        text, kwargs = store.call_args.args[0], store.call_args.kwargs
        self.assertEqual((kwargs['source_key'], kwargs['project'], kwargs['source_type']),
                         (f"recording/{meta['id']}", 'meetings', 'recording'))
        self.assertIn('**You** (00:00:00): you speaking\n**Others** (00:00:02): others speaking', text)
        self.assertEqual((recordings._folder(meta['id']) / 'transcript.md').read_text(), text)

    def test_a_failed_transcription_says_why_and_can_run_again(self):
        meta = recordings.start('Sync', consent=True)
        with self.assertRaisesRegex(ValueError, 'Stop the recording'):
            recordings.transcribe(meta['id'])
        recordings.stop(meta['id'])
        with self.assertRaisesRegex(ValueError, 'no audio'):
            recordings.transcribe(meta['id'])
        webm(recordings._folder(meta['id']) / 'audio.webm', tone(1.0), tone(1.0, 660))
        with patch('imprint.audio_ingest.transcribe', side_effect=ValueError('Audio ingestion is off')), \
                self.assertRaisesRegex(ValueError, 'Audio ingestion is off'):
            recordings.transcribe(meta['id'])
        self.assertEqual((recordings.get(meta['id'])['status'], recordings.get(meta['id'])['error']),
                         ('failed', 'Audio ingestion is off'))

    def test_deleting_removes_the_folder_and_its_memories_and_audio_can_go_alone(self):
        meta = recordings.start('Sync', consent=True)
        recordings.append(meta['id'], b'audio')
        with self.assertRaisesRegex(ValueError, 'stopped and transcribed'):
            recordings.remove_audio(meta['id'])
        recordings.stop(meta['id'])
        self.assertEqual(recordings.remove_audio(meta['id'])['audio_bytes'], 0)
        with patch('imprint.vectorstore.delete_by_source') as delete:
            recordings.delete(meta['id'])
        delete.assert_called_once_with(f"recording/{meta['id']}", None)
        self.assertFalse(recordings._folder(meta['id']).exists())

    def test_the_queue_runs_transcription_for_a_valid_id_only(self):
        self.assertEqual(job_queue._build_argv('transcribe-recording', {'id': 'a' * 32})[1:],
                         ['-m', 'imprint.recordings', 'transcribe', 'a' * 32])
        with self.assertRaisesRegex(ValueError, 'recording ID'):
            job_queue._build_argv('transcribe-recording', {'id': '../x'})

    def test_the_api_starts_records_and_stops_into_a_queued_transcription(self):
        import httpx
        from imprint.api import app

        async def run():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1:8420') as client:
                refused = await client.post('/api/recordings', json={'title': 'Sync'})
                started = (await client.post('/api/recordings', json={'title': 'Sync', 'consent': True})).json()
                piece = await client.post(f"/api/recordings/{started['id']}/audio", content=b'opus-bytes',
                                          headers={'content-type': 'application/octet-stream'})
                with patch.object(job_queue, 'submit', return_value={'job_id': 'j1', 'merged': False}) as submit:
                    stopped = (await client.post(f"/api/recordings/{started['id']}/stop", json={'duration': 3})).json()
                listed = (await client.get('/api/recordings')).json()
                return refused, started, piece, stopped, submit, listed

        refused, started, piece, stopped, submit, listed = asyncio.run(run())
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(piece.json(), {'audio_bytes': 10})
        self.assertEqual((stopped['status'], stopped['job']), ('stopped', {'job_id': 'j1', 'merged': False}))
        submit.assert_called_once_with('transcribe-recording', {'id': started['id']})
        self.assertEqual(listed['recordings'][0]['id'], started['id'])


if __name__ == '__main__':
    unittest.main()
