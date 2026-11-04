"""Meeting recordings (ADR 0131): the dashboard records this computer's
microphone and, when asked, its audio output, and Imprint transcribes them
locally into a searchable source.

Each recording lives in ``data/recordings/<id>/``: ``meta.json``, the raw
``audio.webm`` (the microphone on the left channel, the computer's audio on
the right) and, once transcribed, ``transcript.md``. The transcript is stored
as the source ``recording/<id>`` in the recording's project, and transcribing
again replaces it. The raw audio stays on this device: sync carries the
transcript's memories, never the audio.

Transcription splits the channels and runs each through the audio ingestion
pipeline (ADR 0095, 0096), with its model, limits, voice detection and
redaction, so the transcript can say who spoke: "You" (the microphone) or
"Others" (the computer's audio).
"""
from __future__ import annotations

import json
import math
import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path

from . import config

STATES = ('recording', 'stopped', 'transcribing', 'ready', 'failed')
DEFAULT_PROJECT = 'meetings'
SOURCE = 'recording/'
_ID = re.compile(r'[0-9a-f]{32}')
_RATE = 16000


def root() -> Path:
    return config.get_data_dir() / 'recordings'


def _folder(recording_id: str) -> Path:
    if not _ID.fullmatch(str(recording_id)):
        raise KeyError(recording_id)
    return root() / recording_id


def _write_meta(meta: dict) -> dict:
    folder = _folder(meta['id'])
    staged = folder / 'meta.json.tmp'
    staged.write_text(json.dumps(meta, indent=2), encoding='utf-8')
    staged.replace(folder / 'meta.json')
    return meta


def get(recording_id: str) -> dict:
    try:
        meta = json.loads((_folder(recording_id) / 'meta.json').read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise KeyError(recording_id) from None
    audio = _folder(recording_id) / 'audio.webm'
    meta['audio_bytes'] = audio.stat().st_size if audio.exists() else 0
    meta['transcript'] = (_folder(recording_id) / 'transcript.md').exists()
    return meta


def list_all() -> list[dict]:
    if not root().exists():
        return []
    found = []
    for folder in root().iterdir():
        try:
            found.append(get(folder.name))
        except (KeyError, ValueError):
            continue
    return sorted(found, key=lambda m: -m['started_at'])


def update(recording_id: str, **changes) -> dict:
    meta = get(recording_id)
    meta.update(changes)
    for key in ('audio_bytes', 'transcript'):
        meta.pop(key, None)
    _write_meta(meta)
    return get(recording_id)


def start(title: str, project: str = DEFAULT_PROJECT, others: bool = True, consent: bool = False) -> dict:
    """A new recording, before any audio arrives. Recording other people
    needs their consent; the user confirms they have it."""
    if consent is not True:
        raise ValueError('Confirm that everyone on the call knows it is being recorded')
    from .projects import project_name
    title = ' '.join(str(title or '').split())[:200] or time.strftime('Meeting %Y-%m-%d %H:%M')
    meta = {'id': uuid.uuid4().hex, 'title': title, 'project': project_name(project or DEFAULT_PROJECT),
            'others': bool(others), 'consent': True, 'status': 'recording', 'started_at': time.time(),
            'stopped_at': None, 'duration': 0.0, 'error': '', 'memories': 0, 'transcribed_at': None}
    _folder(meta['id']).mkdir(parents=True)
    _write_meta(meta)
    return get(meta['id'])


def append(recording_id: str, data: bytes) -> dict:
    """Add the next piece of a recording's audio, as the recorder sends it."""
    meta = get(recording_id)
    if meta['status'] != 'recording':
        raise ValueError('This recording has stopped')
    limit = int(_audio_setting('audio.max_file_size_mb')) * 1024 * 1024
    if meta['audio_bytes'] + len(data) > limit:
        raise ValueError('The recording reached audio.max_file_size_mb')
    with (_folder(recording_id) / 'audio.webm').open('ab') as audio:
        audio.write(data)
    return {'audio_bytes': meta['audio_bytes'] + len(data)}


def stop(recording_id: str, duration: float | None = None) -> dict:
    meta = get(recording_id)
    if meta['status'] != 'recording':
        return meta
    elapsed = time.time() - meta['started_at']
    seconds = elapsed if duration is None else max(0.0, min(float(duration), elapsed + 5))
    return update(recording_id, status='stopped', stopped_at=time.time(), duration=round(seconds, 1))


def remove_audio(recording_id: str) -> dict:
    """Delete the raw audio and keep the transcript and its memories."""
    meta = get(recording_id)
    if meta['status'] in ('recording', 'transcribing'):
        raise ValueError('Wait until the recording is stopped and transcribed')
    (_folder(recording_id) / 'audio.webm').unlink(missing_ok=True)
    return get(recording_id)


def delete(recording_id: str, workspace: str | None = None) -> None:
    """Delete the recording, its transcript and the memories made from it."""
    get(recording_id)
    from . import vectorstore as vs
    vs.delete_by_source(SOURCE + recording_id, workspace)
    shutil.rmtree(_folder(recording_id), ignore_errors=True)


def _audio_setting(key: str):
    from .config_schema import resolve
    return resolve(key)[0]


# ── Transcription ───────────────────────────────────────────────

def split_channels(audio: Path, folder: Path) -> list[tuple[str, Path]]:
    """The recording's channels as mono 16 kHz WAV files: ("You", left) and,
    when the right channel carries sound, ("Others", right)."""
    import wave
    import av
    import numpy as np

    left, right = [], []
    options = {'protocol_whitelist': 'file,pipe', 'format_whitelist': 'matroska,webm,ogg,wav'}
    with av.open(str(audio), mode='r', options=options) as container:
        if not container.streams.audio:
            raise ValueError('The recording has no audio')
        resampler = av.AudioResampler(format='fltp', layout='stereo', rate=_RATE)
        for frame in [*container.decode(audio=0), None]:
            if frame is not None:
                frame.pts = None
            for converted in resampler.resample(frame):
                samples = converted.to_ndarray()
                left.append(samples[0])
                right.append(samples[1] if samples.shape[0] > 1 else np.zeros_like(samples[0]))
    channels = []
    for name, parts in (('You', left), ('Others', right)):
        samples = np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)
        # A silent channel (no computer audio was shared) is not transcribed.
        if not samples.size or float(np.sqrt(np.mean(samples ** 2))) < 1e-4:
            continue
        path = folder / f'{name.lower()}.wav'
        with wave.open(str(path), 'wb') as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(_RATE)
            output.writeframes((np.clip(samples, -1, 1) * 32767).astype('<i2').tobytes())
        channels.append((name, path))
    return channels


