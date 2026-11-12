"""Meeting recordings (ADR 0131): the dashboard records this computer's
microphone and, when asked, its audio output, and Imprint transcribes them
locally into a searchable source.

Each recording lives in ``data/recordings/<id>/``: ``meta.json``, the raw
``audio.webm`` (the microphone on the left channel, the computer's audio on
the right) and, once transcribed, ``transcript.md``. The transcript is stored
as the source ``recording/<id>`` in the recording's project, and transcribing
again replaces it. Once the recording stops, its audio is copied into a file
that states its length and can be sought (ADR 0184). The audio stays on this
device unless recordings sync with a paired device (ADR 0177).

Transcription splits the channels and runs each through the audio ingestion
pipeline (ADR 0095, 0096), with its model, limits, voice detection and
redaction, so the transcript can say who spoke: "You" (the microphone) or
"Others" (the computer's audio).
"""
from __future__ import annotations

import json
import logging
import math
import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path

from . import config

log = logging.getLogger(__name__)

STATES = ('recording', 'stopped', 'transcribing', 'ready', 'failed')
DEFAULT_PROJECT = 'meetings'
SOURCE = 'recording/'
_ID = re.compile(r'[0-9a-f]{32}')
# The details paired devices receive with a recording (ADR 0177).
SYNCED = ('title', 'project', 'others', 'consent', 'status', 'started_at', 'stopped_at', 'duration', 'error',
          'memories', 'transcribed_at', 'updated_at', 'audio_removed_at', 'speakers')
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


def version(meta: dict) -> float:
    """When the recording last changed: what paired devices compare (ADR 0177)."""
    return float(meta.get('updated_at') or meta.get('transcribed_at') or meta.get('stopped_at')
                 or meta.get('started_at') or 0)


def update(recording_id: str, **changes) -> dict:
    meta = get(recording_id)
    meta.update(changes)
    meta['updated_at'] = max(time.time(), float(meta.get('updated_at') or 0) + 0.001)
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
    meta['updated_at'] = meta['started_at']
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
    update(recording_id, status='stopped', stopped_at=time.time(), duration=round(seconds, 1))
    index_audio(recording_id)  # Players get its length and can seek (ADR 0184).
    return get(recording_id)


def remove_audio(recording_id: str) -> dict:
    """Delete the raw audio and keep the transcript and its memories."""
    meta = get(recording_id)
    if meta['status'] in ('recording', 'transcribing'):
        raise ValueError('Wait until the recording is stopped and transcribed')
    (_folder(recording_id) / 'audio.webm').unlink(missing_ok=True)
    # A newer version: paired devices that sync recordings delete it too (ADR 0177).
    return update(recording_id, audio_removed_at=time.time())


# Where the indexed copy is written before it replaces the audio; not the
# partial download sync writes (``audio.webm.part``).
INDEXING = 'audio.webm.indexing'
# A finished recording's states: one still recording or transcribing is left alone.
FINISHED = ('stopped', 'ready', 'failed')


def index_audio(recording_id: str) -> bool:
    """Give the recording's audio its length and seek index (ADR 0184).

    The browser's recorder writes WebM as a live stream, without the
    Duration and the Cues a player needs to show the length and to seek, and
    the pieces are stored as they come. Copying its packets into a new file,
    without decoding them, writes both. Audio that has them already, cannot
    be read, or changes meanwhile stays as it is. The new audio is a newer
    version of the recording, so paired devices that sync recordings take it
    (ADR 0177). Returns whether the audio was rewritten."""
    folder = _folder(recording_id)
    audio, staged = folder / 'audio.webm', folder / INDEXING
    try:
        import av
        before = audio.stat()
        options = {'protocol_whitelist': 'file', 'format_whitelist': 'matroska,webm'}
        with av.open(str(audio), mode='r', options=options) as source:
            if source.duration is not None or not source.streams.audio:
                return False
            stream = source.streams.audio[0]
            with av.open(str(staged), mode='w', format='webm') as target:
                copy = target.add_stream_from_template(stream)
                for packet in source.demux(stream):
                    if packet.dts is None:
                        continue  # the demuxer's empty packet at the end
                    packet.stream = copy
                    target.mux(packet)
    except Exception as error:  # noqa: BLE001 - no audio, not WebM, or no PyAV: the audio stays as it was
        staged.unlink(missing_ok=True)
        log.info('Recording %s: its audio was not indexed: %s', recording_id, error)
        return False
    try:
        with av.open(str(staged), mode='r') as check:
            if check.duration is None:
                raise ValueError('the copy states no length')
        after = audio.stat()
        if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
            raise ValueError('the audio changed meanwhile')
        # On Windows this fails while the audio is open, say being played; the next start tries again.
        staged.replace(audio)
    except Exception as error:  # noqa: BLE001 - the audio stays as it was
        staged.unlink(missing_ok=True)
        log.warning('Recording %s: its audio was not indexed: %s', recording_id, error)
        return False
    update(recording_id)
    return True


