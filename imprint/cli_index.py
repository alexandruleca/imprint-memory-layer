"""CLI helper for indexing directories into imprint memory.

Usage:
    python -m imprint.cli_index [--batch-size N] <target_dir> <dir1:project1> <dir2:project2> ...

Options:
    --batch-size N    Chunks to buffer before each embed+insert (default: 32).
                      Higher = faster but more peak memory. Sweet spot 32-64
                      on int8 model; drop to 8-16 if RAM tight.
"""

import os
import sys
import time

# Ensure the imprint package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from imprint import tagger, vectorstore as vs
from imprint import extractors
from imprint.progress import write_progress, clear_progress

# Files worth indexing — code with logic, not styling/config/generated
EXTENSIONS = {
    '.md', '.txt',
    '.ts', '.js', '.jsx', '.tsx', '.vue', '.svelte',  # JS/TS
    '.py',                                              # Python
    '.go',                                              # Go
    '.rs',                                              # Rust
    '.java', '.kt',                                     # JVM
    '.swift',                                           # Swift
    '.rb',                                              # Ruby
    '.php',                                             # PHP
    '.sql', '.graphql', '.proto',                       # Schema/query
    '.sh',                                              # Scripts
}
# Document formats are gated by `ingest.doc_formats` config; the scanner
# merges them into the effective allow-list at walk time. Set the config
# key to an empty string to index only code + prose.


def _enabled_doc_formats() -> set[str]:
    """Resolve ingest.doc_formats into a set of extensions, honoring user
    config. Returns the set to include under EXTENSIONS at scan time."""
    try:
        from imprint.config_schema import resolve
        raw = str(resolve("ingest.doc_formats")[0])
    except Exception:
        raw = "pdf,docx,pptx,xlsx,csv,epub,rtf,html,eml,json"
    base = {"pdf": [".pdf"], "docx": [".docx"], "pptx": [".pptx"],
            "xlsx": [".xlsx"], "csv": [".csv", ".tsv"], "epub": [".epub"],
            "rtf": [".rtf"], "html": [".html", ".htm"],
            "eml": [".eml", ".mbox"], "json": [".json"]}
    out: set[str] = set()
    for name in (x.strip().lower() for x in raw.split(",") if x.strip()):
        out.update(base.get(name, []))
    return out


def _ocr_image_exts() -> set[str]:
    try:
        from imprint.config_schema import resolve
        if bool(resolve("ingest.ocr_enabled")[0]):
            return {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".tif", ".webp"}
    except Exception:
        pass
    return set()


# Skip these — low value for memory context.
# Note: .env / .env.* files are handled by an explicit filename check in
# scan_dir() since they're not true extensions (splitext('.env') → ('.env','')).
SKIP_EXTENSIONS = {
    '.css', '.scss', '.sass', '.less', '.styl',         # Styling
    '.svg', '.ico',                                     # Vector + favicons
    '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.tif', '.webp',  # Bitmaps (gated back in when OCR enabled)
    '.lock', '.map', '.min.js', '.min.css',             # Generated
    '.d.ts',                                            # Type declarations
    '.snap',                                            # Test snapshots
}

# Skip files matching these names regardless of extension
SKIP_FILES = {
    'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml',
    'tsconfig.json', 'tsconfig.build.json',
    '.eslintrc.json', '.eslintrc.js', '.prettierrc',
    'jest.config.js', 'jest.config.ts',
    'webpack.config.js', 'vite.config.ts', 'vite.config.js',
    'next.config.js', 'next.config.ts', 'nuxt.config.ts',
    '.gitignore', '.dockerignore', '.editorconfig',
    'Dockerfile', 'docker-compose.yml', 'docker-compose.yaml',
}

SKIP_DIRS = {
    # Dependency / package directories
    'node_modules', 'vendor', 'bower_components', 'jspm_packages',
    # Python virtual envs + caches
    'venv', '.venv', 'env', '.env', 'virtualenv', '.tox',
    '__pycache__', 'site-packages', 'Lib', 'Scripts',
    '.pytest_cache', '.mypy_cache', '.ruff_cache', '.pyre',
    # Build outputs
    'dist', 'build', 'out', 'target', 'bin', 'obj',
    '.next', '.nuxt', '.svelte-kit', '.astro', '.output',
    # Caches
    '.cache', '.turbo', '.parcel-cache', '.webpack', '.rollup.cache',
    '.gradle', '.mvn',
    # VCS + coverage
    '.git', '.hg', '.svn', 'coverage', '.nyc_output',
    # iOS/macOS
    'Pods', 'DerivedData', 'xcuserdata',
}

