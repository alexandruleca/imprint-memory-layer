"""Re-index only files that changed since last indexing.

Compares current file modification times against stored timestamps.
Updates changed files, adds new files, skips unchanged.

Usage: python -m knowledgebase.cli_refresh <target_dir> <dir:project> ...
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from knowledgebase import tagger, vectorstore as vs
from knowledgebase.chunker import chunk_file
from knowledgebase.cli_index import (
    EXTENSIONS,
    SKIP_DIRS,
    BAR_WIDTH,
    C_RESET,
    C_BOLD,
    C_DIM,
    C_CYAN,
    C_GREEN,
    C_YELLOW,
    scan_dir,
    print_tree,
    print_bar,
)


def get_stored_sources() -> dict[str, float]:
    """Get stored source paths and their file mtimes. Uses source_mtime for accurate comparison."""
    return vs.get_source_mtimes()


def main():
    if len(sys.argv) < 3:
        print(
            "Usage: python -m knowledgebase.cli_refresh <target_dir> <dir:project> ...",
            file=sys.stderr,
        )
        sys.exit(1)

    target = sys.argv[1]
    pairs = []
    for arg in sys.argv[2:]:
        dir_path, project = arg.rsplit(":", 1)
        pairs.append((dir_path, project))

    # ── Phase 1: Scan ──────────────────────────────────────────
    print()
    print(f"  {C_CYAN}Scanning{C_RESET} {target} for changes ...")
    print()

    project_files = {}
    grand_total = 0
    for dir_path, project in pairs:
        files = scan_dir(dir_path)
        project_files[project] = files
        grand_total += len(files)

    # ── Phase 2: Compare ───────────────────────────────────────
    stored_sources = get_stored_sources()

    changed = []  # (project, rel, fpath) — files that need re-indexing
    unchanged = 0
    new_files = 0

    for project, files in project_files.items():
        for rel, fpath in files:
            source_key = f"{project}/{rel}"
            try:
                mtime = os.path.getmtime(fpath)
            except OSError:
                continue

            if source_key in stored_sources:
                stored_ts = stored_sources[source_key]
                if mtime <= stored_ts:
                    unchanged += 1
                    continue
                # File changed since last index
                changed.append((project, rel, fpath))
            else:
                # New file
                changed.append((project, rel, fpath))
                new_files += 1

    print(f"  {C_BOLD}{grand_total}{C_RESET} files scanned")
    print(f"  {C_GREEN}{unchanged}{C_RESET} unchanged (skipped)")
    print(f"  {C_YELLOW}{len(changed)}{C_RESET} to update ({new_files} new)")
    print()

    if not changed:
        print(f"  {C_GREEN}Everything is up to date.{C_RESET}")
        print()
        return

    # ── Phase 3: Re-index changed files ────────────────────────
    stored = 0
    skipped = 0
    t_start = time.time()
    total = len(changed)
    cancelled = False

    try:
        for i, (project, rel, fpath) in enumerate(changed):
            elapsed = time.time() - t_start
            print_bar(i + 1, total, elapsed, rel)
            try:
                with open(fpath, "r", errors="ignore") as f:
                    content = f.read()
                if len(content.strip()) < 10 or len(content) > 50000:
                    skipped += 1
                    continue

                source_key = f"{project}/{rel}"
                # Delete old chunks for this source before re-chunking so we
                # don't accumulate stale versions.
                if source_key in stored_sources:
                    vs.delete_by_source(source_key)

                chunks = chunk_file(content, rel)
                if not chunks:
                    skipped += 1
                    continue

                mtime = os.path.getmtime(fpath)
                records = []
                for chunk_text, chunk_idx in chunks:
                    tags = tagger.build_payload_tags(chunk_text, rel_path=rel)
                    records.append({
                        "content": chunk_text,
                        "project": project,
                        "type": "architecture",
                        "tags": tags,
                        "source": source_key,
                        "chunk_index": chunk_idx,
                        "source_mtime": mtime,
                    })
                inserted, _ = vs.store_batch(records)
                stored += inserted
            except KeyboardInterrupt:
                raise
            except Exception:
                skipped += 1
    except KeyboardInterrupt:
        cancelled = True
        print()
        print()
        print(
            f"  {C_YELLOW}Cancelled{C_RESET} — progress saved. Re-run to continue."
        )

    if not cancelled:
        print_bar(total, total, time.time() - t_start)
        print()

    # ── Summary ────────────────────────────────────────────────
    elapsed = time.time() - t_start
    print()
    if cancelled:
        print(f"  {C_YELLOW}═══ Refresh Cancelled ═══{C_RESET}")
    else:
        print(f"  {C_GREEN}═══ Refresh Complete ═══{C_RESET}")
    print(f"  Updated:  {stored}")
    print(f"  Skipped:  {skipped}")
    print(f"  Time:     {elapsed:.1f}s")
    if cancelled:
        print(f"  {C_DIM}Re-run to continue (unchanged files are skipped){C_RESET}")
    print()


if __name__ == "__main__":
    main()
