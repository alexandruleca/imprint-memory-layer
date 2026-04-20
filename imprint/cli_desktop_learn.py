"""Auto-ingest Claude Desktop / ChatGPT Desktop conversation exports.

Neither consumer desktop app stores full conversation content in a stable
on-disk format — both fetch from the vendor's server on demand. The
supported path is the built-in **export** (claude.ai → Settings → Privacy →
Export data; chat.openai.com → Settings → Data Controls → Export). Each
produces a zip with ``conversations.json`` that Imprint's existing JSON
extractor (imprint/extractors/json_doc.py) already knows how to split into
per-conversation markdown docs.

This module scans the user's Downloads folder(s) on every run, finds new
export zips, indexes them, and records their SHA-256 so subsequent runs
are a no-op. WSL-aware: probes both the Linux ``~/Downloads`` and the
Windows-side ``/mnt/c/Users/<user>/Downloads`` when available.

Usage:
    python -m imprint.cli_desktop_learn            # one-shot scan
    python -m imprint.cli_desktop_learn --watch    # poll every 30s
    python -m imprint.cli_desktop_learn --path <dir>  # extra scan roots

Re-run safe: indexed zips are tracked in ``data/desktop_exports.json``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from imprint import config, extractors, tagger, vectorstore as vs
from imprint.chunker import chunk_file
from imprint.config_schema import resolve
from imprint._cli_signals import install as _install_signals

_install_signals()

C_RESET = "\033[0m"
C_CYAN = "\033[0;36m"
C_GREEN = "\033[0;32m"
C_YELLOW = "\033[1;33m"
C_DIM = "\033[2m"

# Claude export zips: data-YYYY-MM-DD-HH-MM-SS.zip (from Anthropic's
# "Export data" feature). ChatGPT export zips:
# <uuid>-<timestamp>.zip (no fixed prefix) but always contain
# ``conversations.json``. We detect by examining zip contents rather than
# the filename so renamed / moved exports still work.
_CANDIDATE_FILENAME_RE = re.compile(
    r"""(?ix)
    ^(
        data-\d{4}-\d{2}-\d{2}               # Anthropic data-YYYY-MM-DD-*
        | claude[-_].*export
        | chatgpt[-_].*export
        | openai[-_].*export
        | [0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}  # ChatGPT uuid prefix
    ).*\.zip$
    """
)

# Max zip size we'll touch (bytes). Exports above this are almost certainly
# not conversation dumps and processing them would blow through memory.
_MAX_ZIP_SIZE = 500 * 1024 * 1024  # 500 MB

_TRACKER = "desktop_exports.json"


def _downloads_roots() -> list[Path]:
    """Candidate folders to scan for exports. WSL-aware."""
    roots: list[Path] = []
    home = Path.home()
    for candidate in (home / "Downloads", home / "downloads"):
        if candidate.is_dir():
            roots.append(candidate)

    # WSL: probe the Windows-side Downloads. We look for the single
    # non-system user dir under /mnt/c/Users, matching the Go side's
    # resolveFromUsersDir().
    if Path("/proc/version").is_file():
        try:
            if "microsoft" in Path("/proc/version").read_text().lower() or \
               "wsl" in Path("/proc/version").read_text().lower():
                users = Path("/mnt/c/Users")
                if users.is_dir():
                    skip = {"Public", "Default", "Default User", "All Users",
                            "WDAGUtilityAccount"}
                    for d in users.iterdir():
                        if d.name in skip or d.name.startswith("."):
                            continue
                        if not d.is_dir():
                            continue
                        dl = d / "Downloads"
                        if dl.is_dir() and dl not in roots:
                            roots.append(dl)
        except OSError:
            pass
    return roots


def _load_tracker() -> dict:
    path = config.get_data_dir() / _TRACKER
    if not path.exists():
        return {"seen": {}}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {"seen": {}}


def _save_tracker(data: dict) -> None:
    path = config.get_data_dir() / _TRACKER
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def _sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _zip_has_conversations_json(path: Path) -> str | None:
    """Return the source label ('claude' | 'chatgpt') if the zip looks like a
    supported export, else None. Decision based on the zip contents, not
    the filename."""
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
    except (zipfile.BadZipFile, OSError):
        return None

    if not any(n.endswith("conversations.json") for n in names):
        return None

    # Heuristic: Anthropic bundles include `projects.json` and `users.json`;
    # ChatGPT bundles include `message_feedback.json` or `shared_conversations.json`.
    joined = " ".join(sorted(names))
    if "projects.json" in joined or "users.json" in joined:
        return "claude"
    if "message_feedback.json" in joined or "shared_conversations.json" in joined or "user.json" in joined:
        return "chatgpt"
    # Unknown origin but contains conversations.json — index anyway.
    return "unknown"


def _iter_candidate_zips(roots: list[Path]):
    """Yield zip paths that look like plausible exports, filename- or
    content-based."""
    for root in roots:
        try:
            for entry in root.iterdir():
                if not entry.is_file() or entry.suffix.lower() != ".zip":
                    continue
                if entry.stat().st_size > _MAX_ZIP_SIZE:
                    continue
                if _CANDIDATE_FILENAME_RE.match(entry.name):
                    yield entry
                    continue
                # Fallback: sniff contents for conversations.json even if the
                # user renamed the zip.
                if _zip_has_conversations_json(entry):
                    yield entry
        except (OSError, PermissionError):
            continue


def _ingest_json_file(file_path: Path, project: str) -> int:
    """Run the existing JSON extractor + chunker pipeline on one file.
    Returns the number of chunks inserted."""
    try:
        doc_list = extractors.dispatch_by_ext(str(file_path))
    except extractors.ExtractorUnavailable as e:
        print(f"  {C_YELLOW}Extractor unavailable:{C_RESET} {e}")
        return 0
    except extractors.ExtractionError as e:
        print(f"  {C_YELLOW}Extraction failed:{C_RESET} {e}")
        return 0

    enable_llm = resolve("tagger.llm")[0]
    enable_zero_shot = resolve("tagger.zero_shot")[0] and not enable_llm

    records: list[dict] = []
    now = time.time()
    for extracted in doc_list:
        text = extracted.text or ""
        if len(text.strip()) < 40:
            continue
        meta = dict(extracted.metadata or {})
        rel = file_path.name
        chunks = chunk_file(text, rel, chunk_mode=extracted.chunk_mode or "prose")
        if not chunks:
            continue

        # Source key encodes the conversation so re-imports replace cleanly.
        conv_title = meta.get("conversation_title") or meta.get("item_title") or ""
        source_key = f"{project}/{conv_title}" if conv_title else f"{project}/{rel}"

        for i, (chunk_text, chunk_idx) in enumerate(chunks):
            prev_text = chunks[i - 1][0][-200:] if i > 0 else ""
            next_text = chunks[i + 1][0][:200] if i < len(chunks) - 1 else ""
            neighbor_ctx = prev_text + ("\n...\n" if prev_text and next_text else "") + next_text
            tags = tagger.build_payload_tags(
                chunk_text, rel_path=rel,
                llm=None, zero_shot=enable_zero_shot,
                neighbor_context=neighbor_ctx, project_hint=project,
            )
            llm_type = tags.pop("_llm_type", "")
            mem_type = llm_type or "conversation"
            records.append({
                "content": chunk_text,
                "project": project,
                "type": mem_type,
                "tags": tags,
                "source": source_key,
                "source_type": "desktop-export",
                "doc_metadata": meta,
                "chunk_index": chunk_idx,
                "source_mtime": now,
            })

    if not records:
        return 0
    inserted, _ = vs.store_batch(records)
    return inserted


def _process_zip(zip_path: Path, origin: str) -> int:
    """Extract + index one export zip. Returns chunks inserted."""
    project = {
        "claude": "claude-desktop-convos",
        "chatgpt": "chatgpt-desktop-convos",
    }.get(origin, "desktop-convos")

    total = 0
    with tempfile.TemporaryDirectory(prefix="imprint-desktop-") as tmp:
        tmp_dir = Path(tmp)
        try:
            with zipfile.ZipFile(zip_path) as z:
                z.extractall(tmp_dir)
        except zipfile.BadZipFile:
            print(f"  {C_YELLOW}Not a valid zip, skipping{C_RESET}")
            return 0

        for json_path in tmp_dir.rglob("conversations.json"):
            print(f"  {C_DIM}→ {json_path.relative_to(tmp_dir)}{C_RESET}")
            total += _ingest_json_file(json_path, project)
    return total


def _scan_once(extra_paths: list[str], *, verbose: bool = True) -> dict:
    """One pass over all candidate roots. Returns a structured result that
    the CLI and API surfaces both render.

    Shape:

        {
          "roots": [str, ...],
          "scanned": int,
          "skipped_seen": int,
          "indexed_zips": int,
          "inserted_chunks": int,
          "indexed": [  # only newly-indexed entries this scan
              {"path": str, "origin": str, "chunks": int, "indexed_at": int},
              ...
          ],
        }
    """
    roots = _downloads_roots()
    for p in extra_paths:
        path = Path(p).expanduser()
        if path.is_dir() and path not in roots:
            roots.append(path)

    if verbose:
        print()
        print(f"  {C_CYAN}Scanning{C_RESET} {len(roots)} folder(s) for conversation exports")
        for r in roots:
            print(f"    {C_DIM}- {r}{C_RESET}")

    tracker = _load_tracker()
    seen: dict = tracker.get("seen", {})
    result: dict = {
        "roots": [str(r) for r in roots],
        "scanned": 0,
        "skipped_seen": 0,
        "indexed_zips": 0,
        "inserted_chunks": 0,
        "indexed": [],
    }

    for zip_path in _iter_candidate_zips(roots):
        result["scanned"] += 1
        digest = _sha256(zip_path)
        if digest in seen:
            result["skipped_seen"] += 1
            continue
        origin = _zip_has_conversations_json(zip_path)
        if not origin:
            continue
        if verbose:
            print()
            print(f"  {C_CYAN}Indexing{C_RESET} {zip_path.name} ({origin})")
        inserted = _process_zip(zip_path, origin)
        entry = {
            "path": str(zip_path),
            "origin": origin,
            "indexed_at": int(time.time()),
            "chunks": inserted,
        }
        seen[digest] = entry
        result["indexed"].append(entry)
        result["indexed_zips"] += 1
        result["inserted_chunks"] += inserted
        if verbose:
            print(f"  {C_GREEN}+ {inserted} chunks{C_RESET}")

    tracker["seen"] = seen
    _save_tracker(tracker)
    return result


def scan_once_api(extra_paths: list[str] | None = None) -> dict:
    """API-friendly wrapper. Silent (no terminal output) and returns the
    structured scan result straight through.
    """
    return _scan_once(list(extra_paths or []), verbose=False)


def load_history() -> dict:
    """Return the current desktop-exports tracker as a plain dict for the
    dashboard UI. Shape:

        {
          "seen": {
              "<sha>": {"path": str, "origin": str, "indexed_at": int, "chunks": int},
              ...
          },
          "count": int,
        }
    """
    tracker = _load_tracker()
    seen = tracker.get("seen", {})
    return {"seen": seen, "count": len(seen)}


def _print_summary(stats: dict, prefix: str = "") -> None:
    print()
    print(f"  {prefix}Candidates:       {stats['scanned']}")
    print(f"  {prefix}Skipped (seen):   {stats['skipped_seen']}")
    print(f"  {prefix}Newly indexed:    {stats['indexed_zips']}")
    print(f"  {prefix}Chunks stored:    {stats['inserted_chunks']}")


def main() -> int:
    args = sys.argv[1:]
    watch = False
    interval = 30
    extra_paths: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--watch":
            watch = True
            i += 1
        elif a == "--interval" and i + 1 < len(args):
            interval = max(5, int(args[i + 1]))
            i += 2
        elif a == "--path" and i + 1 < len(args):
            extra_paths.append(args[i + 1])
            i += 2
        elif a in ("-h", "--help"):
            print("Usage: python -m imprint.cli_desktop_learn [--watch] [--interval N] [--path DIR]")
            return 0
        else:
            print(f"Unknown argument: {a}", file=sys.stderr)
            return 1

    if watch:
        print()
        print(f"  {C_CYAN}Watching{C_RESET} for new conversation exports every {interval}s (Ctrl+C to stop)")
        try:
            while True:
                stats = _scan_once(extra_paths)
                if stats["indexed_zips"] > 0:
                    _print_summary(stats)
                time.sleep(interval)
        except KeyboardInterrupt:
            print()
            print("  stopped.")
            return 0

    stats = _scan_once(extra_paths)
    _print_summary(stats, prefix="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
