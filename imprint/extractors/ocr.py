"""Local OCR installed with the base runtime; no system tools for defaults."""
from __future__ import annotations

from functools import lru_cache
import threading

from . import ExtractorUnavailable, ExtractionError, OcrRuntimeMissing

_lock = threading.Lock()

# RapidOCR shrinks an image whose longer side exceeds its Global.max_side_len
# (2000 px) before reading it, so a full-page screenshot 15,000 px tall would be
# read at an eighth of its size. Taller images are read in overlapping bands no
# taller than that; each line is kept from the one band that owns its centre,
# and a line up to BAND_OVERLAP px tall lies whole inside that band (ADR 0190).
BAND_HEIGHT = 2000
BAND_OVERLAP = 200


@lru_cache(maxsize=1)
def _engine():
    try:
        from rapidocr import RapidOCR
    except ImportError as error:
        raise OcrRuntimeMissing(f'OCR runtime missing ({error}) — run imprint bootstrap') from error
    # Small, bundled models; keep CPU parallelism bounded on workstations.
    # OCR stays on the CPU whichever ONNX Runtime build is installed: only
    # the Privacy Filter uses a GPU provider (ADR 0122).
    return RapidOCR(params={
        'Global.log_level': 'error',
        'EngineConfig.onnxruntime.intra_op_num_threads': 4,
        'EngineConfig.onnxruntime.inter_op_num_threads': 1,
        'EngineConfig.onnxruntime.use_cuda': False,
        'EngineConfig.onnxruntime.use_dml': False,
        'EngineConfig.onnxruntime.use_coreml': False,
        'EngineConfig.onnxruntime.use_cann': False,
    })


def recognize(image, lang: str = 'eng') -> str:
    """Default bundled Chinese/English model; explicit languages use Tesseract."""
    if lang not in ('eng', 'auto'):
        try:
            import pytesseract
            return pytesseract.image_to_string(image, lang=lang).strip()
        except ImportError as error:
            raise ExtractorUnavailable('Custom OCR languages need pytesseract and system Tesseract language data') from error
        except Exception as error:
            raise ExtractionError(f'Tesseract OCR ({lang}) failed: {error}') from error
    try:
        return '\n'.join(_read_bands(image)).strip()
    except ExtractorUnavailable:
        raise
    except Exception as error:
        raise ExtractionError(f'OCR failed: {error}') from error


def bands(height: int) -> list[tuple[int, int, int, int]]:
    """(top, bottom, owned_from, owned_to) rows covering an image of this height.

    Neighbouring bands overlap by at least BAND_OVERLAP and split the overlap
    in its middle; the last band ends at the bottom edge.
    """
    if height <= BAND_HEIGHT:
        return [(0, height, 0, height)]
    step = BAND_HEIGHT - BAND_OVERLAP
    tops = list(range(0, height - BAND_HEIGHT, step)) + [height - BAND_HEIGHT]
    edges = [0] + [(tops[i + 1] + tops[i] + BAND_HEIGHT) // 2 for i in range(len(tops) - 1)] + [height]
    return [(top, top + BAND_HEIGHT, edges[i], edges[i + 1]) for i, top in enumerate(tops)]


def _read_bands(image) -> list[str]:
    width, height = image.size
    if height <= BAND_HEIGHT:
        with _lock:
            return list(_engine()(image).txts or ())
    lines = []
    for top, bottom, owned_from, owned_to in bands(height):
        with _lock:
            result = _engine()(image.crop((0, top, width, bottom)))
        boxes = result.boxes if result.boxes is not None else ()
        for box, text in zip(boxes, result.txts or ()):
            centre = top + sum(point[1] for point in box) / len(box)
            if owned_from <= centre < owned_to:
                lines.append(text)
    return lines


def available(lang: str = 'eng') -> bool:
    import importlib.util
    if not all(importlib.util.find_spec(name) is not None for name in ('PIL', 'pypdfium2')):
        return False
    if lang not in ('eng', 'auto'):
        import shutil
        return bool(shutil.which('tesseract') and importlib.util.find_spec('pytesseract'))
    return all(importlib.util.find_spec(name) is not None
               for name in ('rapidocr', 'onnxruntime'))
