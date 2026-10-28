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
from imprint.source_state import (allowed_source, fingerprint, stamp_records, unchanged, missing_sources,
                                  extraction_profile, no_text_reason, known_no_text, remember_no_text)
from imprint._cli_signals import install as _install_signals

_install_signals()

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
# key to an empty string to exclude documents. Images/audio have separate gates.


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


def _audio_exts() -> set[str]:
    from .config_schema import resolve
    from .audio_ingest import AUDIO_EXTENSIONS
    return AUDIO_EXTENSIONS if resolve('audio.enabled')[0] else set()


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


def _attach_source_part(document, records, rel, *, base=0, code=False):
    from . import source_memory
    source_memory.attach(document, records, prefix=f"[{rel}]\n", code=code)
    for record in records:
        record['source_start'] += base
        record['source_end'] += base
        record.pop('_source_document', None)
    return records


def _empty_snapshot(document, source, project, provenance):
    from . import projects, source_snapshots
    placed = projects.place([{'project': project, 'source': source, **provenance}])[0]
    prepared = source_snapshots.prepare(document, [], source, placed['project'])
    prepared['metadata']['provenance'] = provenance
    source_snapshots.publish(prepared)


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


def _git_tracked_files(dir_path: str) -> set[str] | None:
    """Files git considers tracked or untracked-but-not-ignored, as POSIX
    paths relative to dir_path. Returns None if dir_path isn't in a git repo
    (or git isn't installed), so callers fall back to the hardcoded filters.

    This defers .gitignore / .git/info/exclude / global excludesFile handling
    to git itself — no in-repo gitignore reimplementation.
    """
    import subprocess
    try:
        proc = subprocess.run(
            ["git", "-C", dir_path, "ls-files",
             "--cached", "--others", "--exclude-standard", "-z"],
            capture_output=True, timeout=30, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    # Names as the filesystem layer decodes them, not in the locale's
    # encoding (cp1252 on Windows cannot decode every UTF-8 path git prints).
    return {os.fsdecode(name) for name in proc.stdout.split(b"\0") if name}


def scan_dir(dir_path, exclude_paths=None):
    """Walk a directory and return list of (rel_path, full_path) for indexable files.

    exclude_paths: optional list of directories to skip entirely (e.g. sub-project
    roots when scanning a root-fallback project so manifested children aren't
    double-indexed).
    """
    if not os.path.isdir(dir_path):
        raise NotADirectoryError(f"Index root is not a readable directory: {dir_path}")
    # Resolve doc/image gates once per scan — avoids reloading config per file.
    doc_exts = _enabled_doc_formats()
    ocr_imgs = _ocr_image_exts()
    effective_exts = EXTENSIONS | doc_exts | ocr_imgs | _audio_exts()
    # Image exts are in SKIP_EXTENSIONS by default; lift that gate when OCR on.
    skip_exts = SKIP_EXTENSIONS - ocr_imgs

    # Git-aware filter — if dir_path is a git repo, only index files git sees
    # (tracked or untracked-but-not-ignored). Skips gitignored dirs like data/
    # and benchmark/results/ without reimplementing gitignore semantics.
    git_files = _git_tracked_files(dir_path)

    exclude_resolved = {os.path.realpath(p) for p in (exclude_paths or [])}

    files = []
    for root, subdirs, fnames in os.walk(dir_path, onerror=lambda error: (_ for _ in ()).throw(error)):
        subdirs[:] = [
            d for d in subdirs
            if d not in SKIP_DIRS
            and not d.startswith('.')
            and os.path.realpath(os.path.join(root, d)) not in exclude_resolved
        ]
        for fname in fnames:
            if fname in SKIP_FILES:
                continue
            # Any .env* file (.env, .env.local, .env.production, etc.) —
            # may contain secrets, never index.
            if fname == '.env' or fname.startswith('.env.'):
                continue
            ext = os.path.splitext(fname)[1].lower()
            if ext in skip_exts or fname.endswith(('.min.js', '.min.css', '.d.ts')):
                continue
            if ext not in effective_exts:
                continue
            fpath = os.path.join(root, fname)
            if not allowed_source(fpath, dir_path):
                continue
            rel = os.path.relpath(fpath, dir_path).replace(os.sep, "/")
            if git_files is not None and rel.replace(os.sep, "/") not in git_files:
                continue
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


def ingest_single_file(file_path: str, project: str, batch_size: int = 32, *, force: bool = False):
    """Index a single file into the vector store."""
    from imprint.chunker import chunk_file
    from imprint.config_schema import resolve

    if not os.path.isfile(file_path):
        print(f"  {C_YELLOW}File not found:{C_RESET} {file_path}")
        sys.exit(1)

    from . import workspace_policy as policy
    policy.ensure_ingest_paths([file_path])
    if policy.is_excluded(path=file_path, source=f'{project}/{os.path.basename(file_path)}'):
        raise ValueError('Source is excluded after purge; restore its workspace exclusion before ingesting')
    policy.map_project(project, os.path.dirname(os.path.abspath(file_path)))

    if not allowed_source(file_path):
        raise ValueError("Secret-bearing files cannot be indexed")
    snapshot = fingerprint(file_path)
    rel = os.path.basename(file_path)
    print()
    print(f"  {C_CYAN}Indexing{C_RESET} {rel} → project={project}")
    print()
    write_progress("ingest", 0, 1, 0, 0, time.time(), [project])

    # LLM tagging is left to the background backlog (ADR 0067).
    from imprint.tag_backlog import deferring, report
    pending = deferring()
    enable_zero_shot = resolve("tagger.zero_shot")[0]

    ext = os.path.splitext(rel)[1].lower()
    is_doc = extractors.is_doc_extension(ext)
    t_start = time.time()

    records = []
    source_document = None
    from . import source_memory
    mtime = os.path.getmtime(file_path)
    source_key = f"{project}/{rel}"
    profile = extraction_profile(file_path)
    reason = None

    if is_doc:
        # A document already found to have no text under this profile is not read again.
        reason = None if force else known_no_text(snapshot["source_hash"], profile)
        doc_list = []
        try:
            if reason is None:
                doc_list = extractors.dispatch_by_ext(file_path, **({'force': True} if force else {}))
        except extractors.ExtractorUnavailable as e:
            print(f"  {C_YELLOW}Extractor unavailable:{C_RESET} {e}")
            sys.exit(1)
        except extractors.ExtractionError as e:
            print(f"  {C_YELLOW}Extraction failed:{C_RESET} {e}")
            sys.exit(1)

        doc_list = [item for item in doc_list if item.text.strip()]
        source_document, segments = source_memory.extracted_documents(doc_list)
        for extracted, (base, content) in zip(doc_list, segments):
            part_start = len(records)
            if not content.strip():
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
                    llm=False, zero_shot=enable_zero_shot,
                    neighbor_context=neighbor_ctx, project_hint=project,
                )
                tags.pop("_llm_type", None)
                records.append({
                    "content": chunk_text,
                    "project": project,
                    "type": "architecture",
                    "tags": tags,
                    "source": source_key,
                    "source_type": "audio" if doc_meta.get('audio') else "file",
                    "doc_metadata": doc_meta,
                    "chunk_index": len(records),
                    "source_mtime": mtime,
                    "tag_pending": pending,
                })
            _attach_source_part({'text': content}, records[part_start:], rel, base=base, code=chunk_mode == 'code')
    else:
        max_bytes = max(1, int(resolve("ingest.max_file_bytes")[0]))
        if snapshot["source_size"] > max_bytes:
            raise ValueError(f"File exceeds ingest.max_file_bytes ({max_bytes}); prior chunks retained")
        with open(file_path, 'r', encoding='utf-8', newline='') as f:
            content = f.read(max_bytes + 1)
        source_document = source_memory.document(content, kind='text')
        content = source_document['text']
        if not content:
            stamp_records([], file_path, snapshot)
            held = vs.get_source_summary(source_key)
            if held:
                vs.delete_by_source(source_key, project=held["project"])
            _empty_snapshot(source_document, source_key, project, snapshot)
            clear_progress()
            print("  Empty file: source snapshot saved; prior chunks cleared")
            return
        chunks = chunk_file(content, rel, chunk_mode=None)
        if not chunks:
            raise ValueError("Chunker produced no chunks for nonempty text; prior chunks retained")
        for i, (chunk_text, chunk_idx) in enumerate(chunks):
            prev_text = chunks[i - 1][0][-200:] if i > 0 else ""
            next_text = chunks[i + 1][0][:200] if i < len(chunks) - 1 else ""
            neighbor_ctx = prev_text + ("\n...\n" if prev_text and next_text else "") + next_text
            tags = tagger.build_payload_tags(
                chunk_text, rel_path=rel,
                llm=False, zero_shot=enable_zero_shot,
                neighbor_context=neighbor_ctx, project_hint=project,
            )
            tags.pop("_llm_type", None)
            records.append({
                "content": chunk_text,
                "project": project,
                "type": "architecture",
                "tags": tags,
                "source": source_key,
                "source_type": "file",
                "doc_metadata": {},
                "chunk_index": chunk_idx,
                "source_mtime": mtime,
                "tag_pending": pending,
            })
        from .chunker import _CODE_LANG
        _attach_source_part(source_document, records, rel, code=os.path.splitext(rel)[1].lower() in _CODE_LANG)

    if not records:
        # Nothing to index is an outcome, not a failure. Chunks of an earlier
        # version stay: the document may still hold that text, unreadable now.
        stamp_records([], file_path, snapshot)
        reason = reason or no_text_reason(file_path, profile)
        remember_no_text(snapshot["source_hash"], profile, reason)
        clear_progress()
        held = vs.get_source_summary(source_key)
        kept = f"; kept {held['chunk_count']} chunks of an earlier version" if held else ""
        print(f"  {C_YELLOW}Nothing to index:{C_RESET} {rel}: {reason}{kept}")
        print()
        return

    # Replace any existing chunks for this source
    records = stamp_records(records, file_path, snapshot)
    records[0]['_source_document'] = source_document
    inserted, _ = vs.replace_source(records, source_key, project, **({"force": True} if force else {}))

    clear_progress()
    elapsed = time.time() - t_start
    print(f"  {C_GREEN}═══ Indexing Complete ═══{C_RESET}")
    print(f"  Stored:   {inserted} chunks")
    print(f"  Project:  {project}")
    print(f"  Time:     {elapsed:.1f}s")
    report(inserted)
    print()


