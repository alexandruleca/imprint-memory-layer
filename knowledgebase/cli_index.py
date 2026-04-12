"""CLI helper for indexing directories into the knowledge base.

Usage: python -m knowledgebase.cli_index <target_dir> <dir1:project1> <dir2:project2> ...
"""

import os
import sys
import time

# Ensure the knowledgebase package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from knowledgebase import vectorstore as vs

EXTENSIONS = {
    '.md', '.txt', '.ts', '.js', '.py', '.go', '.json', '.yaml', '.yml',
    '.toml', '.cfg', '.ini', '.sh', '.sql', '.graphql', '.proto', '.rs',
    '.java', '.kt', '.swift', '.rb', '.php', '.css', '.scss', '.html',
    '.vue', '.svelte', '.jsx', '.tsx',
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


def scan_dir(dir_path):
    """Walk a directory and return list of (rel_path, full_path) for indexable files."""
    files = []
    for root, subdirs, fnames in os.walk(dir_path):
        subdirs[:] = [d for d in subdirs if not d.startswith('.') and d not in SKIP_DIRS]
        for fname in fnames:
            if os.path.splitext(fname)[1].lower() in EXTENSIONS:
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


def print_bar(processed, total, elapsed, current_file=""):
    """Print an in-place progress bar."""
    pct = processed / total if total else 1
    filled = int(BAR_WIDTH * pct)
    bar = "█" * filled + "░" * (BAR_WIDTH - filled)

    if processed > 0 and pct < 1:
        eta = elapsed / pct * (1 - pct)
        time_str = f"eta {int(eta)}s"
    else:
        time_str = f"{elapsed:.1f}s"

    name = current_file[:30] if current_file else ""
    line = f"  {bar} {int(pct * 100):3d}% {processed}/{total}  {time_str}  {C_DIM}{name}{C_RESET}"
    print(f"\r{line:<110}", end="", flush=True)


def main():
    if len(sys.argv) < 3:
        print("Usage: python -m knowledgebase.cli_index <target_dir> <dir:project> ...", file=sys.stderr)
        sys.exit(1)

    target = sys.argv[1]
    pairs = []
    for arg in sys.argv[2:]:
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

    # ── Phase 3: Index ─────────────────────────────────────────
    stored = 0
    skipped = 0
    processed = 0
    t_start = time.time()
    cancelled = False

    try:
        for project, files in project_files.items():
            for rel, fpath in files:
                processed += 1
                elapsed = time.time() - t_start
                print_bar(processed, grand_total, elapsed, rel)
                try:
                    with open(fpath, 'r', errors='ignore') as f:
                        content = f.read()
                    if len(content.strip()) < 10 or len(content) > 50000:
                        skipped += 1
                        continue
                    from knowledgebase.chunker import chunk_file
                    chunks = chunk_file(content, rel)
                    if not chunks:
                        skipped += 1
                        continue
                    mtime = os.path.getmtime(fpath)
                    source_key = f"{project}/{rel}"
                    for chunk_text, chunk_idx in chunks:
                        vs.store(
                            content=chunk_text,
                            project=project,
                            type='architecture',
                            source=source_key,
                            chunk_index=chunk_idx,
                            source_mtime=mtime,
                        )
                    stored += 1
                except KeyboardInterrupt:
                    raise
                except Exception:
                    skipped += 1
    except KeyboardInterrupt:
        cancelled = True
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
