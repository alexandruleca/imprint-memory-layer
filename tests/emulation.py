"""Whether these tests run under qemu-user emulation, as CI's arm64 runner does.

The emulated runner (ADR 0112) runs everything several times slower, and
.NET (PowerShell) crashes under it. Tests that time a real process's startup,
or that run PowerShell, skip there; x64, Windows and macOS run them natively.

QEMU fakes /proc/cpuinfo but not another process's /proc/<pid>/exe, which
names the emulator.
"""

import functools
import os
import subprocess
import sys


@functools.cache
def emulated() -> bool:
    if not sys.platform.startswith('linux'):
        return False
    try:
        child = subprocess.Popen(['sleep', '5'])
    except OSError:
        return False
    try:
        return os.path.basename(os.readlink(f'/proc/{child.pid}/exe')).startswith('qemu-')
    except OSError:
        return False
    finally:
        child.kill()
        child.wait()


REASON = 'runs under qemu-user emulation, too slow for its deadlines and unsupported by .NET'
