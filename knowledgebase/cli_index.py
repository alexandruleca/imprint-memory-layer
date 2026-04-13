"""CLI helper for indexing directories into the knowledge base.

Usage:
    python -m knowledgebase.cli_index [--batch-size N] <target_dir> <dir1:project1> <dir2:project2> ...

Options:
    --batch-size N    Chunks to buffer before each embed+insert (default: 32).
                      Higher = faster but more peak memory. Sweet spot 32-64
                      on int8 model; drop to 8-16 if RAM tight.
"""

import os
import sys
import time

# Ensure the knowledgebase package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from knowledgebase import tagger, vectorstore as vs

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

# Skip these — low value for knowledge context
SKIP_EXTENSIONS = {
    '.css', '.scss', '.sass', '.less', '.styl',         # Styling
    '.svg', '.png', '.jpg', '.jpeg', '.gif', '.ico',    # Assets
    '.lock', '.map', '.min.js', '.min.css',             # Generated
    '.d.ts',                                            # Type declarations
    '.snap',                                            # Test snapshots
    '.env', '.env.local', '.env.example',               # Env files
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
    'node_modules', '__pycache__', '.venv', 'dist', 'build', '.git',
    '.next', '.nuxt', 'vendor', 'coverage', '.cache', '.turbo',
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
    files = []
    for root, subdirs, fnames in os.walk(dir_path):
        subdirs[:] = [d for d in subdirs if not d.startswith('.') and d not in SKIP_DIRS]
        for fname in fnames:
            if fname in SKIP_FILES:
                continue
            ext = os.path.splitext(fname)[1].lower()
            if ext in SKIP_EXTENSIONS:
                continue
            if ext not in EXTENSIONS:
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


def main():
    batch_size, rest = _parse_batch_size(sys.argv[1:], default=32)

    if len(rest) < 2:
        print(
            "Usage: python -m knowledgebase.cli_index [--batch-size N] <target_dir> <dir:project> ...",
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
    from knowledgebase.chunker import chunk_file

    # Conservative batch size to avoid OOM on WSL2 / low-RAM systems.
    # Override via --batch-size N; higher = faster but more peak memory.
    BATCH_SIZE = batch_size

    # Opt-in tag sources. Zero-shot + LLM are off by default because they
    # cost extra compute / $$ at ingest time. Enable via env var.
    enable_llm = os.environ.get("KNOWLEDGE_LLM_TAGS", "0") == "1"
    enable_zero_shot = os.environ.get("KNOWLEDGE_ZERO_SHOT_TAGS", "0") == "1"

    def read_and_chunk(args):
        """Read a file and chunk it. Returns list of record dicts or None.

        Each chunk gets its own structured tag payload derived by the tagger
        (deterministic lang/layer/kind + keyword-matched domain tags).
        """
        project, rel, fpath = args
        try:
            with open(fpath, 'r', errors='ignore') as f:
                content = f.read()
            if len(content.strip()) < 10 or len(content) > 50000:
                return None
            if _is_low_value(content, rel):
                return None
            chunks = chunk_file(content, rel)
            if not chunks:
                return None
            mtime = os.path.getmtime(fpath)
            source_key = f"{project}/{rel}"
            records = []
            for chunk_text, chunk_idx in chunks:
                tags = tagger.build_payload_tags(
                    chunk_text,
                    rel_path=rel,
                    llm=enable_llm,
                    zero_shot=enable_zero_shot,
                )
                records.append({
                    "content": chunk_text,
                    "project": project,
                    "type": "architecture",
                    "tags": tags,
                    "source": source_key,
                    "chunk_index": chunk_idx,
                    "source_mtime": mtime,
                })
            return records
        except Exception:
            return None

    # Build flat list of (project, rel, fpath) for all files
    all_files = []
    for project, files in project_files.items():
        for rel, fpath in files:
            all_files.append((project, rel, fpath))

    # Pre-filter: skip files whose content+mtime already in DB. Saves the
    # read+chunk+tokenize work on re-runs (content-hash dedup downstream
    # would catch them anyway, but only after reading + chunking + embedding).
    known_mtimes = vs.get_source_mtimes()
    pre_skipped = 0
    if known_mtimes:
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
            filtered.append((project, rel, fpath))
        all_files = filtered

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
        inserted, skipped_batch = vs.store_batch(pending_batch)
        stored += inserted
        pending_batch.clear()

    try:
        # Sequential read + chunk, flush to vector store in batches
        for args in all_files:
            result = read_and_chunk(args)
            processed += 1
            elapsed = time.time() - t_start
            print_bar(processed, grand_total, elapsed)

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
    else:
        print(f"  {C_GREEN}═══ Indexing Complete ═══{C_RESET}")
    print(f"  Stored:   {stored}")
    print(f"  Skipped:  {skipped}")
    print(f"  Time:     {elapsed:.1f}s")
    if cancelled:
        print(f"  {C_DIM}Re-run to index remaining files (duplicates are skipped automatically){C_RESET}")
    print()


if __name__ == "__main__":
    main()
