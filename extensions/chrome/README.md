# Imprint Capture (WIP)

Load this directory with Chrome's **Load unpacked** action at
`chrome://extensions`. No extension build or dependencies are needed.

Run the backend from the `wip/chrome-extension` checkout, then open extension
**Settings** for the workspace pairing command. Page/selection capture and
expanded Gmail messages are experimental; PDFs, attachments and Google Docs
exports are not implemented.

See [setup, permissions, data retention and validation](../../docs/browser-extension.md).

```bash
node --test extensions/chrome/tests/*.test.js
# From the repository root, after installing the dashboard's Playwright tooling:
node extensions/chrome/tests/browser-smoke.mjs
```
