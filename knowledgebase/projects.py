"""Project detection — finds real project roots and extracts canonical names.

Instead of using top-level directory names, this scans for manifest files
(package.json, go.mod, pyproject.toml, etc.) to identify actual projects.
The canonical name is used for cross-machine identity — the same project
at different paths on different machines gets the same project name.
"""

import json
import os
import re
from pathlib import Path

# Manifest files that indicate a project root, in priority order
MANIFESTS = [
    "package.json",
    "go.mod",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "Cargo.toml",
    "composer.json",
    "pom.xml",
    "build.gradle",
    "Gemfile",
    "mix.exs",
    "requirements.txt",  # Python fallback — no name extraction, uses dir name
]

# Directories to skip entirely
SKIP_DIRS = {
    "node_modules", "__pycache__", ".venv", "dist", "build", ".git",
    ".next", ".nuxt", "vendor", "coverage", ".cache", ".turbo",
    ".idea", ".vscode", ".gitlab", "target", ".cargo",
}


def find_projects(root_dir: str, max_depth: int = 4) -> list[dict]:
    """Recursively find project roots under root_dir.

    Returns list of {name, path, manifest, type} dicts.
    Name is the canonical project identity (from manifest, not path).
    """
    root = Path(root_dir).resolve()
    projects = []
    seen_paths = set()

    def _walk(current: Path, depth: int):
        if depth > max_depth:
            return
        if not current.is_dir():
            return
        if current.name in SKIP_DIRS or current.name.startswith("."):
            return

        # Check for manifest files at this level
        for manifest in MANIFESTS:
            manifest_path = current / manifest
            if manifest_path.exists():
                name = _extract_name(manifest_path, manifest, current)
                real = str(current.resolve())
                if real not in seen_paths:
                    seen_paths.add(real)
                    projects.append({
                        "name": name,
                        "path": str(current),
                        "manifest": manifest,
                        "type": _project_type(manifest),
                    })
                return  # Don't recurse into sub-projects (e.g. monorepo packages)

        # No manifest here — recurse into subdirs
        try:
            for child in sorted(current.iterdir()):
                if child.is_dir():
                    _walk(child, depth + 1)
        except PermissionError:
            pass

    _walk(root, 0)
    return projects


def _extract_name(manifest_path: Path, manifest: str, project_dir: Path) -> str:
    """Extract canonical project name from a manifest file."""
    try:
        if manifest == "package.json":
            data = json.loads(manifest_path.read_text())
            name = data.get("name", "")
            # Strip npm scope prefix (@org/name → name)
            if "/" in name:
                name = name.split("/")[-1]
            return name or project_dir.name

        if manifest == "go.mod":
            content = manifest_path.read_text()
            match = re.search(r"^module\s+(.+)$", content, re.MULTILINE)
            if match:
                # Use last path segment: github.com/user/repo → repo
                return match.group(1).strip().rsplit("/", 1)[-1]

        if manifest == "pyproject.toml":
            content = manifest_path.read_text()
            match = re.search(r'name\s*=\s*"([^"]+)"', content)
            if match:
                return match.group(1)

        if manifest == "Cargo.toml":
            content = manifest_path.read_text()
            match = re.search(r'name\s*=\s*"([^"]+)"', content)
            if match:
                return match.group(1)

        if manifest == "composer.json":
            data = json.loads(manifest_path.read_text())
            name = data.get("name", "")
            if "/" in name:
                name = name.split("/")[-1]
            return name or project_dir.name

    except Exception:
        pass

    return project_dir.name


def _project_type(manifest: str) -> str:
    """Infer project type from manifest."""
    return {
        "package.json": "node",
        "go.mod": "go",
        "pyproject.toml": "python",
        "setup.py": "python",
        "setup.cfg": "python",
        "Cargo.toml": "rust",
        "composer.json": "php",
        "pom.xml": "java",
        "build.gradle": "java",
        "Gemfile": "ruby",
        "mix.exs": "elixir",
        "requirements.txt": "python",
    }.get(manifest, "unknown")
