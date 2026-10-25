"""Read-only installation diagnostics. Never provisions models or edits client profiles."""
from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import os
import sys
import tempfile
from pathlib import Path


def _onnx_runtime_check() -> tuple[str, bool, str, str]:
    """The embedding runtime is sound when one intact distribution provides
    ``onnxruntime`` and its bindings load."""
    from . import onnx_runtime
    providers = onnx_runtime.providers()
    action = 'Run imprint bootstrap --non-interactive; it reinstalls the runtime of the selected profile.'
    if not providers:
        return 'runtime:onnxruntime', False, 'Missing', action
    detail = ', '.join(f"{name} {importlib.metadata.version(name)}{'' if intact else ' (files missing)'}"
                       for name, intact in providers.items())
    if len(providers) > 1:
        return 'runtime:onnxruntime', False, detail + ' share one package directory', action
    if not all(providers.values()):
        return 'runtime:onnxruntime', False, detail, action
    try:
        import onnxruntime
        onnxruntime.InferenceSession
    except Exception as error:
        return 'runtime:onnxruntime', False, f'{detail}: {type(error).__name__}: {error}', action
    return 'runtime:onnxruntime', True, detail, action


def _embedding_device_check() -> tuple[str, bool, str, str]:
    """``model.device``, what this machine makes of it, and whether the
    installed runtime matches (ADR 0062)."""
    from . import gpu_runtime
    plan = gpu_runtime.plan()
    detail = f'Embeddings run on the {plan.runtime.upper()}: {plan.reason}'
    if plan.requested != 'cpu':
        detail += f' (model.device {plan.requested})'
    change = gpu_runtime.pending(plan)
    if change:
        return 'runtime:embedding-device', False, detail, f'Run imprint setup; it installs {change}.'
    if plan.requested == 'gpu' and plan.runtime == 'cpu':
        return ('runtime:embedding-device', False, detail,
                'Embeddings cannot use the GPU here: set model.device to cpu or auto, or fix the cause and run imprint setup.')
    return 'runtime:embedding-device', True, detail, ''


def report(project_dir: Path, probe_mcp: bool = False) -> dict:
    checks = []
    def check(name, ok, detail, action=''):
        checks.append({'name': name, 'status': 'ok' if ok else 'attention', 'detail': detail, 'action': action if not ok else ''})
    check('python', sys.version_info >= (3, 12), '.'.join(map(str, sys.version_info[:3])), 'Run imprint bootstrap to provision managed Python 3.12.')
    for package in ('fastmcp', 'qdrant-client', 'chonkie', 'fastapi', 'python-multipart'):
        try:
            check('dependency:' + package, True, importlib.metadata.version(package))
        except importlib.metadata.PackageNotFoundError:
            check('dependency:' + package, False, 'Missing', 'Run imprint bootstrap --profile cpu --non-interactive.')
    check(*_onnx_runtime_check())
    check(*_embedding_device_check())
    check('dashboard', (project_dir / 'imprint/ui/out/index.html').is_file(), str(project_dir / 'imprint/ui/out'), 'Reinstall a complete release or run make dashboard-build in a checkout.')
    codex = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex') / 'config.toml'
    if codex.exists():
        try:
            import tomllib
            entry = tomllib.loads(codex.read_text()).get('mcp_servers', {}).get('imprint')
            check('integration:codex', bool(entry and entry.get('enabled', True)), 'Registered' if entry else 'Not registered', 'Run imprint setup codex, then reload the client and trust its hooks.')
        except (ValueError, OSError) as error:
            check('integration:codex', False, f'Invalid config: {type(error).__name__}', 'Repair the config syntax before running setup. Existing content was not changed.')
    else:
        checks.append({'name': 'integration:codex', 'status': 'not-detected', 'detail': str(codex), 'action': ''})
    from . import qdrant_runner
    running = qdrant_runner.is_running()
    checks.append({'name': 'qdrant', 'status': 'ok' if running else 'not-running',
                   'detail': 'Ready' if running else 'Not started; search can start the local daemon.', 'action': ''})
    if probe_mcp:
        async def probe():
            from fastmcp import Client
            from fastmcp.client.transports import StdioTransport
            with tempfile.TemporaryDirectory(prefix='imprint-doctor-') as directory:
                transport = StdioTransport(command=sys.executable, args=['-m', 'imprint'], cwd=str(project_dir),
                                           env=os.environ.copy(), log_file=Path(directory) / 'mcp.log')
                async with Client(transport) as client:
                    return len(await client.list_tools())
        try:
            count = asyncio.run(asyncio.wait_for(probe(), timeout=15))
            check('mcp', True, f'Initialized stdio and discovered {count} tools')
        except Exception as error:
            check('mcp', False, f'Initialization failed: {type(error).__name__}', 'Run imprint bootstrap and inspect the backend dependencies and paths.')
    return {'ok': all(item['status'] != 'attention' for item in checks), 'checks': checks}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--probe-mcp', action='store_true')
    args = parser.parse_args()
    result = report(Path(__file__).resolve().parents[1], args.probe_mcp)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for item in result['checks']:
            print(f"[{item['status']}] {item['name']}: {item['detail']}")
            if item['action']:
                print('  ' + item['action'])
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
