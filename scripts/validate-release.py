#!/usr/bin/env python3
"""Fail packaging on missing runtime files, wrong native architecture or dev state."""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path


def architectures(path: Path) -> set[str]:
    with path.open('rb') as stream:
        header = stream.read(4096)
    if header[:4] == b'\x7fELF':
        machine = struct.unpack_from('<H' if header[5] == 1 else '>H', header, 18)[0]
        return { {62: 'amd64', 183: 'arm64'}.get(machine, 'unknown') }
    if header[:2] == b'MZ':
        offset = struct.unpack_from('<I', header, 60)[0]
        with path.open('rb') as stream:
            stream.seek(offset)
            pe = stream.read(6)
        if pe[:4] != b'PE\0\0':
            raise ValueError(f'Invalid PE header: {path}')
        return { {0x8664: 'amd64', 0xAA64: 'arm64'}.get(struct.unpack_from('<H', pe, 4)[0], 'unknown') }
    cpu = {0x01000007: 'amd64', 0x0100000C: 'arm64'}
    if header[:4] in (b'\xcf\xfa\xed\xfe', b'\xfe\xed\xfa\xcf'):
        endian = '<' if header[0] == 0xcf else '>'
        return {cpu.get(struct.unpack_from(endian + 'I', header, 4)[0], 'unknown')}
    if header[:4] in (b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf'):
        count = struct.unpack_from('>I', header, 4)[0]
        stride = 32 if header[3] == 0xbf else 20
        return {cpu.get(struct.unpack_from('>I', header, 8 + i * stride)[0], 'unknown') for i in range(count)}
    raise ValueError(f'Unrecognized executable: {path}')


def validate(root: Path, platform: str) -> None:
    os_name, arch = platform.split('/')
    ext = '.exe' if os_name == 'windows' else ''
    for rel in ('imprint/__main__.py', 'requirements/base.txt', 'requirements/base.lock.txt', 'requirements/gpu.txt',
                'requirements/llm.txt', 'requirements/gpu.lock.txt', 'requirements/llm.lock.txt',
                'requirements/runtime-manifest.json', 'LICENSE', 'licenses/uv/LICENSE-APACHE',
                'licenses/uv/LICENSE-MIT', 'imprint/ui/out/index.html'):
        path = root / rel
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f'Missing/empty runtime file: {path}')
    if not any((root / 'imprint/ui/out/_next').rglob('*.js')):
        raise ValueError('Dashboard JavaScript is missing')
    for rel in ('bin/imprint' + ext, 'bin/uv' + ext):
        path = root / rel
        if arch not in architectures(path):
            raise ValueError(f'Wrong architecture for {platform}: {path}')
        if os_name != 'windows' and not os.access(path, os.X_OK):
            raise ValueError(f'Not executable: {path}')
    if os_name == 'linux' and (root / 'bin/imprint-ui').exists():
        if architectures(root / 'bin/imprint-ui') != {arch}:
            raise ValueError('Electron AppImage architecture does not match release')
    forbidden = {'.git', '.venv', 'node_modules', '.next', '__pycache__', '.uv-cache'}
    for directory, dirs, files in os.walk(root):
        bad = forbidden.intersection(dirs)
        if bad:
            raise ValueError(f'Development state in archive: {Path(directory) / sorted(bad)[0]}')
        if any(name == '.env' or name.startswith('.env.') for name in files):
            raise ValueError(f'Environment file in archive: {directory}')
    for name in ('data', 'python', '.first-run.done', '.setup-result', 'first-run.log'):
        if (root / name).exists():
            raise ValueError(f'User state in archive: {name}')


if __name__ == '__main__':
    try:
        validate(Path(sys.argv[1]), sys.argv[2])
    except (ValueError, OSError, IndexError, struct.error) as exc:
        sys.exit(f'Release validation failed: {exc}')
    print(f'[+] Validated {sys.argv[2]} runtime tree')
