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


def validate(root: Path, platform: str, *, require_voice: bool = False) -> None:
    os_name, arch = platform.split('/')
    ext = '.exe' if os_name == 'windows' else ''
    targets = {'linux/amd64': 'linux-x64', 'windows/amd64': 'windows-x64', 'darwin/amd64': 'macos-x64', 'darwin/arm64': 'macos-arm64'}
    engine = root / 'imprint' / 'voice-engine'
    if engine.exists() or (require_voice and platform in targets):
        if platform not in targets:
            raise ValueError('Speech engine is not supported for this target')
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from imprint.voice_runtime import verify_engine
        binary = verify_engine(engine, targets[platform])
        if architectures(binary) != {arch}:
            raise ValueError('Speech engine architecture does not match release')
        if not (engine / 'NOTICE.md').is_file():
            raise ValueError('Speech engine notices are missing')
        if os_name != 'windows' and not os.access(binary, os.X_OK):
            raise ValueError('Speech engine is not executable')
        if os_name == 'linux' and not all((engine / 'licenses/gcc' / name).is_file() for name in ('COPYING3', 'COPYING.RUNTIME')):
            raise ValueError('GCC runtime notices are missing')
        if list(engine.rglob('*.gguf')):
            raise ValueError('Voice model weights must not be bundled in releases')
    for name in ('personaplex-test.txt', 'personaplex-test.lock.txt'):
        if (root / 'requirements' / name).exists():
            raise ValueError(f'Development state in archive: requirements/{name}')
    for rel in ('imprint/__main__.py', 'imprint/desktop_server.py', 'imprint/wsl_bridge.py', 'imprint/wsl_paths.py', 'requirements/base.txt', 'requirements/base.lock.txt', 'requirements/cpu.txt', 'requirements/cpu.lock.txt', 'requirements/gpu.txt',
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
    for name in ('data', 'python', '.first-run.done', '.setup-result', '.wsl-integrations.txt', 'first-run.log', 'uninstall-cleanup.log'):
        if (root / name).exists():
            raise ValueError(f'User state in archive: {name}')
    if any(root.glob('.wsl-integrations.txt*')) or any(root.glob('.wsl-registrations.json*')):
        raise ValueError('User state in archive: WSL selections or their backups')


if __name__ == '__main__':
    try:
        validate(Path(sys.argv[1]), sys.argv[2], require_voice=os.environ.get('REQUIRE_VOICE_ENGINE') == '1')
    except (ValueError, OSError, IndexError, struct.error) as exc:
        sys.exit(f'Release validation failed: {exc}')
    print(f'[+] Validated {sys.argv[2]} runtime tree')
