#!/usr/bin/env bash
# Python 3.12 with requirements/base.lock.txt; see docs/benchmarks.md.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${PYTHON:-python3}" "$SCRIPT_DIR/run.py" "$@"
