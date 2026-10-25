"""Installed ONNX Runtime distributions.

``onnxruntime`` and ``onnxruntime-gpu`` (and other vendor builds) are
alternative distributions that install files into the same ``onnxruntime``
package directory. Package installers track them independently: uninstalling
one deletes every file in its RECORD, including files the other also owns,
while the other still counts as installed. A sound runtime therefore has
exactly one provider whose RECORD files all exist (ADR 0032).

``unused`` names what setup removes after installing one build: the
distributions either build brought in that this installation's requirements
no longer name, such as the NVIDIA libraries once embeddings run on the CPU
(ADR 0062).

This module reads distribution metadata without importing ``onnxruntime``,
so it also works on a damaged runtime. ``python -m imprint.onnx_runtime``
prints the providers as JSON for ``imprint setup``, and
``python -m imprint.onnx_runtime unused cpu|gpu`` the distributions to remove.
"""
from __future__ import annotations

import csv
import importlib.metadata as metadata
import json
import re
import sys
from pathlib import Path

REQUIREMENTS = Path(__file__).resolve().parent.parent / 'requirements'
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


def unused(selected: str, requirements: Path = REQUIREMENTS, distributions=None) -> list[str]:
    """Installed distributions to remove once the ``selected`` runtime (cpu or
    gpu) is installed: the other build, what only it needs, and what an older
    lock of either brought in.

    Every distribution either runtime's requirements name is a root. Setup
    follows the installed distributions' requirements from there, a root's
    extras included, since a root may have been installed with extras
    (``onnxruntime-gpu[cuda,cudnn]`` before ADR 0062). Distributions sharing a
    package directory with a removed one go with it: NVIDIA's wheels share
    ``nvidia/``, so libraries an older lock installed go too, even after the
    build that required them is gone. Nothing a requirements file of this
    installation names, other than the other runtime's, is removed, and
    nothing another installed distribution still requires.
    """
    other = {'cpu': 'gpu', 'gpu': 'cpu'}[selected]
    roots, keep = set(), set()
    for path in requirements.glob('*.txt'):
        names = requirement_names(path)
        if path.name in (f'{other}.txt', f'{other}.lock.txt'):
            roots |= names
        else:
            keep |= names
            if path.name in (f'{selected}.txt', f'{selected}.lock.txt'):
                roots |= names
    installed = {normalize(d.metadata['Name']): d
                 for d in (metadata.distributions() if distributions is None else distributions)}
    beside: dict[str, set[str]] = {}
    for name, distribution in installed.items():
        for top in _top_levels(distribution):
            beside.setdefault(top, set()).add(name)
    reached, pending = set(), [name for name in roots if name in installed]
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


if __name__ == '__main__':
    if sys.argv[1:2] == ['unused']:
        print(json.dumps(unused(sys.argv[2])))
    else:
        print(json.dumps(providers()))