def main(command: str = "ingest"):
    # A long-running operation must not follow a dashboard workspace switch.
    from imprint import config
    previous = os.environ.get("IMPRINT_WORKSPACE")
    os.environ["IMPRINT_WORKSPACE"] = config.get_active_workspace()
    try:
        return _main(command)
    finally:
        if previous is None:
            os.environ.pop("IMPRINT_WORKSPACE", None)
        else:
            os.environ["IMPRINT_WORKSPACE"] = previous


def _main(command: str = "ingest"):
    batch_size, rest = _parse_batch_size(sys.argv[1:], default=32)
    force = "--force" in rest
    rest = [arg for arg in rest if arg != "--force"]
    file_path, file_project, rest = _parse_file_flags(rest)

    # Single-file mode
    if file_path:
        ingest_single_file(file_path, file_project or "default", batch_size, force=force)
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

    from . import workspace_policy as policy
    policy.ensure_ingest_paths([target, *(path for path, _ in pairs)])
    for path, project in pairs:
        policy.map_project(project, path)
    exclusion_rules = policy.exclusions()

    # Recover per-project exclude_paths (root-fallback projects need to skip
    # manifested sub-project roots so files aren't double-indexed). The Go
    # wire format only passes path:name, so re-invoke find_projects to get
    # the exclude_paths field.
    from imprint.projects import find_projects
    _project_meta = {
        os.path.realpath(p["path"]): p
        for p in find_projects(target)
    }

    # ── Phase 1: Scan ──────────────────────────────────────────
    print()
    print(f"  {C_CYAN}Scanning{C_RESET} {target} ...")
    print()

    project_files = {}
    grand_total = 0
    for dir_path, project in pairs:
        meta = _project_meta.get(os.path.realpath(dir_path), {})
        excl = meta.get("exclude_paths", [])
        files = scan_dir(dir_path, exclude_paths=excl)
        files = [(rel, path) for rel, path in files if not policy.is_excluded(
            path=path, source=f'{project}/{rel}', rules=exclusion_rules)]
        project_files[project] = files
        grand_total += len(files)

    # ── Phase 2: Tree ──────────────────────────────────────────
    print_tree(target, project_files)
    print()
    print(f"  {C_BOLD}{grand_total}{C_RESET} files across {C_BOLD}{len(pairs)}{C_RESET} projects")
    print()

    _progress_projects = list(project_files.keys())
    write_progress(command, 0, grand_total, 0, 0, time.time(), _progress_projects)
    _last_progress_write = time.time()

    # Dependency manifests are indexed through the same source lifecycle as files.

    # ── Phase 3: Read + chunk files (sequential) ────────────────
    # NOTE: parallel file reading via ThreadPoolExecutor was tried and
    # was *slower* in practice than sequential — the embed step holds the
    # GIL and dominates wall time, so parallel readers just queue up behind
    # it and add overhead. Keep this sequential.
    from imprint.chunker import chunk_file

    # Conservative batch size to avoid OOM on WSL2 / low-RAM systems.
    # Override via --batch-size N; higher = faster but more peak memory.
    BATCH_SIZE = batch_size

    # Zero-shot on by default, resolved through config (env > config.json >
    # default). LLM tagging is not run here: with tagger.llm on, each memory
    # is marked for the background tagging backlog instead (ADR 0067).
    from imprint.config_schema import resolve
    from imprint.tag_backlog import deferring, report
    pending = deferring()
    enable_zero_shot = resolve("tagger.zero_shot")[0]
    max_file_bytes = max(1, int(resolve("ingest.max_file_bytes")[0]))
    failures = []

    def _chunks_to_records(chunks, project, rel, source_key, mtime, doc_meta, chunk_mode):
        """Convert a list of (chunk_text, chunk_idx) into record dicts."""
        source_type = "audio" if doc_meta.get('audio') else ("file" if not doc_meta.get("ocr") else "ocr")
        records = []
        for i, (chunk_text, chunk_idx) in enumerate(chunks):
            prev_text = chunks[i - 1][0][-200:] if i > 0 else ""
            next_text = chunks[i + 1][0][:200] if i < len(chunks) - 1 else ""
            neighbor_ctx = prev_text + ("\n...\n" if prev_text and next_text else "") + next_text
            tags = tagger.build_payload_tags(
                chunk_text,
                rel_path=rel,
                llm=False,
                zero_shot=enable_zero_shot,
                neighbor_context=neighbor_ctx,
                project_hint=project,
            )
            tags.pop("_llm_type", None)
            records.append({
                "content": chunk_text,
                "project": project,
                "type": "architecture",
                "tags": tags,
                "source": source_key,
                "source_type": source_type,
                "doc_metadata": doc_meta,
                "chunk_index": chunk_idx,
                "source_mtime": mtime,
                "tag_pending": pending,
            })
        return records

    prepared_documents = {}

    def read_and_chunk(args):
        """Read a file and chunk it. Returns (records, no_text_reason), or None
        when reading failed. A file with nothing to index returns no records:
        an empty text file with no reason, a document without text with one.

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
            snapshot = fingerprint(fpath)
            ext = os.path.splitext(rel)[1].lower()
            is_doc = extractors.is_doc_extension(ext)

            if is_doc:
                try:
                    doc_list = extractors.dispatch_by_ext(fpath, **({'force': True} if force else {}))
                except extractors.ExtractorUnavailable as e:
                    raise RuntimeError(str(e)) from e
                except extractors.ExtractionError as e:
                    raise RuntimeError(str(e)) from e

                mtime = os.path.getmtime(fpath)
                source_key = f"{project}/{rel}"
                from . import source_memory
                doc_list = [item for item in doc_list if item.text.strip()]
                source_document, segments = source_memory.extracted_documents(doc_list)
                all_records = []
                for extracted, (base, content) in zip(doc_list, segments):
                    if not content.strip():
                        continue
                    doc_meta = dict(extracted.metadata or {})
                    chunk_mode = extracted.chunk_mode or "prose"
                    chunks = chunk_file(content, rel, chunk_mode=chunk_mode)
                    if chunks:
                        part_records = _chunks_to_records(chunks, project, rel, source_key, mtime, doc_meta, chunk_mode)
                        _attach_source_part({'text': content}, part_records, rel, base=base, code=chunk_mode == 'code')
                        all_records.extend(part_records)
                        for index, record in enumerate(all_records):
                            record["chunk_index"] = index
                if not all_records:
                    stamp_records([], fpath, snapshot)
                    file_profile = extraction_profile(fpath)
                    reason = no_text_reason(fpath, file_profile)
                    remember_no_text(snapshot["source_hash"], file_profile, reason)
                    return [], reason
                all_records[0]['_source_document'] = source_document
                return stamp_records(all_records, fpath, snapshot), ""
            else:
                if snapshot["source_size"] > max_file_bytes:
                    raise ValueError(f"File exceeds ingest.max_file_bytes ({max_file_bytes}); prior chunks retained")
                with open(fpath, 'r', encoding='utf-8', newline='') as f:
                    content = f.read(max_file_bytes + 1)
                from . import source_memory
                source_document = source_memory.document(content, kind='text')
                content = source_document['text']
                source_key = f"{project}/{rel}"
                if not content:
                    prepared_documents[source_key] = (source_document, snapshot)
                    return stamp_records([], fpath, snapshot), ""
                chunks = chunk_file(content, rel, chunk_mode=None)
                if not chunks:
                    raise ValueError("Chunker produced no chunks for nonempty text")
                mtime = os.path.getmtime(fpath)
                source_key = f"{project}/{rel}"
                records = _chunks_to_records(chunks, project, rel, source_key, mtime, {}, None)
                from .chunker import _CODE_LANG
                _attach_source_part(source_document, records, rel, code=ext in _CODE_LANG)
                records[0]['_source_document'] = source_document
                return stamp_records(records, fpath, snapshot), ""
        except Exception as e:
            failures.append(rel)
            print(f"\n  [error] {rel}: {e}", file=sys.stderr)
            return None

    # Build flat list of (project, rel, fpath) for all files
    all_files = []
    for project, files in project_files.items():
        for rel, fpath in files:
            all_files.append((project, rel, fpath))

    # Compare complete byte fingerprints, including timestamp-preserving edits.
    # A document already found to have no text is read again only once its
    # bytes or the extraction profile (OCR settings and tools) changed.
    profile = extraction_profile()
    known_states = vs.get_source_states()
    pre_skipped = 0
    known_no_text_count = 0
    no_text = []
    filtered = []
    for project, rel, fpath in all_files:
        source_key = f"{project}/{rel}"
        current = fingerprint(fpath)
        if not force and unchanged(current, known_states.get(source_key)):
            pre_skipped += 1
        elif (not force and extractors.is_doc_extension(os.path.splitext(rel)[1])
              and known_no_text(current["source_hash"], extraction_profile(fpath))):
            known_no_text_count += 1
        else:
            filtered.append((project, rel, fpath))
    all_files = filtered
    stale_sources = missing_sources(known_states, [path for path, _ in pairs])
    cleaned = 0
    stored = 0
    skipped = pre_skipped + known_no_text_count
    processed = skipped
    grand_total = grand_total  # bar total stays on full file count
    t_start = time.time()
    cancelled = False
    pending_batch = []

    def flush_batch():
        nonlocal stored
        if not pending_batch:
            return
        grouped = {}
        for record in pending_batch:
            grouped.setdefault((record["project"], record["source"]), []).append(record)
        for (project, source), records in grouped.items():
            inserted, _ = vs.replace_source(records, source, project, **({"force": True} if force else {}))
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
                write_progress(command, processed, grand_total, stored, skipped, t_start, _progress_projects)
                _last_progress_write = now

            if result is None:
                skipped += 1
                continue

            records, reason = result
            project, rel, _ = args
            source_key = f"{project}/{rel}"
            if reason:
                # Nothing to index, not a failure. Chunks of an earlier version
                # stay: the document may still hold that text, unreadable now.
                skipped += 1
                no_text.append((rel, reason, source_key in known_states))
                continue
            if not records:
                # A verified empty file is an explicit deletion of its old chunks.
                if source_key in known_states:
                    vs.delete_by_source(source_key, project=known_states[source_key].get("project", ""))
                    cleaned += 1
                if source_key in prepared_documents:
                    source_document, provenance = prepared_documents.pop(source_key)
                    _empty_snapshot(source_document, source_key, project, provenance)
                continue
            pending_batch.extend(records)

            # Flush batch when large enough
            if len(pending_batch) >= BATCH_SIZE:
                flush_batch()

        # Flush remaining
        flush_batch()
        for source in stale_sources:
            project = known_states[source].get("project", "")
            if vs.delete_by_source(source, project=project):
                cleaned += 1

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

    # ── Summary ────────────────────────────────────────────────
    elapsed = time.time() - t_start
    print()
    if cancelled:
        print(f"  {C_YELLOW}═══ Indexing Cancelled ═══{C_RESET}")
    elif failures:
        print(f"  {C_YELLOW}═══ Indexing Incomplete ═══{C_RESET}")
    else:
        print(f"  {C_GREEN}═══ Indexing Complete ═══{C_RESET}")
    print(f"  Stored:   {stored}")
    print(f"  Skipped:  {skipped}")
    if no_text or known_no_text_count:
        print(f"  No text:  {len(no_text) + known_no_text_count} documents with nothing to index "
              f"(retried once the file or the OCR settings change)")
        for rel, reason, kept in no_text:
            print(f"    {rel}: {reason}{'; kept the chunks of its earlier version' if kept else ''}")
    if failures:
        print(f"  Failed:   {len(failures)} (prior chunks retained; fix errors and retry)")
    if cleaned:
        print(f"  Cleaned:  {cleaned} stale sources")
    print(f"  Time:     {elapsed:.1f}s")
    report(stored)
    if cancelled:
        print(f"  {C_DIM}Re-run to index remaining files (duplicates are skipped automatically){C_RESET}")
    else:
        clear_progress()
    print()

    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
