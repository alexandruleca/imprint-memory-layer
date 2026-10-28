"""Path context for a WSL client using the Windows runtime over stdio.

The runtime/data/device stay on Windows. Only explicitly absolute Linux
filesystem paths are translated; memory content and source keys stay intact.
"""
import os
import ntpath
import posixpath
import re
import subprocess
import sys
from functools import lru_cache

# Container engines' internal distributions, not user filesystems (ADR 0026).
UTILITY_DISTRIBUTIONS = ('docker-desktop', 'rancher-desktop')


def valid_distribution(name):
    return bool(name) and name not in {'.', '..'} and not any(c in name for c in '\\/:|\r\n\0')


@lru_cache(maxsize=8)
def drive_mounts(table):
    """Parse only drive mappings selected from Linux's kernel mount table."""
    def unescape(value):
        return re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), value)
    mounts = []
    for line in table.splitlines():
        fields = line.split('\t')
        if len(fields) != 2:
            continue
        windows, linux = map(unescape, fields)
        drive, _ = ntpath.splitdrive(windows)
        if len(drive) == 2 and drive[1] == ':' and linux.startswith('/') and ntpath.isabs(windows):
            mounts.append((posixpath.normpath(linux), ntpath.normpath(windows)))
    return sorted(mounts, key=lambda item: len(item[0]), reverse=True)


def linux_to_windows(value, distribution, mounts=''):
    if not valid_distribution(distribution):
        raise ValueError('Invalid WSL distribution')
    if not isinstance(value, str) or '\0' in value:
        raise ValueError('Invalid WSL path')
    if not value.startswith('/'):
        return value
    if '\\' in value:
        raise ValueError('A WSL path containing a backslash cannot be represented as a Windows filesystem path')
    clean = posixpath.normpath('/' + value.lstrip('/'))
    for linux, windows in drive_mounts(mounts):
        if clean == linux or clean.startswith(linux + '/'):
            return ntpath.join(windows, clean[len(linux):].lstrip('/').replace('/', '\\'))
    return '\\\\wsl.localhost\\' + distribution + clean.replace('/', '\\')


_NAMESPACE = re.compile(r'\\\\(?:wsl\.localhost|wsl\$)\\([^\\]+)(.*)', re.IGNORECASE | re.DOTALL)


def windows_to_linux(value):
    """(distribution, Linux path) of a path in a distribution's \\\\wsl.localhost
    (or \\\\wsl$) namespace; None for any other path."""
    match = _NAMESPACE.fullmatch(ntpath.normpath(value)) if isinstance(value, str) else None
    if not match or not valid_distribution(match[1]):
        return None
    return match[1], posixpath.normpath('/' + match[2].replace('\\', '/').lstrip('/'))


def native_path(value):
    distro = os.environ.get('IMPRINT_WSL_DISTRO')
    return linux_to_windows(value, distro, os.environ.get('IMPRINT_WSL_MOUNTS', '')) if sys.platform == 'win32' and distro else value


def client_path(value):
    """Turn a known runtime filesystem path into a WSL client hint."""
    distro = os.environ.get('IMPRINT_WSL_DISTRO')
    if sys.platform != 'win32' or not distro or not isinstance(value, str):
        return value
    located = windows_to_linux(value)
    if located and located[0].lower() == distro.lower():
        return located[1]
    for linux, windows in drive_mounts(os.environ.get('IMPRINT_WSL_MOUNTS', '')):
        normalized = ntpath.normcase(ntpath.normpath(value))
        base = ntpath.normcase(windows).rstrip('\\')
        if normalized == base or normalized.startswith(base + '\\'):
            relative = ntpath.relpath(value, windows)
            return linux if relative == '.' else linux + '/' + relative.replace('\\', '/')
    return value


def client_context():
    if not os.environ.get('IMPRINT_WSL_DISTRO'):
        return {}
    return {'client_environment': 'wsl',
            'client_distribution': os.environ['IMPRINT_WSL_DISTRO'],
            'client_working_directory': os.environ.get('IMPRINT_WSL_CWD', ''),
            'path_guidance': 'Use client_working_directory and WSL paths in the client. '
                             'The runtime and memory store are on Windows. Windows drive paths '
                             'can be translated with wslpath -u. Workspace root approvals still apply.'}


def parse_distributions(output):
    """Names from `wsl.exe --list --quiet`: UTF-16LE unless WSL_UTF8 is set."""
    utf16 = output[:2] == b'\xff\xfe' or output[1:2] == b'\0'
    text = output.decode('utf-16-le' if utf16 else 'utf-8', 'replace').lstrip('\ufeff')
    names = (line.strip() for line in text.splitlines())
    return [name for name in names
            if valid_distribution(name) and not name.lower().startswith(UTILITY_DISTRIBUTIONS)]


def distributions():
    """Registered WSL distributions on a Windows runtime; none elsewhere or without WSL.

    Listing does not start a distribution. CREATE_NO_WINDOW keeps the windowless
    desktop service from opening a console (ADR 0026).
    """
    if sys.platform != 'win32':
        return []
    try:
        listed = subprocess.run(['wsl.exe', '--list', '--quiet'], capture_output=True, timeout=5,
                                check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_distributions(listed.stdout)
