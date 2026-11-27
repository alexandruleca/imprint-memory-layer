#!/usr/bin/env bash
# LongMemEval-S retrieval benchmark for Imprint, scored like ai-memory's evaluator.
#   evals/longmemeval/run.sh --fetch            full run (470 scored questions), resumable
#   evals/longmemeval/run.sh --sample 10        smoke test
#   evals/longmemeval/run.sh --run-dir DIR      resume DIR
#   evals/longmemeval/run.sh report DIR         rebuild DIR/report.md
# See evals/longmemeval/README.md.
set -euo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python="${IMPRINT_BENCH_PYTHON:-$repo/.venv/bin/python}"
if [ ! -x "$python" ]; then
    echo "No Imprint virtualenv at $python; see evals/longmemeval/README.md (Setup)." >&2
    exit 1
fi
cd "$repo"
exec "$python" -I "$repo/evals/longmemeval/harness.py" "$@"
