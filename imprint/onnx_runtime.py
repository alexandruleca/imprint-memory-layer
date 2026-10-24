"""Installed ONNX Runtime distributions.

``onnxruntime`` and ``onnxruntime-gpu`` (and other vendor builds) are
alternative distributions that install files into the same ``onnxruntime``
package directory. Package installers track them independently: uninstalling
one deletes every file in its RECORD, including files the other also owns,
while the other still counts as installed. A sound runtime therefore has
exactly one provider whose RECORD files all exist (ADR 0032).

This module reads distribution metadata without importing ``onnxruntime``,
so it also works on a damaged runtime. ``python -m imprint.onnx_runtime``
prints the state as JSON for ``imprint setup``.
"""
from __future__ import annotations

import csv
import importlib.metadata as metadata
import json


def _record_complete(distribution: metadata.Distribution) -> bool:
    # Distribution.files cannot answer this: it leaves out missing files.
    record = distribution.read_text('RECORD')
    paths = [row[0] for row in csv.reader((record or '').splitlines()) if row]
    return bool(paths) and all(distribution.locate_file(path).exists() for path in paths)


def providers() -> dict[str, bool]:
    """Map each installed distribution that provides the ``onnxruntime``
    package to whether every file listed in its RECORD exists."""
    return {name: _record_complete(metadata.distribution(name))
            for name in sorted(set(metadata.packages_distributions().get('onnxruntime', [])))}


if __name__ == '__main__':
    print(json.dumps(providers()))
