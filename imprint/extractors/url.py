"""URL fetcher.

Single entry point: fetch(url, timeout=..., user_agent=...) → list[ExtractedDoc].
Content-Type on the response routes to the by-mime registry:
    text/html         → html extractor (trafilatura)
    application/pdf   → pdf extractor
    image/*           → image OCR
    text/plain|markdown → raw text
    message/rfc822    → email extractor

Refresh helpers: head_check(url) returns ETag / Last-Modified so callers
can skip re-fetch when the page hasn't changed.
"""

from __future__ import annotations

from urllib.parse import urlparse

from . import (
    ExtractedDoc,
    ExtractorUnavailable,
    ExtractionError,
    dispatch_by_mime,
)


def _get_config(key: str, default):
    try:
        from ..config_schema import resolve
        val, _ = resolve(key)
        return val
    except Exception:
        return default


def _ensure_httpx():
    try:
        import httpx  # type: ignore
        return httpx
    except ImportError as e:
        raise ExtractorUnavailable(
            "URL ingest needs httpx — `pip install httpx`"
        ) from e


def _is_url(s: str) -> bool:
    try:
        p = urlparse(s)
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


def fetch(url: str) -> list[ExtractedDoc]:
    """Fetch a URL, dispatch by Content-Type, return ExtractedDoc.

    Uses streaming download with split timeouts so large files (PDFs, etc.)
    are fully received before parsing.  Verifies Content-Length when the
    server provides it.

    Populates metadata with: source_url, final_url, etag, last_modified,
    status_code, content_type.
    """
    if not _is_url(url):
        raise ExtractionError(f"not an http(s) url: {url}")

    httpx = _ensure_httpx()
    import io

    connect_timeout = float(_get_config("ingest.url_timeout_sec", 30))
    read_timeout = float(_get_config("ingest.url_read_timeout_sec", 300))
    user_agent = str(_get_config("ingest.url_user_agent", "imprint/1.0"))

    with httpx.stream(
        "GET", url,
        follow_redirects=True,
        timeout=httpx.Timeout(connect_timeout, read=read_timeout),
        headers={"User-Agent": user_agent},
    ) as resp:
        resp.raise_for_status()
        expected = int(resp.headers.get("content-length") or 0)
        buf = io.BytesIO()
        for chunk in resp.iter_bytes(chunk_size=1_048_576):
            buf.write(chunk)
        data = buf.getvalue()
        if expected and len(data) < expected:
            raise ExtractionError(
                f"truncated download for {url}: got {len(data)} of {expected} bytes"
            )
        mime = resp.headers.get("content-type", "")
        final_url = str(resp.url)
        etag = resp.headers.get("etag", "")
        last_mod = resp.headers.get("last-modified", "")
        status = resp.status_code

    if not mime:
        # Guess from URL extension.
        ext = urlparse(final_url).path.rsplit(".", 1)
        if len(ext) == 2:
            guess = {
                "pdf": "application/pdf",
                "html": "text/html", "htm": "text/html",
                "txt": "text/plain",
                "md": "text/markdown",
                "csv": "text/csv",
                "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "gif": "image/gif", "webp": "image/webp",
            }.get(ext[1].lower())
            if guess:
                mime = guess
        if not mime:
            mime = "text/html"

    try:
        docs = dispatch_by_mime(mime, data, source_url=final_url)
    except ExtractorUnavailable:
        raise
    except Exception as e:
        raise ExtractionError(f"url extract failed for {url}: {e}") from e

    # Stamp URL-specific metadata on every returned doc.
    for doc in docs:
        doc.metadata.setdefault("source_url", final_url)
        doc.metadata.setdefault("original_url", url)
        if etag:
            doc.metadata["etag"] = etag
        if last_mod:
            doc.metadata["last_modified"] = last_mod
        doc.metadata["status_code"] = status
        doc.metadata["content_type"] = mime
    return docs


def head_check(url: str) -> dict:
    """HEAD request — returns {etag, last_modified, status} for refresh
    dedupe. Empty dict on failure."""
    httpx = _ensure_httpx()
    timeout = float(_get_config("ingest.url_timeout_sec", 30))
    user_agent = str(_get_config("ingest.url_user_agent", "imprint/1.0"))
    try:
        resp = httpx.head(
            url,
            follow_redirects=True,
            timeout=httpx.Timeout(timeout),
            headers={"User-Agent": user_agent},
        )
        return {
            "etag": resp.headers.get("etag", ""),
            "last_modified": resp.headers.get("last-modified", ""),
            "status": resp.status_code,
        }
    except ExtractorUnavailable:
        raise
    except Exception:
        return {}