def index_all() -> int:
    """Index the audio of every finished recording that lacks it (ADR 0184):
    those recorded before audio was indexed, and those a paired device on an
    older release sent. Returns how many were rewritten."""
    done = 0
    for meta in list_all():
        if meta['status'] in FINISHED and meta['audio_bytes']:
            try:
                done += index_audio(meta['id'])
            except KeyError:
                continue  # deleted meanwhile
    return done


def delete(recording_id: str, workspace: str | None = None) -> None:
    """Delete the recording, its transcript and the memories made from it."""
    get(recording_id)
    from . import sync_deletions
    sync_deletions.record(None, [recording_id], dataset='recordings')  # Its folder goes on paired devices (ADR 0177).
    from . import vectorstore as vs
    vs.delete_by_source(SOURCE + recording_id, workspace, propagate='all')  # On paired devices too (ADR 0176).
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


# A microphone passage is the computer's audio heard again through the
# speakers when this share of its words was played within ECHO_MARGIN
# seconds of it (ADR 0173). Echo cancellation removes only what Imprint
# itself plays, not a call in another app. Shorter passages are kept: a
# "yes" said while the others say "yes" is not an echo.
ECHO_SHARE = 0.6
ECHO_MARGIN = 5.0
ECHO_MIN_WORDS = 4


def _words(text: str) -> list[str]:
    return re.findall(r'\w+', text.casefold())


def without_echo(spoken: list[tuple[str, dict]]) -> list[tuple[str, dict]]:
    """The segments without the microphone's passages that repeat what the
    computer played at the same time."""
    import difflib
    played = sorted((segment for speaker, segment in spoken if speaker == 'Others'), key=lambda s: s['start'])
    kept = []
    for speaker, segment in spoken:
        words = _words(segment['text']) if speaker == 'You' else []
        if len(words) >= ECHO_MIN_WORDS:
            heard = [word for other in played if other['end'] >= segment['start'] - ECHO_MARGIN
                     and other['start'] <= segment['end'] + ECHO_MARGIN for word in _words(other['text'])]
            matcher = difflib.SequenceMatcher(None, words, heard, autojunk=False)
            if sum(block.size for block in matcher.get_matching_blocks()) >= ECHO_SHARE * len(words):
                continue
        kept.append((speaker, segment))
    return kept


# ── Invented passages (ADR 0181) ────────────────────────────────
#
# Whisper writes text where no one said it: outros it learned from video
# subtitles ("Thank you.", "Nu uitați să vă abonați la canal") and short
# fragments in other languages. A sentence is left out when Whisper's own
# signals mark it: the anomaly score of its words (improbable, or too long or
# too short; faster-whisper's hallucination_silence_threshold uses it), or a
# language the rest of the recording does not use, decoded with little
# confidence. A recording that mixes languages keeps each one it uses.

ANOMALY_SHARE = 0.65    # anomaly score per word, of the first ANOMALY_WORDS
ANOMALY_WORDS = 8
UNSURE = -0.5           # a segment's average log-probability below this
LANGUAGE_SHARE = 0.15   # a language holding less of the recording's words is foreign to it


def _anomaly(word) -> float:
    """Whisper's anomaly score of a word (faster-whisper's word_anomaly_score)."""
    probability = word[3] if len(word) > 3 else 1.0
    duration = word[1] - word[0]
    score = 1.0 if probability < 0.15 else 0.0
    if duration < 0.133:
        score += (0.133 - duration) * 15
    if duration > 2.0:
        score += duration - 2.0
    return score


