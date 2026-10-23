#!/usr/bin/env bash
set -euo pipefail
image="${1:-imprint-audit:local}"
container_id=$(docker run -d -p 127.0.0.1::8420 "$image")
trap 'docker rm -f "$container_id" >/dev/null' EXIT
docker exec -i "$container_id" python - <<'PY'
import time
import urllib.error
import urllib.request
for attempt in range(60):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8420/api/ping', timeout=2) as response:
            assert response.status == 200
        break
    except (OSError, urllib.error.URLError):
        if attempt == 59:
            raise
        time.sleep(.5)
with urllib.request.urlopen('http://127.0.0.1:8420/') as response:
    assert b'<html' in response.read()
request = urllib.request.Request('http://127.0.0.1:8420/api/ping', headers={'Host': 'attacker.example'})
try:
    urllib.request.urlopen(request)
except urllib.error.HTTPError as error:
    assert error.code == 403
else:
    raise AssertionError('Non-loopback Host was accepted')
print('Container entry point, API/static serving and Host boundary passed')
PY
docker exec "$container_id" imprint doctor --json --probe-mcp
docker run --rm -i --entrypoint python "$image" - --with-storage < scripts/smoke-runtime.py
