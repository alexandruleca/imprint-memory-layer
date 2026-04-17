"""CLI: fetch URL(s) → extract → chunk → store.

Usage:
    python -m imprint.cli_ingest_url <url> [<url>...] [--project NAME]
    python -m imprint.cli_ingest_url --from-file urls.txt [--project NAME]

Each URL becomes a ``source_type=url`` memory group keyed by its canonical
URL. Re-running on the same URL skips unchanged (etag / last-modified
driven) content. To force re-index, delete the existing chunks via
`vectorstore.delete_by_source(url)`.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from imprint import tagger, vectorstore as vs, extractors
from imprint.chunker import chunk_file
from imprint.extractors import url as url_ext
from imprint.progress import write_progress, clear_progress

C_RESET = "\033[0m"
C_CYAN = "\033[0;36m"
C_GREEN = "\033[0;32m"
C_YELLOW = "\033[1;33m"
C_DIM = "\033[2m"


def _parse_args(argv: list[str]) -> tuple[list[str], str, bool]:
    urls: list[str] = []
    project = "urls"
    force = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--project":
            if i + 1 >= len(argv):
                print("--project requires a value", file=sys.stderr)
                sys.exit(1)
            project = argv[i + 1]
            i += 2
            continue
        if a.startswith("--project="):
            project = a.split("=", 1)[1]
            i += 1
            continue
        if a == "--from-file":
            if i + 1 >= len(argv):
                print("--from-file requires a path", file=sys.stderr)
                sys.exit(1)
            path = argv[i + 1]
            try:
                with open(path) as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            urls.append(line)
            except OSError as e:
                print(f"cannot read {path}: {e}", file=sys.stderr)
                sys.exit(1)
            i += 2
            continue
        if a == "--force":
            force = True
            i += 1
            continue
        urls.append(a)
        i += 1
    return urls, project, force


def ingest_one(url: str, project: str, known: dict, force: bool = False) -> tuple[int, str]:
    """Fetch + store a single URL. Returns (chunks_stored, status).
    status: 'stored' | 'skipped-unchanged' | 'error'."""
    existing = known.get(url)
    if existing and not force:
        # Cheap HEAD check — skip if etag / last-modified unchanged.
        try:
            head = url_ext.head_check(url)
        except extractors.ExtractorUnavailable as e:
            return 0, f"error: {e}"
        if head:
            same_etag = head.get("etag") and head["etag"] == existing.get("etag")
            same_mod = head.get("last_modified") and head["last_modified"] == existing.get("last_modified")
            if same_etag or same_mod:
                return 0, "skipped-unchanged"

    try:
        doc_list = url_ext.fetch(url)
    except extractors.ExtractorUnavailable as e:
        return 0, f"error: {e}"
    except extractors.ExtractionError as e:
        return 0, f"error: {e}"
    except Exception as e:
        return 0, f"error: {e}"

    # Filter out empty docs
    doc_list = [d for d in doc_list if d.text and len(d.text.strip()) >= 10]
    if not doc_list:
        return 0, "error: empty content"

    # Delete prior chunks for this URL so we replace, not accumulate.
    source_key = url
    if existing:
        vs.delete_by_source(source_key)

    from imprint.config_schema import resolve
    enable_llm = resolve("tagger.llm")[0]
    if not enable_llm and tagger._get_llm_provider() == "local":
        _, _llm_source = resolve("tagger.llm")
        if _llm_source == "default":
            enable_llm = True
    enable_zero_shot = resolve("tagger.zero_shot")[0] and not enable_llm

    records = []
    now = time.time()
    for extracted in doc_list:
        chunks = chunk_file(extracted.text, url, chunk_mode=extracted.chunk_mode or "prose")
        if not chunks:
            continue

        meta = extracted.metadata or {}
        etag = meta.get("etag", "")
        last_mod = meta.get("last_modified", "")
        doc_meta = {k: v for k, v in meta.items()
                    if k not in ("etag", "last_modified", "source_url", "original_url", "status_code", "content_type")}

        for i, (chunk_text, chunk_idx) in enumerate(chunks):
            prev_text = chunks[i - 1][0][-200:] if i > 0 else ""
            next_text = chunks[i + 1][0][:200] if i < len(chunks) - 1 else ""
            neighbor_ctx = prev_text + ("\n...\n" if prev_text and next_text else "") + next_text
            tags = tagger.build_payload_tags(
                chunk_text, rel_path=url,
                llm=None, zero_shot=enable_zero_shot,
                neighbor_context=neighbor_ctx, project_hint=project,
            )
            llm_type = tags.pop("_llm_type", "")
            mem_type = llm_type or "architecture"
            records.append({
                "content": chunk_text,
                "project": project,
                "type": mem_type,
                "tags": tags,
                "source": source_key,
                "source_type": "url",
                "source_url": url,
                "etag": etag,
                "last_modified": last_mod,
                "doc_metadata": doc_meta,
                "chunk_index": chunk_idx,
                "source_mtime": now,
            })

    if not records:
        return 0, "error: no chunks"

    inserted, _ = vs.store_batch(records)
    return inserted, "stored"


def main():
    urls, project, force = _parse_args(sys.argv[1:])
    if not urls:
        print(
            "Usage: python -m imprint.cli_ingest_url <url> [...] [--project NAME] [--from-file urls.txt] [--force]",
            file=sys.stderr,
        )
        sys.exit(1)

    print()
    print(f"  {C_CYAN}Ingesting{C_RESET} {len(urls)} url(s) → project={project}")
    print()

    known = vs.get_url_sources()
    total_stored = 0
    total_skipped = 0
    total_errors = 0
    t_start = time.time()
    progress_projects = [project] if project else []
    total_urls = len(urls)

    write_progress(
        "ingest-url", 0, total_urls, 0, 0, t_start, progress_projects,
    )

    for idx, url in enumerate(urls):
        n, status = ingest_one(url, project, known, force=force)
        if status == "stored":
            total_stored += n
            print(f"  {C_GREEN}+{C_RESET} {url}  ({n} chunks)")
        elif status == "skipped-unchanged":
            total_skipped += 1
            print(f"  {C_DIM}= {url}  (unchanged){C_RESET}")
        else:
            total_errors += 1
            print(f"  {C_YELLOW}! {url}  ({status}){C_RESET}")
        write_progress(
            "ingest-url", idx + 1, total_urls, total_stored, total_skipped,
            t_start, progress_projects,
        )

    # ── Phase 2: LLM tagging (sequenced after all embeddings) ──
    from imprint.config_schema import resolve
    _enable_llm = resolve("tagger.llm")[0]
    if not _enable_llm and tagger._get_llm_provider() == "local":
        _, _llm_source = resolve("tagger.llm")
        if _llm_source == "default":
            _enable_llm = True

    llm_tagged = 0
    if _enable_llm and total_stored > 0:
        from .cli_index import _llm_tag_recent, print_bar as _idx_print_bar
        print()
        print(f"  {C_CYAN}═══ LLM Tagging ═══{C_RESET}")
        try:
            llm_tagged = _llm_tag_recent(
                ingest_start_ts=t_start,
                total_hint=total_stored,
                print_bar=_idx_print_bar,
                command="ingest-url",
                projects=[],
            )
        finally:
            clear_progress()
    else:
        clear_progress()

    elapsed = time.time() - t_start
    print()
    print(f"  {C_GREEN}═══ URL Ingest Complete ═══{C_RESET}")
    print(f"  Stored:   {total_stored} chunks")
    if llm_tagged:
        print(f"  Tagged:   {llm_tagged} (LLM)")
    print(f"  Skipped:  {total_skipped} url(s)")
    print(f"  Errors:   {total_errors}")
    print(f"  Time:     {elapsed:.1f}s")
    print()


if __name__ == "__main__":
    main()