def without_invented(spoken: list[tuple[str, dict]]) -> tuple[list[tuple[str, dict]], int]:
    """The segments without the sentences Whisper likely invented, and how
    many sentences were left out. A segment that loses some sentences is
    split around them and its kept text is redacted again; segments without
    word timings are kept."""
    from .redaction import redact_all
    languages: dict[str, int] = {}
    for _, segment in spoken:
        language = segment.get('language') or ''
        languages[language] = languages.get(language, 0) + len(segment.get('words') or [segment])
    total = sum(languages.values()) or 1
    foreign = {language for language, count in languages.items() if count < LANGUAGE_SHARE * total}
    kept, rebuilt, left_out = [], [], 0
    for speaker, segment in spoken:
        words = segment.get('words')
        if not words:
            kept.append((speaker, segment))
            continue
        unsure = segment.get('language') in foreign and segment.get('avg_logprob', 0.0) < UNSURE
        runs: list[list] = [[]]
        for sentence in _sentences(words):
            scored = [words[at] for at in sentence][:ANOMALY_WORDS]
            if unsure or sum(map(_anomaly, scored)) >= ANOMALY_SHARE * len(scored):
                left_out += 1
                runs.append([])
            else:
                runs[-1].extend(words[at] for at in sentence)
        runs = [run for run in runs if run]
        if len(runs) == 1 and len(runs[0]) == len(words):
            kept.append((speaker, segment))
            continue
        for run in runs:
            rebuilt.append(len(kept))
            kept.append((speaker, {**segment, 'start': run[0][0], 'end': run[-1][1], 'words': run,
                                   'text': ''.join(word[2] for word in run).strip()}))
    if rebuilt:
        for index, text in zip(rebuilt, redact_all([kept[k][1]['text'] for k in rebuilt])):
            kept[index][1]['text'] = text
    return [(speaker, segment) for speaker, segment in kept if segment['text']], left_out


# ── Speakers (ADR 0180) ─────────────────────────────────────────
#
# Each channel is told apart into voices (``diarize``); each sentence goes to
# the voice whose turns its words overlap most, so a segment two people spoke
# is split between sentences, never inside one. A microphone voice heard
# while the computer's audio speaks is that audio through the speakers and is
# left out. Of the rest, the voice with the most speech is "You"; in the
# computer's audio voices are numbered by when they first speak. Names are
# kept by key in meta['speakers'] and the parts in segments.json, so a
# rename rewrites the transcript without transcribing again.

CHANNELS = {'You': 'mic', 'Others': 'call'}
NAME_CHARS = 60
# A word this close to a turn, but outside every turn, still goes to it.
NEAR = 0.5
# A word ending so ends a sentence; a segment's end does too.
SENTENCE_END = ('.', '?', '!', '…', '。', '？', '！')
# A microphone voice that speaks while the computer's audio speaks for at
# least this share of its time is that audio heard again (ADR 0180).
ECHO_VOICE = 0.7


def _speaker_at(turns, start: float, end: float):
    """The voice whose turn overlaps [start, end] most, else the nearest
    within NEAR seconds; None when none is that close."""
    import numpy as np
    starts, ends, voices = turns
    overlap = np.minimum(ends, end) - np.maximum(starts, start)
    best = int(np.argmax(overlap))
    if overlap[best] > 0:
        return int(voices[best])
    gap = np.maximum(starts - end, start - ends)
    best = int(np.argmin(gap))
    return int(voices[best]) if gap[best] <= NEAR else None


def _sentences(words: list) -> list[list[int]]:
    """A segment's words, by index, grouped into sentences by punctuation."""
    sentences: list[list[int]] = [[]]
    for at, word in enumerate(words):
        sentences[-1].append(at)
        if str(word[2]).strip().endswith(SENTENCE_END):
            sentences.append([])
    return [sentence for sentence in sentences if sentence]


