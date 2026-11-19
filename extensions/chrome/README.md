# Imprint Capture

Load this directory with Chrome's **Load unpacked** action at
`chrome://extensions`. No extension build or dependencies are needed.

Download `imprint-chrome-extension.zip` from the latest GitHub release, extract
it to a permanent folder, and load the extracted folder containing `manifest.json`.
Start the Imprint backend from the same release and choose a workspace in the
popup. Save editable page text or a selection from the open tab, or submit the
**Link** for Imprint to fetch. Use **Settings** if your backend uses a custom port.
Choose **File in this tab** for attachments and Google Docs/Sheets, **Upload a file**
for local downloads, or **Screenshot text** (**Visible** or **Full page**) for
OCR. **Search** and **Record meeting** continue in the local dashboard using the
selected workspace. The recording form prefills available Meet or Teams titles.

See [setup, permissions, data retention and validation](../../docs/browser-extension.md).

```bash
node --test extensions/chrome/tests/*.test.js
make chrome-extension-zip  # creates dist/imprint-chrome-extension.zip + checksum
# From the repository root, after installing the dashboard's Playwright tooling:
node extensions/chrome/tests/browser-smoke.mjs
```
