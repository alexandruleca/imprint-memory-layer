---
title: ADR 0121 — Optional ingestion tools install when turned on
description: Turning on ingest.use_kreuzberg or ingest.use_obscura installs and checks the tool; setup, the dashboard and first use repair a missing one, and failures stay visible.
---

- Status: Accepted
- Date: 2026-10-31
- Related: [ADR 0109](0109-automatic-ocr-runtime-repair.md) (the same repair-once rule for bundled OCR), [ADR 0007](0007-verified-runtime-and-desktop-baseline.md) (hashed dependencies and downloads), [ADR 0111](0111-intel-macs-are-not-supported.md)

## Context

`ingest.use_kreuzberg` and `ingest.use_obscura` turned on extractors whose tools Imprint did not install. The documentation asked users to run `pip install kreuzberg[all]` (into which Python was left to them) and to put an obscura binary on PATH. A user turned both on from the dashboard on Ubuntu; neither tool was installed, `imprint/extractors/kreuzberg.py` registered nothing without the package, and `_obscura_bin()` raised an error that `extractors/url.py` swallowed, so both settings silently did nothing. The user asked Imprint to fix such problems itself.

Checking the kreuzberg extractor against kreuzberg 4.10.4 also showed that `extract_bytes_sync` requires a MIME type, so every URL and MIME extraction through kreuzberg failed, and that its image OCR needs Tesseract language data that the wheel does not ship.

## Decision

Turning a setting on is the request to install its tool, and for kreuzberg the acceptance of the Elastic License 2.0. `imprint/optional_tools.py` installs and checks each tool:

- **kreuzberg** is installed into Imprint's venv from `requirements/kreuzberg.lock.txt`, hash-locked like the other requirement files, with `--require-hashes --only-binary :all:` through setup's uv (`IMPRINT_UV`, the bundled `bin/uv`, then PATH) or, without uv, the venv's pip. The lock pins plain `kreuzberg` 4.10.4, which has no dependencies. The `[all]` extra only adds EasyOCR and PyTorch, a backend the extractor never selects; it does not provide Tesseract's language data either. PyPI has wheels only for Linux x64/arm64 (manylinux 2.28), Apple silicon macOS and Windows x64; the requirement's markers and `--only-binary` keep other platforms from building it from Rust source, and Imprint reports that it cannot be installed there. A fresh interpreter must import it afterwards.
- **obscura** is the pinned v0.2.5 plain release (`obscura-{x86_64,aarch64}-linux.tar.gz`, `obscura-aarch64-macos.tar.gz`, `obscura-x86_64-windows.zip`), with SHA-256 values in `requirements/runtime-manifest.json`. It is downloaded into a staging directory under `data/obscura-bin/`, verified, and only the `obscura` and `obscura-worker` files are extracted; `obscura --version` must run before both are moved into place, the CLI last. `_obscura_bin()` uses that binary, then one on PATH.

Installation happens in four places. `imprint config set` installs the tool before returning and prints the failure as the setting's effect. The dashboard's settings endpoint starts the install in a background thread; the setting's On choice carries its state (installing, the failure with its manual fallback, or nothing once installed), which the settings page shows under the switch. Setup, and so `imprint bootstrap` and `imprint update`, installs every turned-on tool that is missing in a new step before the extractor check, which reports each turned-on tool; a failure there is a warning, not a failed setup. The first extraction that needs a missing tool installs it once per process, as ADR 0109 does for OCR, and prints the failure once with the manual fallback. `imprint doctor` reports a turned-on tool that is not installed.

The kreuzberg extractor now registers whenever its setting is on, wraps the native extractor it replaces, and hands a file to that native extractor when kreuzberg is unavailable or fails on it. Formats only kreuzberg reads (archives, iWork, ODF) report the tool's state instead. Byte extraction passes the MIME type kreuzberg requires.

## Rejected alternatives

Running `imprint bootstrap` from the dashboard or an extraction worker, as ADR 0109 does, was rejected: installing either tool only adds files, so no running process needs to be stopped or restarted, while bootstrap takes the queue and runtime locks, reinstalls every dependency and may restart the dashboard that asked for it. Installing kreuzberg in every installation was rejected because its license is the Elastic License 2.0 and the user must opt in. `kreuzberg[all]` was rejected for the size of PyTorch and EasyOCR, which the extractor does not use. Building kreuzberg from source where no wheel exists was rejected because it needs a Rust toolchain users usually lack. Requiring obscura on PATH was the state the user reported as broken.

## Consequences

The settings now work on a fresh Linux, macOS or Windows installation without manual steps, and an installation that turned them on before this change is repaired by its next setup, update or extraction. The first document or URL after turning a tool on may wait for a download: kreuzberg's wheel is 29 to 37 MB and obscura about 70 MB. Images, scanned PDFs and anything else kreuzberg cannot read fall back to the native extractors, including RapidOCR, so turning kreuzberg on does not lose OCR. Independent processes may each try a failing install once; there is no machine-wide cooldown. A background install's result is visible after the settings page reloads its settings; it does not update itself. Turning a setting off leaves the tool installed. The obscura release is upstream's build, verified by hash only.

## Invalidation conditions

If kreuzberg publishes wheels for more platforms or the extractor needs a newer release, regenerate the lock and widen the markers. If obscura's release layout or CLI changes (`--version`, `fetch --dump text|links`, the worker file), update the pinned version, hashes and extraction. If kreuzberg ships its own OCR language data, reconsider routing images to it without a native fallback. If independent processes repeatedly retry a failing install, add an installation-wide cooldown.

## Validation

On Linux x64: kreuzberg 4.10.4 was installed from the lock into a scratch venv and extracted HTML (with tables) and a ZIP archive; its PNG OCR failed for missing Tesseract language data. The x86_64 Linux, x86_64 Windows and arm64 macOS obscura archives were downloaded and matched the pinned hashes; the Linux and macOS tarballs and the Windows zip hold only `obscura` and `obscura-worker` (`.exe` on Windows), and the Linux binary's `--version` printed `obscura 0.2.5`. Python unit tests cover the install commands, archive extraction with a fake release, the once-per-process rule, status messages, the native fallback and the settings and setup hooks; Go tests cover setup's tool lines. Installing on Windows and macOS, the arm64 Linux archive, and an install from the running dashboard were not exercised.
