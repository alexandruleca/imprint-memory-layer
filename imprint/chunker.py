"""Hybrid chunking.

Dispatches by file type:
  - Code files → Chonkie `CodeChunker` (tree-sitter boundaries, language-aware).
    Falls back to the legacy regex splitter if tree-sitter can't parse.
  - Prose files → Chonkie `SemanticChunker` (splits on topic change via a
    small embedding model, sliding-overlap friendly).
  - Unknown → size-based with paragraph boundaries.

Chunkers are cached per language so init cost (model load for semantic) pays
once per process.
"""

from __future__ import annotations

import os
import re

import numpy as np

from . import config

# ── Language map for CodeChunker ───────────────────────────────
# Chonkie CodeChunker takes a language name; map our file extensions.
_CODE_LANG = {
    ".py": "python",
    ".ts": "typescript", ".tsx": "tsx",
    ".js": "javascript", ".jsx": "jsx", ".mjs": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "c_sharp",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp",
    ".c": "c", ".h": "c",
    ".hpp": "cpp",
    ".swift": "swift",
    ".sh": "bash", ".bash": "bash", ".zsh": "bash",
    ".sql": "sql",
    ".vue": "vue",
    ".svelte": "svelte",
    ".scala": "scala",
    ".lua": "lua",
}

_PROSE_EXTS = {".md", ".txt", ".rst", ".mdx"}

MIN_SIZE = 100
HARD_MAX = config.CHUNK_HARD_MAX
TARGET_SIZE_CODE = config.CHUNK_SIZE_CODE
TARGET_SIZE_PROSE = config.CHUNK_SIZE_PROSE
# Kept for legacy callers (e.g. _merge_small fallback).
TARGET_SIZE = config.CHUNK_SIZE_CHARS
OVERLAP = config.CHUNK_OVERLAP
SEMANTIC_THRESHOLD = config.CHUNK_SEMANTIC_THRESHOLD

_code_chunker_cache: dict[str, object] = {}
_semantic_chunker = None


def _get_code_chunker(language: str):
    if language in _code_chunker_cache:
        return _code_chunker_cache[language]
    try:
        from chonkie import CodeChunker
        # Small chunk_size = CodeChunker emits one top-level definition
        # per chunk for typical methods; bundles tight classes whole when
        # they fit; sub-AST splits only for oversized methods.
        cc = CodeChunker(chunk_size=TARGET_SIZE_CODE, language=language)
        _code_chunker_cache[language] = cc
        return cc
    except Exception:
        _code_chunker_cache[language] = None
        return None


def _safe_similarity(self, u: np.ndarray, v: np.ndarray) -> np.float32:
    """Cosine similarity that handles zero-norm vectors instead of dividing by zero."""
    denom = np.linalg.norm(u) * np.linalg.norm(v)
    if denom == 0:
        return np.float32(0.0)
    return np.divide(np.dot(u, v), denom, dtype=np.float32)


def _get_semantic_chunker():
    global _semantic_chunker
    if _semantic_chunker is not None:
        return _semantic_chunker
    try:
        from chonkie import SemanticChunker
        # Lower threshold = more aggressive topic-shift splitting. Smaller
        # min_characters_per_sentence catches short transitional sentences
        # that often mark topic boundaries.
        _semantic_chunker = SemanticChunker(
            threshold=SEMANTIC_THRESHOLD,
            chunk_size=TARGET_SIZE_PROSE,
            min_characters_per_sentence=16,
            similarity_window=3,
            skip_window=1,
        )
        # Patch: chonkie's Model2VecEmbeddings.similarity divides by zero
        # when a sentence embeds to a zero vector (empty/whitespace input).
        # Replace with a safe version that returns 0.0 for zero-norm vectors.
        if hasattr(_semantic_chunker, 'embedding_model'):
            _semantic_chunker.embedding_model.similarity = (
                _safe_similarity.__get__(_semantic_chunker.embedding_model)
            )
        return _semantic_chunker
    except Exception:
        _semantic_chunker = None
        return None


# ── Public API ─────────────────────────────────────────────────
def chunk_file(content: str, rel_path: str) -> list[tuple[str, int]]:
    """Split file into chunks. Returns list of (chunk_text, chunk_index).

    Each chunk is prefixed with `[rel_path]\\n` so embedding sees context.
    """
    content = content.strip()
    if not content or len(content) < MIN_SIZE:
        return []

    ext = os.path.splitext(rel_path)[1].lower()

    raw: list[str] = []

    is_code = ext in _CODE_LANG
    if is_code:
        raw = _chunk_code(content, _CODE_LANG[ext])
    elif ext in _PROSE_EXTS:
        raw = _chunk_prose(content)
    else:
        raw = []

    # Fallbacks — if chonkie failed or ext unknown.
    if not raw:
        raw = _split_by_size(content)

    # Overlap only for prose: code already has clean AST boundaries; raw
    # char-slice overlap on code lands mid-statement / mid-string and adds
    # embedding noise. Prose benefits from sentence-aware tail context.
    if not is_code:
        raw = _apply_overlap(raw)

    raw = [_enforce_hard_max(c) for c in raw if len(c) >= MIN_SIZE]

    return [(f"[{rel_path}]\n{c}", i) for i, c in enumerate(raw)]


