#!/usr/bin/env bash
# Download the Firefox ESR and geckodriver the Firefox add-on smoke test uses
# (extensions/chrome/tests/firefox-smoke.mjs), pinned by version and SHA-256.
# Linux x86-64 only. Usage: scripts/fetch-firefox.sh <directory>
# Prints FIREFOX_BINARY= and GECKODRIVER= lines, and appends them to
# $GITHUB_ENV when it is set. Firefox's system libraries are not included:
# `npx playwright install-deps firefox` installs them on Debian/Ubuntu.
set -euo pipefail

FIREFOX_VERSION=153.7.0esr
FIREFOX_SHA256=7d2eb92ca3a7b2a83de092eb9b94c853980214de2fbfdccb2ac39d61c593c5a5
GECKODRIVER_VERSION=0.37.1
GECKODRIVER_SHA256=e815130ea95983e162ae91843b48d3a3ce991735635fce83a647afde21e09f7e

dir=${1:?usage: fetch-firefox.sh <directory>}
mkdir -p "$dir"
dir=$(cd "$dir" && pwd)

fetch() {  # url sha256 file
  if [ ! -f "$3" ] || ! echo "$2  $3" | sha256sum -c --status -; then
    curl -fsSL --retry 5 --retry-delay 5 --retry-all-errors -o "$3.part" "$1"
    echo "$2  $3.part" | sha256sum -c --quiet -
    mv "$3.part" "$3"
  fi
}

fetch "https://archive.mozilla.org/pub/firefox/releases/$FIREFOX_VERSION/linux-x86_64/en-US/firefox-$FIREFOX_VERSION.tar.xz" \
  "$FIREFOX_SHA256" "$dir/firefox.tar.xz"
fetch "https://github.com/mozilla/geckodriver/releases/download/v$GECKODRIVER_VERSION/geckodriver-v$GECKODRIVER_VERSION-linux64.tar.gz" \
  "$GECKODRIVER_SHA256" "$dir/geckodriver.tar.gz"
rm -rf "$dir/firefox" "$dir/geckodriver"
tar -xJf "$dir/firefox.tar.xz" -C "$dir"
tar -xzf "$dir/geckodriver.tar.gz" -C "$dir"

echo "FIREFOX_BINARY=$dir/firefox/firefox"
echo "GECKODRIVER=$dir/geckodriver"
if [ -n "${GITHUB_ENV:-}" ]; then
  { echo "FIREFOX_BINARY=$dir/firefox/firefox"; echo "GECKODRIVER=$dir/geckodriver"; } >> "$GITHUB_ENV"
fi
