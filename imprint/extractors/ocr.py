"""Local OCR installed with the base runtime; no system tools for defaults."""
from __future__ import annotations

from functools import lru_cache
import threading

from . import ExtractorUnavailable, ExtractionError

_lock = threading.Lock()


@lru_cache(maxsize=1)
def _engine():
    try:
        from rapidocr import RapidOCR
    except ImportError as error:
        raise ExtractorUnavailable(f'OCR runtime missing ({error}) — run imprint bootstrap') from error
    # Small, bundled models; keep CPU parallelism bounded on workstations.
    return RapidOCR(params={
        'Global.log_level': 'error',
        'EngineConfig.onnxruntime.intra_op_num_threads': 4,
        'EngineConfig.onnxruntime.inter_op_num_threads': 1,
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
        with _lock:
            result = _engine()(image)
        return '\n'.join(result.txts or ()).strip()
    except ExtractorUnavailable:
        raise
    except Exception as error:
        raise ExtractionError(f'OCR failed: {error}') from error


def available(lang: str = 'eng') -> bool:
    import importlib.util
    if not all(importlib.util.find_spec(name) is not None for name in ('PIL', 'pypdfium2')):
        return False
    if lang not in ('eng', 'auto'):
        import shutil
        return bool(shutil.which('tesseract') and importlib.util.find_spec('pytesseract'))
    return all(importlib.util.find_spec(name) is not None
               for name in ('rapidocr', 'onnxruntime'))
