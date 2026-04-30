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


def extract_links_bs4(data: bytes, base_url: str) -> list[str]:
    """Extract same-domain http(s) links from HTML bytes using BS4.

    Returns deduplicated absolute URLs on the same netloc, fragments stripped.
    Falls back gracefully if bs4 is not installed.
    """
    from urllib.parse import urljoin
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError:
        return []

    domain = urlparse(base_url).netloc
    try:
        soup = BeautifulSoup(data, "html.parser")
    except Exception:
        return []

    links: list[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href:
            continue
        abs_url = urljoin(base_url, href)
        parsed = urlparse(abs_url)
        if parsed.scheme not in ("http", "https"):
            continue
        if parsed.netloc != domain:
            continue
        clean = abs_url.split("#")[0].rstrip("/") or abs_url
        if clean and clean not in seen:
            seen.add(clean)
            links.append(clean)
    return links


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

    When ``ingest.use_obscura=true`` and the obscura binary is available,
    short-circuits to obscura for HTML pages (JS-rendered extraction).
    Falls back to the standard httpx + trafilatura path on any failure.

    Populates metadata with: source_url, final_url, etag, last_modified,
    status_code, content_type.
    """
    if not _is_url(url):
        raise ExtractionError(f"not an http(s) url: {url}")

    # ── Obscura fast-path (JS-rendered HTML) ──────────────────────────────
    if _get_config("ingest.use_obscura", False):
        try:
            from . import obscura as _obs
            docs = _obs.fetch(url)
            for doc in docs:
                doc.metadata.setdefault("source_url", url)
                doc.metadata.setdefault("original_url", url)
                doc.metadata.setdefault("content_type", "text/html")
            return docs
        except (ExtractorUnavailable, ExtractionError):
            pass  # fall through to httpx path

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


def extract_links(url: str) -> list[str]:
    """Return same-domain http(s) links found on a URL's page.

    When ``ingest.use_obscura=true`` and the obscura binary is available,
    uses obscura (JS-rendered DOM) for link extraction. Falls back to
    fetching with httpx and parsing with BS4.

    Always returns a list (empty on any failure — never raises).
    """
    domain = urlparse(url).netloc

    if _get_config("ingest.use_obscura", False):
        try:
            from . import obscura as _obs
            return _obs.extract_links(url, domain=domain)
        except Exception:
            pass  # fall through to httpx+bs4 path

    # httpx fetch + BS4 link extraction
    try:
        httpx = _ensure_httpx()
        connect_timeout = float(_get_config("ingest.url_timeout_sec", 30))
        read_timeout = float(_get_config("ingest.url_read_timeout_sec", 300))
        user_agent = str(_get_config("ingest.url_user_agent", "imprint/1.0"))
        import io
        with httpx.stream(
            "GET", url,
            follow_redirects=True,
            timeout=httpx.Timeout(connect_timeout, read=read_timeout),
            headers={"User-Agent": user_agent},
        ) as resp:
            buf = io.BytesIO()
            for chunk in resp.iter_bytes(chunk_size=1_048_576):
                buf.write(chunk)
            data = buf.getvalue()
            final_url = str(resp.url)
        return extract_links_bs4(data, final_url)
    except Exception:
        return []


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
