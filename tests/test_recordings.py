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
        # Voices are told apart only where a test stubs the models (ADR 0180).
        diarize_off = patch.dict(os.environ, {'IMPRINT_AUDIO_DIARIZE': '0'})
        diarize_off.start()
        self.addCleanup(diarize_off.stop)

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
        # Every turn is a paragraph of its own (ADR 0173).
        paragraphs = text.rstrip('\n').split('\n\n')
        self.assertEqual(paragraphs[0], '# Planning')
        self.assertIn('about 2 min, transcribed on this computer with faster-whisper-large-v3-turbo', paragraphs[1])
        self.assertNotIn('left out', paragraphs[1])
        self.assertEqual(paragraphs[2:], ['**You** (00:00:01): Morning.', '**Others** (00:00:05): Can you take the booking bug?',
                                          '**You** (00:00:08): Yes, today.', '**Others** (01:01:40): Thanks.'])
        self.assertIn('No speech was recognized.', recordings.transcript(meta, [], 'model'))

    def test_microphone_passages_that_repeat_the_computers_audio_are_left_out(self):
        # The microphone hears a call played through the speakers; Whisper
        # writes that echo a little differently from the clean computer audio.
        played = ('Others', {'start': 350.0, 'end': 356.0,
                             'text': 'Deci eu acum de când am rămas cu Daria la birou, nivelul de productivitate a crescut'})
        echo = ('You', {'start': 351.0, 'end': 357.5,
                        'text': 'Deci eu acum de când am rămas cu Daria la video, nivelul de productivitate'})
        reply = ('You', {'start': 358.0, 'end': 361.0, 'text': 'Da, se vede, și la mine la fel de bine'})
        short = ('You', {'start': 352.0, 'end': 352.5, 'text': 'Da, crescut.'})
        later = ('You', {'start': 400.0, 'end': 404.0, 'text': 'Deci eu acum de când am rămas cu Daria'})
        kept = recordings.without_echo([played, echo, reply, short, later])
        self.assertEqual(kept, [played, reply, short, later])
        text = recordings.transcript({'title': 'Sync', 'started_at': 0, 'duration': 404}, kept, 'model', echoes=1)
        self.assertIn("1 microphone passage that repeated the computer's audio is left out.", text)
        self.assertNotIn('la video', text)

    def test_transcribing_stores_the_transcript_as_a_replaceable_source(self):
        meta = recordings.start('Sync', project='meetings', consent=True)
        audio = recordings._folder(meta['id']) / 'audio.webm'
        webm(audio, tone(1.0, 440), tone(1.0, 660))
        recordings.stop(meta['id'], duration=60)

        def transcribe(path, force=False, words=False):
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
        self.assertIn('**You** (00:00:00): you speaking\n\n**Others** (00:00:02): others speaking', text)
        self.assertEqual((recordings._folder(meta['id']) / 'transcript.md').read_text(), text)

    def test_words_go_to_the_voice_whose_turn_they_overlap_and_a_one_voice_segment_stays_whole(self):
        turns = [(0.0, 4.0, 0), (4.2, 9.0, 1)]
        whole = {'start': 0.5, 'end': 3.5, 'text': 'Buna, ce faci?', 'words': [[0.5, 1.0, ' Buna,'], [1.2, 3.5, ' ce faci?']]}
        both = {'start': 3.0, 'end': 6.0, 'text': 'Bine. Si tu?',
                'words': [[3.0, 3.8, ' Bine.'], [4.4, 5.0, ' Si'], [5.1, 6.0, ' tu?']]}
        stray = {'start': 9.3, 'end': 9.6, 'text': 'Da.', 'words': [[9.3, 9.6, ' Da.']]}  # just after a turn
        parts = recordings.speaker_parts([whole, both, stray], turns)
        self.assertEqual([(p['voice'], p['text']) for p in parts],
                         [(0, 'Buna, ce faci?'), (0, 'Bine.'), (1, 'Si tu?'), (1, 'Da.')])
        self.assertEqual((parts[2]['start'], parts[2]['end']), (4.4, 6.0))
        # Without turns, everything is one voice.
        self.assertEqual({p['voice'] for p in recordings.speaker_parts([whole, both], [])}, {0})

    def test_a_sentence_stays_with_one_voice_when_the_turns_change_inside_it(self):
        # Turns changed voice a second into this sentence on a real call.
        said = {'start': 248.9, 'end': 253.4, 'text': 'Ma gandesc la chestia asta, apropo de birou. Da?',
                'words': [[248.9, 249.6, ' Ma gandesc'], [249.6, 250.1, ' la chestia'],
                          [250.1, 252.8, ' asta, apropo de birou.'], [253.0, 253.4, ' Da?']]}
        turns = [(249.2, 250.0, 0), (250.0, 252.9, 1), (252.95, 253.5, 0)]
        parts = recordings.speaker_parts([said], turns)
        self.assertEqual([(p['voice'], p['text']) for p in parts], [(1, 'Ma gandesc la chestia asta, apropo de birou.'),
                                                                    (0, 'Da?')])

    def test_sentences_whisper_likely_invented_are_left_out_in_any_language_the_recording_uses(self):
        def said(text, start, language='ro', logprob=-0.2, seconds=0.4, probability=0.9):
            words = [[start + n * seconds, start + (n + 1) * seconds, ' ' + word, probability]
                     for n, word in enumerate(text.split())]
            return {'text': text, 'start': start, 'end': words[-1][1], 'language': language,
                    'avg_logprob': logprob, 'words': words}
        romanian = [('Others', said('Am dat eu pe contul gresit si nu am auzit nimic.', n * 10.0)) for n in range(6)]
        # Heard on the user's meeting: an outro stretched over seconds of the
        # call's echo, after a real sentence of the same segment.
        outro = said('Da, sigur. Nu uitati sa va abonati la canal!', 70.0)
        for word in outro['words'][2:]:
            word[1] = word[0] + 3.0
        foreign = said('nao e?', 90.0, language='pt', logprob=-0.92)
        english = said('Okay, let us go.', 95.0, language='en', logprob=-0.2)
        bare = {'text': 'Without words.', 'start': 99.0, 'end': 100.0}
        kept, left_out = recordings.without_invented(romanian + [('You', outro), ('Others', foreign),
                                                                 ('Others', english), ('Others', bare)])
        self.assertEqual(left_out, 2)
        texts = [segment['text'] for _, segment in kept]
        self.assertIn('Da, sigur.', texts)
        self.assertNotIn('nao e?', texts)
        self.assertIn('Okay, let us go.', texts)   # Whisper was sure of it
        self.assertIn('Without words.', texts)
        shortened = next(segment for _, segment in kept if segment['text'] == 'Da, sigur.')
        self.assertEqual((shortened['start'], shortened['end']), (70.0, 70.8))
        # In a recording that speaks English too, English is not foreign.
        mixed = romanian + [('You', said('Thanks all for joining us today, really.', 80.0 + n, language='en',
                                         logprob=-0.6)) for n in range(4)]
        self.assertEqual(recordings.without_invented(mixed)[1], 0)

    def test_a_microphone_voice_heard_while_the_computer_plays_is_its_echo(self):
        played = [(0.0, 5.0, 0), (6.0, 9.0, 1)]
        mic = [(0.5, 4.5, 1), (5.0, 6.0, 0), (6.2, 8.8, 1), (9.0, 12.0, 0), (8.0, 9.5, 0)]
        self.assertEqual(recordings.echo_voices(mic, played), {1})
        self.assertEqual(recordings.echo_voices(mic, []), set())

    def test_default_names_follow_the_channel(self):
        part = lambda voice, start, end: {'voice': voice, 'start': start, 'end': end, 'text': 'x'}  # noqa: E731
        # On the microphone the voice with the most speech is "You", whoever spoke first.
        self.assertEqual(recordings.voice_names('mic', [part(1, 0, 2), part(0, 3, 30), part(2, 31, 33)]),
                         {0: 'You', 1: 'In the room 1', 2: 'In the room 2'})
        self.assertEqual(recordings.voice_names('call', [part(3, 5, 9)]), {3: 'Others'})
        self.assertEqual(recordings.voice_names('call', [part(1, 9, 20), part(0, 2, 4)]), {0: 'Speaker 1', 1: 'Speaker 2'})
        self.assertEqual(recordings.clean_name('  **Ana**  [Pop] '), 'Ana Pop')
        with self.assertRaises(ValueError):
            recordings.clean_name(' ** ')

    def transcribed_with_voices(self):
        """A recording transcribed with voices told apart, the models stubbed."""
        meta = recordings.start('Planning', project='meetings', consent=True)
        webm(recordings._folder(meta['id']) / 'audio.webm', tone(1.0, 440), tone(1.0, 660))
        recordings.stop(meta['id'], duration=60)
        # The microphone also heard "I can join" from the speakers, too short
        # to be found by its words: its voice is heard only while the call speaks.
        said = {'you': [{'text': 'Let us start.', 'start': 0.5, 'end': 1.5, 'words': [[0.5, 1.5, ' Let us start.']]},
                        {'text': 'I can join', 'start': 4.2, 'end': 5.5, 'words': [[4.2, 5.5, ' I can join']]}],
                'others': [{'text': 'Sure. I can join later.', 'start': 2.0, 'end': 6.0,
                            'words': [[2.0, 2.5, ' Sure.'], [4.0, 6.0, ' I can join later.']]},
                           # Whisper's classic: improbable, its first word too short.
                           {'text': 'Thank you.', 'start': 8.0, 'end': 8.3, 'language': 'en', 'avg_logprob': -0.9,
                            'words': [[8.0, 8.05, ' Thank', 0.07], [8.05, 8.3, ' you.', 0.9]]}]}
        turns = {'you': [(0.0, 2.0, 0), (4.0, 6.0, 1)], 'others': [(1.8, 3.0, 0), (3.5, 7.0, 1)]}

        def transcribe(path, force=False, words=False):
            return said[Path(path).stem], {'transcription_model': 'Systran/faster-whisper-large-v3'}

        def diarize_channel(samples, progress=None):
            channel = ['you', 'others'][len(diarize_channel.done)]
            diarize_channel.done.append(channel)
            return turns[channel]
        diarize_channel.done = []
        with patch.dict(os.environ, {'IMPRINT_AUDIO_DIARIZE': '1'}), \
                patch('imprint.audio_ingest.transcribe', side_effect=transcribe), \
                patch('imprint.diarize.ensure_models'), patch('imprint.diarize.diarize', side_effect=diarize_channel), \
                patch('imprint.ingest_core.store_inline_content', return_value=(2, 'stored')) as store:
            done = recordings.transcribe(meta['id'], workspace='default')
        return meta, done, store.call_args.args[0]

    def test_transcribing_tells_voices_apart_and_keeps_the_parts_for_renaming(self):
        meta, done, text = self.transcribed_with_voices()
        self.assertEqual(done['speakers'], {'mic-1': 'You', 'call-1': 'Speaker 1', 'call-2': 'Speaker 2'})
        self.assertIn('**You** (00:00:00): Let us start.', text)
        self.assertIn('**Speaker 1** (00:00:02): Sure.', text)
        self.assertIn('**Speaker 2** (00:00:04): I can join later.', text)
        self.assertIn('Voices were told apart on this computer', text)
        self.assertIn('1 microphone passage that repeated the computer\'s audio is left out', text)
        self.assertIn('1 sentence the speech model likely invented is left out', text)
        self.assertNotIn('Thank you', text)
        self.assertNotIn('**In the room', text)
        saved = json.loads((recordings._folder(meta['id']) / 'segments.json').read_text())
        self.assertEqual([e['speaker'] for e in saved['entries']], ['mic-1', 'call-1', 'call-2'])
        self.assertNotIn('words', saved['entries'][0])

    def test_renaming_a_speaker_rewrites_the_transcript_and_replaces_its_memories_everywhere(self):
        meta, _, _ = self.transcribed_with_voices()
        with patch('imprint.vectorstore.delete_by_source') as delete, \
                patch('imprint.ingest_core.store_inline_content', return_value=(2, 'stored')) as store:
            renamed = recordings.rename_speakers(meta['id'], {'call-2': '  *Ana*  '})
        self.assertEqual(renamed['speakers']['call-2'], 'Ana')
        text = store.call_args.args[0]
        self.assertIn('**Ana** (00:00:04): I can join later.', text)
        self.assertIn('1 sentence the speech model likely invented is left out', text)
        self.assertEqual((recordings._folder(meta['id']) / 'transcript.md').read_text(), text)
        # The old transcript's memories go on paired devices too (ADR 0176).
        self.assertEqual(delete.call_args.kwargs, {'propagate': 'all'})
        with self.assertRaisesRegex(ValueError, 'No speaker'):
            recordings.rename_speakers(meta['id'], {'call-9': 'Nobody'})

    def test_the_api_lists_speakers_with_the_transcript_and_renames_them(self):
        import httpx
        from imprint.api import app
        meta, _, _ = self.transcribed_with_voices()
        (recordings._folder(meta['id']) / 'transcript.md').write_text('# Planning\n', encoding='utf-8')

        async def run():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1:8420') as client:
                listed = (await client.get(f"/api/recordings/{meta['id']}/transcript")).json()
                with patch('imprint.vectorstore.delete_by_source'), \
                        patch('imprint.ingest_core.store_inline_content', return_value=(2, 'stored')):
                    renamed = await client.patch(f"/api/recordings/{meta['id']}/speakers",
                                                 json={'speakers': {'call-1': 'Ana'}})
                unknown = await client.patch(f"/api/recordings/{meta['id']}/speakers",
                                             json={'speakers': {'call-7': 'Ana'}})
                empty = await client.patch(f"/api/recordings/{meta['id']}/speakers", json={'speakers': {}})
                missing = await client.patch(f"/api/recordings/{'0' * 32}/speakers", json={'speakers': {'mic-1': 'x'}})
                return listed, renamed, unknown, empty, missing

        listed, renamed, unknown, empty, missing = asyncio.run(run())
        self.assertEqual(listed['speakers'], {'mic-1': 'You', 'call-1': 'Speaker 1', 'call-2': 'Speaker 2'})
        self.assertEqual(renamed.json()['speakers']['call-1'], 'Ana')
        self.assertEqual([unknown.status_code, empty.status_code, missing.status_code], [400, 400, 404])
        self.assertIn('**Ana** (00:00:02): Sure.', (recordings._folder(meta['id']) / 'transcript.md').read_text())

    def test_without_the_models_each_channel_is_one_speaker(self):
        meta = recordings.start('Sync', consent=True)
        webm(recordings._folder(meta['id']) / 'audio.webm', tone(1.0, 440), tone(1.0, 660))
        recordings.stop(meta['id'], duration=60)

        def transcribe(path, force=False, words=False):
            said = {'you': 'Hello from here', 'others': 'Welcome to the call'}[Path(path).stem]
            return [{'text': said, 'start': 1.0, 'end': 2.0}], {'transcription_model': 'm'}
        with patch.dict(os.environ, {'IMPRINT_AUDIO_DIARIZE': '1'}), \
                patch('imprint.audio_ingest.transcribe', side_effect=transcribe), \
                patch('imprint.diarize.ensure_models', side_effect=OSError('offline')), \
                patch('imprint.ingest_core.store_inline_content', return_value=(1, 'stored')) as store:
            done = recordings.transcribe(meta['id'])
        self.assertEqual(done['speakers'], {'mic-1': 'You', 'call-1': 'Others'})
        self.assertIn('"You" is this computer\'s microphone', store.call_args.args[0])

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
        delete.assert_called_once_with(f"recording/{meta['id']}", None, propagate='all')  # On paired devices too (ADR 0176).
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

    def test_the_audio_downloads_as_a_file_named_after_the_recording(self):
        import httpx
        from imprint.api import app
        meta = recordings.start('Meeting 2026-11-11 09:58', consent=True)
        recordings.append(meta['id'], b'opus-bytes')
        recordings.stop(meta['id'], duration=3)

        async def run():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1:8420') as client:
                return (await client.get(f"/api/recordings/{meta['id']}/audio"),
                        await client.get(f"/api/recordings/{meta['id']}/audio?download=1"),
                        await client.get(f"/api/recordings/{'0' * 32}/audio?download=1"))

        played, saved, missing = asyncio.run(run())
        self.assertEqual((played.content, played.headers.get('content-disposition')), (b'opus-bytes', None))
        self.assertEqual(saved.content, b'opus-bytes')
        # The time's colon is not allowed in a Windows file name. A name with
        # spaces is sent percent-encoded (RFC 5987), which browsers decode.
        from urllib.parse import unquote
        disposition = saved.headers['content-disposition']
        self.assertTrue(disposition.startswith('attachment;'))
        self.assertIn('Meeting 2026-11-11 09-58.webm', unquote(disposition))
        self.assertEqual(missing.status_code, 404)


if __name__ == '__main__':
    unittest.main()
