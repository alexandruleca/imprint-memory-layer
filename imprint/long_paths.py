"""Windows extended-length spellings for filesystem calls on deep trees (ADR 0147).

Win32 file calls stop at MAX_PATH (260 characters) unless the machine turns
on LongPathsEnabled. A repository read from a WSL distribution through
\\\\wsl.localhost\\<distribution> gains about 30 characters, so a tree that
Linux lists fine fails on Windows. The \\\\?\\ prefix lifts that limit for one
call without any machine setting.

Only the filesystem call gets the prefix: ``os_path`` spells a path for one
call, ``plain`` takes the prefix off again, and every path Imprint compares,
shows or stores (source paths, exclusions, display) stays in its usual form.
A path below the margin is returned unchanged, so short paths behave exactly
as before.
"""
from __future__ import annotations

import ntpath
import os
import sys

# A directory listing appends "\\*" and directory creation stops at 248, so
# the prefix starts below MAX_PATH with room to spare.
MARGIN = 240

_VERBATIM = '\\\\?\\'
_VERBATIM_UNC = '\\\\?\\UNC\\'


def os_path(path, *, platform: str | None = None) -> str:
    """``path`` as one filesystem call should spell it on this platform."""
    text = os.fspath(path)
    if (platform or sys.platform) != 'win32' or len(text) < MARGIN:
        return text
    if text.startswith((_VERBATIM, '\\\\.\\')):
        return text
    # A verbatim path is passed through untouched: it must already be
    # absolute, use backslashes, and hold no "." or ".." segments.
    text = ntpath.normpath(ntpath.join(os.getcwd(), text) if not ntpath.isabs(text) else text)
    if text.startswith('\\\\'):
        return _VERBATIM_UNC + text[2:]
    return _VERBATIM + text


def plain(path) -> str:
    """``path`` without an extended-length prefix."""
    text = os.fspath(path)
    if text.startswith(_VERBATIM_UNC):
        return '\\\\' + text[len(_VERBATIM_UNC):]
    if text.startswith(_VERBATIM):
        return text[len(_VERBATIM):]
    return text


def realpath(path) -> str:
    """The resolved path, in the usual form, also beyond MAX_PATH."""
    return plain(os.path.realpath(os_path(path)))
