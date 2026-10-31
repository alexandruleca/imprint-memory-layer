"""Installed ONNX Runtime distributions.

``onnxruntime`` and ``onnxruntime-gpu`` (and other vendor builds) are
alternative distributions that install files into the same ``onnxruntime``
package directory. Package installers track them independently: uninstalling
one deletes every file in its RECORD, including files the other also owns,
while the other still counts as installed. A sound runtime therefore has
exactly one provider whose RECORD files all exist (ADR 0032).

OCR, audio VAD and the Privacy Filter run on it; embeddings run on
llama.cpp (ADR 0086). Imprint installs the CPU build, or where a GPU can run
the Privacy Filter, a build with that GPU's provider in its place (ADR
0122): ``BUILDS`` lists them. OCR and VAD use the CPU provider of whichever
build is installed. ``unused`` names what setup removes from an
installation that holds another build than the one chosen, such as a CUDA
build and its NVIDIA libraries.

This module reads distribution metadata without importing ``onnxruntime``,
so it also works on a damaged runtime. For ``imprint setup``,
``python -m imprint.onnx_runtime`` prints the providers as JSON,
``... unused <build>`` the distributions to remove, ``... plan <profile>``
the builds to try in order, and ``... check <build>`` whether the installed
build runs a tiny model on its GPU provider.
"""
from __future__ import annotations

import base64
import csv
import importlib.metadata as metadata
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

REQUIREMENTS = Path(__file__).resolve().parent.parent / 'requirements'


@dataclass(frozen=True)
class Build:
    name: str
    distribution: str  # The distribution that provides the ``onnxruntime`` package.
    requirements: str  # Its requirements file, installed with base and cpu (whose onnxruntime it replaces).
    provider: str      # The execution provider it adds; empty for the CPU build.


BUILDS = {
    'cpu': Build('cpu', 'onnxruntime', 'cpu', ''),
    'cuda': Build('cuda', 'onnxruntime-gpu', 'onnx-cuda', 'CUDAExecutionProvider'),
    'rocm': Build('rocm', 'onnxruntime-migraphx', 'onnx-rocm', 'MIGraphXExecutionProvider'),
    'directml': Build('directml', 'onnxruntime-directml', 'onnx-directml', 'DmlExecutionProvider'),
}
# Mul(x, x) on a float[1, 4]: setup's check that a build's GPU provider loads and runs.
_TINY_MODEL = base64.b64decode('CAgSB2ltcHJpbnQ6QQoOCgF4CgF4EgF5IgNNdWwSBXByb2JlWhMKAXgSDgoMCAESCAoCCAEKAggEYhMKAX'
                               'kSDgoMCAESCAoCCAEKAggEQgQKABAN')
_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*')
_EXTRA = re.compile(r'\bextra\s*==')


def _record(distribution: metadata.Distribution) -> list[str]:
    # Distribution.files cannot answer what a distribution owns: it leaves
    # out missing files.
    record = distribution.read_text('RECORD')
    return [row[0] for row in csv.reader((record or '').splitlines()) if row]


def _record_complete(distribution: metadata.Distribution) -> bool:
    paths = _record(distribution)
    return bool(paths) and all(distribution.locate_file(path).exists() for path in paths)


def providers() -> dict[str, bool]:
    """Map each installed distribution that provides the ``onnxruntime``
    package to whether every file listed in its RECORD exists."""
    return {name: _record_complete(metadata.distribution(name))
            for name in sorted(set(metadata.packages_distributions().get('onnxruntime', [])))}


def normalize(name: str) -> str:
    """PEP 503 name normalization."""
    return re.sub(r'[-_.]+', '-', name).lower()


def requirement_names(path: Path) -> set[str]:
    """The distributions a requirements file lists. Comments, options and
    hash lines name none."""
    names = set()
    for line in path.read_text(encoding='utf-8').splitlines():
        match = _NAME.match(line.strip())
        if match:
            names.add(normalize(match.group(0)))
    return names


def _top_levels(distribution: metadata.Distribution) -> set[str]:
    """The package directories and modules a distribution installs."""
    text = distribution.read_text('top_level.txt')
    if text:
        return set(text.split())
    tops = set()
    for path in _record(distribution):
        top = Path(path).parts[0]
        if top.startswith('..') or top == '__pycache__' or top.endswith(('.dist-info', '.data', '.pth')):
            continue
        tops.add(top[:-3] if top.endswith('.py') else top)
    return tops


