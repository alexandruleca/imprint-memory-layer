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
from imprint._cli_signals import install as _install_signals

_install_signals()

C_RESET = "\033[0m"
C_CYAN = "\033[0;36m"
C_GREEN = "\033[0;32m"
C_YELLOW = "\033[1;33m"
C_DIM = "\033[2m"


def _parse_args(argv: list[str]) -> tuple[list[str], str, bool, bool, bool, int | None]:
    """Returns (urls, project, force, follow, no_follow, follow_depth_override)."""
    urls: list[str] = []
    project = "urls"
    force = False
    follow = False
    no_follow = False
    follow_depth_override: int | None = None
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
        if a == "--follow":
            follow = True
            i += 1
            continue
        if a == "--no-follow":
            no_follow = True
            i += 1
            continue
        if a == "--follow-depth":
            if i + 1 >= len(argv):
                print("--follow-depth requires a value", file=sys.stderr)
                sys.exit(1)
            try:
                follow_depth_override = int(argv[i + 1])
            except ValueError:
                print("--follow-depth must be an integer", file=sys.stderr)
                sys.exit(1)
            i += 2
            continue
        if a.startswith("--follow-depth="):
            try:
                follow_depth_override = int(a.split("=", 1)[1])
            except ValueError:
                print("--follow-depth must be an integer", file=sys.stderr)
                sys.exit(1)
            i += 1
            continue
        urls.append(a)
        i += 1
    return urls, project, force, follow, no_follow, follow_depth_override


def ingest_one(url: str, project: str, known: dict, force: bool = False,
               workspace: str | None = None) -> tuple[int, str]:
    """Fetch + store a single URL. Returns (chunks_stored, status).
    status: 'stored' | 'skipped-unchanged' | 'error'."""
    from imprint import config
    workspace = workspace or config.get_active_workspace()
    existing = known.get(url)
    if existing and existing.get("project") != project:
        existing = None
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

    # Prepare every chunk before replacing prior content.
    source_key = url

    from imprint.config_schema import resolve
    from imprint.tag_backlog import deferring
    pending = deferring()  # LLM tagging runs in the background (ADR 0067)
    enable_zero_shot = resolve("tagger.zero_shot")[0]

    records = []
    now = time.time()
    from . import source_memory
    source_document, segments = source_memory.extracted_documents(doc_list)
    source_document['kind'] = 'url'
    for extracted, (base, content) in zip(doc_list, segments):
        part_start = len(records)
        chunks = chunk_file(content, url, chunk_mode=extracted.chunk_mode or "prose")
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
                llm=False, zero_shot=enable_zero_shot,
                neighbor_context=neighbor_ctx, project_hint=project, workspace=workspace,
            )
            tags.pop("_llm_type", None)
            records.append({
                "content": chunk_text,
                "project": project,
                "type": "architecture",
                "tags": tags,
                "source": source_key,
                "source_type": "url",
                "source_url": url,
                "etag": etag,
                "last_modified": last_mod,
                "doc_metadata": doc_meta,
                "chunk_index": len(records),
                "source_mtime": now,
                "tag_pending": pending,
            })

        part_rows = records[part_start:]
        source_memory.attach({'text': content}, part_rows, prefix=f'[{url}]\n', code=extracted.chunk_mode == 'code')
        for row in part_rows:
            row['source_start'] += base
            row['source_end'] += base
            row.pop('_source_document', None)
    if not records:
        return 0, "error: no chunks produced; previous content retained"
    records[0]['_source_document'] = source_document
    inserted, _ = vs.replace_source(records, source_key, project, workspace=workspace)
    return inserted, "stored"


def main():
    urls, project, force, follow, no_follow, follow_depth_override = _parse_args(sys.argv[1:])
    if not urls:
        print(
            "Usage: python -m imprint.cli_ingest_url <url> [...] [--project NAME] "
            "[--from-file urls.txt] [--force] [--follow] [--no-follow] [--follow-depth N]",
            file=sys.stderr,
        )
        sys.exit(1)

    from imprint.config_schema import resolve as _resolve
    url_follow   = (follow or _resolve("ingest.url_follow")[0]) and not no_follow
    follow_depth = follow_depth_override if follow_depth_override is not None else _resolve("ingest.url_follow_depth")[0]
    follow_max   = _resolve("ingest.url_follow_max")[0]

    print()
    print(f"  {C_CYAN}Ingesting{C_RESET} {len(urls)} url(s) → project={project}", end="")
    if url_follow:
        print(f"  {C_DIM}(follow depth={follow_depth} max={follow_max}){C_RESET}", end="")
    print()
    print()

    known = vs.get_url_sources()
    total_stored = 0
    total_skipped = 0
    total_errors = 0
    total_discovered = 0
    t_start = time.time()
    progress_projects = [project] if project else []

    # BFS queue: (url, depth). Seed URLs start at depth 0.
    queue: list[tuple[str, int]] = [(u, 0) for u in urls]
    visited: set[str] = set()
    processed = 0

    write_progress(
        "ingest-url", 0, len(urls), 0, 0, t_start, progress_projects, unit="pages",
    )

    while queue:
        url, depth = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        processed += 1

        depth_tag = f"  {C_DIM}depth {depth}{C_RESET}" if depth > 0 else ""
        n, status = ingest_one(url, project, known, force=force)
        if status == "stored":
            total_stored += n
            print(f"  {C_GREEN}+{C_RESET} {url}  ({n} chunks){depth_tag}")
        elif status == "skipped-unchanged":
            total_skipped += 1
            print(f"  {C_DIM}= {url}  (unchanged){C_RESET}{depth_tag}")
        else:
            total_errors += 1
            print(f"  {C_YELLOW}! {url}  ({status}){C_RESET}{depth_tag}")

        write_progress(
            "ingest-url", processed, max(processed, len(queue) + processed),
            total_stored, total_skipped, t_start, progress_projects, unit="pages",
        )

        # Follow same-domain links if enabled and within depth limit
        if url_follow and depth < follow_depth and status in ("stored", "skipped-unchanged"):
            links = url_ext.extract_links(url)
            new_links = 0
            for link in links:
                if link not in visited and total_discovered < follow_max:
                    queue.append((link, depth + 1))
                    total_discovered += 1
                    new_links += 1
            if new_links:
                print(f"  {C_DIM}  → discovered {new_links} link(s) from {url}{C_RESET}")
            # Refresh known sources so freshness checks work for newly discovered URLs
            if new_links:
                known = vs.get_url_sources()

    clear_progress()

    elapsed = time.time() - t_start
    print()
    print(f"  {C_GREEN}═══ URL Ingest Complete ═══{C_RESET}")
    print(f"  Stored:   {total_stored} chunks")
    print(f"  Skipped:  {total_skipped} url(s)")
    print(f"  Errors:   {total_errors}")
    if total_discovered:
        print(f"  Followed: {total_discovered} discovered url(s)")
    print(f"  Time:     {elapsed:.1f}s")
    from imprint.tag_backlog import report
    report(total_stored)
    print()


if __name__ == "__main__":
    main()