BAR_WIDTH = 40
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_CYAN = "\033[0;36m"
C_GREEN = "\033[0;32m"
C_YELLOW = "\033[1;33m"


def summarize_file(content: str, rel_path: str, max_len: int = 1500) -> str:
    """Create a compact, searchable summary of a file.

    Instead of storing raw content (wastes tokens), extract:
    - File purpose (from comments, docstrings, README headers)
    - Key exports (functions, classes, interfaces)
    - Configuration values
    - Important patterns

    Returns empty string if file has no meaningful content to index.
    """
    lines = content.split("\n")
    ext = os.path.splitext(rel_path)[1].lower()

    parts = [f"[{rel_path}]"]

    # Extract doc comments, headers, and key declarations
    meaningful = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # Comments and docstrings — often describe intent
        if stripped.startswith(("//", "#", "/*", "/**", "*", "'''", '"""')):
            text = stripped.lstrip("/#*' \"").strip()
            if len(text) > 15:  # skip trivial comments
                meaningful.append(text)
            continue

        # Markdown headers
        if ext == ".md" and stripped.startswith("#"):
            meaningful.append(stripped)
            continue

        # Key declarations (functions, classes, interfaces, exports)
        if any(
            stripped.startswith(kw)
            for kw in [
                "export ", "def ", "class ", "interface ", "type ",
                "func ", "function ", "const ", "pub fn ", "pub struct ",
                "module.exports", "CREATE TABLE", "CREATE INDEX",
            ]
        ):
            # Take just the signature, not the body
            sig = stripped[:200].split("{")[0].split("(")
            if len(sig) > 1:
                meaningful.append(sig[0].strip() + "(...)")
            else:
                meaningful.append(sig[0].strip())
            continue

        # Config/env keys
        if "=" in stripped and any(
            stripped.upper().startswith(p)
            for p in ["DB_", "API_", "PORT", "HOST", "SECRET", "AUTH_", "CORS"]
        ):
            meaningful.append(stripped.split("=")[0].strip())
            continue

    if not meaningful:
        return ""

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for m in meaningful:
        key = m.lower().strip()
        if key not in seen:
            seen.add(key)
            unique.append(m)

    summary = "\n".join(unique)
    if len(summary) > max_len:
        summary = summary[:max_len].rsplit("\n", 1)[0]

    parts.append(summary)
    return "\n".join(parts)


def _is_low_value(content: str, rel_path: str) -> bool:
    """Detect files that look like data/config/generated — not worth indexing."""
    name = os.path.basename(rel_path).lower()
    ext = os.path.splitext(name)[1]

    # JSON files: only index if they have comments or are short (likely config)
    # Large JSON = data fixture, skip
    if ext == '.json' and len(content) > 5000:
        return True

    # i18n / translation files
    if any(p in rel_path.lower() for p in ['i18n', 'locale', 'translation', 'messages']):
        return True

    # Test fixture data
    if any(p in rel_path.lower() for p in ['fixture', 'mock', '__snapshots__', 'testdata']):
        return True

    # Generated / migration files that are mostly SQL dumps or schemas
    if 'migration' in rel_path.lower() and ext == '.sql' and len(content) > 10000:
        return True

    # Files that are mostly one-liners (re-exports, barrel files)
    lines = content.strip().split('\n')
    if len(lines) < 3 and all(l.strip().startswith(('export ', 'module.exports', 'from ', 'import ')) for l in lines if l.strip()):
        return True

    return False