def _clock(seconds: float) -> str:
    seconds = int(seconds)
    return f'{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}'


def transcript(meta: dict, spoken: list[tuple[str, dict]], model: str) -> str:
    """Markdown: a heading, how it was made, then each turn with its speaker
    and time. Consecutive segments of one speaker form one turn."""
    turns: list[list] = []
    for speaker, segment in sorted(spoken, key=lambda item: (item[1]['start'], item[0])):
        if turns and turns[-1][0] == speaker and segment['start'] - turns[-1][2] < 4:
            turns[-1][1].append(segment['text'])
            turns[-1][2] = segment['end']
        else:
            turns.append([speaker, [segment['text']], segment['end'], segment['start']])
    started = time.strftime('%Y-%m-%d %H:%M', time.localtime(meta['started_at']))
    minutes = max(1, math.ceil((meta.get('duration') or 0) / 60))
    header = [f"# {meta['title']}", '',
              f'Recorded {started}, about {minutes} min, transcribed on this computer with {model}. '
              '"You" is this computer\'s microphone; "Others" is the audio it played.', '']
    body = [f"**{speaker}** ({_clock(start)}): {' '.join(texts)}" for speaker, texts, _, start in turns]
    return '\n'.join(header + (body or ['No speech was recognized.'])) + '\n'


def transcribe(recording_id: str, workspace: str | None = None) -> dict:
    """Transcribe a stopped recording and store its transcript as memories."""
    from . import audio_ingest
    from .ingest_core import store_inline_content
    from .progress import Task

    meta = get(recording_id)
    if meta['status'] == 'recording':
        raise ValueError('Stop the recording first')
    audio = _folder(recording_id) / 'audio.webm'
    if not audio.exists() or not audio.stat().st_size:
        raise ValueError('The recording has no audio to transcribe')
    update(recording_id, status='transcribing', error='')
    try:
        with Task('transcribe-recording', phase='transcribing_audio', unit='seconds') as task, \
                tempfile.TemporaryDirectory() as scratch:
            spoken, model = [], ''
            for speaker, path in split_channels(audio, Path(scratch)):
                task.update(detail=speaker)
                with audio_ingest.reporting(task):
                    segments, details = audio_ingest.transcribe(str(path), force=True)
                model = details['transcription_model'].rsplit('/', 1)[-1]
                spoken += [(speaker, segment) for segment in segments]
            text = transcript(get(recording_id), spoken, model or 'Whisper')
            stored, status = store_inline_content(
                text, source_key=SOURCE + recording_id, project=meta['project'], chunk_mode='prose',
                source_type='recording', rel_path=f'{SOURCE}{recording_id}.md', workspace=workspace)
            if status not in ('stored', 'skipped-empty'):
                raise RuntimeError(f'Storing the transcript failed: {status}')
            (_folder(recording_id) / 'transcript.md').write_text(text, encoding='utf-8')
            task.finish(f"Transcribed {meta['title']}: {stored} memories in {meta['project']}")
    except Exception as error:
        update(recording_id, status='failed', error=str(error)[:500])
        raise
    return update(recording_id, status='ready', memories=stored, transcribed_at=time.time())


def main():
    import argparse
    import sys
    from . import queue_lock
    parser = argparse.ArgumentParser(prog='imprint recordings')
    parser.add_argument('action', choices=['transcribe'])
    parser.add_argument('id')
    args = parser.parse_args()
    fd = queue_lock.acquire('transcribe-recording', '', block=True)
    try:
        meta = transcribe(args.id)
        print(f"Transcribed {meta['title']}: {meta['memories']} memories", flush=True)
    except (KeyError, ValueError, RuntimeError) as error:
        print(f'Transcription failed: {error}', file=sys.stderr, flush=True)
        sys.exit(1)
    finally:
        if fd is not None:
            queue_lock.release(fd)


if __name__ == '__main__':
    main()
