// Uses the repository dashboard's existing Playwright dependency; no extension build/deps.
import assert from "node:assert/strict";
import {mkdtemp, rm} from "node:fs/promises";
import {execFileSync} from "node:child_process";
import {tmpdir} from "node:os";
import {resolve, dirname} from "node:path";
import {fileURLToPath} from "node:url";
import {createRequire} from "node:module";
import {createServer} from "node:http";
import {extractCapture} from "../capture.js";

const here = dirname(fileURLToPath(import.meta.url));
const packagedZip = process.argv[2] ? resolve(process.argv[2]) : null;
const require = createRequire(import.meta.url);
const {chromium} = require(resolve(here, "../../../imprint/ui/node_modules/@playwright/test"));
const profile = await mkdtemp(resolve(tmpdir(), "imprint-extension-"));
const requests = [];
const api = createServer(async (request, response) => {
  let body = "";
  for await (const chunk of request) body += chunk;
  requests.push({url: request.url, headers: request.headers, body});
  if (request.url === "/signed-in") {
    response.setHeader("Content-Type", "text/html");
    response.end(request.headers.cookie === "fixture-session=ok"
      ? "<title>Signed in</title><main><p>Private workspace article visible in this tab.</p></main>"
      : "<main>Please sign in to read this article.</main>");
    return;
  }
  response.setHeader("Content-Type", "application/json");
  if (request.url === "/api/browser/local/status") response.end(JSON.stringify({active: "fixture", workspaces: ["fixture", "reading"]}));
  else if (request.url === "/api/browser/local/captures") {
    response.statusCode = 202;
    response.end(JSON.stringify({job_id: "a".repeat(32)}));
  } else if (request.url.startsWith("/api/browser/local/jobs/")) response.end(JSON.stringify({status: "done"}));
  else {response.statusCode = 404; response.end("{}");}
});
await new Promise((done) => api.listen(0, "127.0.0.1", done));
let context;
try {
  const python = process.env.PYTHON || 'python3';
  const zip = packagedZip || resolve(profile, 'extension.zip');
  if (!process.argv[2]) execFileSync(python, [resolve(here, '../../../scripts/package_chrome_extension.py'), '--output', zip]);
  execFileSync(python, ['-m', 'zipfile', '-e', zip, resolve(profile, 'unpacked')]);
  const extensionPath = resolve(profile, 'unpacked/imprint-chrome-extension');
  context = await chromium.launchPersistentContext(resolve(profile, 'browser'), {channel: "chromium", headless: true,
    ignoreDefaultArgs: ["--disable-extensions"],
    args: [`--disable-extensions-except=${extensionPath}`, `--load-extension=${extensionPath}`]});
  const worker = context.serviceWorkers()[0] || await context.waitForEvent("serviceworker");
  const extensionId = new URL(worker.url()).hostname;
  const options = await context.newPage();
  // Slow startup must not allow saved defaults to overwrite a typed address.
  await options.addInitScript(() => {
    const ready = new Promise((resume) => {globalThis.__resumeSettings = resume;});
    const original = chrome.runtime.sendMessage.bind(chrome.runtime);
    chrome.runtime.sendMessage = async (...args) => {
      if (args[0]?.type === 'settings') await ready;
      return original(...args);
    };
  });
  await options.goto(`chrome-extension://${extensionId}/options.html`);
  assert.equal(await options.getByLabel('Local Imprint address').isDisabled(), true);
  await options.evaluate(() => globalThis.__resumeSettings());
  await options.waitForFunction(() => !document.getElementById('save').disabled);
  await options.getByLabel("Local Imprint address").fill(`http://127.0.0.1:${api.address().port}`);
  await options.getByRole("button", {name: "Check and save"}).click();
  await options.waitForFunction(() => document.getElementById('feedback').textContent.length > 0);
  assert.equal(await options.locator('#feedback').textContent(), 'Connected · 2 local workspace(s)');
  assert.equal(requests[0].headers["x-imprint-extension-id"], extensionId);
  assert.equal(requests[0].headers.authorization, undefined);

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
  for (const mode of ['page', 'selection']) {
    const result = await gmail.evaluate(`(${extractCapture.toString()})(${JSON.stringify(mode)})`);
    assert.match(result.error, /Gmail capture is coming soon/);
  }
  await gmail.route('https://docs.google.com/**', (route) => route.fulfill({contentType: 'text/html', body: '<main>Document text</main>'}));
  await gmail.goto('https://docs.google.com/document/d/fixture');
  assert.match((await gmail.evaluate(extractCapture)).error, /Google Docs & Sheets capture is coming soon/);

  // Imprint never receives the browser session cookie used to render this tab.
  const origin = `http://127.0.0.1:${api.address().port}`;
  const url = origin + "/signed-in";
  await context.addCookies([{name: "fixture-session", value: "ok", url: origin}]);
  const privatePage = await context.newPage();
  await privatePage.goto(url);
  await privatePage.getByText("Private workspace article visible in this tab.").waitFor();
  const popup = await context.newPage();
  // Playwright opens the popup as a page, so target the fixture tab explicitly.
  await popup.addInitScript((url) => {
    const query = chrome.tabs.query.bind(chrome.tabs);
    chrome.tabs.query = () => query({url});
  }, url);
  await popup.goto(`chrome-extension://${extensionId}/popup.html`);
  await popup.getByText("Connected to local Imprint").waitFor();
  await popup.getByText('Coming soon', {exact: true}).waitFor();
  assert.deepEqual(await popup.locator('#workspace option').allTextContents(), ["fixture", "reading"]);
  await popup.getByLabel("Workspace").selectOption("reading");
  await popup.getByRole("button", {name: "Page text"}).click();
  await popup.getByLabel("Content to save").fill("Private workspace article, edited in preview.");
  await popup.getByRole("button", {name: "Save to Imprint"}).click();
  await popup.getByText("Indexed in Imprint.").waitFor();
  await popup.reload();
  await popup.getByText("Connected to local Imprint").waitFor();
  assert.equal(await popup.getByLabel("Workspace").inputValue(), "reading");
  await popup.getByRole("button", {name: "URL", exact: true}).click();
  await popup.locator("#preview").getByText(url).waitFor();
  await popup.getByRole("button", {name: "Save to Imprint"}).click();
  await popup.getByText("Sent to reading.").waitFor();
  const [pageCapture, urlCapture] = requests.filter((request) => request.url.endsWith("/captures"));
  assert.equal(pageCapture.headers.cookie, undefined);
  assert.equal(pageCapture.headers.authorization, undefined);
  assert.equal(pageCapture.headers.origin, `chrome-extension://${extensionId}`);
  const pagePayload = JSON.parse(pageCapture.body);
  assert.equal(pagePayload.workspace, "reading");
  assert.equal(pagePayload.documents[0].kind, "page");
  assert.equal(pagePayload.documents[0].text, "Private workspace article, edited in preview.");
  assert.deepEqual(JSON.parse(urlCapture.body), {url, project: "browser", workspace: "reading"});
  const local = await context.newPage();
  await local.goto(`http://127.0.0.1:${api.address().port}/fixture`);
  const contentAccess = await worker.evaluate(async (url) => {
    const [tab] = await chrome.tabs.query({url});
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
  }, local.url());
  assert.deepEqual(contentAccess[0].result, {storageDenied: true, bridgeDenied: true});
  console.log("Packaged Chromium extension: token-free discovery, workspace choice, signed-in page capture, URL submission and isolation passed.");
} finally {
  await context?.close();
  await new Promise((done) => api.close(done));
  await rm(profile, {recursive: true, force: true});
}
