#!/usr/bin/env python3
"""Opt-in native WSL -> Windows integration smoke, with disposable profiles.

Run in WSL with the base Python dependencies installed. --windows-cli must be
an actual Windows release CLI (or a Windows build inside a source checkout).
The supplied Windows interpreter is reused read-only; this never bootstraps or
registers against a real client profile.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--windows-cli', required=True, help='WSL path to imprint.exe')
    parser.add_argument('--windows-python', required=True, help='Windows path to the installed venv Python')
    parser.add_argument('--distribution', default=os.environ.get('WSL_DISTRO_NAME'))
    args = parser.parse_args()
    if not args.distribution:
        parser.error('Run in WSL or pass --distribution')
    # cmd.exe prints its real native temp path; /d avoids autorun commands.
    temp = subprocess.check_output(['cmd.exe', '/d', '/c', 'echo', '%TEMP%'], text=True).strip()
    data = temp + '\\imprint-wsl-smoke-' + uuid.uuid4().hex
    local_data = Path(subprocess.check_output(['wslpath', '-u', data], text=True).strip())
    env = {**os.environ, 'IMPRINT_DATA_DIR': data,
           'WSLENV': os.environ.get('WSLENV', '') + ':IMPRINT_DATA_DIR'}
    with tempfile.TemporaryDirectory(prefix="imprint's WSL 测试 ") as home:
        home = Path(home)
        for name in ('.codex', '.claude', 'project'):
            (home / name).mkdir()
        (home / '.codex/config.toml').write_text('model = "fixture"\n[mcp_servers.other]\ncommand = "keep-me"\n')
        (home / '.claude.json').write_text('{"custom":true,"mcpServers":{"other":{"command":"keep-me"}}}')
        def register(target):
            subprocess.run([args.windows_cli, 'wsl', 'setup', '--distribution', args.distribution,
                '--home', str(home), '--target', target, '--python', args.windows_python], check=True, env=env)
        for target in ('claude-code', 'codex'):
            register(target)
            config = home / ('.codex/config.toml' if target == 'codex' else '.claude.json')
            original = config.read_bytes()
            register(target)
            assert config.read_bytes() == original, 'Registration must be idempotent'
            assert b'keep-me' in original, 'Other MCP servers must survive'
        launcher = str(home / '.codex/imprint-wsl')
        command = home / '.local/bin/imprint'
        assert os.access(command, os.X_OK), 'Registration must put the imprint command in the home'
        cwd = str(home / 'project')
        for action, code in [('session-start', 0), ('pre-read', 2), ('post-search', 0), ('pre-read', 0), ('stop', 0)]:
            result = subprocess.run([launcher, 'hook', action], input='{"session_id":"fixture"}',
                                    text=True, capture_output=True, cwd=cwd)
            assert result.returncode == code, (action, result.stderr)
        # A conversation without a recorded cwd belongs to no workspace (ADR 0072).
        transcript = home / 'project/session.jsonl'
        transcript.write_text(json.dumps({'type': 'fixture'}))
        blocked = subprocess.run([launcher, 'hook', 'stop'],
            input=json.dumps({'transcript_path': str(transcript)}), text=True, capture_output=True, cwd=cwd)
        assert blocked.returncode == 0 and 'Imprint skipped a conversation' in blocked.stderr, blocked.stderr
        probe = subprocess.run([launcher, 'probe'], capture_output=True, text=True, check=True, cwd=cwd)
        report = json.loads(probe.stdout)
        assert report['runtime'] == 'win32' and report['data_dir'] == data
        assert report['client_working_directory'] == cwd
        # The imprint command can explicitly approve a WSL project; setup itself
        # has not approved or indexed any source directory.
        subprocess.run([str(command), 'workspace', 'roots', 'add', cwd], check=True, cwd=cwd)
        refused = subprocess.run([str(command), 'update'], capture_output=True, text=True, cwd=cwd)
        assert refused.returncode == 2 and 'In WSL, imprint runs' in refused.stderr, refused.stderr
        # A Linux installation's imprint is a symlink, which Windows reads
        # through \\wsl.localhost as missing; registration must keep it.
        native = home / 'native profile'
        (native / '.codex').mkdir(parents=True)
        (native / '.local/bin').mkdir(parents=True)
        (native / 'linux-imprint').write_text('#!/bin/sh\n')
        (native / '.local/bin/imprint').symlink_to(native / 'linux-imprint')
        for verb in ('setup', 'remove'):
            subprocess.run([args.windows_cli, 'wsl', verb, '--distribution', args.distribution, '--home', str(native),
                            '--target', 'codex'] + (['--python', args.windows_python] if verb == 'setup' else []), check=True, env=env)
            assert os.readlink(native / '.local/bin/imprint') == str(native / 'linux-imprint'), 'Kept the Linux imprint'

        async def handshake(working=cwd, expected_roots=None):
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            async with stdio_client(StdioServerParameters(command=launcher, args=['mcp'], cwd=working)) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    tools = await client.list_tools()
                    result = await client.call_tool('device_context', {})
                    assert not getattr(result, 'is_error', getattr(result, 'isError', False))
                    context = json.loads(result.content[0].text)
                    assert context['client_distribution'] == args.distribution
                    assert context['client_working_directory'] == working
                    assert sorted(context['roots']) == sorted(expected_roots or [cwd]), context
                    print(f'Native WSL/Windows MCP discovery passed ({len(tools.tools)} tools)')
        asyncio.run(handshake())
        # A Windows drive is not accessible through WSL's UNC provider in the
        # same way as Linux files. Exercise both cwd and approved-root mapping.
        mounted = local_data / 'mounted project'
        mounted.mkdir()
        drive_probe = subprocess.run([launcher, 'probe'], capture_output=True, text=True, check=True, cwd=mounted)
        assert json.loads(drive_probe.stdout)['working_directory'][1:3] == ':\\'
        subprocess.run([launcher, 'cli', 'workspace', 'roots', 'add', str(mounted)], check=True, cwd=mounted)
        asyncio.run(handshake(str(mounted), [cwd, str(mounted)]))
        drive_home = local_data / 'drive-profile'
        (drive_home / '.codex').mkdir(parents=True)
        (drive_home / '.codex/config.toml').write_text('model = "keep-drive-model"\n')
        subprocess.run([args.windows_cli, 'wsl', 'setup', '--distribution', args.distribution,
            '--home', str(drive_home), '--target', 'codex', '--python', args.windows_python], check=True, env=env)
        subprocess.run([str(drive_home / '.codex/imprint-wsl'), 'probe'], check=True, cwd=mounted, capture_output=True)
        subprocess.run([args.windows_cli, 'wsl', 'remove', '--distribution', args.distribution,
            '--home', str(drive_home), '--target', 'codex'], check=True, env=env)
        assert 'keep-drive-model' in (drive_home / '.codex/config.toml').read_text()
        assert not (drive_home / '.local/bin/imprint').exists()
        for target in ('claude-code', 'codex'):
            assert command.exists(), 'The imprint command stays while a client in the home is registered'
            subprocess.run([args.windows_cli, 'wsl', 'remove', '--distribution', args.distribution,
                            '--home', str(home), '--target', target], check=True, env=env)
        assert b'keep-me' in (home / '.codex/config.toml').read_bytes()
        assert b'keep-me' in (home / '.claude.json').read_bytes()
        assert not (home / '.codex/imprint-wsl').exists()
        assert not (home / '.claude/imprint-wsl').exists()
        assert not command.exists(), "The imprint command leaves with the home's last registration"
    # Keep failed-run data for diagnosis. Only this successful fixture's
    # uniquely allocated data directory is removed, never an installed store.
    import shutil
    if local_data.is_dir():
        shutil.rmtree(local_data)
    print('Disposable WSL profiles, preserved config, hooks, cwd, roots and shared Windows runtime passed')


if __name__ == '__main__':
    main()
