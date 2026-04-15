"""URL fetcher.

Single entry point: fetch(url, timeout=..., user_agent=...) → ExtractedDoc.
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


def _httpx_client(timeout: float, user_agent: str):
    try:
        import httpx  # type: ignore
    except ImportError as e:
        raise ExtractorUnavailable(
            "URL ingest needs httpx — `pip install httpx`"
        ) from e
    return httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": user_agent},
    )


def _is_url(s: str) -> bool:
    try:
        p = urlparse(s)
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


def fetch(url: str) -> ExtractedDoc:
    """Fetch a URL, dispatch by Content-Type, return ExtractedDoc.

    Populates metadata with: source_url, final_url, etag, last_modified,
    status_code, content_type.
    """
    if not _is_url(url):
        raise ExtractionError(f"not an http(s) url: {url}")

    timeout = float(_get_config("ingest.url_timeout_sec", 30))
    user_agent = str(_get_config("ingest.url_user_agent", "imprint/1.0"))

    with _httpx_client(timeout, user_agent) as client:
        resp = client.get(url)
        resp.raise_for_status()
        data = resp.content
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
        doc = dispatch_by_mime(mime, data, source_url=final_url)
    except ExtractorUnavailable:
        raise
    except Exception as e:
        raise ExtractionError(f"url extract failed for {url}: {e}") from e

    # Stamp URL-specific metadata for refresh logic + payload.
    doc.metadata.setdefault("source_url", final_url)
    doc.metadata.setdefault("original_url", url)
    if etag:
        doc.metadata["etag"] = etag
    if last_mod:
        doc.metadata["last_modified"] = last_mod
    doc.metadata["status_code"] = status
    doc.metadata["content_type"] = mime
    return doc


def head_check(url: str) -> dict:
    """HEAD request — returns {etag, last_modified, status} for refresh
    dedupe. Empty dict on failure."""
    timeout = float(_get_config("ingest.url_timeout_sec", 30))
    user_agent = str(_get_config("ingest.url_user_agent", "imprint/1.0"))
    try:
        with _httpx_client(timeout, user_agent) as client:
            resp = client.head(url)
        return {
            "etag": resp.headers.get("etag", ""),
            "last_modified": resp.headers.get("last-modified", ""),
            "status": resp.status_code,
        }
    except ExtractorUnavailable:
        raise
    except Exception:
        return {}
