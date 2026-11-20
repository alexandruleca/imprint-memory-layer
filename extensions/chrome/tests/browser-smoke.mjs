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
import {googleExport, readTabFile} from "../files.js";

const here = dirname(fileURLToPath(import.meta.url));
const packagedZip = process.argv[2] ? resolve(process.argv[2]) : null;
const require = createRequire(import.meta.url);
const {chromium} = require(resolve(here, "../../../imprint/ui/node_modules/@playwright/test"));
const profile = await mkdtemp(resolve(tmpdir(), "imprint-extension-"));
const requests = [];
const api = createServer(async (request, response) => {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  const body = Buffer.concat(chunks);
  requests.push({url: request.url, headers: request.headers, body});
  if (request.url === "/signed-in") {
    response.setHeader("Content-Type", "text/html");
    response.end(request.headers.cookie === "fixture-session=ok"
      ? "<title>Signed in</title><main><p>Private workspace article visible in this tab.</p><a href=\"/download/fixture\">Planning attachment</a></main>"
      : "<main>Please sign in to read this article.</main>");
    return;
  }
  if (request.url === "/long") {
    response.setHeader("Content-Type", "text/html");
    response.end(`<title>Long fixture</title><style>body{margin:0}header{position:sticky;top:0;height:40px;background:#123}.banner{position:fixed;bottom:0;height:30px;width:100%;background:#fd0}p{height:300px;margin:0}</style><header>Sticky header</header><main>${"<p>Long page paragraph.</p>".repeat(12)}</main><div class="banner">Fixed banner</div>`);
    return;
  }
  if (request.url === "/download/fixture") {
    response.setHeader("Content-Type", "text/plain");
    response.setHeader("Content-Disposition", "attachment; filename*=UTF-8''attachment.txt");
    response.statusCode = request.headers.cookie === "fixture-session=ok" ? 200 : 401;
    response.end("The attachment is available only in the signed-in session.");
    return;
  }
  response.setHeader("Content-Type", "application/json");
  if (request.url === "/api/browser/local/status") response.end(JSON.stringify({active: "fixture", workspaces: ["fixture", "reading"], max_file_bytes: 1048576, file_extensions: [".txt", ".png", ".docx", ".xlsx"]}));
  else if (["/api/browser/local/captures", "/api/browser/local/files"].includes(request.url)) {
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
  await options.locator('#feedback:is(.success, .error)').waitFor();
  assert.equal(await options.locator('#feedback').textContent(), 'Connected · 2 local workspaces');
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
  const google = await context.newPage();
  await context.addCookies([{name: "fake-google-session", value: "ok", url: "https://docs.google.com"}]);
  for (const [path, format] of [["document", "docx"], ["spreadsheets", "xlsx"]]) {
    const docURL = `https://docs.google.com/${path}/u/1/d/fixture/edit`;
    const exportURL = googleExport(docURL).url;
    await google.route(docURL, (route) => route.fulfill({contentType: "text/html", body: "<main>Office fixture</main>"}));
    await google.route(exportURL, (route) => {
      assert.equal(route.request().headers().cookie, "fake-google-session=ok");
      return route.fulfill({contentType: "application/octet-stream", body: `fixture ${format} bytes`});
    });
    await google.goto(docURL);
    assert.match((await google.evaluate(extractCapture)).error, /Use Google Doc or Google Sheet/);
    const args = JSON.stringify([exportURL, docURL, [".docx", ".xlsx"], 1024]);
    const result = await google.evaluate(`(${readTabFile.toString()})(...${args})`);
    assert.equal(Buffer.from(result.base64, "base64").toString(), `fixture ${format} bytes`);
  }

  // Imprint never receives the browser session cookie used to render this tab.
  const origin = `http://127.0.0.1:${api.address().port}`;
  const url = origin + "/signed-in";
  await context.addCookies([{name: "fixture-session", value: "ok", url: origin}]);
  const privatePage = await context.newPage();
  await privatePage.goto(url);
  await privatePage.getByText("Private workspace article visible in this tab.").waitFor();
  const popup = await context.newPage();
  // Playwright opens the popup as a page, so target the fixture tab explicitly.
  const screenshot = "data:image/png;base64," + (await privatePage.screenshot({clip: {x: 0, y: 0, width: 360, height: 120}})).toString("base64");
  await popup.addInitScript(({url, screenshot}) => {
    const query = chrome.tabs.query.bind(chrome.tabs);
    chrome.tabs.query = () => query({url});
    chrome.tabs.captureVisibleTab = async () => screenshot;
  }, {url, screenshot});
  await popup.goto(`chrome-extension://${extensionId}/popup.html`);
  await popup.locator('#connection[data-state="connected"]').waitFor();
  assert.deepEqual(await popup.locator('#workspace option').allTextContents(), ["fixture", "reading"]);
  await popup.getByLabel("Workspace").selectOption("reading");
  await popup.getByRole("button", {name: "Page text"}).click();
  await popup.getByLabel("Content to save").fill("Private workspace article, edited in preview.");
  await popup.getByRole("button", {name: "Save to Imprint"}).click();
  await popup.getByText(/Indexed in Imprint/).waitFor();
  assert.equal(await popup.locator("#job-title").textContent(), "Signed in");
  await popup.reload();
  await popup.locator('#connection[data-state="connected"]').waitFor();
  assert.equal(await popup.getByLabel("Workspace").inputValue(), "reading");
  await popup.getByRole("button", {name: "Link", exact: true}).click();
  await popup.locator("#preview").getByText(url).waitFor();
  await popup.getByRole("button", {name: "Save to Imprint"}).click();
  await popup.getByText("Sent to reading › browser.").waitFor();
  const [pageCapture, urlCapture] = requests.filter((request) => request.url.endsWith("/captures"));
  assert.equal(pageCapture.headers.cookie, undefined);
  assert.equal(pageCapture.headers.authorization, undefined);
  assert.equal(pageCapture.headers.origin, `chrome-extension://${extensionId}`);
  const pagePayload = JSON.parse(pageCapture.body);
  assert.equal(pagePayload.workspace, "reading");
  assert.equal(pagePayload.documents[0].kind, "page");
  assert.equal(pagePayload.documents[0].text, "Private workspace article, edited in preview.");
  assert.deepEqual(JSON.parse(urlCapture.body), {url, project: "browser", workspace: "reading"});
  await privatePage.getByRole("link", {name: "Planning attachment"}).focus();
  await popup.getByRole("button", {name: "File in this tab"}).click();
  await popup.locator("#preview").getByText(/attachment\.txt/).waitFor();
  await popup.getByRole("button", {name: "Save to Imprint"}).click();
  await popup.getByText("Sent to reading › browser.").waitFor();
  const attachment = requests.find((request) => request.url.endsWith("/files"));
  assert.equal(attachment.body.toString(), "The attachment is available only in the signed-in session.");
  assert.equal(attachment.headers.cookie, undefined);
  assert.equal(attachment.headers.origin, `chrome-extension://${extensionId}`);
  assert.equal(attachment.headers["content-type"], "application/octet-stream");
  assert.equal(JSON.parse(decodeURIComponent(attachment.headers["x-imprint-file"])).workspace, "reading");

  await popup.getByRole("button", {name: "Screenshot of the visible area"}).click();
  await popup.getByRole("img", {name: "Visible tab to send for text recognition"}).waitFor();
  await popup.getByRole("button", {name: "Save to Imprint"}).click();
  await popup.getByText("Sent to reading › browser.").waitFor();
  const image = requests.filter((request) => request.url.endsWith("/files")).at(-1);
  assert.equal(JSON.parse(decodeURIComponent(image.headers["x-imprint-file"])).kind, "screenshot");

  const [uploads] = await Promise.all([
    context.waitForEvent("page"),
    popup.getByRole("button", {name: "Upload a file"}).click(),
  ]);
  await uploads.locator('#connection[data-state="connected"]').waitFor();
  await uploads.getByLabel("Choose a file").setInputFiles({name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("Chosen document for upload.")});
  await uploads.getByRole("button", {name: "Save to Imprint"}).click();
  await uploads.getByText("Sent to reading › browser.").waitFor();

  // A full-page screenshot scrolls the tab, stitches each viewport and puts the
  // page back as it was, sticky header and fixed banner included.
  const long = await context.newPage();
  await long.setViewportSize({width: 400, height: 300});
  await long.goto(origin + "/long");
  await long.evaluate(() => scrollTo(0, 500));
  const viewport = "data:image/png;base64," + (await long.screenshot()).toString("base64");
  const fullPopup = await context.newPage();
  await fullPopup.addInitScript(({url, viewport}) => {
    const query = chrome.tabs.query.bind(chrome.tabs);
    chrome.tabs.query = () => query({url});
    chrome.tabs.captureVisibleTab = async () => viewport;
  }, {url: origin + "/long", viewport});
  await fullPopup.goto(`chrome-extension://${extensionId}/popup.html`);
  await fullPopup.locator('#connection[data-state="connected"]').waitFor();
  await fullPopup.getByRole("button", {name: "Full-page screenshot"}).click();
  await fullPopup.getByRole("img", {name: "Full page to send for text recognition"}).waitFor({timeout: 30000});
  await fullPopup.getByText(/Full page · 3,640 px tall/).waitFor();
  assert.deepEqual(await long.evaluate(() => [scrollY, ...[...document.querySelectorAll("header, .banner")].map((e) => getComputedStyle(e).visibility)]),
    [500, "visible", "visible"]);
  await fullPopup.getByRole("button", {name: "Save to Imprint"}).click();
  await fullPopup.getByText("Sent to reading › browser.").waitFor();
  const full = requests.filter((request) => request.url.endsWith("/files")).at(-1);
  const fullMetadata = JSON.parse(decodeURIComponent(full.headers["x-imprint-file"]));
  assert.deepEqual([fullMetadata.kind, fullMetadata.filename, fullMetadata.title], ["screenshot", "full-page.png", "Long fixture"]);
  assert.equal(full.body.readUInt32BE(20), 3640);
  await fullPopup.getByRole("button", {name: "Dismiss"}).click();
  await fullPopup.reload();
  await fullPopup.locator('#connection[data-state="connected"]').waitFor();
  assert.equal(await fullPopup.locator("#job-card").isHidden(), true);

  await popup.getByLabel("Search memory").fill("planning & decisions");
  const [search] = await Promise.all([
    context.waitForEvent("page"),
    popup.getByRole("button", {name: "Search", exact: true}).click(),
  ]);
  await search.waitForURL((url) => url.pathname === "/search/");
  assert.deepEqual(Object.fromEntries(new URLSearchParams(new URL(search.url()).hash.slice(1))),
    {workspace: "reading", q: "planning & decisions"});
  const meetingPopup = await context.newPage();
  await meetingPopup.addInitScript(() => {
    chrome.tabs.query = async () => [{id: 23, url: "https://meet.google.com/abc-defg-hij", title: "Meet - Weekly planning"}];
  });
  await meetingPopup.goto(`chrome-extension://${extensionId}/popup.html`);
  const [recordings] = await Promise.all([
    context.waitForEvent("page"),
    meetingPopup.getByRole("button", {name: "Record meeting"}).click(),
  ]);
  await recordings.waitForURL((url) => url.pathname === "/recordings/");
  assert.deepEqual(Object.fromEntries(new URLSearchParams(new URL(recordings.url()).hash.slice(1))),
    {workspace: "reading", title: "Weekly planning", project: "browser"});
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
  console.log("Packaged Chromium extension: token-free discovery, workspace choice, signed-in page capture, URL submission, file intake, export requests, OCR and full-page previews, search and recording handoff, and isolation passed.");
} finally {
  await context?.close();
  await new Promise((done) => api.close(done));
  await rm(profile, {recursive: true, force: true});
}