def scan_dir(dir_path):
    """Walk a directory and return list of (rel_path, full_path) for indexable files."""
    # Resolve doc/image gates once per scan — avoids reloading config per file.
    doc_exts = _enabled_doc_formats()
    ocr_imgs = _ocr_image_exts()
    effective_exts = EXTENSIONS | doc_exts | ocr_imgs
    # Image exts are in SKIP_EXTENSIONS by default; lift that gate when OCR on.
    skip_exts = SKIP_EXTENSIONS - ocr_imgs

    files = []
    for root, subdirs, fnames in os.walk(dir_path):
        subdirs[:] = [
            d for d in subdirs
            if d not in SKIP_DIRS and not d.startswith('.')
        ]
        for fname in fnames:
            if fname in SKIP_FILES:
                continue
            # Any .env* file (.env, .env.local, .env.production, etc.) —
            # may contain secrets, never index.
            if fname == '.env' or fname.startswith('.env.'):
                continue
            ext = os.path.splitext(fname)[1].lower()
            if ext in skip_exts:
                continue
            if ext not in effective_exts:
                continue
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, dir_path)
            files.append((rel, fpath))
    return files


def extract_dependencies(dir_path) -> str | None:
    """Read package.json and return a formatted dependency list.
    This captures what node_modules contains without indexing the actual files."""
    pkg_path = os.path.join(dir_path, "package.json")
    if not os.path.exists(pkg_path):
        return None

    try:
        import json
        with open(pkg_path, 'r') as f:
            pkg = json.load(f)

        name = pkg.get("name", os.path.basename(dir_path))
        parts = [f"[package.json] {name}"]

        deps = pkg.get("dependencies", {})
        dev_deps = pkg.get("devDependencies", {})

        if deps:
            parts.append(f"\nDependencies ({len(deps)}):")
            for dep, ver in sorted(deps.items()):
                parts.append(f"  {dep}: {ver}")

        if dev_deps:
            parts.append(f"\nDevDependencies ({len(dev_deps)}):")
            for dep, ver in sorted(dev_deps.items()):
                parts.append(f"  {dep}: {ver}")

        if not deps and not dev_deps:
            return None

        return "\n".join(parts)
    except Exception:
        return None


def print_tree(target, project_files):
    """Print a directory tree with file counts."""
    print(f"  {C_BOLD}{os.path.basename(target)}{C_RESET}")
    items = list(project_files.items())

    for i, (project, files) in enumerate(items):
        is_last = i == len(items) - 1
        branch = "└── " if is_last else "├── "
        count_str = f"{C_CYAN}{len(files)} files{C_RESET}"
        print(f"  {branch}{project} ({count_str})")

        # Show top-level subdirs/files
        subdirs = set()
        for rel, _ in files:
            parts = rel.split(os.sep)
            subdirs.add(parts[0] if len(parts) > 1 else rel)

        sorted_subs = sorted(subdirs)[:8]
        prefix = "    " if is_last else "│   "
        for j, sub in enumerate(sorted_subs):
            sub_last = j == len(sorted_subs) - 1 and len(subdirs) <= 8
            sub_branch = "└── " if sub_last else "├── "
            print(f"  {prefix}{sub_branch}{C_DIM}{sub}{C_RESET}")
        if len(subdirs) > 8:
            print(f"  {prefix}└── {C_DIM}... {len(subdirs) - 8} more{C_RESET}")


def _term_width():
    try:
        return os.get_terminal_size().columns
    except (ValueError, OSError):
        return 80


def print_bar(processed, total, elapsed, current_file=""):
    """Print an in-place progress bar that fits the terminal width."""
    cols = _term_width()
    pct = processed / total if total else 1

    if processed > 0 and pct < 1:
        eta = elapsed / pct * (1 - pct)
        time_str = f"eta {int(eta)}s"
    else:
        time_str = f"{elapsed:.1f}s"

    stats = f" {int(pct * 100):3d}% {processed}/{total} {time_str}"
    # Reserve space: 2 indent + bar + stats + 1 margin
    bar_width = max(10, cols - len(stats) - 3)
    filled = int(bar_width * pct)
    bar = "█" * filled + "░" * (bar_width - filled)

    line = f"  {bar}{stats}"
    # Pad to terminal width to clear previous content, but never exceed
    print(f"\r{line[:cols]}", end="", flush=True)


