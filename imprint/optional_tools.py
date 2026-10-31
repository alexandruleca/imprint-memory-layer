"""Optional ingestion tools that Imprint installs when their setting is on.

``ingest.use_kreuzberg`` and ``ingest.use_obscura`` name tools the base
install leaves out. Turning one on asks for it (for kreuzberg it also accepts
its Elastic License 2.0), and Imprint installs it itself (ADR 0121):
kreuzberg from ``requirements/kreuzberg.lock.txt`` into Imprint's venv, and
obscura's pinned release binary into ``data/obscura-bin/``. Both are checked
after installing.

Setup installs every enabled tool, the dashboard installs one in the
background when it is switched on, and an extractor that finds its tool
missing asks for one install per process. A failure is reported with the
manual fallback; extraction then falls back to the native extractors.

``python -m imprint.optional_tools`` installs every enabled tool that is
missing and prints one line per enabled tool, for ``imprint setup``.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import urllib.request
import zipfile
from pathlib import Path

from . import config
from .downloads import expected_digest, verify_archive

SETTINGS = {"kreuzberg": "ingest.use_kreuzberg", "obscura": "ingest.use_obscura"}
OBSCURA_VERSION = "v0.2.5"
REQUIREMENTS = Path(__file__).resolve().parent.parent / "requirements"
_INSTALL_TIMEOUT_S = 600

_lock = threading.Lock()
_attempted: dict[str, bool] = {}  # tool -> whether its one install worked
_state: dict[str, str] = {}  # tool -> "installing" or the last failure


class InstallFailed(RuntimeError):
    pass


def enabled(tool: str) -> bool:
    try:
        from .config_schema import resolve
        return bool(resolve(SETTINGS[tool])[0])
    except Exception:
        return False


def _machine() -> str:
    return {"amd64": "x86_64", "arm64": "aarch64"}.get(platform.machine().lower(), platform.machine().lower())


def unsupported(tool: str) -> str:
    """Why this platform has no build of tool; empty when it has one."""
    system, machine = platform.system().lower(), _machine()
    if tool == "kreuzberg":
        # PyPI publishes kreuzberg wheels for these only (requirements/kreuzberg.txt).
        builds = {("linux", "x86_64"), ("linux", "aarch64"), ("darwin", "aarch64"), ("windows", "x86_64")}
    else:
        builds = set(_OBSCURA_ASSETS)
    if (system, machine) in builds:
        return ""
    return f"{tool} publishes no build for {platform.system()} {platform.machine()}"


def manual_hint(tool: str) -> str:
    if tool == "kreuzberg":
        return (f"run imprint bootstrap --non-interactive, or install it into Imprint's venv yourself: "
                f"{sys.executable} -m pip install kreuzberg")
    return ("run imprint bootstrap --non-interactive, or download obscura from "
            "https://github.com/h4ckf0r0day/obscura/releases and put it on PATH")


# ── kreuzberg ────────────────────────────────────────────────

def _uv() -> str | None:
    """The uv setup installs with: IMPRINT_UV, the bundled one, or PATH."""
    candidates = [os.environ.get("IMPRINT_UV", ""),
                  str(config.get_project_dir() / "bin" / ("uv.exe" if sys.platform == "win32" else "uv"))]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return shutil.which("uv")


def _kreuzberg_installed() -> bool:
    return importlib.util.find_spec("kreuzberg") is not None


def _install_kreuzberg() -> None:
    lock = REQUIREMENTS / "kreuzberg.lock.txt"
    # Wheels only: building kreuzberg from source needs a Rust toolchain.
    args = ["install", "--require-hashes", "--only-binary", ":all:", "-r", str(lock)]
    uv = _uv()
    env = os.environ.copy()
    if uv:
        command = [uv, "pip", *args[:1], "--python", sys.executable, *args[1:]]
        # Setup scopes uv's cache to the install tree; keep using that cache.
        env.setdefault("UV_CACHE_DIR", str(Path(sys.prefix).parent / ".uv-cache"))
    else:
        command = [sys.executable, "-m", "pip", *args]
    try:
        done = subprocess.run(command, capture_output=True, text=True, env=env, timeout=_INSTALL_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise InstallFailed(f"installing kreuzberg failed: {error}") from error
    if done.returncode != 0:
        raise InstallFailed("installing kreuzberg failed: " + _tail(done.stderr or done.stdout))
    check = subprocess.run([sys.executable, "-c", "import kreuzberg"], capture_output=True, text=True)
    if check.returncode != 0:
        raise InstallFailed("kreuzberg was installed but does not load: " + _tail(check.stderr))
    importlib.invalidate_caches()  # So this process finds it too.


def _tail(text: str, lines: int = 3) -> str:
    kept = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return " | ".join(kept[-lines:]) or "(no output)"


# ── obscura ──────────────────────────────────────────────────

# Plain builds (rendering, no stealth); their sha256 values are pinned in
# requirements/runtime-manifest.json. Intel Macs are not supported (ADR 0111).
_OBSCURA_ASSETS = {
    ("linux", "x86_64"): "obscura-x86_64-linux.tar.gz",
    ("linux", "aarch64"): "obscura-aarch64-linux.tar.gz",
    ("darwin", "aarch64"): "obscura-aarch64-macos.tar.gz",
    ("windows", "x86_64"): "obscura-x86_64-windows.zip",
}
# The release archive holds the CLI and the worker it starts, side by side.
_OBSCURA_FILES = ("obscura", "obscura-worker")


def _exe(name: str) -> str:
    return name + ".exe" if sys.platform == "win32" else name


def obscura_dir() -> Path:
    return config.get_data_dir() / "obscura-bin"


def obscura_path() -> str | None:
    """The obscura binary Imprint installed, else the one on PATH."""
    managed = obscura_dir() / _exe("obscura")
    if managed.is_file():
        return str(managed)
    return shutil.which("obscura")


def _install_obscura() -> None:
    asset = _OBSCURA_ASSETS[(platform.system().lower(), _machine())]
    url = f"https://github.com/h4ckf0r0day/obscura/releases/download/{OBSCURA_VERSION}/{asset}"
    target = obscura_dir()
    target.mkdir(parents=True, exist_ok=True)
    checksum = expected_digest("obscura", OBSCURA_VERSION, asset)
    names = [_exe(name) for name in _OBSCURA_FILES]
    # A private staging directory keeps an interrupted or concurrent download
    # from leaving a partial binary where obscura_path() looks.
    with tempfile.TemporaryDirectory(prefix="download-", dir=target) as directory:
        staging = Path(directory)
        archive = staging / asset
        print(f"  Downloading obscura {OBSCURA_VERSION} ({asset}) ...", file=sys.stderr, flush=True)
        try:
            with urllib.request.urlopen(url, timeout=120) as response, archive.open("wb") as stream:
                shutil.copyfileobj(response, stream)
        except OSError as error:
            raise InstallFailed(f"downloading {url} failed: {error}") from error
        try:
            verify_archive(archive, checksum)
        except ValueError as error:
            raise InstallFailed(str(error)) from error
        out = staging / "out"
        if asset.endswith(".zip"):
            with zipfile.ZipFile(archive) as bundle:
                for member in bundle.namelist():
                    if member.removeprefix("./") in names:
                        bundle.extract(member, out)
        else:
            with tarfile.open(archive, "r:gz") as bundle:
                bundle.extractall(out, members=[m for m in bundle.getmembers() if m.name.removeprefix("./") in names], filter="data")
        for name in names:
            path = out / name
            if not path.is_file() or path.is_symlink():
                raise InstallFailed(f"{name} not found in {asset}")
            path.chmod(0o755)
        try:
            check = subprocess.run([str(out / names[0]), "--version"], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise InstallFailed(f"obscura does not run here: {error}") from error
        if check.returncode != 0 or not check.stdout.startswith("obscura"):
            raise InstallFailed("obscura does not run here: " + _tail(check.stderr or check.stdout))
        # The CLI goes last: its presence is what marks the install complete.
        for name in reversed(names):
            os.replace(out / name, target / name)


# ── installing ───────────────────────────────────────────────

def installed(tool: str) -> bool:
    return _kreuzberg_installed() if tool == "kreuzberg" else obscura_path() is not None


def ensure(tool: str) -> None:
    """Install tool when it is missing and check it works; raises
    InstallFailed with the manual fallback when it cannot."""
    with _lock:
        if installed(tool):
            _state.pop(tool, None)
            return
        reason = unsupported(tool)
        if reason:
            _state[tool] = f"{reason}; it cannot be installed here"
            raise InstallFailed(_state[tool])
        _state[tool] = "installing"
        try:
            _install_kreuzberg() if tool == "kreuzberg" else _install_obscura()
        except InstallFailed as error:
            _state[tool] = f"{error}. To install it by hand, {manual_hint(tool)}"
            raise InstallFailed(_state[tool]) from error
        _state.pop(tool, None)


def ensure_once(tool: str) -> bool:
    """Whether tool is available, installing it at most once per process.
    A failed install is reported on stderr once."""
    if tool in _attempted:
        return _attempted[tool]
    if installed(tool):
        return True
    with _lock:
        if tool in _attempted:
            return _attempted[tool]
        _attempted[tool] = False
    print(f"{SETTINGS[tool]} is on but {tool} is missing; installing it…", file=sys.stderr, flush=True)
    try:
        ensure(tool)
    except InstallFailed as error:
        print(f"Warning: {error}. Falling back to Imprint's native extractors.", file=sys.stderr, flush=True)
        return False
    print(f"Installed {tool}.", file=sys.stderr, flush=True)
    _attempted[tool] = True
    return True


def start_background(tool: str) -> None:
    """Install tool in a daemon thread; status() reports progress."""
    if installed(tool) or _state.get(tool) == "installing":
        return
    _state[tool] = "installing"

    def run():
        try:
            ensure(tool)
        except InstallFailed:
            pass  # ensure() recorded the failure for status().
        except Exception as error:
            _state[tool] = f"installing {tool} failed: {type(error).__name__}: {error}"

    threading.Thread(target=run, name=f"install-{tool}", daemon=True).start()


def status(tool: str) -> str:
    """Why an enabled tool is not in use yet; empty when it is installed."""
    if installed(tool):
        return ""
    state = _state.get(tool, "")
    if state == "installing":
        return f"Installing {tool}… extraction uses the native extractors until it is done."
    if state:
        return state
    reason = unsupported(tool)
    if reason:
        return f"{reason}; the native extractors are used instead."
    return f"{tool} is not installed yet; Imprint installs it on its next use or imprint bootstrap."


def health_lines() -> list[str]:
    """One line per enabled tool, for setup's extractor report."""
    lines = []
    for tool in SETTINGS:
        if enabled(tool):
            reason = status(tool)
            lines.append(f"{tool}: enabled BUT {reason}" if reason else f"{tool}: enabled (installed)")
    return lines


def main() -> int:
    for tool in SETTINGS:
        if not enabled(tool):
            continue
        try:
            ensure(tool)
        except InstallFailed as error:
            print(f"{tool}: enabled BUT {error}", flush=True)
        else:
            print(f"{tool}: enabled (installed)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
