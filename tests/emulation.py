"""Whether these tests run under qemu-user emulation, as CI's arm64 runner does,
and how much longer than locally their deadlines should allow.

The emulated runner (ADR 0112) runs everything several times slower, and
.NET (PowerShell) crashes under it. Tests that time a real process's startup,
or that run PowerShell, skip there; x64, Windows and macOS run them natively.

QEMU fakes /proc/cpuinfo but not another process's /proc/<pid>/exe, which
names the emulator.

CI also runs on busy hosts and VMs, so every deadline a test waits under goes
through slack(): IMPRINT_TEST_TIME_SCALE if set, else 5 under emulation, else 3
on CI (env CI set), else 1. internal/testtime and imprint/ui/tests/slack.ts
apply the same allowance to the Go and Playwright tests.
"""

import asyncio
import functools
import os
import subprocess
import sys
import time


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


@functools.cache
def scale() -> float:
    if os.environ.get('IMPRINT_TEST_TIME_SCALE'):
        return float(os.environ['IMPRINT_TEST_TIME_SCALE'])
    if emulated():
        return 5
    return 3 if os.environ.get('CI') else 1


def slack(seconds: float) -> float:
    """seconds, stretched to what this machine needs to reliably finish in it."""
    return seconds * scale()


async def until(done, seconds=10, every=.01):
    """Poll done() until it is truthy or slack(seconds) pass; return its last result."""
    deadline = time.monotonic() + slack(seconds)
    while not (result := done()) and time.monotonic() < deadline:
        await asyncio.sleep(every)
    return result
