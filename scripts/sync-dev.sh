#!/usr/bin/env bash
# Never overwrite dev-only commits. A divergence needs an explicit merge.
set -euo pipefail
git fetch origin main dev
if ! git merge-base --is-ancestor origin/dev origin/main; then
  echo 'dev has commits outside main; synchronize the histories before retrying.' >&2
  exit 1
fi
git push origin refs/remotes/origin/main:refs/heads/dev