def _parse_batch_size(argv: list[str], default: int) -> tuple[int, list[str]]:
    """Strip --batch-size N / --batch-size=N from argv. Returns (value, remaining argv)."""
    batch_size = default
    remaining: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--batch-size":
            if i + 1 >= len(argv):
                print("Error: --batch-size requires a value", file=sys.stderr)
                sys.exit(1)
            try:
                batch_size = int(argv[i + 1])
            except ValueError:
                print(f"Error: --batch-size must be an integer, got {argv[i + 1]!r}", file=sys.stderr)
                sys.exit(1)
            i += 2
            continue
        if a.startswith("--batch-size="):
            try:
                batch_size = int(a.split("=", 1)[1])
            except ValueError:
                print(f"Error: --batch-size must be an integer, got {a.split('=', 1)[1]!r}", file=sys.stderr)
                sys.exit(1)
            i += 1
            continue
        remaining.append(a)
        i += 1
    if batch_size < 1:
        print(f"Error: --batch-size must be >= 1, got {batch_size}", file=sys.stderr)
        sys.exit(1)
    return batch_size, remaining


def _parse_file_flags(argv: list[str]) -> tuple[str | None, str | None, list[str]]:
    """Strip --file PATH and --project NAME from argv.
    Returns (file_path, project, remaining)."""
    file_path = None
    project = None
    remaining: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--file":
            if i + 1 >= len(argv):
                print("--file requires a path", file=sys.stderr)
                sys.exit(1)
            file_path = argv[i + 1]
            i += 2
            continue
        if a == "--project":
            if i + 1 >= len(argv):
                print("--project requires a name", file=sys.stderr)
                sys.exit(1)
            project = argv[i + 1]
            i += 2
            continue
        remaining.append(a)
        i += 1
    return file_path, project, remaining


def ingest_single_file(file_path: str, project: str, batch_size: int = 32):
    """Index a single file into the vector store."""
    from imprint.chunker import chunk_file
    from imprint.config_schema import resolve

    if not os.path.isfile(file_path):
        print(f"  {C_YELLOW}File not found:{C_RESET} {file_path}")
        sys.exit(1)

    rel = os.path.basename(file_path)
    print()
    print(f"  {C_CYAN}Indexing{C_RESET} {rel} → project={project}")
    print()
    write_progress("ingest", 0, 1, 0, 0, time.time(), [project])

    enable_llm = resolve("tagger.llm")[0]
    enable_zero_shot = resolve("tagger.zero_shot")[0] and not enable_llm

    ext = os.path.splitext(rel)[1].lower()
    is_doc = extractors.is_doc_extension(ext)
    t_start = time.time()

    records = []
    mtime = os.path.getmtime(file_path)
    source_key = f"{project}/{rel}"

    if is_doc:
        try:
            doc_list = extractors.dispatch_by_ext(file_path)
        except extractors.ExtractorUnavailable as e:
            print(f"  {C_YELLOW}Extractor unavailable:{C_RESET} {e}")
            sys.exit(1)
        except extractors.ExtractionError as e:
            print(f"  {C_YELLOW}Extraction failed:{C_RESET} {e}")
            sys.exit(1)

        for extracted in doc_list:
            content = extracted.text
            if len(content.strip()) < 10:
                continue
            doc_meta = dict(extracted.metadata or {})
            chunk_mode = extracted.chunk_mode or "prose"
            chunks = chunk_file(content, rel, chunk_mode=chunk_mode)
            if not chunks:
                continue
            for i, (chunk_text, chunk_idx) in enumerate(chunks):
                prev_text = chunks[i - 1][0][-200:] if i > 0 else ""
                next_text = chunks[i + 1][0][:200] if i < len(chunks) - 1 else ""
                neighbor_ctx = prev_text + ("\n...\n" if prev_text and next_text else "") + next_text
                tags = tagger.build_payload_tags(
                    chunk_text, rel_path=rel,
                    llm=enable_llm, zero_shot=enable_zero_shot,
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
                    "source_type": "file",
                    "doc_metadata": doc_meta,
                    "chunk_index": chunk_idx,
                    "source_mtime": mtime,
                    "llm_tagged": bool(llm_type),
                })
    else:
        with open(file_path, 'r', errors='ignore') as f:
            content = f.read()
        if len(content.strip()) < 10:
            print(f"  {C_YELLOW}File too small or empty{C_RESET}")
            return
        chunks = chunk_file(content, rel, chunk_mode=None)
        if not chunks:
            print(f"  {C_YELLOW}No chunks produced{C_RESET}")
            return
        for i, (chunk_text, chunk_idx) in enumerate(chunks):
            prev_text = chunks[i - 1][0][-200:] if i > 0 else ""
            next_text = chunks[i + 1][0][:200] if i < len(chunks) - 1 else ""
            neighbor_ctx = prev_text + ("\n...\n" if prev_text and next_text else "") + next_text
            tags = tagger.build_payload_tags(
                chunk_text, rel_path=rel,
                llm=enable_llm, zero_shot=enable_zero_shot,
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
                "source_type": "file",
                "doc_metadata": {},
                "chunk_index": chunk_idx,
                "source_mtime": mtime,
                "llm_tagged": bool(llm_type),
            })

    if not records:
        print(f"  {C_YELLOW}No indexable content{C_RESET}")
        clear_progress()
        return

    # Replace any existing chunks for this source
    vs.delete_by_source(source_key)
    inserted, _ = vs.store_batch(records)

    clear_progress()
    elapsed = time.time() - t_start
    print(f"  {C_GREEN}═══ Indexing Complete ═══{C_RESET}")
    print(f"  Stored:   {inserted} chunks")
    print(f"  Project:  {project}")
    print(f"  Time:     {elapsed:.1f}s")
    print()


