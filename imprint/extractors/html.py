"""HTML extractor.

Primary: trafilatura — readability-style extraction (strips nav, ads,
footers). Produces clean article text with title + publish date metadata.

Fallback: beautifulsoup4 get_text() when trafilatura is unavailable.

Last-resort fallback: raw byte decode with tag stripping (no deps). We'd
rather return rough text than crash the ingest.

Large HTML files: extracted text is split into sections (by headings or
paragraph boundaries) and returned as multiple ExtractedDocs so we don't
blow up the chunker/embedder with a single huge document.
"""

from __future__ import annotations

import os
import re

from . import ExtractedDoc, ExtractorResult, ExtractionError, register_ext, register_mime


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


# ── Large-text splitter ───────────────────────────────────────
# When extracted text exceeds this threshold, split into multiple docs
# so the chunker processes manageable pieces.
_SPLIT_THRESHOLD = 200_000  # ~200KB of text

_HEADING_RE = re.compile(r"(?m)^(?=#{1,3} )")  # markdown-style headings
_PARA_RE = re.compile(r"\n{2,}")                 # paragraph breaks


def _split_large_text(text: str, meta: dict) -> list[ExtractedDoc]:
    """Split large extracted text into sections, each under _SPLIT_THRESHOLD."""
    # Try heading splits first, fall back to paragraph splits
    parts = _HEADING_RE.split(text)
    if len(parts) < 2:
        parts = _PARA_RE.split(text)

    docs: list[ExtractedDoc] = []
    current: list[str] = []
    current_len = 0
    part_idx = 0

    for part in parts:
        part = part.strip()
        if not part:
            continue
        if current_len + len(part) > _SPLIT_THRESHOLD and current:
            part_idx += 1
            docs.append(ExtractedDoc(
                text="\n\n".join(current),
                mime="text/html",
                metadata={**meta, "split_part": part_idx},
                chunk_mode="prose",
            ))
            current = []
            current_len = 0
        current.append(part)
        current_len += len(part)

    if current:
        part_idx += 1
        docs.append(ExtractedDoc(
            text="\n\n".join(current),
            mime="text/html",
            metadata={**meta, "split_part": part_idx},
            chunk_mode="prose",
        ))

    return docs if docs else [ExtractedDoc(
        text=text, mime="text/html", metadata=meta, chunk_mode="prose",
    )]


def extract(path: str) -> ExtractorResult:
    with open(path, "rb") as f:
        data = f.read()
    doc = _extract_bytes(data)
    del data  # free raw bytes early
    doc.metadata.setdefault("filename", os.path.basename(path))

    if len(doc.text) > _SPLIT_THRESHOLD:
        return _split_large_text(doc.text, doc.metadata)
    return doc


register_ext(".html", extract)
register_ext(".htm", extract)
register_mime("text/html", _extract_bytes)
register_mime("application/xhtml+xml", _extract_bytes)
