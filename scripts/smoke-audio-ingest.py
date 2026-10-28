#!/usr/bin/env python3
"""Native ASR smoke using caller-supplied English/Romanian speech fixtures.

Uses a disposable data directory. --model-cache may reuse verified weights;
otherwise the selected pinned model is downloaded into the disposable directory.
No transcripts are saved or printed. This checks behavior, not WER or latency.
"""
import argparse
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
import wave

import numpy as np


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--english', type=Path, required=True)
    parser.add_argument('--romanian', type=Path, required=True)
    parser.add_argument('--model-cache', type=Path)
    parser.add_argument('--model', choices=('small', 'large-v3-turbo'), default='large-v3-turbo')
    args = parser.parse_args()
    from imprint import audio_ingest as audio
    with tempfile.TemporaryDirectory(prefix='imprint-audio-smoke-') as temporary:
        cache = args.model_cache or Path(temporary) / 'model'
        with patch.dict(os.environ, {'IMPRINT_DATA_DIR': temporary, 'IMPRINT_WORKSPACE': 'default',
                                     'IMPRINT_AUDIO_MODEL': args.model, 'IMPRINT_AUDIO_ENABLED': 'true',
                                     'IMPRINT_AUDIO_LANGUAGE': 'auto', 'IMPRINT_REDACT_PRIVACY_FILTER': 'false'}), \
                patch.object(audio, 'model_root', return_value=cache):
            for language, path in (('en', args.english), ('ro', args.romanian)):
                segments, _ = audio.transcribe(str(path.resolve()), force=True)
                detected = {segment['language'] for segment in segments}
                assert language in detected and segments, (language, detected)
                assert all(0 <= segment['start'] <= segment['end'] for segment in segments)
                print(json.dumps({'expected_language': language, 'detected': sorted(detected), 'segments': len(segments)}), flush=True)
            # Put Romanian after the next decoding boundary to exercise mixed
            # language detection and timestamps across bounded audio windows.
            english = np.concatenate([samples[int(start > 0) * audio.OVERLAP:] for samples, start, _ in audio.windows(str(args.english), 120)])
            romanian = np.concatenate([samples[int(start > 0) * audio.OVERLAP:] for samples, start, _ in audio.windows(str(args.romanian), 120)])
            assert len(english) < audio.WINDOW, 'Use an English fixture shorter than 30 seconds'
            mixed = np.concatenate((english, np.zeros(audio.WINDOW - len(english), dtype=np.float32), romanian))
            path = Path(temporary) / 'mixed.wav'
            with wave.open(str(path), 'wb') as output:
                output.setnchannels(1); output.setsampwidth(2); output.setframerate(audio.RATE)
                output.writeframes((np.clip(mixed, -1, 1) * 32767).astype('<i2').tobytes())
            segments, _ = audio.transcribe(str(path), force=True)
            assert {'en', 'ro'} <= {segment['language'] for segment in segments}, segments
            assert any(segment['language'] == 'ro' and segment['start'] >= 29 for segment in segments)
            print('Native mixed-language audio ingestion and timestamp smoke passed', flush=True)


if __name__ == '__main__': main()