def speaker_parts(segments: list[dict], turns: list[tuple[float, float, int]]) -> list[dict]:
    """The segments as parts said by one voice each: ``{start, end, text,
    voice}``. Each sentence goes to the voice its words' turns cover longest:
    turns can change voice within a sentence, a speaker does not. A segment
    one voice said keeps its (redacted) text; split ones are rebuilt from
    their words and redacted again."""
    import numpy as np
    if not turns:
        return [{'start': seg['start'], 'end': seg['end'], 'text': seg['text'], 'voice': 0} for seg in segments]
    timeline = tuple(np.array(column, dtype=np.float64) for column in zip(*turns))
    parts, split = [], []
    last = 0
    for seg in segments:
        words = seg.get('words') or [[seg['start'], seg['end'], seg['text']]]
        voices = [_speaker_at(timeline, word[0], word[1]) for word in words]
        # A word no turn reaches belongs to the voice around it.
        known = [v for v in voices if v is not None]
        fill = known[0] if known else last
        for at, voice in enumerate(voices):
            voices[at] = fill = voice if voice is not None else fill
        groups = []
        for sentence in _sentences(words):
            # Ties go to the voice heard first in the sentence.
            length: dict[int, float] = {}
            for at in sentence:
                length[voices[at]] = length.get(voices[at], 0.0) + max(words[at][1] - words[at][0], 0.01)
            voice = max(length, key=length.__getitem__)
            if groups and groups[-1][0] == voice:
                groups[-1][1].extend(words[at] for at in sentence)
            else:
                groups.append((voice, [words[at] for at in sentence]))
        if len(groups) == 1:
            parts.append({'start': seg['start'], 'end': seg['end'], 'text': seg['text'], 'voice': groups[0][0]})
        else:
            for voice, group in groups:
                split.append(len(parts))
                parts.append({'start': round(group[0][0], 3), 'end': round(group[-1][1], 3),
                              'text': ''.join(word[2] for word in group).strip(), 'voice': voice})
        last = groups[-1][0]
    if split:
        from .redaction import redact_all
        for index, text in zip(split, redact_all([parts[k]['text'] for k in split])):
            parts[index]['text'] = text
    return [part for part in parts if part['text']]


def echo_voices(turns: list[tuple[float, float, int]], played: list[tuple[float, float, int]]) -> set[int]:
    """The microphone's voices that speak while the computer's audio speaks
    for ECHO_VOICE of their time or more: that audio through the speakers."""
    if not turns or not played:
        return set()
    merged: list[list[float]] = []
    for start, end, _ in sorted(played):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    talk: dict[int, float] = {}
    heard: dict[int, float] = {}
    for start, end, voice in turns:
        talk[voice] = talk.get(voice, 0.0) + end - start
        heard[voice] = heard.get(voice, 0.0) + sum(max(0.0, min(end, b) - max(start, a)) for a, b in merged
                                                 if a < end and b > start)
    return {voice for voice in talk if talk[voice] > 0 and heard[voice] / talk[voice] >= ECHO_VOICE}


def voice_names(channel: str, parts: list[dict]) -> dict[int, str]:
    """Default names of a channel's voices: on the microphone the one with the
    most speech is "You"; in the computer's audio one voice is "Others",
    several are "Speaker 1", "Speaker 2"... by when they first speak."""
    talk: dict[int, float] = {}
    first: dict[int, float] = {}
    for part in parts:
        talk[part['voice']] = talk.get(part['voice'], 0.0) + part['end'] - part['start']
        first.setdefault(part['voice'], part['start'])
    order = sorted(talk, key=lambda voice: first[voice])
    if channel == 'mic':
        main = max(order, key=lambda voice: (talk[voice], -first[voice])) if order else 0
        return {main: 'You', **{voice: f'In the room {n}' for n, voice in
                                enumerate([v for v in order if v != main], 1)}}
    if len(order) <= 1:
        return {voice: 'Others' for voice in order}
    return {voice: f'Speaker {n}' for n, voice in enumerate(order, 1)}


def clean_name(name) -> str:
    """A speaker's name as the transcript shows it: one line, no Markdown."""
    name = ' '.join(re.sub(r'[*_`\[\]<>#|\\]', '', str(name or '')).split())[:NAME_CHARS]
    if not name:
        raise ValueError('A speaker needs a name')
    return name


