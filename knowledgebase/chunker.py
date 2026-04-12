"""Smart file chunking for indexing.

Dynamic chunk sizing — keeps logical units whole instead of hard-cutting.
Small functions get merged together. Large ones get their own chunk.
Soft limit allows overflow to keep complete thoughts.
"""

import os
import re

BOUNDARY_PATTERNS = [
    re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+\w+", re.MULTILINE),
    re.compile(r"^(?:export\s+)?class\s+\w+", re.MULTILINE),
    re.compile(r"^(?:export\s+)?interface\s+\w+", re.MULTILINE),
    re.compile(r"^(?:export\s+)?type\s+\w+\s*=", re.MULTILINE),
    re.compile(r"^(?:export\s+)?const\s+\w+\s*[:=]", re.MULTILINE),
    re.compile(r"^def\s+\w+", re.MULTILINE),
    re.compile(r"^class\s+\w+", re.MULTILINE),
    re.compile(r"^func\s+", re.MULTILINE),
    re.compile(r"^##?\s+", re.MULTILINE),
]

# Soft limit: target size, can be exceeded to keep a logical unit whole
TARGET_SIZE = 1500       # preferred chunk size (chars)
SOFT_MAX = 4000          # allowed overflow — keeps complete functions/classes
HARD_MAX = 6000          # absolute max — model context is 2048 tokens (~6000 chars)
MIN_SIZE = 100           # below this, merge with next chunk
OVERLAP = 100            # chars carried over between size-based chunks


def chunk_file(content: str, rel_path: str) -> list[tuple[str, int]]:
    """Split file into chunks. Returns list of (chunk_text, chunk_index)."""
    content = content.strip()
    if not content or len(content) < MIN_SIZE:
        return []

    ext = os.path.splitext(rel_path)[1].lower()

    if ext in (".md", ".txt"):
        raw = _split_by_headers(content)
    else:
        raw = _split_by_boundaries(content)

    if not raw:
        raw = _split_by_size(content)

    # Merge small chunks and enforce soft limit
    merged = _merge_small_chunks(raw)

    # Prefix each with file path and assign index
    return [(f"[{rel_path}]\n{c}", i) for i, c in enumerate(merged)]


def _split_by_headers(content: str) -> list[str]:
    """Split markdown by headers."""
    sections = re.split(r"(?=^##?\s+)", content, flags=re.MULTILINE)
    return [s.strip() for s in sections if s.strip()]


def _split_by_boundaries(content: str) -> list[str]:
    """Split code at logical boundaries (functions, classes, etc)."""
    boundaries = set()
    for pattern in BOUNDARY_PATTERNS:
        for match in pattern.finditer(content):
            boundaries.add(match.start())

    if not boundaries:
        return []

    positions = sorted(boundaries)
    # Include content before first boundary if substantial
    chunks = []
    if positions[0] > MIN_SIZE:
        chunks.append(content[:positions[0]].strip())

    for i, start in enumerate(positions):
        end = positions[i + 1] if i + 1 < len(positions) else len(content)
        chunk = content[start:end].strip()
        if chunk:
            chunks.append(chunk)

    return chunks


def _split_by_size(content: str) -> list[str]:
    """Fallback: paragraph-aware size-based splitting with overlap."""
    chunks = []
    start = 0

    while start < len(content):
        end = min(start + TARGET_SIZE, len(content))

        if end < len(content):
            # Try paragraph boundary first
            nl2 = content.rfind("\n\n", start, end)
            if nl2 > start + TARGET_SIZE // 2:
                end = nl2
            else:
                nl1 = content.rfind("\n", start, end)
                if nl1 > start + TARGET_SIZE // 2:
                    end = nl1

        chunk = content[start:end].strip()
        if chunk:
            chunks.append(chunk)

        start = end - OVERLAP if end < len(content) else end

    return chunks


def _merge_small_chunks(chunks: list[str]) -> list[str]:
    """Merge small chunks together up to SOFT_MAX. Keeps large chunks as-is."""
    if not chunks:
        return []

    merged = []
    current = ""

    for chunk in chunks:
        # If adding this chunk stays under soft limit, merge
        if current and len(current) + len(chunk) + 2 <= SOFT_MAX:
            current = current + "\n\n" + chunk
        elif not current and len(chunk) <= SOFT_MAX:
            current = chunk
        else:
            # Current buffer is full — flush it
            if current:
                merged.append(_enforce_hard_max(current))
            # Start new buffer with this chunk
            if len(chunk) > SOFT_MAX:
                # This chunk alone exceeds soft limit — store as-is (up to hard max)
                merged.append(_enforce_hard_max(chunk))
                current = ""
            else:
                current = chunk

    if current:
        merged.append(_enforce_hard_max(current))

    # Filter out anything too small to be useful
    return [c for c in merged if len(c) >= MIN_SIZE]


def _enforce_hard_max(text: str) -> str:
    """Cut at hard max, trying to break at a line boundary."""
    if len(text) <= HARD_MAX:
        return text
    # Try to cut at a newline
    cut = text.rfind("\n", HARD_MAX - 500, HARD_MAX)
    if cut > 0:
        return text[:cut]
    return text[:HARD_MAX]
