"""Running git on a folder where the folder lives.

A Windows runtime reaches a WSL project through its ``\\\\wsl.localhost``
share, where git is slow and sees Windows paths. git runs inside the
distribution instead, on the folder's Linux path, as ingest already does
([`cli_index.py`](../cli_index.py), ``_git_tracked_files``; ADR 0222).
"""

from __future__ import annotations

import os
import subprocess
import sys

from ..wsl_paths import windows_to_linux

TIMEOUT = 30


class GitError(RuntimeError):
    """git could not run, or refused, on a folder."""


def location(path: str) -> tuple[str | None, str]:
    """(WSL distribution, path git works with) of a folder: the distribution
    and the folder's Linux path for a WSL folder on a Windows runtime, else
    (None, the path itself)."""
    if sys.platform == 'win32' and (located := windows_to_linux(path)):
        return located
    return None, path


def command(path: str, *args: str) -> tuple[list[str], dict]:
    """The argv and subprocess options that run ``git -C <folder> <args>``
    where the folder lives."""
    distro, where = location(path)
    options: dict = {}
    if sys.platform == 'win32':
        # Output is captured; the windowless desktop service must not open a console (ADR 0026).
        options['creationflags'] = subprocess.CREATE_NO_WINDOW
    if distro:
        # wsl.exe's own errors in UTF-8 rather than UTF-16.
        options['env'] = {**os.environ, 'WSL_UTF8': '1'}
        return ['wsl.exe', '-d', distro, '--exec', 'git', '-C', where, *args], options
    return ['git', '-C', where, *args], options


def run(path: str, *args: str, timeout: float = TIMEOUT) -> str:
    """git's standard output, stripped; GitError when git is missing, slow or fails."""
    argv, options = command(path, *args)
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=timeout, check=False, **options)
    except FileNotFoundError:
        raise GitError(f'{argv[0]} is not installed') from None
    except subprocess.TimeoutExpired:
        raise GitError(f'git did not answer within {timeout:g} seconds on {path}') from None
    if proc.returncode:
        error = proc.stderr.decode('utf-8', 'replace').strip() or f'git exited with {proc.returncode}'
        raise GitError(error.splitlines()[-1])
    return proc.stdout.decode('utf-8', 'replace').strip()
