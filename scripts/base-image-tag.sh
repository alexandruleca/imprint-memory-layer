#!/usr/bin/env bash
# Prints the base image (Dockerfile.base) for this checkout. Its tag hashes
# everything the base is built from, so it changes only when one of them does
# and an existing tag is never rebuilt.
set -euo pipefail
cd "$(dirname "$0")/.."
hash=$(cat Dockerfile.base requirements/base.lock.txt requirements/cpu.lock.txt requirements/llm.lock.txt \
    | sha256sum | cut -c1-16)
echo "${IMPRINT_BASE_REPOSITORY:-ghcr.io/alexandruleca/imprint-base}:$hash"