def _llm_tag_recent(
    ingest_start_ts: float,
    total_hint: int = 0,
    print_bar=None,
    command: str = "ingest",
    projects: list[str] | None = None,
) -> int:
    """Phase 2: LLM-tag chunks that were just ingested (by timestamp).

    Scrolls chunks with timestamp >= ingest_start_ts, runs LLM classification,
    and updates tags + type in-place. Returns count of tagged chunks.
    """
    from qdrant_client import models as qm

    client, coll = vs._ensure_collection()
    scroll_filter = qm.Filter(must=[
        qm.FieldCondition(
            key="timestamp",
            range=qm.Range(gte=ingest_start_ts),
        ),
    ])

    tagged = 0
    offset = None
    t0 = time.time()
    batch_updates: list[tuple[str, dict, str]] = []
    SCROLL_BATCH = 100
    FLUSH_SIZE = 20

    projects = projects or []
    write_progress(command, 0, total_hint, 0, 0, t0, projects, phase="llm_tagging")

    while True:
        points, offset = client.scroll(
            collection_name=coll,
            limit=SCROLL_BATCH,
            offset=offset,
            scroll_filter=scroll_filter,
            with_payload=["content", "source", "tags"],
            with_vectors=False,
        )

        for pt in points:
            content = pt.payload.get("content", "")
            source = pt.payload.get("source", "")
            existing_tags = pt.payload.get("tags") or {}
            proj_hint = source.split("/", 1)[0] if "/" in source else ""

            new_tags = tagger.build_payload_tags(
                content,
                rel_path=source,
                llm=True,
                zero_shot=False,
                project_hint=proj_hint,
            )
            new_type = new_tags.pop("_llm_type", "")
            # Preserve deterministic fields set at ingest (e.g. conversations
            # set lang=conversation/layer=session/kind=qa). rel_path-based
            # derivation returns blanks for non-file sources and would wipe them.
            for k in ("lang", "layer", "kind"):
                if existing_tags.get(k) and not new_tags.get(k):
                    new_tags[k] = existing_tags[k]
            batch_updates.append((pt.id, new_tags, new_type))

            if len(batch_updates) >= FLUSH_SIZE:
                for point_id, tags, typ in batch_updates:
                    payload: dict = {"tags": tags}
                    if typ:
                        payload["type"] = typ
                        payload["llm_tagged"] = True
                    client.set_payload(
                        collection_name=coll,
                        payload=payload,
                        points=[point_id],
                    )
                tagged += len(batch_updates)
                batch_updates = []

                if print_bar and total_hint:
                    elapsed = time.time() - t0
                    print_bar(tagged, total_hint, elapsed)
                write_progress(command, tagged, total_hint, tagged, 0, t0, projects, phase="llm_tagging")

        if offset is None:
            break

    # Flush remaining
    for point_id, tags, typ in batch_updates:
        payload = {"tags": tags}
        if typ:
            payload["type"] = typ
            payload["llm_tagged"] = True
        client.set_payload(
            collection_name=coll,
            payload=payload,
            points=[point_id],
        )
    tagged += len(batch_updates)

    if print_bar and total_hint:
        print_bar(tagged, total_hint, time.time() - t0)
        print()
    write_progress(command, tagged, total_hint or tagged, tagged, 0, t0, projects, phase="llm_tagging")

    return tagged


