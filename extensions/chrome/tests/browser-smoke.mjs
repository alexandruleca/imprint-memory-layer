// Uses the repository dashboard's existing Playwright dependency; no extension build/deps.
import assert from "node:assert/strict";
import {mkdtemp, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {resolve, dirname} from "node:path";
import {fileURLToPath} from "node:url";
import {createRequire} from "node:module";
import {createServer} from "node:http";
import {extractCapture} from "../capture.js";

const here = dirname(fileURLToPath(import.meta.url));
const extensionPath = resolve(here, "..");
const require = createRequire(import.meta.url);
const {chromium} = require(resolve(here, "../../../imprint/ui/node_modules/@playwright/test"));
const profile = await mkdtemp(resolve(tmpdir(), "imprint-extension-"));
const requests = [];
const api = createServer(async (request, response) => {
  let body = "";
  for await (const chunk of request) body += chunk;
  requests.push({url: request.url, headers: request.headers, body});
  response.setHeader("Content-Type", "application/json");
  if (request.url === "/api/browser/status") response.end(JSON.stringify({workspace: "fixture"}));
  else if (request.url === "/api/browser/captures") {
    response.statusCode = 202;
    response.end(JSON.stringify({job_id: "a".repeat(32)}));
  } else if (request.url.startsWith("/api/browser/jobs/")) response.end(JSON.stringify({status: "done"}));
  else {response.statusCode = 404; response.end("{}");}
});
await new Promise((done) => api.listen(0, "127.0.0.1", done));
let context;
try {
  context = await chromium.launchPersistentContext(profile, {channel: "chromium", headless: true,
    ignoreDefaultArgs: ["--disable-extensions"],
    args: [`--disable-extensions-except=${extensionPath}`, `--load-extension=${extensionPath}`]});
  const worker = context.serviceWorkers()[0] || await context.waitForEvent("serviceworker");
  const extensionId = new URL(worker.url()).hostname;
  const options = await context.newPage();
  await options.goto(`chrome-extension://${extensionId}/options.html`);
  await options.getByLabel("Local Imprint address").fill(`http://127.0.0.1:${api.address().port}`);
  await options.getByLabel("Pairing token").fill("disposable-fixture-token");
  await options.getByRole("button", {name: "Connect and save"}).click();
  await options.waitForFunction(() => document.getElementById('feedback').textContent.length > 0);
  assert.equal(await options.locator('#feedback').textContent(), 'Connected to workspace: fixture');
  assert.equal(requests[0].headers["x-imprint-extension-id"], extensionId);
  assert.equal(requests[0].headers.authorization, "Bearer disposable-fixture-token");

  const article = await context.newPage();
  await article.route("https://capture.example/**", (route) => route.fulfill({contentType: "text/html", body:
    `<title>Fixture article</title><nav>Navigation should disappear</nav><article><h1>Useful article</h1><p>Visible article text worth saving to memory.</p><p hidden>Hidden secret</p><textarea>Unsent draft</textarea><div contenteditable="true">Editable secret</div><input type="password" value="password-secret"></article>`}));
  await article.goto("https://capture.example/article");
  let documents = await article.evaluate(extractCapture);
  assert.equal(documents[0].title, "Fixture article");
  assert.match(documents[0].text, /Visible article text/);
  for (const excluded of ["Navigation", "Hidden secret", "Unsent draft", "Editable secret", "password-secret"]) assert.ok(!documents[0].text.includes(excluded));
  await article.evaluate(() => {
    const range = document.createRange();
    range.selectNodeContents(document.querySelector("p"));
    getSelection().removeAllRanges();
    getSelection().addRange(range);
  });
  documents = await article.evaluate(`(${extractCapture.toString()})("selection")`);
  assert.equal(documents[0].kind, "selection");
  assert.equal(documents[0].text, "Visible article text worth saving to memory.");

  const gmail = await context.newPage();
  await gmail.route("https://mail.google.com/**", (route) => route.fulfill({contentType: "text/html", body:
    `<title>Gmail fixture</title><div role="main"><h2 class="hP">Fixture subject</h2><div data-legacy-message-id="abc123"><span class="gD" email="sender@example.com">Sender</span><span class="g3" title="2026-10-23">Today</span><div class="a3s">Expanded email body worth remembering.</div></div><div data-legacy-message-id="def456" hidden><div class="a3s">Collapsed email must not be captured.</div></div><div role="textbox" contenteditable="true">Unsent email draft</div></div>`}));
  await gmail.goto("https://mail.google.com/mail/u/0/#inbox/thread");
  documents = await gmail.evaluate(`(${extractCapture.toString()})("page", "me@example.com")`);
  assert.equal(documents.length, 1);
  assert.equal(documents[0].kind, "gmail");
  assert.equal(documents[0].message_id, "abc123");
  assert.match(documents[0].text, /sender@example.com/);
  assert.ok(!documents[0].text.includes("Unsent"));
  await gmail.locator('[data-legacy-message-id="abc123"]').evaluate((node) => node.remove());
  const missing = await gmail.evaluate(`(${extractCapture.toString()})("page", "me@example.com")`);
  assert.match(missing.error, /Open an email/);

  // Drive the actual popup bridge/save/poll flow from its extension origin.
  const popup = await context.newPage();
  await popup.goto(`chrome-extension://${extensionId}/popup.html`);
  await popup.getByText("Connected · workspace: fixture").waitFor();
  const saved = await popup.evaluate(async (documents) => chrome.runtime.sendMessage({type: "save", capture: {project: "fixture", documents}}), documents);
  assert.equal(saved.ok, true);
  const job = await popup.evaluate(async () => chrome.runtime.sendMessage({type: "job"}));
  assert.equal(job.data.status, "done");
  assert.equal(JSON.parse(requests.find((request) => request.url.endsWith("/captures")).body).documents[0].message_id, "abc123");
  const accessLevel = await worker.evaluate(async () => chrome.storage.local.get("settings"));
  assert.equal(accessLevel.settings.token, "disposable-fixture-token");
  const local = await context.newPage();
  await local.goto(`http://127.0.0.1:${api.address().port}/fixture`);
  const contentAccess = await worker.evaluate(async () => {
    const [tab] = await chrome.tabs.query({url: 'http://127.0.0.1/*'});
    return chrome.scripting.executeScript({target: {tabId: tab.id}, func: async () => {
      let storageDenied = false;
      let bridgeDenied = false;
      try {await chrome.storage.local.get('settings');} catch {storageDenied = true;}
      try {
        const response = await chrome.runtime.sendMessage({type: 'status'});
        bridgeDenied = response === undefined;
      } catch {bridgeDenied = true;}
      return {storageDenied, bridgeDenied};
    }});
  });
  assert.deepEqual(contentAccess[0].result, {storageDenied: true, bridgeDenied: true});
  console.log("Chromium extension pairing, extraction, Gmail fixtures and save/status smoke passed.");
} finally {
  await context?.close();
  await new Promise((done) => api.close(done));
  await rm(profile, {recursive: true, force: true});
}