def _requirements(distribution: metadata.Distribution, extras: bool) -> list[str]:
    names = []
    for requirement in distribution.requires or []:
        spec, _, marker = requirement.partition(';')
        if extras or not _EXTRA.search(marker):
            names.append(normalize(_NAME.match(spec.strip()).group(0)))
    return names


def _kept_requirements(requirements: Path, build: str) -> set[str]:
    """The distributions this installation's requirements name, leaving out
    the files of the ONNX Runtime builds not chosen."""
    others = {BUILDS[name].requirements for name in BUILDS if name not in ('cpu', build)}
    keep = {BUILDS[build].distribution}
    for path in requirements.glob('*.txt'):
        if path.name.split('.')[0] not in others:
            keep |= requirement_names(path)
    return keep


def unused(requirements: Path = REQUIREMENTS, distributions=None, build: str = 'cpu') -> list[str]:
    """Installed distributions to remove: every ONNX Runtime build but
    ``build``'s, and what only those builds brought in.

    The other builds are the roots. Setup follows the installed
    distributions' requirements from there, a root's extras included, since a
    root may have been installed with extras (``onnxruntime-gpu[cuda,cudnn]``
    before ADR 0062). Distributions sharing a package directory with a
    removed one go with it: NVIDIA's wheels share ``nvidia/``, so libraries
    an older lock installed go too, even after the build that required them
    is gone. Nothing a requirements file of this installation names is
    removed, and nothing another installed distribution still requires.
    """
    keep = _kept_requirements(requirements, build)
    installed = {normalize(d.metadata['Name']): d
                 for d in (metadata.distributions() if distributions is None else distributions)}
    beside: dict[str, set[str]] = {}
    for name, distribution in installed.items():
        for top in _top_levels(distribution):
            beside.setdefault(top, set()).add(name)
    roots = beside.get('onnxruntime', set()) - {'onnxruntime', BUILDS[build].distribution}
    reached, pending = set(), list(roots)
    while pending:
        name = pending.pop()
        if name in reached:
            continue
        reached.add(name)
        pending += [dependency for dependency in _requirements(installed[name], name in roots) if dependency in installed]
        if name not in keep:
            pending += [sibling for top in _top_levels(installed[name]) for sibling in beside[top]]
    remove = reached - keep
    while True:  # Keep what a remaining distribution requires.
        required = {dependency for name, distribution in installed.items() if name not in remove
                    for dependency in _requirements(distribution, extras=False)}
        if not remove & required:
            return sorted(remove)
        remove -= required


def installed_version() -> str:
    """The version of the distribution that provides ``onnxruntime``, which
    ever build it is; empty when none or several do."""
    names = list(providers())
    return metadata.version(names[0]) if len(names) == 1 else ''


# ── Choosing a build (ADR 0122) ─────────────────────────────────
def _run(command: list[str], timeout: float = 20) -> str:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return ''


def nvidia_gpu() -> str:
    """The first NVIDIA GPU the driver lists, or empty: nvidia-smi ships
    with the driver on Linux and Windows."""
    tool = shutil.which('nvidia-smi')
    listed = _run([tool, '-L']) if tool else ''
    return next((line.split(' (UUID')[0].strip() for line in listed.splitlines() if line.startswith('GPU ')), '')


def rocm_version(root: Path | None = None) -> str:
    """The installed ROCm release as ``major.minor.patch``, or empty."""
    root = root or Path(os.environ.get('ROCM_PATH') or '/opt/rocm')
    try:
        text = (root / '.info' / 'version').read_text(encoding='utf-8').strip()
    except OSError:
        return ''
    match = re.match(r'(\d+)\.(\d+)(?:\.(\d+))?', text)
    return f'{match.group(1)}.{match.group(2)}.{match.group(3) or 0}' if match else ''


def rocm_gpu(root: Path | None = None) -> bool:
    """Whether ROCm's runtime sees a GPU agent (an integrated GPU ROCm does
    not support has none)."""
    root = root or Path(os.environ.get('ROCM_PATH') or '/opt/rocm')
    tool = shutil.which('rocminfo') or str(root / 'bin' / 'rocminfo')
    return bool(re.search(r'Device Type:\s*GPU', _run([tool])))


def amd_gpu(drm: Path = Path('/sys/class/drm')) -> bool:
    """Whether Linux lists an AMD display device (PCI vendor 0x1002)."""
    for card in drm.glob('card*'):
        if re.fullmatch(r'card\d+', card.name):
            try:
                if (card / 'device' / 'vendor').read_text().strip() == '0x1002':
                    return True
            except OSError:
                continue
    return False


