---
title: Chrome capture extension (WIP)
description: Download the Chrome extension ZIP, install it manually, pair one workspace, and preview pages or selected text before indexing.
---

## Status and scope

The Manifest V3 extension lives in [`extensions/chrome`](../extensions/chrome/)
and is distributed as `imprint-chrome-extension.zip` in GitHub releases. This is
a preview, installed manually rather than through the Chrome Web Store. No cloud capture service or Gmail OAuth client
is required. It sends the text you explicitly preview and save to the local
Imprint HTTP API, then the existing queue runs the inline ingestion pipeline.

Available now: ordinary readable HTTP(S) pages and selected text. The preview is
editable. Saving shows queued/running/completed status; accepted jobs continue
after the popup closes. Reopening the popup checks the last submitted job.

**Coming soon:** Gmail, PDFs and attachments, Google Docs/Sheets, OCR, background
capture, mailbox sync, offline retries, extension search and Chrome Web Store
distribution. Gmail and Google Docs/Sheets show a coming-soon message instead
of being captured, including when selected text is requested on those sites.

## Download and install

The [Chrome extension page](https://imprintmcp.alexandruleca.com/chrome-extension/)
links to the newest published stable release containing the extension ZIP. A
newer desktop release without that asset does not break the previous download.

1. Install and start Imprint from the same release as the ZIP. Older backends
   without `/api/browser` need an update; development setup is below.
2. Download and extract `imprint-chrome-extension.zip` to a permanent folder.
3. Open `chrome://extensions`, enable **Developer mode**, choose **Load unpacked**,
   and select the extracted `imprint-chrome-extension` folder containing
   `manifest.json`. Pin the extension from Chrome's Extensions menu.
4. Open its **Settings**, run the pairing command with the backend's interpreter
   and data directory, paste the token and choose **Connect and save**.

The ZIP includes `INSTALL.txt` with standalone instructions. Chrome loads the
extracted folder; it does not install this ZIP directly. For updates, replace
the files in that same folder and click **Reload** in `chrome://extensions`.
Moving the folder can change the unpacked extension ID and require pairing again.

Build locally with `make chrome-extension-zip`, or
`python3 scripts/package_chrome_extension.py`. Outputs:
`dist/imprint-chrome-extension.zip` and its `.zip.sha256` checksum. The builder
uses an explicit runtime-file allowlist and fixed ZIP timestamps; tests,
package manifests, development dependencies, caches and local credentials are
excluded. Release CI loads the extracted ZIP in disposable Chromium before
including it in the complete release and `SHA256SUMS`. See [ADR 0030](adr/0030-chrome-extension-release-zip.md).

## Install from this checkout

1. Use this branch's backend and an interpreter with Imprint's base requirements.
   From the repository root, start it with
   `python -m imprint.api --dev --no-browser`. Default address:
   `http://127.0.0.1:8420`. An older installed backend has no browser API; stop
   the old listener or use `--port 8421` and set that address in the extension.
2. Open `chrome://extensions`, enable **Developer mode**, choose **Load unpacked**,
   and select the repository's `extensions/chrome` directory. No npm install or
   build is needed. Pin **Imprint Capture (WIP)** to the toolbar.
3. Open the extension's **Settings**. It shows a pairing command containing its
   actual extension ID. Run that command from this checkout with the backend's
   interpreter and the same `IMPRINT_DATA_DIR`, if overridden:

   ```bash
   python -m imprint.browser_capture EXTENSION_ID --workspace default
   ```

   The workspace must already exist. Each pairing grants one workspace;
   re-pairing rotates its token and can select another workspace. Paste the
   printed token into settings, confirm the local address, then **Connect and
   save**. Do not commit or share tokens. Loading from a different checkout path
   can change the unpacked extension ID and require a new pairing.
4. On a page, click the toolbar icon, choose **Preview page** or
   **Preview selection**, review/edit the extracted text, choose a project,
   then **Save to Imprint**. The paired workspace is shown in the popup.

Gmail account settings are hidden in this preview. The backend retains the
initial message identity scaffold for future development; it does not make
Gmail capture an available extension feature.

## Connection and data boundaries

The extension requests `activeTab`, `scripting`, `storage`, and HTTP host access
to `127.0.0.1`. Page extraction runs only when requested. It does not register
persistent content scripts, access cookies, or automatically follow browsing.
Generic extraction excludes forms, editable regions, hidden text and common
navigation elements. Review the preview on sites with unusual markup.

The API preserves its loopback peer and Host checks. Only `/api/browser/*`
admits the exact paired `chrome-extension://ID` origin, with an ID header and
Bearer token. Chrome may omit Origin on extension GETs; the ID and token are
still required. Preflights allow only GET/POST and the three required headers.
The extension uses fixed capture/status routes and refuses redirects and
non-loopback addresses. Pairing credentials live in `chrome.storage.local`
with `TRUSTED_CONTEXTS` access; they are never passed into the captured page.
The worker accepts messages only from this extension's popup/settings pages.
The existing unauthenticated local dashboard/native-client API remains local;
pairing is not a general defense against other software on the same machine.

Pairings are stored as token hashes in `browser-pairings.sqlite3`, bound to an
extension ID and workspace. POSIX creates this file with mode 0600; on Windows
the data directory's ACL governs access. Capture JSON is bounded to 1,000,000
UTF-8 bytes, at most 50 documents, with 10–800,000 characters per document.
Only text and bounded metadata are accepted, never commands or filesystem
paths. The queue pins the workspace at submission. The worker takes the shared
ingestion lock and rechecks the grant before indexing; revoked/rotated queued
captures fail before writing. Revoking a grant does not interrupt a job already
indexing. Multi-message jobs can partially complete if a later message fails.

Captured text and metadata are retained in the local queue database/history
as well as indexed memories. Browser job status excludes raw payloads; the
local dashboard still has its normal queue access. These captures inherit the
selected workspace's sync settings and Imprint's model/tagger configuration;
an explicitly configured remote provider may receive content through existing
processing. Local browser transport does not override those settings.

Pages update by exact normalized URL (including query and fragment). The dormant
Gmail backend scaffold uses account + message ID rather than account-tab numbering.
Selected passages have separate content-based identities
and never replace full-page captures. Capture kind, title, account/message ID
and capture date are stored in existing `doc_metadata`, with `source_url`,
content hash and byte size on each chunk. Unchanged chunks retain their existing
document metadata (the shared source fingerprint/mtime is refreshed). Short
inline captures/notes that meet the 10-character floor are kept as one chunk,
even below the file chunker's 100-character minimum. Browser-supplied provenance
is not verified source authenticity.
`source_type=browser` keeps these snapshots out of URL auto-refresh.

To revoke backend access, run:

```bash
python -m imprint.browser_capture EXTENSION_ID --revoke
```

**Forget local connection** removes the browser's saved token/settings and last
job pointer. It does not revoke backend access or delete indexed memories.
Use Imprint's existing source purge workflow to remove indexed captures.

## Coming soon and validation

Gmail, PDF bytes, attachments, OCR, Google Docs/Sheets exports, iframe/shadow-DOM
coverage, background capture, mailbox synchronization, offline retries, search
inside the extension and Chrome Web Store publication are coming soon. Restricted
Chrome pages cannot be captured. Planned features have no announced delivery date.

Run from the repository root:

```bash
python -m unittest discover -s tests -p 'test_browser_capture.py' -v
python -m unittest discover -s tests -p 'test_http_security.py' -v
node --test extensions/chrome/tests/*.test.js
python -m unittest discover -s tests -p 'test_chrome_extension_package.py' -v
make chrome-extension-zip
# Reuses the dashboard's development dependency; never shipped with the extension.
npm ci --prefix imprint/ui
npx --prefix imprint/ui playwright install chromium
node extensions/chrome/tests/browser-smoke.mjs dist/imprint-chrome-extension.zip
```

The browser smoke extracts the actual ZIP and uses a disposable Chromium profile
and synthetic pages to test page/selection capture and coming-soon gates;
it never opens a real Gmail account or contributor profile. See
[platform validation](platform-validation.md) for executed checks and limits.
The design and alternatives are recorded in [ADR 0029](adr/0029-paired-browser-capture.md).
