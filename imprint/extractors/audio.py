"""Audio files produce timestamped, unidentified-speaker conversation segments."""
from pathlib import Path
from . import ExtractedDoc, ExtractionError, ExtractorUnavailable, register_ext
from ..audio_ingest import AUDIO_EXTENSIONS


def extract_file(path: str, *, force=False):
    from .. import audio_ingest
    try:
        segments, metadata = audio_ingest.transcribe(path, force=force)
        return [ExtractedDoc(text=segment["text"], mime="text/plain", chunk_mode="prose", metadata={
            "audio": True, **metadata, "title": Path(path).name,
            "audio_start": segment["start"], "audio_end": segment["end"], "language": segment["language"],
            "speaker": None,
        }) for segment in segments]
    except ImportError as error:
        raise ExtractorUnavailable("Audio runtime unavailable; run imprint bootstrap and retry") from error
    except Exception as error:
        raise ExtractionError(f"Audio transcription failed: {error}") from error


for extension in AUDIO_EXTENSIONS:
    register_ext(extension, extract_file)