def main():
    batch_size, rest = _parse_batch_size(sys.argv[1:], default=32)
    file_path, file_project, rest = _parse_file_flags(rest)

    # Single-file mode
    if file_path:
        ingest_single_file(file_path, file_project or "default", batch_size)
        return

    if len(rest) < 2:
        print(
            "Usage: python -m imprint.cli_index [--batch-size N] <target_dir> <dir:project> ...\n"
            "       python -m imprint.cli_index --file <path> --project <name>",
            file=sys.stderr,
        )
        sys.exit(1)

    target = rest[0]
    pairs = []
    for arg in rest[1:]:
        dir_path, project = arg.rsplit(":", 1)
        pairs.append((dir_path, project))

    # ── Phase 1: Scan ──────────────────────────────────────────
    print()
    print(f"  {C_CYAN}Scanning{C_RESET} {target} ...")
    print()

    project_files = {}
    grand_total = 0
    for dir_path, project in pairs:
        files = scan_dir(dir_path)
        project_files[project] = files
        grand_total += len(files)

    # ── Phase 2: Tree ──────────────────────────────────────────
    print_tree(target, project_files)
    print()
    print(f"  {C_BOLD}{grand_total}{C_RESET} files across {C_BOLD}{len(pairs)}{C_RESET} projects")
    print()

    if grand_total == 0:
        print("  Nothing to index.")
        return

    _progress_projects = list(project_files.keys())
    write_progress("ingest", 0, grand_total, 0, 0, time.time(), _progress_projects)
    _last_progress_write = time.time()

    # ── Phase 2.5: Index dependency lists from package.json ────
    # Check the target dir and each subdir for package.json
    checked = set()
    for dir_path, project in pairs:
        # Check the subdir itself
        dep_text = extract_dependencies(dir_path)
        if dep_text:
            vs.store(
                content=dep_text,
                project=project,
                type='architecture',
                source=f"{project}/package.json",
            )
            checked.add(dir_path)
        # Also check the parent (the target dir) — covers monorepo root
        parent = os.path.dirname(dir_path)
        if parent not in checked:
            checked.add(parent)
            dep_text = extract_dependencies(parent)
            if dep_text:
                parent_name = os.path.basename(parent)
                vs.store(
                    content=dep_text,
                    project=parent_name,
                    type='architecture',
                    source=f"{parent_name}/package.json",
                )

    # ── Phase 3: Read + chunk files (sequential) ────────────────
    # NOTE: parallel file reading via ThreadPoolExecutor was tried and
    # was *slower* in practice than sequential — the embed step holds the
    # GIL and dominates wall time, so parallel readers just queue up behind
    # it and add overhead. Keep this sequential.
    from imprint.chunker import chunk_file

    # Conservative batch size to avoid OOM on WSL2 / low-RAM systems.
    # Override via --batch-size N; higher = faster but more peak memory.
    BATCH_SIZE = batch_size

    # Zero-shot on by default; LLM opt-in replaces zero-shot.
    # Resolved through config (env > config.json > default).
    # LLM tagging is deferred to phase 2 (after all embeddings) to avoid
    # GPU contention between embedding model and LLM tagger.
    from imprint.config_schema import resolve
    enable_llm = resolve("tagger.llm")[0]
    if not enable_llm and tagger._get_llm_provider() == "local":
        _, _llm_source = resolve("tagger.llm")
        if _llm_source == "default":
            enable_llm = True
    enable_zero_shot = resolve("tagger.zero_shot")[0]

    def _chunks_to_records(chunks, project, rel, source_key, mtime, doc_meta, chunk_mode):
        """Convert a list of (chunk_text, chunk_idx) into record dicts."""
        source_type = "file" if not doc_meta.get("ocr") else "ocr"
        records = []
        for i, (chunk_text, chunk_idx) in enumerate(chunks):
            prev_text = chunks[i - 1][0][-200:] if i > 0 else ""
            next_text = chunks[i + 1][0][:200] if i < len(chunks) - 1 else ""
            neighbor_ctx = prev_text + ("\n...\n" if prev_text and next_text else "") + next_text
            tags = tagger.build_payload_tags(
                chunk_text,
                rel_path=rel,
                llm=None,
                zero_shot=enable_zero_shot,
                neighbor_context=neighbor_ctx,
                project_hint=project,
            )
            llm_type = tags.pop("_llm_type", "")
            mem_type = llm_type or "architecture"
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

    def read_and_chunk(args):
        """Read a file and chunk it. Returns list of record dicts or None.

        Each chunk gets its own structured tag payload derived by the tagger
        (deterministic lang/layer/kind + keyword-matched domain tags).

        Documents (pdf/docx/etc) route through imprint.extractors; the
        extractor returns extracted plain text + metadata, then we chunk
        the text via the standard pipeline. Extractors may return multiple
        docs (e.g. one per conversation in a ChatGPT export) — each is
        chunked independently.
        """
        project, rel, fpath = args
        try:
            ext = os.path.splitext(rel)[1].lower()
            is_doc = extractors.is_doc_extension(ext)

            if is_doc:
                try:
                    doc_list = extractors.dispatch_by_ext(fpath)
                except extractors.ExtractorUnavailable as e:
                    # Silent skip — user can install the dep later and re-run.
                    print(f"\n  [skip] {rel}: {e}", file=sys.stderr)
                    return None
                except extractors.ExtractionError as e:
                    print(f"\n  [skip] {rel}: {e}", file=sys.stderr)
                    return None

                mtime = os.path.getmtime(fpath)
                source_key = f"{project}/{rel}"
                all_records = []
                for extracted in doc_list:
                    content = extracted.text
                    if len(content.strip()) < 10:
                        continue
                    doc_meta = dict(extracted.metadata or {})
                    chunk_mode = extracted.chunk_mode or "prose"
                    chunks = chunk_file(content, rel, chunk_mode=chunk_mode)
                    if chunks:
                        all_records.extend(_chunks_to_records(
                            chunks, project, rel, source_key,
                            mtime, doc_meta, chunk_mode,
                        ))
                return all_records or None
            else:
                with open(fpath, 'r', errors='ignore') as f:
                    content = f.read()
                if len(content.strip()) < 10 or len(content) > 50000:
                    return None
                if _is_low_value(content, rel):
                    return None

                chunks = chunk_file(content, rel, chunk_mode=None)
                if not chunks:
                    return None
                mtime = os.path.getmtime(fpath)
                source_key = f"{project}/{rel}"
                return _chunks_to_records(
                    chunks, project, rel, source_key, mtime, {}, None,
                )
        except Exception as e:
            print(f"\n  [error] {rel}: {e}", file=sys.stderr)
            return None

    # Build flat list of (project, rel, fpath) for all files
    all_files = []
    for project, files in project_files.items():
        for rel, fpath in files:
            all_files.append((project, rel, fpath))

    # Pre-filter unchanged files + clean up stale/modified sources.
    #
    # Three cases for each source already in the DB:
    #   unchanged (same mtime) → skip entirely
    #   modified  (different mtime) → delete old chunks, re-index
    #   deleted   (file gone from disk) → delete old chunks
    known_mtimes = vs.get_source_mtimes()
    pre_skipped = 0
    stale_sources: list[str] = []

    # Build set of all current on-disk sources for projects we're indexing
    all_disk_sources = {f"{p}/{r}" for p, files in project_files.items() for r, _ in files}

    if known_mtimes:
        # Detect deleted files: in DB for a project we're indexing, but gone from disk
        indexing_projects = {p for p, _ in pairs}
        for src in known_mtimes:
            proj = src.split("/", 1)[0]
            if proj in indexing_projects and src not in all_disk_sources:
                stale_sources.append(src)

        # Filter unchanged, mark modified for cleanup
        filtered = []
        for project, rel, fpath in all_files:
            source_key = f"{project}/{rel}"
            try:
                fmtime = os.path.getmtime(fpath)
            except OSError:
                continue
            stored_mtime = known_mtimes.get(source_key, 0)
            if stored_mtime and abs(stored_mtime - fmtime) < 1.0:
                pre_skipped += 1
                continue
            if stored_mtime:
                stale_sources.append(source_key)
            filtered.append((project, rel, fpath))
        all_files = filtered

    # Delete old chunks for modified + deleted files
    if stale_sources:
        for src in stale_sources:
            vs.delete_by_source(src)

    cleaned = len(stale_sources)
    stored = 0
    skipped = pre_skipped
    processed = pre_skipped
    grand_total = grand_total  # bar total stays on full file count
    t_start = time.time()
    cancelled = False
    pending_batch = []

    def flush_batch():
        nonlocal stored
        if not pending_batch:
            return
        try:
            inserted, skipped_batch = vs.store_batch(pending_batch)
        except Exception as exc:
            from .embeddings import _is_gpu_error
            if _is_gpu_error(exc):
                import gc
                print("\n  [warn] GPU error during batch store, retrying...", file=sys.stderr)
                gc.collect()
                inserted, skipped_batch = vs.store_batch(pending_batch)
            else:
                raise
        stored += inserted
        pending_batch.clear()

    try:
        # Sequential read + chunk, flush to vector store in batches
        for args in all_files:
            result = read_and_chunk(args)
            processed += 1
            elapsed = time.time() - t_start
            print_bar(processed, grand_total, elapsed)

            now = time.time()
            if now - _last_progress_write >= 1.0:
                write_progress("ingest", processed, grand_total, stored, skipped, t_start, _progress_projects)
                _last_progress_write = now

            if result is None:
                skipped += 1
                continue

            pending_batch.extend(result)

            # Flush batch when large enough
            if len(pending_batch) >= BATCH_SIZE:
                flush_batch()

        # Flush remaining
        flush_batch()

    except KeyboardInterrupt:
        cancelled = True
        flush_batch()  # Save what we have
        clear_progress()
        print()
        print()
        print(f"  {C_YELLOW}Cancelled{C_RESET} — progress saved. Re-run to continue where you left off.")

    if not cancelled:
        print_bar(grand_total, grand_total, time.time() - t_start)
        print()

    # ── Phase 2: LLM tagging (sequenced after all embeddings) ──
    llm_tagged = 0
    if enable_llm and stored > 0 and not cancelled:
        print()
        print(f"  {C_CYAN}═══ LLM Tagging ═══{C_RESET}")
        llm_tagged = _llm_tag_recent(
            ingest_start_ts=t_start,
            total_hint=stored,
            print_bar=print_bar,
            command="ingest",
            projects=_progress_projects,
        )

    # ── Summary ────────────────────────────────────────────────
    elapsed = time.time() - t_start
    print()
    if cancelled:
        print(f"  {C_YELLOW}═══ Indexing Cancelled ═══{C_RESET}")
    else:
        print(f"  {C_GREEN}═══ Indexing Complete ═══{C_RESET}")
    from imprint.embeddings import get_gpu_retries, reset_gpu_retries
    gpu_retries = get_gpu_retries()
    reset_gpu_retries()
    print(f"  Stored:   {stored}")
    print(f"  Skipped:  {skipped}")
    if llm_tagged:
        print(f"  Tagged:   {llm_tagged} (LLM)")
    if cleaned:
        print(f"  Cleaned:  {cleaned} stale sources")
    if gpu_retries:
        print(f"  {C_DIM}GPU batch reductions: {gpu_retries} (memory pressure){C_RESET}")
    print(f"  Time:     {elapsed:.1f}s")
    if cancelled:
        print(f"  {C_DIM}Re-run to index remaining files (duplicates are skipped automatically){C_RESET}")
    else:
        clear_progress()
    print()


if __name__ == "__main__":
    main()
