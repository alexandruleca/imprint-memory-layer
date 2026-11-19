# Imprint Capture

Load this directory with Chrome's **Load unpacked** action at
`chrome://extensions`. No extension build or dependencies are needed.

Download `imprint-chrome-extension.zip` from the latest GitHub release, extract
it to a permanent folder, and load the extracted folder containing `manifest.json`.
Start the Imprint backend from the same release and choose a workspace in the
popup. Save editable page text or a selection from the open tab, or submit the
URL for Imprint to fetch. Use **Settings** if your backend uses a custom port.
Gmail, PDFs, attachments, Google Docs/Sheets, OCR, background capture, mailbox
sync, offline retries and extension search are **Coming soon**.

See [setup, permissions, data retention and validation](../../docs/browser-extension.md).

```bash
node --test extensions/chrome/tests/*.test.js
make chrome-extension-zip  # creates dist/imprint-chrome-extension.zip + checksum
# From the repository root, after installing the dashboard's Playwright tooling:
node extensions/chrome/tests/browser-smoke.mjs
```