def render(meta: dict, entries: list[dict], names: dict[str, str], model: str, echoes: int, voices: bool,
           invented: int = 0) -> str:
    """The transcript from saved parts and the speakers' current names."""
    spoken = [(names.get(entry['speaker'], entry['speaker']), entry) for entry in entries]
    return transcript(meta, spoken, model, echoes=echoes, voices=voices, invented=invented)


def transcript(meta: dict, spoken: list[tuple[str, dict]], model: str, echoes: int = 0,
               voices: bool = False, invented: int = 0) -> str:
    """Markdown: a heading, how it was made, then each turn with its speaker
    and time, a paragraph each. Consecutive segments of one speaker form one
    turn. ``echoes`` says how many microphone passages were left out;
    ``voices``, that speakers were told apart within each channel;
    ``invented``, how many sentences the speech model likely invented were
    left out."""
    turns: list[list] = []
    for speaker, segment in sorted(spoken, key=lambda item: (item[1]['start'], item[0])):
        if turns and turns[-1][0] == speaker and segment['start'] - turns[-1][2] < 4:
            turns[-1][1].append(segment['text'])
            turns[-1][2] = segment['end']
        else:
            turns.append([speaker, [segment['text']], segment['end'], segment['start']])
    started = time.strftime('%Y-%m-%d %H:%M', time.localtime(meta['started_at']))
    minutes = max(1, math.ceil((meta.get('duration') or 0) / 60))
    left_out = (f' {echoes} microphone passage{"s" if echoes != 1 else ""} that repeated the computer\'s audio '
                f'{"are" if echoes != 1 else "is"} left out.') if echoes else ''
    left_out += (f' {invented} sentence{"s" if invented != 1 else ""} the speech model likely invented '
                 f'{"are" if invented != 1 else "is"} left out.') if invented else ''
    who = ('Voices were told apart on this computer: on its microphone, the main one is "You" and any other an '
           '"In the room" speaker; in the audio it played, each is a numbered "Speaker", or "Others" when there was '
           'one. Speakers can be renamed on the Recordings page.' if voices
           else '"You" is this computer\'s microphone; "Others" is the audio it played.')
    header = [f"# {meta['title']}",
              f'Recorded {started}, about {minutes} min, transcribed on this computer with {model}. {who}{left_out}']
    body = [f"**{speaker}** ({_clock(start)}): {' '.join(texts)}" for speaker, texts, _, start in turns]
    # A blank line between turns makes each one a paragraph: Markdown joins
    # single line breaks into one block of text.
    return '\n\n'.join(header + (body or ['No speech was recognized.'])) + '\n'


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
            channels = split_channels(audio, Path(scratch))
            for speaker, path in channels:
                task.update(detail=speaker)
                with audio_ingest.reporting(task):
                    segments, details = audio_ingest.transcribe(str(path), force=True, words=True)
                model = details['transcription_model'].rsplit('/', 1)[-1]
                spoken += [(speaker, segment) for segment in segments]
            spoken, invented = without_invented(spoken)
            heard = without_echo(spoken)
            echoes = len(spoken) - len(heard)
            turns = _turns(channels, task)
            entries, names, left_out = _speakers(channels, heard, turns)
            echoes += left_out
            voices = turns is not None
            model = model or 'Whisper'
            text = render(get(recording_id), entries, names, model, echoes, voices, invented)
            (_folder(recording_id) / 'segments.json').write_text(json.dumps(
                {'model': model, 'echoes': echoes, 'voices': voices, 'invented': invented, 'entries': entries},
                ensure_ascii=False), encoding='utf-8')
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
    return update(recording_id, status='ready', memories=stored, transcribed_at=time.time(), speakers=names,
                  workspace=workspace or '')


