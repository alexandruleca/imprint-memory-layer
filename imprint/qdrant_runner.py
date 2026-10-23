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
import tempfile
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
from .processes import is_alive
from .downloads import expected_digest, verify_archive
from .http_security import loopback_address

log = logging.getLogger("imprint.qdrant_runner")

# All qdrant settings resolved through config (env > config.json > default).
QDRANT_VERSION = config.QDRANT_VERSION
QDRANT_HOST = config.QDRANT_HOST
QDRANT_PORT = config.QDRANT_PORT
QDRANT_GRPC_PORT = config.QDRANT_GRPC_PORT
DISABLE_SPAWN = config.QDRANT_NO_SPAWN

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
def _server_url() -> str:
    host = f"[{QDRANT_HOST}]" if ":" in QDRANT_HOST else QDRANT_HOST
    return f"http://{host}:{QDRANT_PORT}"


def is_running() -> bool:
    """Cheap HTTP probe — returns True if /readyz responds 200."""
    url = f"{_server_url()}/readyz"
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
            return f"qdrant-aarch64-unknown-linux-musl.tar.gz"
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
    4. Download pinned version from GitHub releases.
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
    checksum = expected_digest("qdrant", QDRANT_VERSION, asset)
    # A unique download/extraction directory prevents interrupted or concurrent
    # downloads from leaving a partially written executable at the cache path.
    with tempfile.TemporaryDirectory(prefix="download-", dir=bin_dir) as directory:
        staging = Path(directory)
        archive_path = staging / asset
        log.info("Downloading Qdrant %s from %s", QDRANT_VERSION, url)
        print(f"  Downloading Qdrant {QDRANT_VERSION} ({asset}) ...", file=sys.stderr)
        with urllib.request.urlopen(url, timeout=120) as resp, archive_path.open("wb") as stream:
            shutil.copyfileobj(resp, stream)
        verify_archive(archive_path, checksum)
        if asset.endswith(".tar.gz"):
            with tarfile.open(archive_path, "r:gz") as tar:
                tar.extractall(staging, filter="data")
        elif asset.endswith(".zip"):
            with zipfile.ZipFile(archive_path) as archive:
                archive.extractall(staging)
        target = _binary_path()
        found = next(staging.rglob(target.name), None)
        if found is None or not found.is_file() or found.is_symlink():
            raise RuntimeError(f"Qdrant binary not found after extracting {asset}")
        found.chmod(0o755)
        os.replace(found, target)
    return target


# ── Daemon spawn ──────────────────────────────────────────────
def _validate_managed_host() -> None:
    if QDRANT_HOST != "localhost" and not loopback_address(QDRANT_HOST):
        raise ValueError(
            "Auto-spawned Qdrant requires a loopback host (127.0.0.1 or ::1). "
            "For an independently secured external server, set qdrant.no_spawn=true."
        )


def _spawn(binary: Path) -> int:
    """Launch qdrant detached from this process. Returns PID."""
    _validate_managed_host()
    storage = _storage_dir()
    snapshots = _snapshots_dir()
    storage.mkdir(parents=True, exist_ok=True)
    snapshots.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    # Configure via env so we don't ship a YAML file.
    env["QDRANT__SERVICE__HOST"] = "127.0.0.1" if QDRANT_HOST == "localhost" else QDRANT_HOST
    env["QDRANT__SERVICE__HTTP_PORT"] = str(QDRANT_PORT)
    env["QDRANT__SERVICE__GRPC_PORT"] = str(QDRANT_GRPC_PORT)
    env["QDRANT__STORAGE__STORAGE_PATH"] = str(storage)
    env["QDRANT__STORAGE__SNAPSHOTS_PATH"] = str(snapshots)
    # Don't print bright ANSI banner to log file
    env["QDRANT__SERVICE__ENABLE_TLS"] = "false"
    # This managed daemon is single-node and is not a browser API. Inherited
    # Qdrant settings must not open a cluster listener or enable wildcard CORS.
    env["QDRANT__CLUSTER__ENABLED"] = "false"
    env["QDRANT__SERVICE__ENABLE_CORS"] = "false"
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
    return is_alive(pid)


# ── Public API ────────────────────────────────────────────────
def ensure_running() -> tuple[str, int]:
    """Serialize first startup across CLI, API and MCP processes."""
    if not DISABLE_SPAWN:
        _validate_managed_host()
    if is_running():
        return QDRANT_HOST, QDRANT_PORT
    from .queue_lock import _lock, _unlock
    directory = config.get_data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(directory / "qdrant-start.lock", os.O_RDWR | os.O_CREAT, 0o600)
    acquired = False
    deadline = time.monotonic() + 180
    try:
        while not acquired:
            acquired = _lock(descriptor, False)
            if not acquired:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Timed out waiting for another Imprint process to start Qdrant")
                time.sleep(0.1)
        return _ensure_running_locked()
    finally:
        if acquired:
            _unlock(descriptor)
        os.close(descriptor)


def _ensure_running_locked() -> tuple[str, int]:
    """Make sure a Qdrant server is reachable on the configured host:port.
    Spawns one (downloading the binary if needed) when nothing answers.
    Returns (host, port) suitable for QdrantClient(host=..., port=...).
    """
    if not DISABLE_SPAWN:
        _validate_managed_host()
    if is_running():
        return QDRANT_HOST, QDRANT_PORT

    if DISABLE_SPAWN:
        raise RuntimeError(
            f"Qdrant server not reachable at {_server_url()} "
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
