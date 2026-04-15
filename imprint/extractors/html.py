"""HTML extractor.

Primary: trafilatura — readability-style extraction (strips nav, ads,
footers). Produces clean article text with title + publish date metadata.

Fallback: beautifulsoup4 get_text() when trafilatura is unavailable.

Last-resort fallback: raw byte decode with tag stripping (no deps). We'd
rather return rough text than crash the ingest.
"""

from __future__ import annotations

import os
import re

from . import ExtractedDoc, ExtractorUnavailable, ExtractionError, register_ext, register_mime


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")
_NL_RE = re.compile(r"\n{3,}")


def _stripping_fallback(raw: str) -> str:
    # Very crude: drop script/style blocks, then tags, collapse whitespace.
    raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    text = _TAG_RE.sub(" ", raw)
    text = _WS_RE.sub(" ", text)
    text = _NL_RE.sub("\n\n", text)
    return text.strip()


def _extract_with_trafilatura(raw_bytes: bytes, source_url: str) -> tuple[str, dict] | None:
    try:
        import trafilatura  # type: ignore
    except ImportError:
        return None

    try:
        text = trafilatura.extract(
            raw_bytes,
            url=source_url or None,
            include_comments=False,
            include_tables=True,
            no_fallback=False,
        )
        if not text:
            return None
        meta: dict = {}
        try:
            md = trafilatura.extract_metadata(raw_bytes, default_url=source_url or None)
            if md:
                for attr in ("title", "author", "date", "sitename", "url"):
                    v = getattr(md, attr, None)
                    if v:
                        meta[attr] = str(v)
        except Exception:
            pass
        return text, meta
    except Exception:
        return None


def _extract_with_bs4(raw: str) -> tuple[str, dict] | None:
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError:
        return None
    soup = BeautifulSoup(raw, "html.parser")
    for bad in soup(["script", "style", "noscript"]):
        bad.decompose()
    title = soup.title.get_text().strip() if soup.title else ""
    text = soup.get_text(separator="\n")
    text = _NL_RE.sub("\n\n", text).strip()
    return text, ({"title": title} if title else {})


def _extract_bytes(data: bytes, source_url: str = "") -> ExtractedDoc:
    # Try trafilatura → bs4 → regex strip. None should raise Unavailable
    # because raw-regex fallback needs no deps.
    result = _extract_with_trafilatura(data, source_url)
    if result is None:
        raw = data.decode("utf-8", errors="ignore")
        result = _extract_with_bs4(raw)
    if result is None:
        raw = data.decode("utf-8", errors="ignore")
        result = (_stripping_fallback(raw), {})

    text, meta = result
    if not text:
        raise ExtractionError("html parse produced empty output")
    return ExtractedDoc(
        text=text.strip(),
        mime="text/html",
        metadata=meta,
        chunk_mode="prose",
    )


def extract(path: str) -> ExtractedDoc:
    with open(path, "rb") as f:
        doc = _extract_bytes(f.read())
    doc.metadata.setdefault("filename", os.path.basename(path))
    return doc


register_ext(".html", extract)
register_ext(".htm", extract)
register_mime("text/html", _extract_bytes)
register_mime("application/xhtml+xml", _extract_bytes)
