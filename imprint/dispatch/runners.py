"""How each client is started for a Dispatched session (ADR 0222).

A client is started only through a one-shot mode that has an OS sandbox or
a check of each action, and never with one of its bypass flags. Each
runner says how to find the client's binary, which flags its ``--help``
must list for it to be used (an older client without them is refused, not
run with less safety), and builds its exact command line; every command
line is checked against the refused flags before it runs. Adding a client
is one entry in ``RUNNERS``.
"""

from __future__ import annotations

import glob
import os
import platform
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

# Flags and values that switch a client's safety off (ADR 0222's table). No
# runner passes them; `check` refuses a command line that contains one.
REFUSED = frozenset({
    '--dangerously-skip-permissions', '--allow-dangerously-skip-permissions', 'bypassPermissions',
    '--dangerously-bypass-approvals-and-sandbox', '--dangerously-bypass-hook-trust', 'danger-full-access',
    '--yolo', '--force', '-f', '--allow-all', '--allow-all-tools', '--allow-all-paths', '--allow-all-urls',
    '--auto',
})
# Variables that do the same.
REFUSED_ENV = frozenset({'COPILOT_ALLOW_ALL'})


@dataclass(frozen=True)
class Runner:
    client: str     # as the Board names it: "claude-code"
    label: str      # as people read it: "Claude Code"
    binary: str     # the command's name on PATH
    setting: str    # the setting that names the binary when it is not on PATH
    help_args: tuple[str, ...] = ('--help',)
    required: tuple[str, ...] = ()   # what that help must list
    # The VS Code extension that bundles the client, and its binary's path inside it
    # ({exe} and {platform} filled in for the system it is looked for on).
    extension: str = ''
    inside: str = ''
    # What a session of this client needs to know about its own sandbox, added to the prompt.
    note: str = ''

    def argv(self, binary: str, context: dict) -> list[str]:
        """The command line; ``context`` holds ``session_id``, ``cwd``,
        ``git_dir`` (the repository's git folder, which commits write to) and
        ``last_message`` (a file for the client's final message)."""
        raise NotImplementedError

    def bundle(self, system: str | None = None) -> tuple[str, str] | None:
        """(extension folder glob, binary inside it) on ``system`` (VS Code's
        name, ``linux-x64``; default: this one), or None without an extension."""
        if not self.extension:
            return None
        system = system or _vscode_suffix()
        os_name, arch = system.split('-', 1)
        exe = '.exe' if os_name == 'win32' else ''
        platform_name = {'win32': 'windows', 'darwin': 'macos'}.get(os_name, 'linux') + \
            ('-aarch64' if arch == 'arm64' else '-x86_64')
        return f'{self.extension}-*-{system}', self.inside.format(exe=exe, platform=platform_name)

    @property
    def bundles(self) -> tuple[tuple[str, str], ...]:
        found = self.bundle()
        return (found,) if found else ()

    def bundled(self) -> list[str]:
        """The binaries VS Code extensions installed for this client here, newest first."""
        found = []
        for root in (Path.home() / '.vscode' / 'extensions', Path.home() / '.vscode-server' / 'extensions'):
            for pattern, inside in self.bundles:
                found += [os.path.normpath(os.path.join(folder, inside)) for folder in glob.glob(str(root / pattern))]
        return sorted((path for path in found if os.path.isfile(path)), key=_version_key, reverse=True)


def _version_key(path: str) -> list:
    return [int(part) if part.isdigit() else part for part in re.split(r'(\d+)', path)]


def _arm() -> bool:
    return platform.machine().lower() in ('arm64', 'aarch64')


def _vscode_suffix() -> str:
    """VS Code's name for this system in an extension folder: ``linux-x64``, ``win32-x64``, ``darwin-arm64``."""
    system = {'win32': 'win32', 'darwin': 'darwin'}.get(sys.platform, 'linux')
    return f"{system}-{'arm64' if _arm() else 'x64'}"


class ClaudeCode(Runner):
    def argv(self, binary: str, context: dict) -> list[str]:
        # The prompt goes in on stdin. "auto" has a classifier check each
        # action; stream-json with --verbose keeps every event in the log.
        return [binary, '-p', '--permission-mode', 'auto', '--session-id', context['session_id'],
                '--output-format', 'stream-json', '--verbose']


class Codex(Runner):
    def argv(self, binary: str, context: dict) -> list[str]:
        # The prompt goes in on stdin. --approve-for-me runs commands in the
        # workspace-write sandbox and has approval requests reviewed for it
        # (Codex refuses --sandbox beside it). The sandbox writes in the
        # worktree only, plus the repository's git folder its commits go to.
        writable = f"sandbox_workspace_write.writable_roots=['{context['git_dir']}']"
        return [binary, 'exec', '--approve-for-me', '-C', context['cwd'], '-c', writable, '--json',
                '--output-last-message', context['last_message']]


CLAUDE_CODE = ClaudeCode('claude-code', 'Claude Code', 'claude', 'dispatch.claude_code.command',
                         required=('--permission-mode', 'auto', '--session-id', '--output-format'),
                         extension='anthropic.claude-code', inside='resources/native-binary/claude{exe}')
CODEX = Codex('codex', 'Codex', 'codex', 'dispatch.codex.command', help_args=('exec', '--help'),
              required=('--sandbox', 'workspace-write', '--approve-for-me', '--json', '--output-last-message'),
              extension='openai.chatgpt', inside='bin/{platform}/codex{exe}',
              note=('Your sandbox keeps git\'s own folders read-only, so `git add` and `git commit` fail with '
                    '"Permission denied" on an index.lock. Run each git command that writes (add, commit) with '
                    'escalated permissions: the reviewer approves commits on your task branch in your worktree. '
                    'Never escalate a push, a merge, or a command outside your worktree.'))
# In the default order: the first allowed one is the default client (ADR 0222).
RUNNERS = {runner.client: runner for runner in (CLAUDE_CODE, CODEX)}


def get(client: str) -> Runner:
    try:
        return RUNNERS[client]
    except KeyError:
        raise LookupError(f'No Dispatcher runner for {client}: available are {", ".join(sorted(RUNNERS))}') from None


def find(runner: Runner) -> str | None:
    """The client's binary on this system: the runner's setting when set,
    else PATH, else the binary a VS Code extension bundles."""
    from ..config_schema import resolve
    configured = str(resolve(runner.setting)[0] or '').strip()
    if configured:
        return configured if os.path.isfile(configured) else shutil.which(configured)
    found = shutil.which(runner.binary)
    if found:
        return found
    bundled = runner.bundled()
    return bundled[0] if bundled else None


def check(argv: list[str], env: dict | None = None) -> list[str]:
    """``argv`` when it holds none of the refused flags; ValueError otherwise."""
    refused = [arg for arg in argv[1:] if arg in REFUSED or arg.split('=', 1)[0] in REFUSED
               or arg.split('=', 1)[-1] in REFUSED]
    refused += sorted(name for name in REFUSED_ENV if (env or {}).get(name))
    if refused:
        raise ValueError(f'A Dispatched session never runs with {", ".join(refused)} (ADR 0222)')
    return argv


def command(runner: Runner, context: dict) -> list[str]:
    """The checked command line that starts ``runner``'s client; LookupError
    when its binary is not found."""
    binary = find(runner)
    if not binary:
        raise LookupError(f'{runner.label} was not found: put {runner.binary} on PATH or set {runner.setting}')
    return check(runner.argv(binary, context))
