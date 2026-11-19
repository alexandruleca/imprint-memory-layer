---
title: Chrome capture extension (WIP)
description: Connect to local Imprint, choose a workspace, and save page text, selected text, or a URL from Chrome.
---

## Status and scope

The Manifest V3 extension lives in [`extensions/chrome`](../extensions/chrome/)
and is distributed as `imprint-chrome-extension.zip` in GitHub releases. This is
a preview, installed manually rather than through the Chrome Web Store. No cloud capture service or Gmail OAuth client
is required. The popup connects automatically to the local Imprint HTTP API and lists existing
workspaces. Choose a destination and preview what you want to save.

**Page text** and **Selection** save editable snapshots of readable text already
rendered in the tab, including on signed-in pages. **URL** queues a fresh fetch
by Imprint, which has no access to your browser session and fetches only the
submitted page without crawling links. Saving shows queued/running/completed status; accepted jobs continue
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
   without `/api/browser/local/status` need an update; development setup is below.
2. Download and extract `imprint-chrome-extension.zip` to a permanent folder.
3. Open `chrome://extensions`, enable **Developer mode**, choose **Load unpacked**,
   and select the extracted `imprint-chrome-extension` folder containing
   `manifest.json`. Pin the extension from Chrome's Extensions menu.
4. Open the popup and choose a workspace. Use **Settings** only if Imprint runs
   on a port other than 8420.

The ZIP includes `INSTALL.txt` with standalone instructions and the project's
`LICENSE`. Chrome loads the
extracted folder; it does not install this ZIP directly. For updates, replace
the files in that same folder and click **Reload** in `chrome://extensions`.
Keep the same folder to preserve the extension ID and saved preferences.

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
   `http://127.0.0.1:8420`. If that port is taken, use `--port 8421` and enter
   the new address in extension **Settings**.
2. In `chrome://extensions`, enable **Developer mode**, choose **Load unpacked**,
   and select `extensions/chrome`. Pin **Imprint Capture** to the toolbar.
3. Open a web page and click the toolbar icon. Pick a workspace and project,
   choose **Page text**, **Selection**, or **URL**, review the preview, then
   click **Save to Imprint**. The first popup defaults to the active local
   workspace and remembers subsequent choices. Choosing a workspace here
   leaves the dashboard's active workspace as it was.

Gmail account settings are hidden in this preview. The backend retains the
initial message identity scaffold for future development; it does not make
Gmail capture an available extension feature.

## Connection and data boundaries

The extension requests `activeTab`, `scripting`, `storage`, and HTTP host access
to `127.0.0.1`. Page extraction runs only when requested. It does not register
persistent content scripts, access cookies, or automatically follow browsing.
Generic extraction excludes forms, editable regions, hidden text and common
navigation elements. Review the preview on sites with unusual markup.

The credential-free capture routes under `/api/browser/local/` require an
actual loopback peer, a loopback Host, a Chrome extension ID header, and an
exact extension Origin on submissions. Chrome can omit Origin on GET requests. Website origins are rejected, and an extension cannot
use this header to reach general dashboard routes. Any installed extension
with loopback host access can use the capture routes. This is a local trust
boundary. The automatic connection requires a native loopback backend and
does not traverse Docker NAT. Older extensions can keep using the original
paired routes described in the [HTTP API docs](api.md#browser-capture).

The extension refuses redirects and non-loopback addresses. The worker
accepts messages from its own popup and settings, and page scripts cannot
access its saved settings. Capture JSON is bounded to 1,000,000 UTF-8 bytes,
with at most 50 documents and 10–800,000 characters per document. A request
submits either text with bounded metadata or one HTTP(S) URL. The queue pins
the chosen workspace at submission and validates it again before ingestion.
Job status returns status and timing fields for the submitting extension ID.

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
and synthetic pages to test discovery, workspace choice, signed-in page text,
URL submission, selection extraction, and coming-soon gates;
it never opens a real Gmail account or contributor profile. See
[platform validation](platform-validation.md) for executed checks and limits.
The local connection and its trust boundary are recorded in [ADR 0185](adr/0185-local-chrome-capture.md).
