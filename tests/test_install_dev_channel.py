"""The installers' dev channel installs the highest release version (ADR 0066).

Runs the selection code from install.sh and install.ps1 against release lists
shaped like GitHub's (newest first). `imprint update --dev` makes the same
choice; internal/release/version_test.go covers it.
"""
import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from emulation import REASON as EMULATED, emulated, slack

ROOT = Path(__file__).resolve().parents[1]

CASES = [
    ('a dev build ahead of stable', ['v0.25.0-dev.2', 'v0.25.0-dev.1', 'v0.24.0'], 'v0.25.0-dev.2'),
    # dev was fast-forwarded to main's release; no prerelease was built for it.
    ('stable caught up with dev', ['v0.25.0', 'v0.25.0-dev.2', 'v0.24.0'], 'v0.25.0'),
    # Builds before ADR 0066 were numbered after the last stable release.
    ('historical prereleases rank below their stable', ['v0.24.0', 'v0.8.0-dev.76', 'v0.8.0'], 'v0.24.0'),
    ('creation order is not version order', ['v0.25.1', 'v0.26.0-dev.1', 'v0.25.0'], 'v0.26.0-dev.1'),
    ('dev builds compare numerically', ['v0.25.0-dev.9', 'v0.25.0-dev.10'], 'v0.25.0-dev.10'),
    ('tags outside the scheme are ignored', ['nightly', 'v0.25.0-rc.1', 'v0.24.0'], 'v0.24.0'),
]


def releases(tags):
    return json.dumps([{'tag_name': tag, 'prerelease': '-' in tag} for tag in tags])


def function(path, pattern):
    match = re.search(pattern, path.read_text(encoding='utf-8'), re.M | re.S)
    assert match, f'{pattern} not found in {path.name}'
    return match.group(0)


class InstallShDevChannelTests(unittest.TestCase):
    @unittest.skipIf(os.name == 'nt' or not shutil.which('bash'), 'install.sh is the Linux/macOS installer')
    def test_resolves_the_highest_version(self):
        resolve = function(ROOT / 'install.sh', r'^resolve_latest_dev\(\) \{\n.*?^\}\n')
        for name, tags, expected in CASES:
            with self.subTest(name), tempfile.TemporaryDirectory() as directory:
                listing = Path(directory) / 'releases.json'
                listing.write_text(releases(tags))
                curl = Path(directory) / 'curl'
                curl.write_text(f'#!/bin/sh\ncat "{listing}"\n')
                curl.chmod(0o755)
                script = f'fail() {{ echo "$1" >&2; exit 1; }}\nREPO=fixture/imprint\n{resolve}\nresolve_latest_dev\n'
                result = subprocess.run(['bash', '-c', script], capture_output=True, text=True,
                                        env={**os.environ, 'PATH': f'{directory}{os.pathsep}{os.environ["PATH"]}'})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), expected)


def powershell():
    for name in ('pwsh', 'powershell', 'powershell.exe'):
        if shutil.which(name):
            return name
    return None


class InstallPs1DevChannelTests(unittest.TestCase):
    @unittest.skipUnless(powershell(), 'needs PowerShell')
    @unittest.skipIf(emulated(), EMULATED)
    def test_selects_the_highest_version(self):
        select = function(ROOT / 'install.ps1', r'^function Select-LatestDev \{\n.*?^\}\n')
        for name, tags, expected in CASES:
            with self.subTest(name):
                script = f"{select}\nSelect-LatestDev ('{releases(tags)}' | ConvertFrom-Json)\n"
                encoded = base64.b64encode(script.encode('utf-16-le')).decode()
                result = subprocess.run([powershell(), '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                                        capture_output=True, text=True, timeout=slack(120))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), expected)


if __name__ == '__main__':
    unittest.main()