def _turns(channels, task) -> dict[str, list] | None:
    """Each channel's speaker turns (``audio.diarize``), or None when voices
    are not told apart: the setting is off, or the models cannot run (a
    download fails, say), and each channel is then one speaker."""
    import logging
    from . import audio_ingest
    from .config_schema import resolve
    if not resolve('audio.diarize')[0]:
        return None
    try:
        import wave
        import numpy as np
        from . import diarize
        with audio_ingest.reporting(task):
            diarize.ensure_models(audio_ingest.report)
        turns = {}
        for label, path in channels:
            with wave.open(str(path)) as audio:
                samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype='<i2').astype(np.float32) / 32768
            task.update(0, total=int(len(samples) / diarize.RATE), phase='telling_voices_apart', unit='seconds',
                        detail=label)
            turns[label] = diarize.diarize(samples, progress=lambda done, total: task.update(int(done), total=int(total)))
        return turns
    except Exception as error:  # noqa: BLE001 - the transcript still comes, by channel
        logging.getLogger(__name__).warning('Voices were not told apart: %s', error)
        return None


def _speakers(channels, heard, turns) -> tuple[list[dict], dict[str, str], int]:
    """The transcript's parts with a speaker key each, the speakers' default
    names and how many microphone parts were left out as the computer's
    audio heard again. Without turns, each channel is one speaker."""
    by_channel = {label: [seg for speaker, seg in heard if speaker == label] for label, _ in channels}
    if turns is None:
        entries = [{'speaker': f'{CHANNELS[label]}-1', 'start': seg['start'], 'end': seg['end'], 'text': seg['text']}
                   for label, _ in channels for seg in by_channel[label]]
        names = {f'{CHANNELS[label]}-1': label for label, _ in channels if by_channel[label]}
        return sorted(entries, key=lambda e: (e['start'], e['speaker'])), names, 0
    entries, names, left_out = [], {}, 0
    for label, _ in channels:
        channel = CHANNELS[label]
        parts = speaker_parts(by_channel[label], turns[label])
        if channel == 'mic':
            echo = echo_voices(turns[label], turns.get('Others', []))
            left_out += sum(part['voice'] in echo for part in parts)
            parts = [part for part in parts if part['voice'] not in echo]
        defaults = voice_names(channel, parts)
        order = sorted(defaults, key=lambda voice: min(p['start'] for p in parts if p['voice'] == voice))
        keys = {voice: f'{channel}-{n}' for n, voice in enumerate(order, 1)}
        names.update({keys[voice]: defaults[voice] for voice in order})
        entries += [{'speaker': keys[p['voice']], 'start': p['start'], 'end': p['end'], 'text': p['text']}
                    for p in parts]
    return sorted(entries, key=lambda e: (e['start'], e['speaker'])), names, left_out


def rename_speakers(recording_id: str, changes: dict, workspace: str | None = None) -> dict:
    """Give speakers names: the transcript is written again from its parts
    and its memories replaced, here and, as the user's own change, on paired
    devices (ADR 0176). KeyError for an unknown recording, ValueError for an
    unknown speaker or a recording transcribed before speakers were kept."""
    from . import vectorstore as vs
    from .ingest_core import store_inline_content
    meta = get(recording_id)
    if meta['status'] in ('recording', 'transcribing'):
        raise ValueError('Wait until the recording is transcribed')
    saved = _folder(recording_id) / 'segments.json'
    if not saved.exists() or not meta.get('speakers'):
        raise ValueError('Transcribe this recording again to name its speakers')
    names = dict(meta['speakers'])
    for key, name in (changes or {}).items():
        if key not in names:
            raise ValueError(f'No speaker {key} in this recording')
        names[key] = clean_name(name)
    parts = json.loads(saved.read_text(encoding='utf-8'))
    text = render(meta, parts['entries'], names, parts.get('model') or 'Whisper', int(parts.get('echoes') or 0),
                  bool(parts.get('voices')), int(parts.get('invented') or 0))
    workspace = workspace or meta.get('workspace') or None
    if workspace:
        from . import config
        if workspace not in config.get_known_workspaces():
            workspace = None
    vs.delete_by_source(SOURCE + recording_id, workspace, propagate='all')
    stored, status = store_inline_content(
        text, source_key=SOURCE + recording_id, project=meta['project'], chunk_mode='prose',
        source_type='recording', rel_path=f'{SOURCE}{recording_id}.md', workspace=workspace)
    if status not in ('stored', 'skipped-empty'):
        raise RuntimeError(f'Storing the transcript failed: {status}')
    (_folder(recording_id) / 'transcript.md').write_text(text, encoding='utf-8')
    return update(recording_id, speakers=names, memories=stored)


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
