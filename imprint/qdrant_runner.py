"""Auto-spawn local Qdrant server.

Replaces embedded mode (single-writer, lock collisions) with a long-running
local server on 127.0.0.1:6333. All clients (MCP, CLI, hooks) connect via
HTTP — Qdrant supports unlimited concurrent readers + serializes writes
internally.

Lifecycle:
  ensure_running() →
    1. If server already reachable on configured host:port, return.
    2. Else if a binary is on PATH or in data/qdrant-bin/, spawn daemon.
    3. Else download a pinned binary from GitHub releases, then spawn.
    4. Wait until /readyz responds 200 (timeout: 30s).

The daemon survives the parent process (start_new_session=True). Stops when
the user runs `imprint stop` or when the OS kills it. PID file at
data/qdrant.pid lets us restart cleanly without orphaning processes.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path

from . import config

log = logging.getLogger("imprint.qdrant_runner")

# Pinned version. Bump when needed; client compatibility is generous.
QDRANT_VERSION = os.environ.get("IMPRINT_QDRANT_VERSION", "v1.17.1")

QDRANT_HOST = os.environ.get("IMPRINT_QDRANT_HOST", "127.0.0.1")
QDRANT_PORT = int(os.environ.get("IMPRINT_QDRANT_PORT", "6333"))
QDRANT_GRPC_PORT = int(os.environ.get("IMPRINT_QDRANT_GRPC_PORT", "6334"))

# Disable auto-spawn entirely — operator runs their own server (Docker etc.).
DISABLE_SPAWN = os.environ.get("IMPRINT_QDRANT_NO_SPAWN", "0") == "1"

_READY_TIMEOUT_S = float(os.environ.get("IMPRINT_QDRANT_READY_TIMEOUT_S", "30"))


def _bin_dir() -> Path:
    return config.get_data_dir() / "qdrant-bin"


def _binary_path() -> Path:
    name = "qdrant.exe" if sys.platform == "win32" else "qdrant"
    return _bin_dir() / name


def _pid_file() -> Path:
    return config.get_data_dir() / "qdrant.pid"


def _log_file() -> Path:
    return config.get_data_dir() / "qdrant.log"


def _storage_dir() -> Path:
    return config.get_data_dir() / "qdrant_storage"


def _snapshots_dir() -> Path:
    return config.get_data_dir() / "qdrant_snapshots"


# ── Health probe ───────────────────────────────────────────────
def is_running() -> bool:
    """Cheap HTTP probe — returns True if /readyz responds 200."""
    url = f"http://{QDRANT_HOST}:{QDRANT_PORT}/readyz"
    try:
        with urllib.request.urlopen(url, timeout=1.0) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def _wait_until_ready(timeout: float = _READY_TIMEOUT_S) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_running():
            return True
        time.sleep(0.25)
    return False


# ── Binary discovery / download ───────────────────────────────
def _platform_asset() -> str:
    """Return the Qdrant release asset filename for the current platform."""
    sysname = platform.system().lower()
    machine = platform.machine().lower()

    if sysname == "linux":
        if machine in ("x86_64", "amd64"):
            return f"qdrant-x86_64-unknown-linux-gnu.tar.gz"
        if machine in ("aarch64", "arm64"):
            return f"qdrant-aarch64-unknown-linux-gnu.tar.gz"
    elif sysname == "darwin":
        if machine in ("x86_64", "amd64"):
            return f"qdrant-x86_64-apple-darwin.tar.gz"
        if machine in ("arm64", "aarch64"):
            return f"qdrant-aarch64-apple-darwin.tar.gz"
    elif sysname == "windows":
        return f"qdrant-x86_64-pc-windows-msvc.zip"

    raise RuntimeError(f"Unsupported platform for auto-download: {sysname}/{machine}")


def _find_or_download_binary() -> Path:
    """Locate qdrant binary. Search order:
    1. IMPRINT_QDRANT_BIN env var (explicit path)
    2. data/qdrant-bin/qdrant (previously downloaded)
    3. `qdrant` on PATH (system install)
    4. Download from GitHub releases.
    """
    env_path = os.environ.get("IMPRINT_QDRANT_BIN")
    if env_path and Path(env_path).is_file():
        return Path(env_path)

    local = _binary_path()
    if local.is_file():
        return local

    onpath = shutil.which("qdrant")
    if onpath:
        return Path(onpath)

    return _download_binary()


def _download_binary() -> Path:
    asset = _platform_asset()
    url = f"https://github.com/qdrant/qdrant/releases/download/{QDRANT_VERSION}/{asset}"
    bin_dir = _bin_dir()
    bin_dir.mkdir(parents=True, exist_ok=True)
    archive_path = bin_dir / asset

    log.info("Downloading Qdrant %s from %s", QDRANT_VERSION, url)
    print(f"  Downloading Qdrant {QDRANT_VERSION} ({asset}) ...", file=sys.stderr)
    with urllib.request.urlopen(url, timeout=120) as resp, open(archive_path, "wb") as f:
        shutil.copyfileobj(resp, f)

    # Extract
    if asset.endswith(".tar.gz"):
        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(bin_dir)
    elif asset.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as z:
            z.extractall(bin_dir)
    else:
        raise RuntimeError(f"Unknown archive format: {asset}")

    archive_path.unlink(missing_ok=True)

    # The archive drops `qdrant` (or qdrant.exe) at the top of bin_dir
    target = _binary_path()
    if not target.is_file():
        # Some releases nest under a subdir; flatten.
        for child in bin_dir.rglob("qdrant" + (".exe" if sys.platform == "win32" else "")):
            shutil.move(str(child), str(target))
            break
    if not target.is_file():
        raise RuntimeError(f"Qdrant binary not found after extracting {asset}")
    target.chmod(0o755)
    return target


# ── Daemon spawn ──────────────────────────────────────────────
def _spawn(binary: Path) -> int:
    """Launch qdrant detached from this process. Returns PID."""
    storage = _storage_dir()
    snapshots = _snapshots_dir()
    storage.mkdir(parents=True, exist_ok=True)
    snapshots.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    # Configure via env so we don't ship a YAML file.
    env["QDRANT__SERVICE__HOST"] = QDRANT_HOST
    env["QDRANT__SERVICE__HTTP_PORT"] = str(QDRANT_PORT)
    env["QDRANT__SERVICE__GRPC_PORT"] = str(QDRANT_GRPC_PORT)
    env["QDRANT__STORAGE__STORAGE_PATH"] = str(storage)
    env["QDRANT__STORAGE__SNAPSHOTS_PATH"] = str(snapshots)
    # Don't print bright ANSI banner to log file
    env["QDRANT__SERVICE__ENABLE_TLS"] = "false"
    env["RUST_LOG"] = env.get("RUST_LOG", "warn,qdrant=info")

    log_path = _log_file()
    log_fp = open(log_path, "ab", buffering=0)

    kwargs: dict = {
        "stdout": log_fp,
        "stderr": log_fp,
        "stdin": subprocess.DEVNULL,
        "env": env,
        "cwd": str(config.get_data_dir()),
    }
    if sys.platform == "win32":
        # New process group + detached so it survives parent.
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        kwargs["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    proc = subprocess.Popen([str(binary)], **kwargs)
    _pid_file().write_text(str(proc.pid))
    return proc.pid


def _is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


# ── Public API ────────────────────────────────────────────────
def ensure_running() -> tuple[str, int]:
    """Make sure a Qdrant server is reachable on the configured host:port.
    Spawns one (downloading the binary if needed) when nothing answers.
    Returns (host, port) suitable for QdrantClient(host=..., port=...).
    """
    if is_running():
        return QDRANT_HOST, QDRANT_PORT

    if DISABLE_SPAWN:
        raise RuntimeError(
            f"Qdrant server not reachable at http://{QDRANT_HOST}:{QDRANT_PORT} "
            "and IMPRINT_QDRANT_NO_SPAWN=1 disables auto-spawn. "
            "Start the server manually or unset the flag."
        )

    # Stale PID file? Clear it before spawning.
    pid_file = _pid_file()
    if pid_file.exists():
        try:
            old_pid = int(pid_file.read_text().strip())
            if not _is_pid_alive(old_pid):
                pid_file.unlink(missing_ok=True)
        except Exception:
            pid_file.unlink(missing_ok=True)

    binary = _find_or_download_binary()
    _spawn(binary)

    if not _wait_until_ready():
        raise RuntimeError(
            f"Qdrant server failed to become ready within {_READY_TIMEOUT_S}s. "
            f"Check {_log_file()} for errors."
        )
    return QDRANT_HOST, QDRANT_PORT


def stop() -> bool:
    """Terminate the running daemon if its PID file is valid. Returns True
    if a process was killed."""
    pid_file = _pid_file()
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text().strip())
    except Exception:
        pid_file.unlink(missing_ok=True)
        return False
    if not _is_pid_alive(pid):
        pid_file.unlink(missing_ok=True)
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    # Wait briefly for shutdown
    for _ in range(20):
        if not _is_pid_alive(pid):
            break
        time.sleep(0.25)
    pid_file.unlink(missing_ok=True)
    return True


def status() -> dict:
    pid_file = _pid_file()
    pid = None
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
        except Exception:
            pass
    return {
        "host": QDRANT_HOST,
        "port": QDRANT_PORT,
        "running": is_running(),
        "pid": pid,
        "pid_alive": pid is not None and _is_pid_alive(pid),
        "log": str(_log_file()),
        "storage": str(_storage_dir()),
    }
