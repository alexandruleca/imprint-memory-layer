"""Process liveness without Windows' destructive os.kill(pid, 0) behavior."""
from __future__ import annotations

import os
import sys


def is_alive(pid: int | None) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return ctypes.get_last_error() == 5  # Access denied: assume alive.
        try:
            return kernel.WaitForSingleObject(handle, 0) == 0x102  # WAIT_TIMEOUT
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def started(pid: int | None) -> str | None:
    """An identity of the process that has ``pid`` now: when it started, as
    the system records it. A pid the system gives to a new process after
    the old one ended gets another identity. None when there is no such
    process or its start cannot be read (another user's protected process)."""
    if not isinstance(pid, int) or pid <= 0:
        return None
    if sys.platform.startswith('linux'):
        try:
            with open(f'/proc/{pid}/stat', encoding='utf-8', errors='replace') as stat:
                text = stat.read()
        except OSError:
            return None
        fields = text[text.rfind(')') + 2:].split()
        return f'linux:{fields[19]}' if len(fields) > 19 else None
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        kernel.GetProcessTimes.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            times = [wintypes.FILETIME() for _ in range(4)]
            if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                return None
            created = times[0]
            return f'win32:{(created.dwHighDateTime << 32) | created.dwLowDateTime}'
        finally:
            kernel.CloseHandle(handle)
    import subprocess
    try:
        out = subprocess.run(['ps', '-o', 'lstart=', '-p', str(pid)], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    text = out.stdout.strip()
    return f'{sys.platform}:{text}' if out.returncode == 0 and text else None


def same(pid: int | None, identity: str | None) -> bool:
    """Whether ``pid`` is still the running process whose identity
    (``started``) was ``identity``; False without an identity to compare.
    It must also be running: Windows answers for a process that ended as
    long as a handle to it stays open."""
    return bool(identity) and is_alive(pid) and started(pid) == identity
