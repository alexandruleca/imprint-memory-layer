"""Dispatched sessions for projects in a WSL distribution (ADR 0222, "Where it runs").

On a Windows runtime, a project whose folder is in a WSL distribution runs
inside that distribution: its worktree goes in the distribution's home, git
and the client run there, and the client is found there (its setting when
it names a Linux path, the login shell's PATH, then the binary its VS Code
Server extension bundles). The client runs under ``setsid --wait`` with its
process group written to a file, so Stop and the time limit end the
session inside the distribution, not only the ``wsl.exe`` that started it.
``WSLENV`` carries the Task's workspace to the client and on to the Imprint
server its WSL profile starts on Windows.
"""

from __future__ import annotations

import os
import posixpath
import shlex
import subprocess
import sys
from functools import lru_cache

from ..wsl_paths import linux_to_windows

TIMEOUT = 30
# The variables Dispatched sessions get, carried into the distribution and back to Windows.
CARRIED = ('IMPRINT_WORKSPACE', 'IMPRINT_DISPATCH_RUN')


def _options() -> dict:
    options = {'env': {**os.environ, 'WSL_UTF8': '1'}}
    if sys.platform == 'win32':
        options['creationflags'] = subprocess.CREATE_NO_WINDOW
    return options


def run(distro: str, argv: list[str], timeout: float = TIMEOUT) -> str:
    """An argv's standard output inside ``distro``; RuntimeError when it fails."""
    try:
        done = subprocess.run(['wsl.exe', '-d', distro, '--exec', *argv], capture_output=True, timeout=timeout,
                              check=False, stdin=subprocess.DEVNULL, **_options())
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError(f'wsl.exe could not run in {distro}: {error}') from None
    if done.returncode:
        raise RuntimeError((done.stderr or done.stdout).decode('utf-8', 'replace').strip()
                           or f'{argv[0]} exited with {done.returncode} in {distro}')
    return done.stdout.decode('utf-8', 'replace')


@lru_cache(maxsize=8)
def home(distro: str) -> str:
    """The distribution's home folder, as a Linux path."""
    found = run(distro, ['sh', '-c', 'printf %s "$HOME"']).strip()
    if not found.startswith('/'):
        raise RuntimeError(f'{distro} reported no home folder')
    return found


def native(path: str, distro: str) -> str:
    """A Linux path of ``distro`` as this runtime opens it (its \\\\wsl.localhost share)."""
    return linux_to_windows(path, distro)


def which(distro: str, name: str) -> str | None:
    """A command on the login shell's PATH in ``distro``."""
    try:
        found = run(distro, ['sh', '-lc', f'command -v {shlex.quote(name)}']).strip().splitlines()
    except RuntimeError:
        return None
    return found[-1] if found and found[-1].startswith('/') else None


def bundled(distro: str, runner) -> list[str]:
    """Binaries VS Code Server extensions installed for ``runner``'s client in ``distro``, newest first."""
    found = []
    for system in ('linux-x64', 'linux-arm64'):
        bundle = runner.bundle(system)
        if not bundle:
            return []
        pattern, inside = bundle
        script = (f'for f in "$HOME"/.vscode-server/extensions/{pattern}/{inside}; '
                  'do [ -x "$f" ] && printf "%s\\n" "$f"; done; true')
        try:
            found += run(distro, ['sh', '-c', script]).split()
        except RuntimeError:
            continue
    from .runners import _version_key
    return sorted(found, key=_version_key, reverse=True)


def linux_path(distro: str, windows_path: str) -> str:
    """A Windows path as programs in ``distro`` reach it (``wslpath -u``)."""
    return run(distro, ['wslpath', '-u', windows_path]).strip()


def is_file(distro: str, path: str) -> bool:
    try:
        return os.path.isfile(native(path, distro))
    except (OSError, ValueError):
        return False


def read(distro: str, path: str) -> str | None:
    try:
        with open(native(path, distro), encoding='utf-8') as handle:
            return handle.read()
    except (OSError, ValueError):
        return None


def pid_file(run_id: str) -> str:
    return f'/tmp/imprint-dispatch-{run_id}.pid'


def launch(distro: str, cwd: str, argv: list[str], run_id: str) -> list[str]:
    """The ``wsl.exe`` command line that starts ``argv`` in ``cwd`` inside
    ``distro`` as the leader of its own process group, whose id it writes
    to ``pid_file(run_id)``; ``setsid --wait`` keeps ``wsl.exe`` running
    until the client ends and passes its exit code back."""
    return ['wsl.exe', '-d', distro, '--cd', cwd, '--exec', 'setsid', '--wait', 'sh', '-c',
            'echo $$ > "$0"; exec "$@"', pid_file(run_id), *argv]


def launch_env(env: dict) -> dict:
    """``env`` with WSLENV naming the carried variables, so they reach the
    client in the distribution and, through its WSL profile, Imprint's
    server on Windows."""
    names = [part for part in env.get('WSLENV', '').split(':') if part]
    names += [name for name in CARRIED if name in env and name not in {n.split('/')[0] for n in names}]
    return {**env, 'WSLENV': ':'.join(names)}


def _group(run_id: str) -> str:
    return f'pgid=$(cat {shlex.quote(pid_file(run_id))} 2>/dev/null) && [ -n "$pgid" ] || exit 1; '


def kill(distro: str, run_id: str, *, force: bool) -> bool:
    """Signal the session's process group inside ``distro``; False when it is gone.
    procps' kill takes ``--`` before a group; dash's builtin, Ubuntu's sh, does not."""
    signal = 'KILL' if force else 'TERM'
    script = _group(run_id) + (f'if [ -x /bin/kill ]; then /bin/kill -s {signal} -- "-$pgid"; '
                               f'else kill -{signal} "-$pgid"; fi 2>/dev/null')
    try:
        run(distro, ['sh', '-c', script], timeout=10)
        return True
    except RuntimeError:
        return False


def group_alive(distro: str, run_id: str) -> bool:
    """Whether any process of the session's group still runs inside ``distro``."""
    try:
        run(distro, ['sh', '-c', _group(run_id) + 'ps -o pid= -g "$pgid" >/dev/null'], timeout=10)
        return True
    except RuntimeError:
        return False


def worktree_root(distro: str) -> str:
    return posixpath.join(home(distro), '.imprint', 'worktrees')
