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

With ``ingest.url_respect_robots`` every request first consults the site's
robots.txt (RFC 9309), cached per origin for an hour (five minutes after a failure).
"""

from __future__ import annotations

import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from . import (
    DocumentTooLarge,
    ExtractedDoc,
    ExtractorUnavailable,
    ExtractionError,
    dispatch_by_mime,
    max_doc_bytes,
)


class RobotsDisallowed(ExtractionError):
    """robots.txt forbids the URL, or could not be read (RFC 9309: refuse)."""


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


# origin -> (checked at, rules): a parser, None (no robots.txt: allow all), or a
# string saying why robots.txt could not be read (refuse the origin).
_ROBOTS: dict[str, tuple[float, RobotFileParser | str | None]] = {}
_ROBOTS_TTL = 3600
_ROBOTS_RETRY = 300  # a robots.txt that could not be read is retried sooner
_ROBOTS_MAX_BYTES = 512 * 1024  # RFC 9309 asks crawlers to parse at least 500 KiB


def _robots_rules(origin: str, user_agent: str):
    cached = _ROBOTS.get(origin)
    if cached and time.monotonic() - cached[0] < (_ROBOTS_RETRY if isinstance(cached[1], str) else _ROBOTS_TTL):
        return cached[1]
    httpx = _ensure_httpx()
    timeout = httpx.Timeout(float(_get_config("ingest.url_timeout_sec", 30)),
                            read=float(_get_config("ingest.url_read_timeout_sec", 300)))
    try:
        with httpx.stream("GET", origin + "/robots.txt", follow_redirects=True, timeout=timeout,
                          headers={"User-Agent": user_agent}) as resp:
            status = resp.status_code
            body = bytearray()
            if 200 <= status < 300:
                for chunk in resp.iter_bytes():
                    body += chunk
                    if len(body) >= _ROBOTS_MAX_BYTES:
                        break
    except Exception as e:
        rules = f"robots.txt at {origin} unreachable ({type(e).__name__})"
    else:
        if 200 <= status < 300:
            rules = RobotFileParser(origin + "/robots.txt")
            rules.parse(bytes(body[:_ROBOTS_MAX_BYTES]).decode("utf-8", "replace").splitlines())
        elif 400 <= status < 500:
            rules = None  # no robots.txt (404, 403, ...): everything allowed
        else:
            rules = f"robots.txt at {origin} returned HTTP {status}"
    _ROBOTS[origin] = (time.monotonic(), rules)
    return rules


def robots_refusal(url: str) -> str:
    """Why robots.txt keeps Imprint from fetching ``url``; empty when allowed,
    when ingest.url_respect_robots is off, or for non-http(s) URLs."""
    if not _is_url(url) or not _get_config("ingest.url_respect_robots", True):
        return ""
    p = urlparse(url)
    origin = f"{p.scheme}://{p.netloc}"
    user_agent = str(_get_config("ingest.url_user_agent", "imprint/1.0"))
    rules = _robots_rules(origin, user_agent)
    if rules is None:
        return ""
    if isinstance(rules, str):
        # RFC 9309: a server or network error means assume complete disallow.
        return f"{rules}; refusing the site until it can be read (ingest.url_respect_robots)"
    if rules.can_fetch(user_agent, url):
        return ""
    return f"robots.txt at {origin} disallows {url} for {user_agent!r} (ingest.url_respect_robots)"


def _check_robots(url: str) -> None:
    reason = robots_refusal(url)
    if reason:
        raise RobotsDisallowed(reason)


def _read_capped(resp, url: str) -> bytes:
    """The response body, refused past ingest.max_doc_size_mb (Content-Length first)."""
    import io
    limit = max_doc_bytes()
    over = f"{url} exceeds ingest.max_doc_size_mb ({limit // 1048576} MiB); previous content retained"
    if int(resp.headers.get("content-length") or 0) > limit:
        raise DocumentTooLarge(over)
    buf = io.BytesIO()
    for chunk in resp.iter_bytes(chunk_size=1_048_576):
        buf.write(chunk)
        if buf.tell() > limit:
            raise DocumentTooLarge(over)
    return buf.getvalue()


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
    _check_robots(url)

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
        if str(resp.url) != url:
            _check_robots(str(resp.url))  # a redirect may land on another origin
        expected = int(resp.headers.get("content-length") or 0)
        data = _read_capped(resp, url)
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
    try:
        if robots_refusal(url):
            return []
    except Exception:
        return []
    allowed = lambda links: [link for link in links if not robots_refusal(link)]

    if _get_config("ingest.use_obscura", False):
        try:
            from . import obscura as _obs
            return allowed(_obs.extract_links(url, domain=domain))
        except Exception:
            pass  # fall through to httpx+bs4 path

    # httpx fetch + BS4 link extraction
    try:
        httpx = _ensure_httpx()
        connect_timeout = float(_get_config("ingest.url_timeout_sec", 30))
        read_timeout = float(_get_config("ingest.url_read_timeout_sec", 300))
        user_agent = str(_get_config("ingest.url_user_agent", "imprint/1.0"))
        with httpx.stream(
            "GET", url,
            follow_redirects=True,
            timeout=httpx.Timeout(connect_timeout, read=read_timeout),
            headers={"User-Agent": user_agent},
        ) as resp:
            data = _read_capped(resp, url)
            final_url = str(resp.url)
        return allowed(extract_links_bs4(data, final_url))
    except Exception:
        return []


def head_check(url: str) -> dict:
    """HEAD request — returns {etag, last_modified, status} for refresh
    dedupe. Empty dict on failure."""
    httpx = _ensure_httpx()
    timeout = float(_get_config("ingest.url_timeout_sec", 30))
    user_agent = str(_get_config("ingest.url_user_agent", "imprint/1.0"))
    try:
        if robots_refusal(url):
            return {}  # the fetch that follows reports why
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