def chunk_prose(content: str) -> list[str]:
    """Chunk arbitrary prose (no rel_path wrap). For conversation exchanges
    and auto-extracted memories where the source isn't a file."""
    content = content.strip()
    if not content or len(content) < MIN_SIZE:
        return []
    raw = _chunk_prose(content) or _split_by_size(content)
    raw = _apply_overlap(raw)
    return [_enforce_hard_max(c) for c in raw if len(c) >= MIN_SIZE]


# ── Internals ──────────────────────────────────────────────────
def _chunk_code(content: str, language: str) -> list[str]:
    cc = _get_code_chunker(language)
    if cc is None:
        return _legacy_split_by_boundaries(content)
    try:
        chunks = cc.chunk(content)
        return [c.text for c in chunks if c.text and len(c.text.strip()) >= MIN_SIZE]
    except Exception:
        return _legacy_split_by_boundaries(content)


def _chunk_prose(content: str) -> list[str]:
    sc = _get_semantic_chunker()
    if sc is None:
        return _split_by_headers(content)
    try:
        chunks = sc.chunk(content)
        return [c.text for c in chunks if c.text and len(c.text.strip()) >= MIN_SIZE]
    except Exception:
        return _split_by_headers(content)


def _apply_overlap(chunks: list[str]) -> list[str]:
    """Tail-of-previous / head-of-next sliding overlap.

    Preserves boundary context so retrieval doesn't miss signal that sits
    right at a chunk split. Tail is trimmed to the nearest sentence /
    paragraph boundary inside the last OVERLAP chars so we don't paste a
    half-word onto the next chunk's head.
    """
    if OVERLAP <= 0 or len(chunks) < 2:
        return chunks
    out = [chunks[0]]
    for i in range(1, len(chunks)):
        prev_tail = _clean_tail(chunks[i - 1], OVERLAP)
        out.append(prev_tail + "\n" + chunks[i])
    return out


def _clean_tail(text: str, max_len: int) -> str:
    """Take the last <=max_len chars of text starting at a clean boundary
    (paragraph > sentence > line > word). Avoids mid-word overlap noise."""
    if len(text) <= max_len:
        return text
    tail = text[-max_len:]
    # Paragraph boundary
    pos = tail.find("\n\n")
    if 0 <= pos < max_len // 2:
        return tail[pos + 2:]
    # Sentence boundary
    for marker in [". ", ".\n", "? ", "?\n", "! ", "!\n"]:
        pos = tail.find(marker)
        if 0 <= pos < max_len // 2:
            return tail[pos + len(marker):]
    # Line boundary
    pos = tail.find("\n")
    if 0 <= pos < max_len // 2:
        return tail[pos + 1:]
    # Word boundary
    pos = tail.find(" ")
    if 0 <= pos < max_len // 2:
        return tail[pos + 1:]
    return tail


def _enforce_hard_max(text: str) -> str:
    if len(text) <= HARD_MAX:
        return text
    cut = text.rfind("\n", HARD_MAX - 500, HARD_MAX)
    if cut > 0:
        return text[:cut]
    return text[:HARD_MAX]


# ── Legacy fallbacks ───────────────────────────────────────────
_LEGACY_BOUNDARIES = [
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


def _legacy_split_by_boundaries(content: str) -> list[str]:
    boundaries = set()
    for pat in _LEGACY_BOUNDARIES:
        for m in pat.finditer(content):
            boundaries.add(m.start())
    if not boundaries:
        return _split_by_size(content)
    positions = sorted(boundaries)
    chunks = []
    if positions[0] > MIN_SIZE:
        chunks.append(content[:positions[0]].strip())
    for i, start in enumerate(positions):
        end = positions[i + 1] if i + 1 < len(positions) else len(content)
        piece = content[start:end].strip()
        if piece:
            chunks.append(piece)
    # Merge small chunks up to TARGET_SIZE
    return _merge_small(chunks)


def _merge_small(chunks: list[str]) -> list[str]:
    out = []
    cur = ""
    for c in chunks:
        if cur and len(cur) + len(c) + 2 <= TARGET_SIZE:
            cur = cur + "\n\n" + c
        elif not cur and len(c) <= TARGET_SIZE:
            cur = c
        else:
            if cur:
                out.append(cur)
            cur = c
    if cur:
        out.append(cur)
    return out


def _split_by_headers(content: str) -> list[str]:
    sections = re.split(r"(?=^##?\s+)", content, flags=re.MULTILINE)
    return [s.strip() for s in sections if s.strip()]


def _split_by_size(content: str) -> list[str]:
    chunks = []
    start = 0
    while start < len(content):
        end = min(start + TARGET_SIZE, len(content))
        if end < len(content):
            nl2 = content.rfind("\n\n", start, end)
            if nl2 > start + TARGET_SIZE // 2:
                end = nl2
            else:
                nl1 = content.rfind("\n", start, end)
                if nl1 > start + TARGET_SIZE // 2:
                    end = nl1
        piece = content[start:end].strip()
        if piece:
            chunks.append(piece)
        start = end
    return chunks
