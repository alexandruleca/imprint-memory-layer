"""Re-check every stored URL via HEAD and re-fetch the ones whose ETag
or Last-Modified has changed.

Usage:
    python -m imprint.cli_refresh_urls [--project NAME]
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from imprint import vectorstore as vs
from imprint.cli_ingest_url import ingest_one
from imprint.extractors import url as url_ext
from imprint import extractors as _ext
from imprint.progress import Task

C_RESET = "\033[0m"
C_CYAN = "\033[0;36m"
C_GREEN = "\033[0;32m"
C_YELLOW = "\033[1;33m"
C_DIM = "\033[2m"


def main():
    project_filter = ""
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--project" and i + 1 < len(args):
            project_filter = args[i + 1]
            i += 2
            continue
        if a.startswith("--project="):
            project_filter = a.split("=", 1)[1]
            i += 1
            continue
        i += 1

    known = vs.get_url_sources()
    if project_filter:
        known = {u: v for u, v in known.items() if v.get("project") == project_filter}

    if not known:
        print()
        print(f"  {C_DIM}No URL sources stored yet.{C_RESET}")
        print()
        return

    print()
    print(f"  {C_CYAN}Refreshing{C_RESET} {len(known)} url(s) ...", flush=True)
    print()

    updated = 0
    unchanged = 0
    errors = 0

    # Each URL's result is a line of its own above the progress line.
    with Task("refresh-urls", phase="refreshing", unit="urls", total=len(known),
              projects=[project_filter] if project_filter else []) as task:
        for url, info in known.items():
            project = info.get("project") or "urls"
            task.update(detail=url)
            try:
                head = url_ext.head_check(url)
            except _ext.ExtractorUnavailable as e:
                errors += 1
                task.advance(skipped=1)
                task.note(f"  {C_YELLOW}! {url}  ({e}){C_RESET}")
                continue

            same_etag = head.get("etag") and head["etag"] == info.get("etag")
            same_mod = head.get("last_modified") and head["last_modified"] == info.get("last_modified")
            if head and (same_etag or same_mod):
                unchanged += 1
                task.advance(skipped=1)
                task.note(f"  {C_DIM}= {url}  (unchanged){C_RESET}")
                continue

            # Changed (or HEAD didn't provide validators) → re-fetch.
            n, status = ingest_one(url, project, known, force=True)
            if status == "stored":
                updated += 1
                task.advance(stored=n)
                task.note(f"  {C_GREEN}+{C_RESET} {url}  (re-indexed, {n} chunks)")
            else:
                errors += 1
                task.advance(skipped=1)
                task.note(f"  {C_YELLOW}! {url}  ({status}){C_RESET}")

        task.update(detail="")
        print(flush=True)
        task.finish(f"{C_GREEN}═══ URL Refresh Complete ═══{C_RESET}")
    print(f"  Unchanged: {unchanged}")
    print(f"  Updated:   {updated}")
    print(f"  Errors:    {errors}")
    print()

if __name__ == "__main__":
    main()
