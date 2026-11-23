---
title: Chrome and Firefox capture extension (WIP)
description: Connect to local Imprint, choose a workspace, and capture pages and files, start meetings, and search Imprint from Chrome or Firefox.
---

## Status and scope

The Manifest V3 extension lives in [`extensions/chrome`](../extensions/chrome/)
and is published as [Imprint Capture in the Chrome Web Store](https://chromewebstore.google.com/detail/imprint-capture/ammleledmkghliflljeapomaaccnhmme),
which every stable release updates ([ADR 0202](adr/0202-stable-releases-submit-the-extension-to-the-chrome-web-store.md)).
GitHub releases also carry it as `imprint-chrome-extension.zip` to load by hand. The same
source is built as a Firefox add-on ([Firefox](#firefox) below). No cloud capture service or Gmail OAuth client
is required. The popup connects automatically to the local Imprint HTTP API and lists existing
workspaces. Choose a destination and preview what you want to save.

The popup names the current tab's kind (web page, PDF, Google Doc or Sheet,
Gmail, meeting, or a browser page) and puts the action that suits it first.
Actions the tab cannot support are disabled with the reason in place of their
description; on Chrome's own pages only **Upload a file** is offered. Choosing
an action opens a review step with the title, the content or image, and the
workspace and project next to **Save to Imprint** (Ctrl+Enter; Esc goes back). The project field suggests the last six projects used.

**Page text** and **Selection** save editable snapshots of readable text already
rendered in the tab, including on signed-in pages. **Link** queues a fresh fetch
by Imprint, which has no access to your browser session and fetches only the
submitted page without crawling links. After saving, a card shows the capture's
title and its queued, indexing, indexed or failed status, with **View** (search
for it in the dashboard) or **Open queue**; accepted jobs continue after the
popup closes. Reopening the popup checks the last submitted job for a day, and
the card can be dismissed. If Imprint is not running, the popup says so and
connects again by itself once it starts.

### Files, Google Workspace and OCR

- **File in this tab** reads a supported file at the current HTTP(S) address or a same-origin
  attachment link you have selected or focused. On a Google Docs or Sheets tab
  the action is named **Google Doc** or **Google Sheet** and exports it to DOCX or XLSX
  using the browser session. The workbook export includes all sheets.
- **Upload a file** opens an extension tab where you choose, drop or paste a file
  (a pasted screenshot uploads as an image). Use it for downloads,
  local PDFs, attachments behind a restricted viewer, or a Google export if the
  direct export is unavailable. Supported files are PDF, DOCX, XLSX, PPTX, TXT,
  Markdown, CSV, TSV, PNG, JPEG, WebP, GIF, BMP and TIFF. The file limit is the
  smaller of 25 MiB and Imprint's `ingest.max_doc_size_mb`.
- **Screenshot text** reads a screenshot with local text recognition. **Visible**
  captures the current viewport. **Full page** scrolls the tab one screen at a
  time, about two screens a second, and stitches them into one image: fixed
  banners are left out, a sticky header appears once at the top, and the page
  returns to where it was. A page that scrolls inside a panel, such as Gmail,
  gives its visible area, and a very long page is cut at 32,000 px
  ([ADR 0191](adr/0191-chrome-full-page-screenshots-are-stitched-in-the-popup.md)).
  Imprint reads images taller than 2,000 px in overlapping bands, so long
  captures stay legible ([ADR 0190](adr/0190-ocr-reads-tall-images-in-bands.md)).
  Image uploads and PDFs with little or no text also use Imprint's existing OCR
  pipeline when it is enabled. For all pages of a scanned PDF, upload the PDF itself.

Chrome can withhold direct access to its built-in PDF viewer, and sites can
block exports or cross-origin file reads. Use the viewer's download button or
Google's **File → Download**, then select the resulting file. The capture
does not read the browser's downloads folder on its own.

### Search and meetings

- **Search** opens the dashboard with the query in the selected workspace.
  Results, individual memories and source reading use that workspace.
- **Record meeting** opens the dashboard recording form with the chosen
  workspace and project. Google Meet and Microsoft Teams tab titles are used as
  editable suggestions. Confirm that participants know about the recording,
  click **Start recording**, and choose the call tab with audio in Chrome's
  sharing picker. Keep the dashboard tab open and use **Stop and transcribe**
  when the meeting ends.

## Download and install

The [Chrome extension page](https://imprintmcp.alexandruleca.com/chrome-extension/)
links to the newest published stable release containing the extension ZIP. A
newer desktop release without that asset does not break the previous download.

1. Install and start Imprint, and keep it up to date. Older backends without
   `/api/browser/local/status` need an update; development setup is below.
2. Open [Imprint Capture in the Chrome Web Store](https://chromewebstore.google.com/detail/imprint-capture/ammleledmkghliflljeapomaaccnhmme)
   and choose **Add to Chrome**. Chrome installs each new version by itself.
   Pin the extension from Chrome's Extensions menu.
3. Or, to load a release by hand: extract `imprint-chrome-extension.zip` to a
   permanent folder, open `chrome://extensions`, enable **Developer mode**, choose
   **Load unpacked**, and select the extracted `imprint-chrome-extension` folder
   containing `manifest.json`. This copy does not update itself.
4. Open the popup and choose a workspace. Use **Settings** only if Imprint runs
   on a port other than 8420. Settings also links to Chrome's shortcuts page,
   where you can give Capture a keyboard shortcut.

The release ZIP includes `INSTALL.txt` with standalone instructions and the project's
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
`--browser chrome-web-store` builds the same files at the archive's root, without
`INSTALL.txt`, as `dist/imprint-chrome-web-store.zip`: the store's upload.

## Firefox

The Firefox add-on is the same extension with a Firefox manifest, for Firefox 140
or later ([ADR 0193](adr/0193-firefox-capture-is-the-chrome-extension-signed-as-an-unlisted-add-on.md)).
It behaves as described above; the differences are where the browser differs.

- **Install.** Download `imprint-firefox-extension.xpi` from the release, or use the
  **Firefox add-on** button on the [extension page](https://imprintmcp.alexandruleca.com/chrome-extension/),
  and open it in Firefox (drag it onto a window, or **File → Open File**), then confirm.
  Mozilla signs it as an unlisted add-on: it installs in any Firefox but is not listed on
  addons.mozilla.org and does not update itself, so install a newer release's file the
  same way ([ADR 0206](adr/0206-the-firefox-add-on-goes-back-to-unlisted-signing.md)).
- **Unsigned builds.** Prereleases, and releases whose signing addons.mozilla.org
  throttled, carry `imprint-firefox-extension-unsigned.xpi`; so do v0.61.0 and v0.61.1,
  released while the add-on was meant to be listed. Release Firefox refuses it. Load it
  for the session from `about:debugging#/runtime/this-firefox` with **Load Temporary
  Add-on**, or install it in Developer Edition or Nightly with
  `xpinstall.signatures.required` set to `false` in `about:config`.
- **Shortcut.** **Set a shortcut** in the add-on's settings opens Firefox's
  **Manage Extension Shortcuts**.
- **Connection.** Firefox gives the add-on a random ID for each installation; Imprint
  checks requests against it ([ADR 0192](adr/0192-the-capture-api-accepts-firefox-extension-origins.md)).
  Reinstalling starts a new ID, so the popup forgets the previous installation's last capture.

Maintainers: signing needs an addons.mozilla.org API key (**Developer Hub → Manage
API Keys**) stored as the repository secrets `AMO_JWT_ISSUER` (the JWT issuer) and
`AMO_JWT_SECRET`. With both set, each stable release signs the add-on through
`scripts/sign_firefox_extension.py` and attaches the signed file in place of the
unsigned one. AMO throttles uploads: the script waits out a short throttle, and when
AMO asks it to wait longer than 20 minutes, the release ships with
only the unsigned add-on and a warning, and the website keeps offering the newest
signed one. Any other signing failure fails the release. AMO takes each version
number once across channels, so a number it already holds as listed fails the
signing; never hand-upload a prerelease build, because `v0.62.0-dev.1` becomes
0.62.0.1.

## Store publishing

After a stable release is published, the `chrome-web-store` job in `build-release.yml`
submits the package that passed the smoke tests. Prereleases are not submitted. The
job skips with a warning when its secrets are missing, and is safe to rerun: a version
the store already holds is not submitted again.

- **Chrome Web Store** (`chrome-web-store`, [ADR 0202](adr/0202-stable-releases-submit-the-extension-to-the-chrome-web-store.md)).
  `scripts/publish_chrome_extension.py` uploads `imprint-chrome-web-store.zip` to
  item `ammleledmkghliflljeapomaaccnhmme` through the Chrome Web Store API v2 and
  submits it for review with `DEFAULT_PUBLISH`: it goes live once approved, and Chrome
  updates every copy. While an earlier version is still in review it submits nothing
  and says so; rerun the job after that review, or the next release submits. Setup:
  1. In a Google Cloud project, enable the **Chrome Web Store API** and create a
     service account. It needs no IAM roles. Create a JSON key for it.
  2. In the Chrome Web Store Developer Dashboard, add the service account's email
     under **Account** (one service account per publisher), and copy the publisher
     ID from **Publisher → Settings**.
  3. Store the key file's whole JSON as the repository secret `CWS_SERVICE_ACCOUNT_JSON`
     and the publisher ID as `CWS_PUBLISHER_ID`.

## Install from this checkout

1. Use this branch's backend and an interpreter with Imprint's base requirements.
   From the repository root, run `make dashboard-build`, then
   `python -m imprint.api --no-browser`. Default address:
   `http://127.0.0.1:8420`. If that port is taken, use `--port 8421` and enter
   the new address in extension **Settings**.
2. In `chrome://extensions`, enable **Developer mode**, choose **Load unpacked**,
   and select `extensions/chrome`. Pin **Imprint Capture** to the toolbar.
3. Open a web page and click the toolbar icon. Pick a workspace and project,
   choose a capture action, review the preview, then click **Save to Imprint**. The first popup defaults to the active local
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
actual loopback peer, a loopback Host, an extension ID header (Chrome's
extension ID, or the UUID Firefox gives the installation), and the exact
matching extension Origin on submissions. Browsers can omit Origin on GET requests. Website origins are rejected, and an extension cannot
use this header to reach general dashboard routes. Any installed extension
with loopback host access can use the capture routes. This is a local trust
boundary. The automatic connection requires a native loopback backend and
does not traverse Docker NAT. Older extensions can keep using the original
paired routes described in the [HTTP API docs](api.md#browser-capture).

The extension refuses redirects and non-loopback addresses. The worker
accepts messages from its own popup and settings, and page scripts cannot
access its saved settings. Capture JSON is bounded to 1,000,000 UTF-8 bytes,
with at most 50 documents and 10–800,000 characters per document. A request
submits text with bounded metadata, one HTTP(S) URL, or a bounded binary file. The queue pins
the chosen workspace at submission and validates it again before ingestion.
Job status returns status and timing fields for the submitting extension ID.

Captured text and metadata are retained in the local queue database/history
as well as indexed memories. Uploaded bytes are kept locally in the queue,
deduplicated by digest, only while a job can use them: until the capture is
indexed, or for a week after it failed or was cancelled, so **Retry** works
without another upload. They are then deleted and their pages zeroed
([ADR 0189](adr/0189-captured-files-are-kept-only-while-a-job-can-use-them.md)).
They do not appear in job status, logs or command arguments. Browser job status excludes raw payloads; the
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

The [privacy policy](https://imprintmcp.alexandruleca.com/privacy/#extension) states the same boundaries for users and for the Chrome Web Store and addons.mozilla.org reviews: what each action reads, that captures go only to the loopback backend, and what the extension keeps in `chrome.storage.local`. Update it with any change to permissions, network destinations or stored settings.

## Coming soon and validation

Gmail message capture, iframe/shadow-DOM text capture, background capture,
mailbox synchronization and offline retries remain outside this preview.

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
# Firefox: a pinned Firefox ESR and geckodriver (Linux x86-64), then the add-on smoke.
npx --prefix imprint/ui playwright install-deps firefox   # system libraries, Debian/Ubuntu
export $(scripts/fetch-firefox.sh /tmp/imprint-firefox)
python3 scripts/package_chrome_extension.py --browser firefox
node extensions/chrome/tests/firefox-smoke.mjs dist/imprint-firefox-extension-unsigned.xpi
python -m unittest discover -s tests -p 'test_sign_firefox_extension.py' -v
python -m unittest discover -s tests -p 'test_publish_chrome_extension.py' -v
```

The browser smoke extracts the actual ZIP and uses a disposable Chromium profile
and synthetic pages to test discovery, workspace choice, signed-in page text,
URL submission, selection extraction, file upload, synthetic Workspace exports,
visible and full-page screenshot previews and search/recording links;
it never opens a real Gmail account or contributor profile. See
[platform validation](platform-validation.md) for executed checks and limits.
See [ADR 0185](adr/0185-local-chrome-capture.md), [ADR 0187](adr/0187-browser-file-capture.md), [ADR 0188](adr/0188-browser-dashboard-handoff.md) and [ADR 0191](adr/0191-chrome-full-page-screenshots-are-stitched-in-the-popup.md) for the connection, file storage, dashboard handoff and full-page screenshot decisions.
