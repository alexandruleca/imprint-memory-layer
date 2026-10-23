#!/usr/bin/env python3
"""Opt-in native Windows uninstall cleanup with disposable native/WSL profiles.

Run inside WSL. Copies the supplied Windows CLI and stdlib bridge sources to a
private Windows tree; never runs disable/uninstall against the installed tree.
The existing Windows interpreter is used read-only for bridge setup probes.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--windows-cli', required=True)
    parser.add_argument('--windows-python', required=True)
    parser.add_argument('--distribution', default=os.environ.get('WSL_DISTRO_NAME'))
    args = parser.parse_args()
    if not args.distribution:
        parser.error('Run in WSL or pass --distribution')
    native_temp = subprocess.check_output(['cmd.exe', '/d', '/c', 'echo', '%TEMP%'], text=True).strip()
    native_root = native_temp + '\\imprint-uninstall-smoke-' + uuid.uuid4().hex
    root = Path(subprocess.check_output(['wslpath', '-u', native_root], text=True).strip())
    root.mkdir()
    source = Path(__file__).resolve().parents[1]
    (root / 'bin').mkdir()
    shutil.copyfile(args.windows_cli, root / 'bin/imprint.exe')
    (root / 'requirements.txt').write_text('# private fixture root marker\n')
    (root / 'imprint').mkdir()
    for name in ('__init__.py', 'wsl_bridge.py', 'wsl_paths.py'):
        shutil.copyfile(source / 'imprint' / name, root / 'imprint' / name)
    user = root / 'user'
    (user / '.claude').mkdir(parents=True)
    (user / '.codex').mkdir()
    data = root / 'data'
    data.mkdir()
    (data / 'memories.txt').write_text('keep memories')
    env = {**os.environ, 'USERPROFILE': native_root + '\\user',
           'APPDATA': native_root + '\\user\\AppData\\Roaming',
           'LOCALAPPDATA': native_root + '\\user\\AppData\\Local',
           'CODEX_HOME': native_root + '\\user\\.codex',
           'CLAUDE_CONFIG_DIR': native_root + '\\user\\.claude',
           'IMPRINT_DATA_DIR': native_root + '\\data'}
    overrides = ('USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'CODEX_HOME', 'CLAUDE_CONFIG_DIR', 'IMPRINT_DATA_DIR')
    inherited = [entry for entry in os.environ.get('WSLENV', '').split(':') if entry.split('/')[0] not in overrides]
    env['WSLENV'] = ':'.join(inherited + list(overrides))
    def cli(*arguments, success=True):
        result = subprocess.run([str(root / 'bin/imprint.exe'), *arguments], env=env,
                                text=True, capture_output=True)
        assert (result.returncode == 0) == success, result.stdout + result.stderr
        return result
    with tempfile.TemporaryDirectory(prefix="imprint uninstall's WSL 测试 ") as linux_home:
        home = Path(linux_home)
        (home / '.codex').mkdir()
        (home / '.claude').mkdir()
        (home / '.claude.json').write_text('{"mcpServers":{"other":{"command":"keep-server"}}}')
        (home / '.codex/config.toml').write_text('model="keep-model"\n[mcp_servers.other]\ncommand="keep-server"\n')
        for target in ('claude-code', 'codex'):
            cli('wsl', 'setup', '--distribution', args.distribution, '--home', linux_home,
                '--target', target, '--claude-home', str(home / '.claude'),
                '--codex-home', str(home / '.codex'), '--python', args.windows_python)
        spec = {'command': native_root + '\\python', 'env': {'PYTHONPATH': native_root}}
        registry = user / '.claude/.claude.json'
        registry.write_text(json.dumps({'custom': True, 'mcpServers': {'imprint': spec, 'other': {'command': 'keep-native'}}}))
        hooks = user / '.claude/settings.json'
        hooks.write_text(json.dumps({'permissions': {'allow': ['mcp__imprint__*', 'keep-permission']},
            'hooks': {'Stop': [{'hooks': [{'command': 'imprint/hook_entry.py stop'}, {'command': 'keep-hook'}]}]}}))
        cli('disable')
        cli('disable')
        for target in ('.claude', '.codex'):
            assert not (home / target / 'imprint-wsl').exists()
        assert 'keep-server' in (home / '.codex/config.toml').read_text()
        assert 'keep-server' in (home / '.claude.json').read_text()
        assert 'mcp__imprint__*' not in (home / '.claude/settings.json').read_text()
        assert 'imprint' not in registry.read_text() and 'keep-native' in registry.read_text()
        assert 'imprint' not in hooks.read_text() and 'keep-hook' in hooks.read_text()
        assert json.loads((root / '.wsl-registrations.json').read_text()) == []
        # A config failure must block runtime deletion and leave a retry path.
        (root / '.venv').mkdir()
        runtime_sentinel = root / '.venv/keep-on-failure'
        runtime_sentinel.write_text('runtime')
        config = user / '.codex/config.toml'
        config.write_text('[invalid\n')
        cli('uninstall', '-y', success=False)
        assert runtime_sentinel.exists()
        config.write_text('model="keep-model"\n')
        cli('uninstall', '-y')
        assert not runtime_sentinel.exists()
        assert (data / 'memories.txt').read_text() == 'keep memories'
    shutil.rmtree(root)
    print('Native Windows disable/uninstall: WSL + native cleanup, retry and default memory preservation passed')


if __name__ == '__main__':
    main()
