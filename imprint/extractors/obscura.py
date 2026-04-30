"""Obscura headless browser extractor (opt-in, Apache 2.0).

Wraps the ``obscura`` CLI binary (https://github.com/h4ckf0r0day/obscura) to
provide JS-rendered HTML extraction and same-domain link discovery.

Enable:
    # Install binary: download from GitHub releases and put in PATH
    imprint config set ingest.use_obscura true

``fetch(url)`` → full page text after JS execution (vastly better than static
trafilatura for JS-heavy SPAs and docs sites).

``extract_links(url, domain)`` → list of same-domain absolute URLs discovered
on the page (for BFS crawling in cli_ingest_url.py).

Both raise ExtractorUnavailable when the binary is not in PATH.
"""

from __future__ import annotations

import shutil
import subprocess
from urllib.parse import urlparse, urljoin

from . import ExtractedDoc, ExtractorUnavailable, ExtractionError


def _get_config(key: str, default):
    try:
        from ..config_schema import resolve
        val, _ = resolve(key)
        return val
    except Exception:
        return default


def _obscura_bin() -> str:
    binary = shutil.which("obscura")
    if not binary:
        raise ExtractorUnavailable(
            "obscura not in PATH — download from https://github.com/h4ckf0r0day/obscura/releases"
        )
    return binary


def fetch(url: str) -> list[ExtractedDoc]:
    """Fetch URL with JS execution, return extracted text as ExtractedDoc."""
    binary = _obscura_bin()
    timeout = int(_get_config("ingest.url_timeout_sec", 30))

    try:
        result = subprocess.run(
            [binary, "fetch", url, "--dump", "text"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise ExtractionError(f"obscura timed out fetching {url}") from e
    except OSError as e:
        raise ExtractionError(f"obscura subprocess error: {e}") from e

    if result.returncode != 0:
        err = result.stderr.strip()[:300] if result.stderr else "(no stderr)"
        raise ExtractionError(f"obscura exited {result.returncode} for {url}: {err}")

    text = result.stdout.strip()
    if not text:
        raise ExtractionError(f"obscura returned empty text for {url}")

    return [ExtractedDoc(
        text=text,
        mime="text/html",
        metadata={"source_url": url},
        chunk_mode="prose",
    )]


def extract_links(url: str, domain: str | None = None) -> list[str]:
    """Return same-domain absolute URLs found on the page.

    Uses ``obscura fetch <url> --dump links``. Output format per line is
    either ``text<TAB>url`` or bare ``url``; we handle both.
    Returns empty list (never raises) — caller treats missing links as no-op.
    """
    try:
        binary = _obscura_bin()
    except ExtractorUnavailable:
        return []

    timeout = int(_get_config("ingest.url_timeout_sec", 30))
    target_domain = domain or urlparse(url).netloc

    try:
        result = subprocess.run(
            [binary, "fetch", url, "--dump", "links"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception:
        return []

    if result.returncode != 0:
        return []

    links: list[str] = []
    seen: set[str] = set()

    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        # format: "link text\tURL" or bare URL
        parts = line.split("\t")
        candidate = parts[-1].strip() if parts else ""
        if not candidate:
            continue

        # resolve relative URLs against the page URL
        if not candidate.startswith("http"):
            candidate = urljoin(url, candidate)

        parsed = urlparse(candidate)
        if parsed.scheme not in ("http", "https"):
            continue
        if parsed.netloc != target_domain:
            continue

        # strip fragment
        clean = candidate.split("#")[0].rstrip("/") or candidate
        if clean and clean not in seen:
            seen.add(clean)
            links.append(clean)

    return links