def rocm_wheels(requirements: Path = REQUIREMENTS) -> dict:
    """AMD's pinned onnxruntime-migraphx wheels, by ROCm release."""
    try:
        return json.loads((requirements / 'onnx-rocm.json').read_text(encoding='utf-8'))['wheels']
    except (OSError, ValueError, KeyError):
        return {}


def directx12_gpus() -> list[str]:
    """Windows display adapters other than the software and remote ones."""
    listed = _run(['powershell', '-NoProfile', '-NonInteractive', '-Command',
                   '(Get-CimInstance Win32_VideoController).Name'], timeout=30)
    software = re.compile(r'Microsoft Basic|Remote Display|Hyper-V|Virtual', re.I)
    return [name.strip() for name in listed.splitlines() if name.strip() and not software.search(name)]


def plan(profile: str = 'auto', system: str | None = None, machine: str | None = None) -> list[dict]:
    """The builds setup tries, best first, each with why; the CPU build
    always ends the list. The cpu profile asks for the CPU only. macOS uses
    the CPU build, whose Apple Silicon wheel includes Core ML."""
    system = system or platform.system()
    machine = (machine or platform.machine()).lower()
    steps, notes = [], []

    def add(name: str, reason: str, **extra):
        steps.append({**asdict(BUILDS[name]), 'reason': reason, **extra})

    if profile != 'cpu' and machine in ('x86_64', 'amd64'):
        nvidia = nvidia_gpu() if system in ('Linux', 'Windows') else ''
        if nvidia:
            add('cuda', nvidia)
        if system == 'Linux':
            version, wheels = rocm_version(), rocm_wheels()
            if version and version not in wheels:
                notes.append(f'ROCm {version} has no pinned ONNX Runtime build (pinned: {", ".join(sorted(wheels))})')
            elif version and rocm_gpu():
                add('rocm', f'ROCm {version}', wheel=wheels[version])
            elif version:
                notes.append(f'ROCm {version} lists no GPU agent')
            elif amd_gpu():
                notes.append(f'an AMD GPU needs ROCm {" or ".join(sorted(wheels))} installed; '
                             'install it and run imprint setup again')
        if system == 'Windows':
            adapters = directx12_gpus()
            if adapters:
                add('directml', ', '.join(adapters))
    add('cpu', '; '.join(notes) or ('the fallback' if steps else 'no GPU build applies'))
    return steps


def check(build: str) -> dict:
    """Whether the installed ONNX Runtime is ``build``, complete and alone,
    and runs the tiny model on its GPU provider."""
    expected = BUILDS[build]
    found = providers()
    if list(found) != [expected.distribution] or not all(found.values()):
        return {'ok': False, 'detail': f'installed: {found}'}
    try:
        import numpy as np
        import onnxruntime as ort
        if not expected.provider:
            ort.InferenceSession
            return {'ok': True, 'detail': 'CPU build'}
        if expected.provider not in ort.get_available_providers():
            return {'ok': False, 'detail': f'{expected.provider} is not available'}
        if build == 'cuda' and hasattr(ort, 'preload_dlls'):
            ort.preload_dlls()
        options = ort.SessionOptions()
        options.log_severity_level = 4
        session = ort.InferenceSession(_TINY_MODEL, sess_options=options,
                                       providers=[expected.provider, 'CPUExecutionProvider'])
        if session.get_providers()[0] != expected.provider:
            return {'ok': False, 'detail': f'{expected.provider} did not load'}
        x = np.array([[1.0, 2.0, 3.0, 4.0]], dtype=np.float32)
        if not np.allclose(session.run(None, {'x': x})[0], x * x):
            return {'ok': False, 'detail': f'{expected.provider} computed a wrong result'}
    except Exception as error:
        return {'ok': False, 'detail': f'{type(error).__name__}: {error}'}
    return {'ok': True, 'detail': f'{expected.provider} runs'}


if __name__ == '__main__':
    command = sys.argv[1:2]
    if command == ['unused']:
        print(json.dumps(unused(build=(sys.argv[2:3] or ['cpu'])[0])))
    elif command == ['plan']:
        print(json.dumps(plan((sys.argv[2:3] or ['auto'])[0])))
    elif command == ['check']:
        print(json.dumps(check(sys.argv[2])))
    else:
        print(json.dumps(providers()))
