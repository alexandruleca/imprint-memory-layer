"""Stdlib launcher for WSL clients using a Windows-installed Imprint.

Invoked by an executable shell script installed inside the chosen WSL profile.
No network listener, Linux Python installation or second memory store is used.
"""
import argparse
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys

# Absolute script execution works before PYTHONPATH is established. Remove the
# script directory: imprint/queue.py must never shadow Python's stdlib queue.
_script_dir = str(Path(__file__).resolve().parent)
_project_dir = str(Path(__file__).resolve().parent.parent)
sys.path[:] = [_project_dir] + [entry for entry in sys.path if entry not in {_script_dir, _project_dir}]
from imprint.wsl_paths import linux_to_windows


def cli_arguments(arguments, distribution, mounts=''):
    """Translate filesystem operands, leaving logical source keys/content alone."""
    result = list(arguments)
    indexes = []
    if result and (result[0] in {'ingest', 'refresh'} or
                   result[0] == 'purge' and '--source' not in result or
                   result[:2] == ['workspace', 'roots']):
        indexes = range(1, len(result))
    elif result[:2] == ['workspace', 'map']:
        indexes = [3] if len(result) > 3 else []
    elif result[:2] == ['config', 'set'] and len(result) > 3 and result[2] in {'chat.model_path', 'tagger.local.model_path'}:
        indexes = [3]
    elif result and result[0] == 'learn':
        indexes = [i + 1 for i, arg in enumerate(result[:-1]) if arg == '--path']
    for index in indexes:
        if result[index].startswith('/'):
            result[index] = linux_to_windows(result[index], distribution, mounts)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--distribution', required=True)
    parser.add_argument('--cwd', required=True)
    parser.add_argument('--home', required=True)
    parser.add_argument('--cli', default=str(Path(__file__).resolve().parent.parent / 'bin' / 'imprint.exe'))
    parser.add_argument('--mounts', default='')
    parser.add_argument('action', choices=['mcp', 'hook', 'cli', 'probe'])
    parser.add_argument('args', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    project = str(Path(__file__).resolve().parent.parent)
    cwd = linux_to_windows(args.cwd, args.distribution, args.mounts)
    if not args.cwd.startswith('/') or not args.home.startswith('/'):
        parser.error('WSL working directory and home must be absolute Linux paths')
    if sys.platform != 'win32':
        parser.error('This bridge must run with the Windows Imprint interpreter')
    os.environ.update(PYTHONPATH=project, IMPRINT_DATA_DIR=args.data_dir,
                      IMPRINT_WSL_DISTRO=args.distribution, IMPRINT_WSL_CWD=args.cwd,
                      IMPRINT_WSL_HOME=args.home, IMPRINT_WSL_PYTHON=sys.executable,
                      IMPRINT_WSL_MOUNTS=args.mounts)
    os.chdir(cwd)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    if args.action == 'probe':
        print(json.dumps({'runtime': sys.platform, 'data_dir': args.data_dir,
                          'working_directory': os.getcwd(), 'client_working_directory': args.cwd}))
        return 0
    if args.action == 'mcp':
        runpy.run_module('imprint', run_name='__main__')
        return 0
    if args.action == 'cli':
        supported = {'ingest', 'refresh', 'workspace', 'purge', 'config', 'device',
                     'status', 'doctor', 'learn', 'ingest-url', 'refresh-urls', 'retag'}
        if not args.args or args.args[0] not in supported:
            parser.error('Use Windows Imprint directly for setup, installation, updates and desktop commands')
        # CLI commands accept filesystem paths as individual arguments. Never
        # interpolate a command string or alter URLs, flags or relative keys.
        translated = cli_arguments(args.args, args.distribution, args.mounts)
        return subprocess.call([args.cli, *translated])
    allowed = {'session-start', 'copilot-session-start', 'stop', 'pre-compact', 'post-search', 'pre-read'}
    if len(args.args) != 1 or args.args[0] not in allowed:
        parser.error('Unknown bridge hook')
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            payload = {}
    except (ValueError, OSError):
        payload = {}
    # Only filesystem metadata is translated. Transcript messages themselves
    # remain unchanged, and policy checks translate their recorded cwd values.
    for key in ('cwd', 'transcript_path'):
        if isinstance(payload.get(key), str):
            payload[key] = linux_to_windows(payload[key], args.distribution, args.mounts)
    if args.args[0] == 'stop' and payload.get('transcript_path'):
        # All Stop consumers must share the same scope check, including the
        # extraction/summarization modules that do not inspect roots themselves.
        # The check and the hook use the session's workspace (ADR 0072).
        from imprint.hook_entry import pin_session
        from imprint.workspace_policy import transcript_allowed
        try:
            pin_session(payload)
            approved = transcript_allowed(payload['transcript_path'])
        except (OSError, ValueError):
            approved = False
        if not approved:
            payload.pop('transcript_path', None)
            print('Imprint skipped a conversation that belongs to another workspace or records no working directory.', file=sys.stderr)
    sys.stdin = io.StringIO(json.dumps(payload))
    sys.argv = ['hook_entry.py', args.args[0], args.data_dir]
    from imprint.hook_entry import main as hook_main
    return hook_main()


if __name__ == '__main__':
    sys.exit(main())
