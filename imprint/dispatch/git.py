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


# Fetching and pushing reach the network; nobody is there to answer a prompt for credentials.
NETWORK_TIMEOUT = 120
_NO_PROMPTS = {'GIT_TERMINAL_PROMPT': '0'}


def command(path: str, *args: str, env: dict | None = None) -> tuple[list[str], dict]:
    """The argv and subprocess options that run ``git -C <folder> <args>``
    where the folder lives, with ``env`` added to git's environment."""
    distro, where = location(path)
    options: dict = {}
    if sys.platform == 'win32':
        # Output is captured; the windowless desktop service must not open a console (ADR 0026).
        options['creationflags'] = subprocess.CREATE_NO_WINDOW
    if distro:
        # wsl.exe's own errors in UTF-8 rather than UTF-16; variables reach git inside through env.
        options['env'] = {**os.environ, 'WSL_UTF8': '1'}
        added = [f'{key}={value}' for key, value in (env or {}).items()]
        return ['wsl.exe', '-d', distro, '--exec', *(['env', *added] if added else []), 'git', '-C', where, *args], \
            options
    if env:
        options['env'] = {**os.environ, **env}
    return ['git', '-C', where, *args], options


def result(path: str, *args: str, timeout: float = TIMEOUT, network: bool = False) -> tuple[int, str, str]:
    """git's exit code, standard output and standard error, both stripped,
    for a caller that tells failures apart; GitError only when git is
    missing or slow. ``network``: a fetch or push, which never prompts for
    credentials."""
    argv, options = command(path, *args, env=_NO_PROMPTS if network else None)
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=timeout, check=False, **options)
    except FileNotFoundError:
        raise GitError(f'{argv[0]} is not installed') from None
    except subprocess.TimeoutExpired:
        raise GitError(f'git did not answer within {timeout:g} seconds on {path}') from None
    return (proc.returncode, proc.stdout.decode('utf-8', 'replace').strip(),
            proc.stderr.decode('utf-8', 'replace').strip())


def run(path: str, *args: str, timeout: float = TIMEOUT, network: bool = False) -> str:
    """git's standard output, stripped; GitError when git is missing, slow or
    fails. ``network``: a fetch or push, which never prompts for credentials."""
    code, out, err = result(path, *args, timeout=timeout, network=network)
    if code:
        raise GitError((err or f'git exited with {code}').splitlines()[-1])
    return out


def has_commit(path: str, commit: str) -> bool:
    """Whether ``commit`` is in the repository of the folder at ``path``."""
    try:
        run(path, 'cat-file', '-e', f'{commit}^{{commit}}')
        return True
    except GitError:
        return False


def upstream_remote(path: str, branch: str | None) -> str | None:
    """The remote ``branch`` tracks in the folder's repository; None when it
    tracks none, tracks a local branch, or is not a branch (ADR 0238)."""
    if not branch:
        return None
    try:
        remote = run(path, 'config', '--get', f'branch.{branch}.remote')
    except GitError:
        return None
    return remote if remote and remote != '.' else None


def push_branch(path: str, remote: str, branch: str) -> None:
    """Push ``branch`` to the same name on ``remote``, never with force (ADR 0238)."""
    run(path, 'push', '--quiet', remote, f'refs/heads/{branch}:refs/heads/{branch}', timeout=NETWORK_TIMEOUT,
        network=True)


def fetch_branch(path: str, remote: str, branch: str) -> None:
    """Fetch ``branch`` from ``remote`` into its remote-tracking ref, so its
    commits are here (ADR 0238)."""
    run(path, 'fetch', '--quiet', '--no-tags', remote, f'+refs/heads/{branch}:refs/remotes/{remote}/{branch}',
        timeout=NETWORK_TIMEOUT, network=True)


def is_ancestor(path: str, older: str, newer: str) -> bool:
    """Whether ``older`` is in ``newer``'s history (or is it)."""
    code, _, err = result(path, 'merge-base', '--is-ancestor', older, newer)
    if code not in (0, 1):
        raise GitError((err or f'git exited with {code}').splitlines()[-1])
    return code == 0


def default_branch(path: str, remote: str) -> str | None:
    """The branch ``remote``'s HEAD names (its default, often ``main``): from
    the local ``refs/remotes/<remote>/HEAD`` when it is set, else asked of
    the remote; None when neither says."""
    code, out, _ = result(path, 'symbolic-ref', '--quiet', '--short', f'refs/remotes/{remote}/HEAD')
    if code == 0 and out.startswith(f'{remote}/'):
        return out[len(remote) + 1:]
    code, out, _ = result(path, 'ls-remote', '--symref', remote, 'HEAD', timeout=NETWORK_TIMEOUT, network=True)
    for line in out.splitlines() if code == 0 else []:
        if line.startswith('ref: refs/heads/') and line.endswith('\tHEAD'):
            return line[len('ref: refs/heads/'):-len('\tHEAD')]
    return None
