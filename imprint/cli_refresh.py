"""Re-index only files that changed since last indexing.

Compares current file modification times against stored timestamps.
Updates changed files, adds new files, skips unchanged.

Usage: python -m imprint.cli_refresh <target_dir> <dir:project> ...
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from imprint import tagger, vectorstore as vs, extractors
from imprint.chunker import chunk_file
from imprint.progress import write_progress, clear_progress
from imprint.cli_index import (
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
            "Usage: python -m imprint.cli_refresh <target_dir> <dir:project> ...",
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

    _progress_projects = list(project_files.keys())
    write_progress("refresh", 0, len(changed), 0, 0, time.time(), _progress_projects)
    _last_progress_write = time.time()

    # ── Phase 3: Re-index changed files ────────────────────────
    # Zero-shot on by default; LLM deferred to phase 2 (after embedding).
    from imprint.config_schema import resolve
    enable_llm = resolve("tagger.llm")[0]
    if not enable_llm and tagger._get_llm_provider() == "local":
        _, _llm_source = resolve("tagger.llm")
        if _llm_source == "default":
            enable_llm = True
    enable_zero_shot = resolve("tagger.zero_shot")[0]

    stored = 0
    skipped = 0
    t_start = time.time()
    total = len(changed)
    cancelled = False

    def _build_records(chunks, project, rel, source_key, mtime, doc_meta):
        source_type = "file" if not doc_meta.get("ocr") else "ocr"
        records = []
        for j, (chunk_text, chunk_idx) in enumerate(chunks):
            prev_text = chunks[j - 1][0][-200:] if j > 0 else ""
            next_text = chunks[j + 1][0][:200] if j < len(chunks) - 1 else ""
            neighbor_ctx = prev_text + ("\n...\n" if prev_text and next_text else "") + next_text
            # Phase 1: deterministic + keyword tags only (LLM deferred to phase 2)
            tags = tagger.build_payload_tags(
                chunk_text, rel_path=rel,
                zero_shot=enable_zero_shot, llm=False,
                neighbor_context=neighbor_ctx, project_hint=project,
            )
            tags.pop("_llm_type", "")
            mem_type = "architecture"
            records.append({
                "content": chunk_text,
                "project": project,
                "type": mem_type,
                "tags": tags,
                "source": source_key,
                "source_type": source_type,
                "doc_metadata": doc_meta,
                "chunk_index": chunk_idx,
                "source_mtime": mtime,
            })
        return records

    try:
        for i, (project, rel, fpath) in enumerate(changed):
            elapsed = time.time() - t_start
            print_bar(i + 1, total, elapsed, rel)

            now = time.time()
            if now - _last_progress_write >= 1.0:
                write_progress("refresh", i + 1, total, stored, skipped, t_start, _progress_projects)
                _last_progress_write = now

            try:
                ext = os.path.splitext(rel)[1].lower()
                is_doc = extractors.is_doc_extension(ext)

                source_key = f"{project}/{rel}"
                # Delete old chunks for this source before re-chunking so we
                # don't accumulate stale versions.
                if source_key in stored_sources:
                    vs.delete_by_source(source_key)

                if is_doc:
                    try:
                        doc_list = extractors.dispatch_by_ext(fpath)
                    except (extractors.ExtractorUnavailable, extractors.ExtractionError):
                        skipped += 1
                        continue

                    mtime = os.path.getmtime(fpath)
                    all_records = []
                    for extracted in doc_list:
                        content = extracted.text
                        if len(content.strip()) < 10:
                            continue
                        doc_meta = dict(extracted.metadata or {})
                        chunk_mode = extracted.chunk_mode or "prose"
                        chunks = chunk_file(content, rel, chunk_mode=chunk_mode)
                        if chunks:
                            all_records.extend(_build_records(
                                chunks, project, rel, source_key, mtime, doc_meta,
                            ))
                    if not all_records:
                        skipped += 1
                        continue
                    inserted, _ = vs.store_batch(all_records)
                    stored += inserted
                else:
                    with open(fpath, "r", errors="ignore") as f:
                        content = f.read()
                    if len(content.strip()) < 10 or len(content) > 50000:
                        skipped += 1
                        continue

                    chunks = chunk_file(content, rel, chunk_mode=None)
                    if not chunks:
                        skipped += 1
                        continue
                    mtime = os.path.getmtime(fpath)
                    records = _build_records(
                        chunks, project, rel, source_key, mtime, {},
                    )
                    inserted, _ = vs.store_batch(records)
                    stored += inserted
            except KeyboardInterrupt:
                raise
            except Exception:
                skipped += 1
    except KeyboardInterrupt:
        cancelled = True
        clear_progress()
        print()
        print()
        print(
            f"  {C_YELLOW}Cancelled{C_RESET} — progress saved. Re-run to continue."
        )

    if not cancelled:
        print_bar(total, total, time.time() - t_start)
        print()

    # ── Phase 2: LLM tagging (sequenced after all embeddings) ──
    llm_tagged = 0
    if enable_llm and stored > 0 and not cancelled:
        from .cli_index import _llm_tag_recent
        print()
        print(f"  {C_CYAN}═══ LLM Tagging ═══{C_RESET}")
        llm_tagged = _llm_tag_recent(
            ingest_start_ts=t_start,
            total_hint=stored,
            print_bar=print_bar,
        )

    # ── Summary ────────────────────────────────────────────────
    elapsed = time.time() - t_start
    print()
    if cancelled:
        print(f"  {C_YELLOW}═══ Refresh Cancelled ═══{C_RESET}")
    else:
        print(f"  {C_GREEN}═══ Refresh Complete ═══{C_RESET}")
    print(f"  Updated:  {stored}")
    print(f"  Skipped:  {skipped}")
    if llm_tagged:
        print(f"  Tagged:   {llm_tagged} (LLM)")
    print(f"  Time:     {elapsed:.1f}s")
    if cancelled:
        print(f"  {C_DIM}Re-run to continue (unchanged files are skipped){C_RESET}")
    else:
        clear_progress()
    print()


if __name__ == "__main__":
    main()
