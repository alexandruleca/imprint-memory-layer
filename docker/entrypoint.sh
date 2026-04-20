#!/usr/bin/env sh
# Imprint container entrypoint.
#
# Dispatches to the right Python process based on the Docker CMD. Qdrant is
# spawned lazily by imprint/vectorstore.py on first use (via qdrant_runner),
# so no manual bootstrapping needed.
#
# Known commands:
#   api     — dashboard FastAPI server on :8420 (default)
#   mcp     — stdio MCP server (for `docker exec -i` bridging)
#   shell   — drop to sh (for debugging)
#   <any>   — forwarded to `imprint` (the Go CLI), e.g. `ingest /workspace`

set -e

CMD="${1:-api}"
shift || true

case "$CMD" in
    api)
        exec python -m imprint.api --host 0.0.0.0 --port "${IMPRINT_PORT:-8420}" "$@"
        ;;
    mcp)
        exec python -m imprint
        ;;
    shell)
        exec sh "$@"
        ;;
    *)
        exec imprint "$CMD" "$@"
        ;;
esac
