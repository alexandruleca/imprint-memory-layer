#!/usr/bin/env bash
# Builds the throwaway runners' image (Dockerfile.ci-runner) for amd64 and
# arm64, as imprint-ci-runner:amd64 and imprint-ci-runner:arm64, from this
# checkout's lock files and go.mod. Run it again after a lock or Go change;
# running slots pick the new image up with their next job. The arm64 build
# installs under emulation and takes a while.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
GO_VERSION=$(awk '$1 == "go" { print $2; exit }' "$ROOT/go.mod")
for arch in ${IMPRINT_CI_RUNNER_ARCHES:-amd64 arm64}; do
    sha=$(curl -fsSL 'https://go.dev/dl/?mode=json&include=all' | python3 -c '
import json, sys
name = "go%s.linux-%s.tar.gz" % (sys.argv[1], sys.argv[2])
print(next(f["sha256"] for r in json.load(sys.stdin) for f in r["files"] if f["filename"] == name))
' "$GO_VERSION" "$arch")
    echo "Building imprint-ci-runner:$arch (Go $GO_VERSION)..."
    docker build --platform "linux/$arch" \
        --build-arg GO_VERSION="$GO_VERSION" --build-arg GO_SHA256="$sha" \
        -f "$ROOT/.github/runners/Dockerfile.ci-runner" -t "imprint-ci-runner:$arch" "$ROOT"
done
