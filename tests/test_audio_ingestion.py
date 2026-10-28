import contextlib
import hashlib
import io
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import wave

import numpy as np

from imprint import audio_ingest as audio, config, extractors, source_memory as sm, source_snapshots as ss, vectorstore as vs
from imprint.conversation_source import read
from imprint.source_state import fingerprint, unchanged
from test_source_snapshots import SourceFixture


def wav(path, seconds=1, rate=16000):
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(1); output.setsampwidth(2); output.setframerate(rate)
        output.writeframes(np.full(int(rate * seconds), 1000, dtype='<i2').tobytes())


def segment(text, start=0, end=.8):
    return SimpleNamespace(words=[SimpleNamespace(word=text, start=start, end=end)])


class AudioDecodeTests(unittest.TestCase):
    def test_long_recording_is_decoded_in_bounded_overlapping_windows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'long.wav'; wav(path, seconds=32)
            result = list(audio.windows(str(path), 60))
            self.assertEqual([(len(samples), start) for samples, start, _ in result], [(480000, 0), (48000, 29)])
            np.testing.assert_array_equal(result[0][0][-16000:], result[1][0][:16000])
            self.assertEqual(len(result[0][0]) + len(result[1][0]) - 16000, 32 * audio.RATE)
            with self.assertRaisesRegex(ValueError, 'duration'):
                list(audio.windows(str(path), 1))

    def test_compressed_mp3_and_flac_need_no_system_ffmpeg(self):
        import av
        with tempfile.TemporaryDirectory() as directory:
            for extension, codec in (('flac', 'flac'), ('mp3', 'mp3'), ('m4a', 'aac')):
                path = Path(directory) / ('recording.' + extension)
                with av.open(str(path), 'w') as container:
                    stream = container.add_stream(codec, rate=16000)
                    stream.layout = 'mono'
                    frame = av.AudioFrame.from_ndarray(np.full((1, 16000), 1000, dtype='<i2'), format='s16', layout='mono')
                    frame.sample_rate = 16000
                    for packet in stream.encode(frame): container.mux(packet)
                    for packet in stream.encode(None): container.mux(packet)
                decoded = list(audio.windows(str(path), 10))
                self.assertEqual(len(decoded), 1)
                self.assertAlmostEqual(len(decoded[0][0]) / audio.RATE, 1, delta=.1)

    def test_stereo_48khz_audio_is_resampled_to_mono_16khz(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'stereo.wav'
            with wave.open(str(path), 'wb') as output:
                output.setnchannels(2); output.setsampwidth(2); output.setframerate(48000)
                output.writeframes(np.full(48000 * 2 * 2, 1000, dtype='<i2').tobytes())
            decoded = list(audio.windows(str(path), 10))
            self.assertEqual(len(decoded), 1)
            self.assertEqual(decoded[0][0].shape, (32000,))
            self.assertTrue(np.isfinite(decoded[0][0]).all())

    def test_network_playlists_are_rejected_before_any_model_download(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'playlist.mp3'
            path.write_text('#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXTINF:10,\nhttp://127.0.0.1:1/private.wav\n#EXT-X-ENDLIST\n')
            import av
            with self.assertRaises(av.error.FFmpegError):
                list(audio.windows(str(path), 60))


class AudioTranscribeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = patch.dict(os.environ, {'IMPRINT_DATA_DIR': str(self.root), 'IMPRINT_WORKSPACE': 'default',
                                     'IMPRINT_AUDIO_MODEL': 'small', 'IMPRINT_AUDIO_LANGUAGE': 'auto',
                                     'IMPRINT_AUDIO_ENABLED': 'true', 'IMPRINT_REDACT_PRIVACY_FILTER': 'false'})
        env.start(); self.addCleanup(env.stop)

    def test_language_is_detected_per_window_and_overlap_words_are_not_repeated(self):
        path = self.root / 'mixed.wav'; wav(path, seconds=32)
        requests = []

        def transcribe(samples, **kwargs):
            requests.append((len(samples), kwargs))
            if len(requests) == 1:
                return iter([segment('Salut', .1, .8), segment(' lume', 29, 29.8)]), SimpleNamespace(language='ro')
            return iter([segment(' lume', .1, .8), segment(' Hello', 1.5, 2.5)]), SimpleNamespace(language='en')

        model = SimpleNamespace(transcribe=transcribe)
        with patch.object(audio, 'cached', return_value=None), patch.object(audio, 'load_model', return_value=model), patch.object(audio, 'report'):
            result, meta = audio.transcribe(str(path))
        self.assertEqual([row['text'] for row in result], ['Salut', 'lume', 'Hello'])
        self.assertEqual([row['language'] for row in result], ['ro', 'ro', 'en'])
        self.assertEqual(result[-1]['start'], 30.5)
        self.assertEqual(meta['transcription_model'], audio.REPO)
        self.assertTrue(all(kwargs['language'] is None and kwargs['word_timestamps'] for _, kwargs in requests))
        self.assertLessEqual(max(length for length, _ in requests), audio.WINDOW)

    def test_explicit_language_and_redaction_precede_storage(self):
        path = self.root / 'meeting.wav'; wav(path)
        model = SimpleNamespace(transcribe=lambda *args, **kwargs: (iter([segment('api_key = sk-proj-' + 'A' * 40)]), SimpleNamespace(language='ro')))
        with patch.dict(os.environ, {'IMPRINT_AUDIO_LANGUAGE': 'ro'}), \
                patch.object(audio, 'cached', return_value=None), patch.object(audio, 'load_model', return_value=model), \
                patch.object(audio, 'report'):
            result, _ = audio.transcribe(str(path))
        self.assertNotIn('sk-proj-', result[0]['text'])
        self.assertIn('[REDACTED:', result[0]['text'])

    def test_size_disabled_invalid_language_and_corrupt_audio_never_download_a_model(self):
        path = self.root / 'bad.wav'; path.write_bytes(b'not audio')
        with patch.object(audio, 'load_model') as load:
            for env in ({'IMPRINT_AUDIO_ENABLED': 'false'}, {'IMPRINT_AUDIO_LANGUAGE': 'fr'}, {'IMPRINT_AUDIO_MAX_MINUTES': '0'}):
                with patch.dict(os.environ, env), self.assertRaises(ValueError):
                    audio.transcribe(str(path))
            with self.assertRaises(extractors.ExtractionError):
                extractors.dispatch_by_ext(str(path))
            path.write_bytes(bytes(1024 * 1024 + 1))
            with patch.dict(os.environ, {'IMPRINT_AUDIO_MAX_FILE_MB': '1'}), self.assertRaisesRegex(ValueError, 'size'):
                audio.transcribe(str(path))
            load.assert_not_called()

    def test_profile_changes_only_for_audio_runtime_and_settings(self):
        original = audio.profile()
        for env in ({'IMPRINT_AUDIO_MODEL': 'large-v3-turbo'}, {'IMPRINT_AUDIO_LANGUAGE': 'ro'}, {'IMPRINT_AUDIO_MAX_MINUTES': '30'}):
            with patch.dict(os.environ, env): self.assertNotEqual(audio.profile(), original)
        with patch.dict(os.environ, {'IMPRINT_CHAT_ANALYSIS_WORKERS': '7'}):
            self.assertEqual(audio.profile(), original)

    def test_partial_model_download_never_becomes_a_verified_asset(self):
        content = b'pinned model'
        spec = audio.ModelSpec('fixture/model', 'fixed-revision', {'model.bin': (len(content), hashlib.sha256(content).hexdigest())})

        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def raise_for_status(self): pass
            def iter_bytes(self, *args): yield b'wrong bytes'

        import httpx
        with patch.object(httpx, 'stream', return_value=Response()), patch.object(audio, 'report'):
            with self.assertRaisesRegex(ValueError, 'checksum/size'):
                audio.ensure_model(spec)
        self.assertFalse((audio.model_root(spec) / 'model.bin').exists())
        self.assertFalse((audio.model_root(spec) / 'model.bin.part').exists())

    def test_corrupt_completed_weights_are_never_loaded_or_silently_replaced(self):
        content = b'pinned model'
        spec = audio.ModelSpec('fixture/model', 'revision', {'model.bin': (len(content), hashlib.sha256(content).hexdigest())})
        directory = audio.model_root(spec); directory.mkdir(parents=True)
        (directory / 'model.bin').write_bytes(b'X' * len(content))
        with patch('httpx.stream') as network, patch.object(audio, 'report'):
            with self.assertRaisesRegex(ValueError, 'Checksum mismatch'):
                audio.ensure_model(spec)
            network.assert_not_called()


class AudioIndexTests(SourceFixture):
    def ingest(self, path, *, force=False):
        from imprint import cli_index
        with patch('imprint.tagger.build_payload_tags', return_value={}), \
                patch.object(cli_index, 'write_progress'), patch.object(cli_index, 'clear_progress'), \
                contextlib.redirect_stdout(io.StringIO()):
            cli_index.ingest_single_file(str(path), 'recordings', force=force)

    def test_audio_is_indexed_as_timestamped_conversation_and_cached_in_its_snapshot(self):
        path = self.root / 'meeting.wav'; wav(path)
        model = SimpleNamespace(transcribe=lambda *args, **kwargs: (iter([segment('Bună ziua, discutăm despre proiect.')]), SimpleNamespace(language='ro')))
        with patch.object(audio, 'load_model', return_value=model) as load, patch.object(audio, 'report'):
            self.ingest(path)
            self.ingest(path)
            self.assertEqual(load.call_count, 1)
            self.ingest(path, force=True)
            self.assertEqual(load.call_count, 2)
        snapshot = ss.get('recordings/meeting.wav', 'recordings')
        self.assertEqual(snapshot['metadata']['kind'], 'conversation')
        self.assertEqual(snapshot['metadata']['details']['media'], 'audio')
        events = read(snapshot['text'])
        self.assertEqual(events[0]['role'], 'speaker')
        self.assertEqual(events[0]['audio_start'], 0)
        self.assertEqual(events[0]['audio_end'], .8)
        self.assertEqual(events[0]['language'], 'ro')
        self.assertTrue(ss.integrity(snapshot)['complete'])
        self.assertEqual(ss.conversation_titles()['recordings/meeting.wav'], 'meeting.wav')
        state = vs.get_source_states()['recordings/meeting.wav']
        self.assertEqual(state['source_type'], 'audio')
        self.assertTrue(unchanged(fingerprint(path), state))
        with patch.dict(os.environ, {'IMPRINT_AUDIO_LANGUAGE': 'en'}):
            self.assertFalse(unchanged(fingerprint(path), state))
        from imprint.cli_viz import build_source_read
        readable = build_source_read('recordings/meeting.wav', project='recordings')
        self.assertEqual(readable['title'], 'meeting.wav')
        self.assertEqual(readable['blocks'][0]['role'], 'speaker')

    def test_decoder_failure_keeps_prior_transcript_and_purge_removes_cache(self):
        path = self.root / 'meeting.wav'; wav(path)
        model = SimpleNamespace(transcribe=lambda *args, **kwargs: (iter([segment('The original transcript remains available.')]), SimpleNamespace(language='en')))
        with patch.object(audio, 'load_model', return_value=model), patch.object(audio, 'report'):
            self.ingest(path)
        old = ss.get('recordings/meeting.wav', 'recordings')
        path.write_bytes(b'corrupt replacement audio')
        with self.assertRaises(SystemExit): self.ingest(path)
        self.assertEqual(ss.get('recordings/meeting.wav', 'recordings')['text'], old['text'])
        digest = old['metadata']['provenance']['source_hash']
        identity = old['metadata']['details']['audio_profile']
        self.assertIsNotNone(audio.cached(digest, identity))
        vs.delete_by_source('recordings/meeting.wav', project='recordings')
        self.assertIsNone(audio.cached(digest, identity))

    def test_snapshot_cache_never_crosses_workspaces(self):
        path = self.root / 'meeting.wav'; wav(path)
        model = SimpleNamespace(transcribe=lambda *args, **kwargs: (iter([segment('Workspace private transcript.')]), SimpleNamespace(language='en')))
        with patch.object(audio, 'load_model', return_value=model), patch.object(audio, 'report'):
            self.ingest(path)
        snapshot = ss.get('recordings/meeting.wav', 'recordings')
        config.register_workspace('other')
        with patch.dict(os.environ, {'IMPRINT_WORKSPACE': 'other'}):
            self.assertIsNone(audio.cached(snapshot['metadata']['provenance']['source_hash'], snapshot['metadata']['details']['audio_profile']))

    def test_identical_transcript_reuses_vectors_but_updates_model_profile(self):
        path = self.root / 'meeting.wav'; wav(path)
        model = SimpleNamespace(transcribe=lambda *args, **kwargs: (iter([segment('An unchanged spoken sentence.')]), SimpleNamespace(language='en')))
        with patch.object(audio, 'load_model', return_value=model), patch.object(audio, 'report'):
            self.ingest(path)
            first = ss.get('recordings/meeting.wav', 'recordings')
            with patch.dict(os.environ, {'IMPRINT_AUDIO_MODEL': 'small'}), \
                    patch('imprint.embeddings.embed_documents_batch') as embed:
                self.ingest(path)
                embed.assert_not_called()
                state = vs.get_source_states()['recordings/meeting.wav']
                self.assertEqual(state['doc_metadata']['audio_profile'], audio.profile())
                self.assertTrue(unchanged(fingerprint(path), state))
                self.assertEqual(ss.get('recordings/meeting.wav', 'recordings')['text'], first['text'])

    def test_folder_scans_audio_only_when_enabled(self):
        from imprint.cli_index import scan_dir
        path = self.root / 'meeting.wav'; wav(path)
        with patch.dict(os.environ, {'IMPRINT_AUDIO_ENABLED': 'true'}):
            self.assertIn(('meeting.wav', str(path)), scan_dir(str(self.root)))
        with patch.dict(os.environ, {'IMPRINT_AUDIO_ENABLED': 'false'}):
            self.assertNotIn(('meeting.wav', str(path)), scan_dir(str(self.root)))


if __name__ == '__main__': unittest.main()
