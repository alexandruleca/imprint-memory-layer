// Loads the packaged Firefox add-on in a disposable headless Firefox through
// geckodriver's WebDriver API; needs no npm dependency (ADR 0193).
//   FIREFOX_BINARY=.../firefox GECKODRIVER=.../geckodriver node firefox-smoke.mjs [addon.xpi]
// scripts/fetch-firefox.sh downloads the pinned, checksummed pair.
import assert from "node:assert/strict";
import {execFileSync, spawn} from "node:child_process";
import {mkdir, mkdtemp, rm, writeFile} from "node:fs/promises";
import {createServer} from "node:http";
import {tmpdir} from "node:os";
import {dirname, resolve} from "node:path";
import {fileURLToPath} from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const firefoxBinary = process.env.FIREFOX_BINARY;
const geckodriver = process.env.GECKODRIVER;
if (!firefoxBinary || !geckodriver) throw new Error("Set FIREFOX_BINARY and GECKODRIVER (scripts/fetch-firefox.sh).");
const uuid = "6a8a5c4e-1b2d-4c3e-9f00-123456789abc";
const gecko = "capture@imprintmcp.alexandruleca.com";
const extensionOrigin = `moz-extension://${uuid}`;
const workdir = await mkdtemp(resolve(tmpdir(), "imprint-firefox-"));
const requests = [];
const long = `<title>Long fixture</title><style>body{margin:0}header{position:sticky;top:0;height:40px;background:#123}.banner{position:fixed;bottom:0;height:30px;width:100%;background:#fd0}p{height:300px;margin:0}</style><header>Sticky header</header><main>${"<p>Long page paragraph.</p>".repeat(12)}</main><div class="banner">Fixed banner</div>`;
const api = createServer(async (request, response) => {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  requests.push({url: request.url, method: request.method, headers: request.headers, body: Buffer.concat(chunks)});
  if (request.url === "/signed-in") {
    response.setHeader("Content-Type", "text/html; charset=utf-8");
    response.end("<title>Signed in</title><main><p>Private workspace article visible in this tab.</p><a href=\"/download/fixture\">Planning attachment</a></main>");
    return;
  }
  if (request.url === "/long") {
    response.setHeader("Content-Type", "text/html; charset=utf-8");
    response.end(long);
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
  if (request.url === "/api/browser/local/status") response.end(JSON.stringify({active: "fixture", workspaces: ["fixture", "reading"], max_file_bytes: 1048576, file_extensions: [".txt", ".png"]}));
  else if (["/api/browser/local/captures", "/api/browser/local/files"].includes(request.url)) {
    response.statusCode = 202;
    response.end(JSON.stringify({job_id: "a".repeat(32)}));
  } else if (request.url.startsWith("/api/browser/local/jobs/")) response.end(JSON.stringify({status: "done", ended_at: Date.now() / 1000}));
  else {
    response.statusCode = 404;
    response.end("{}");
  }
});
const listen = (server) => new Promise((done) => server.listen(0, "127.0.0.1", () => done(server.address().port)));
const apiPort = await listen(api);
const origin = `http://127.0.0.1:${apiPort}`;
const spare = createServer();
const driverPort = await listen(spare);
await new Promise((done) => spare.close(done));

let addon = process.argv[2] && resolve(process.argv[2]);
if (!addon) {
  addon = resolve(workdir, "addon.xpi");
  execFileSync(process.env.PYTHON || "python3", [resolve(here, "../../../scripts/package_chrome_extension.py"), "--browser", "firefox", "--output", addon]);
}
// Opening the browser's own context lets the test open extension pages, which
// WebDriver navigation refuses.
const driver = spawn(geckodriver, ["--port", String(driverPort), "--allow-system-access"], {stdio: ["ignore", "ignore", "inherit"]});
async function wd(method, path, body) {
  for (let attempt = 0; ; attempt++) {
    try {
      const response = await fetch(`http://127.0.0.1:${driverPort}${path}`, {
        method, headers: {"content-type": "application/json"}, body: body && JSON.stringify(body)});
      const result = await response.json();
      if (result.value?.error) throw Object.assign(new Error(`${path}: ${result.value.error}: ${result.value.message}`), {webdriver: true});
      return result.value;
    } catch (error) {
      if (error.webdriver || attempt > 40) throw error;
      await new Promise((done) => setTimeout(done, 250));
    }
  }
}
const pause = (ms) => new Promise((done) => setTimeout(done, ms));
let session;
try {
  await mkdir(resolve(workdir, "profile"));
  session = (await wd("POST", "/session", {capabilities: {alwaysMatch: {browserName: "firefox", "moz:firefoxOptions": {
    binary: firefoxBinary, args: ["-headless", "-profile", resolve(workdir, "profile")],
    prefs: {"extensions.webextensions.uuids": JSON.stringify({[gecko]: uuid})},
  }}}})).sessionId;
  const at = (path) => `/session/${session}${path}`;
  const run = (script, ...args) => wd("POST", at("/execute/async"), {script: `const done = arguments[arguments.length - 1];
    (async () => { ${script} })().then((value) => done({value}), (error) => done({error: String(error?.message || error)}));`, args})
    .then((result) => {
      if (result.error) throw new Error(result.error);
      return result.value;
    });
  const until = async (script, ...args) => {
    for (let attempt = 0; attempt < 120; attempt++) {
      const value = await run(script, ...args);
      if (value) return value;
      await pause(250);
    }
    throw new Error(`Timed out waiting for: ${script}`);
  };
  const handles = () => wd("GET", at("/window/handles"));
  async function openExtensionPage(page) {
    const before = await handles();
    await wd("POST", at("/moz/context"), {context: "chrome"});
    await wd("POST", at("/execute/sync"), {
      script: "gBrowser.selectedTab = gBrowser.addTab(arguments[0], {triggeringPrincipal: Services.scriptSecurityManager.getSystemPrincipal()});",
      args: [`${extensionOrigin}/${page}`]});
    await wd("POST", at("/moz/context"), {context: "content"});
    let handle;
    while (!(handle = (await handles()).find((candidate) => !before.includes(candidate)))) await pause(100);
    await wd("POST", at("/window"), {handle});
    await until("return document.readyState === 'complete';");
    return handle;
  }
  await wd("POST", at("/moz/addon/install"), {path: addon, temporary: true});

  // A signed-in page whose attachment needs the browser session.
  await wd("POST", at("/url"), {url: `${origin}/signed-in`});
  await wd("POST", at("/cookie"), {cookie: {name: "fixture-session", value: "ok"}});
  const articleHandle = await wd("GET", at("/window"));
  const longHandle = (await wd("POST", at("/window/new"), {type: "tab"})).handle;
  await wd("POST", at("/window"), {handle: longHandle});
  await wd("POST", at("/url"), {url: `${origin}/long`});
  await run("scrollTo(0, 500);");

  await openExtensionPage("options.html");
  await until("return !document.getElementById('save').disabled;");
  await run(`document.getElementById('base').value = arguments[0]; document.getElementById('connect').requestSubmit();`, origin);
  assert.equal(await until("const f = document.getElementById('feedback'); return f.matches('.success, .error') && f.textContent;"),
    "Connected · 2 local workspaces");
  const status = requests.find((request) => request.url === "/api/browser/local/status");
  assert.equal(status.headers["x-imprint-extension-id"], uuid);
  assert.equal(status.headers.authorization, undefined);

  // The popup opens on its own tab here, which reads as a browser page.
  const popupHandle = await openExtensionPage("popup.html");
  await until("return document.getElementById('connection').dataset.state === 'connected';");
  assert.deepEqual(await run("return [...document.querySelectorAll('#workspace option')].map((o) => o.textContent);"), ["fixture", "reading"]);
  assert.deepEqual(await run(`return [document.body.getBoundingClientRect().width, document.getElementById('tab-kind').textContent,
    document.getElementById('upload').disabled, document.getElementById('tab-blocked').hidden];`), [380, "Browser page", false, false]);
  await run("const s = document.getElementById('workspace'); s.value = 'reading'; s.dispatchEvent(new Event('change'));");

  // The capture functions run in Firefox's content scripts, with the tab's session.
  const captured = await run(`
    const tabs = await chrome.tabs.query({});
    const article = tabs.find((tab) => tab.url === arguments[0] + "/signed-in");
    const {extractCapture} = await import("./capture.js");
    const {readTabFile} = await import("./files.js");
    const [page] = await chrome.scripting.executeScript({target: {tabId: article.id}, func: extractCapture, args: ["page"]});
    await chrome.scripting.executeScript({target: {tabId: article.id}, func: () => {
      const range = document.createRange();
      range.selectNodeContents(document.querySelector("p"));
      getSelection().removeAllRanges();
      getSelection().addRange(range);
    }});
    const [selection] = await chrome.scripting.executeScript({target: {tabId: article.id}, func: extractCapture, args: ["selection"]});
    const [file] = await chrome.scripting.executeScript({target: {tabId: article.id}, func: readTabFile,
      args: [arguments[0] + "/download/fixture", article.url, [".txt"], 100000]});
    return {page: page.result, selection: selection.result, file: file.result.error || atob(file.result.base64)};`, origin);
  assert.equal(captured.page[0].title, "Signed in");
  assert.match(captured.page[0].text, /Private workspace article/);
  assert.equal(captured.selection[0].text, "Private workspace article visible in this tab.");
  assert.equal(captured.file, "The attachment is available only in the signed-in session.");

  // Full page: scroll, stitch and restore. Screenshots need a toolbar click, so
  // a blank viewport-sized image stands in for each capture.
  const full = await run(`
    const tab = (await chrome.tabs.query({})).find((candidate) => candidate.url === arguments[0] + "/long");
    const [size] = await chrome.scripting.executeScript({target: {tabId: tab.id}, func: () => [innerWidth, innerHeight]});
    const canvas = new OffscreenCanvas(...size.result);
    const blob = await canvas.convertToBlob({type: "image/png"});
    const viewport = "data:image/png;base64," + btoa(String.fromCharCode(...new Uint8Array(await blob.arrayBuffer())));
    const {captureVisibleTab, query} = chrome.tabs;
    chrome.tabs.captureVisibleTab = async () => viewport;
    chrome.tabs.query = async () => [tab];
    const {captureFullPage} = await import("./screenshot.js");
    const shot = await captureFullPage(tab).finally(() => Object.assign(chrome.tabs, {captureVisibleTab, query}));
    const [after] = await chrome.scripting.executeScript({target: {tabId: tab.id}, func: () =>
      [scrollY, ...[...document.querySelectorAll("header, .banner")].map((e) => getComputedStyle(e).visibility)]});
    return {height: shot.cssHeight, scrolls: shot.scrolls, after: after.result, type: shot.blob.type};`, origin);
  assert.deepEqual(full, {height: 3640, scrolls: true, after: [500, "visible", "visible"], type: "image/png"});

  // Saving goes through the background with the add-on's moz-extension origin.
  await run(`await chrome.runtime.sendMessage({type: "save", title: "Signed in",
    capture: {documents: [{url: arguments[0] + "/signed-in", title: "Signed in", kind: "page", text: "Private workspace article."}], project: "browser", workspace: "reading"}});`, origin);
  const save = requests.find((request) => request.url === "/api/browser/local/captures");
  assert.equal(save.headers.origin, extensionOrigin);
  assert.equal(save.headers["x-imprint-extension-id"], uuid);
  assert.equal(save.headers.cookie, undefined);
  assert.equal(JSON.parse(save.body).workspace, "reading");

  // The upload page takes a chosen file and sends its bytes.
  await openExtensionPage("popup.html?upload");
  await until("return document.getElementById('connection').dataset.state === 'connected';");
  const notes = resolve(workdir, "notes.txt");
  await writeFile(notes, "Chosen document for upload.");
  const input = await wd("POST", at("/element"), {using: "css selector", value: "#attachment"});
  await wd("POST", at(`/element/${Object.values(input)[0]}/value`), {text: notes});
  await until("return !document.getElementById('review').hidden;");
  await run("document.getElementById('save').click();");
  await until("return document.getElementById('feedback').textContent === 'Sent to reading › browser.';");
  const upload = requests.find((request) => request.url === "/api/browser/local/files");
  assert.equal(upload.body.toString(), "Chosen document for upload.");
  assert.equal(upload.headers.origin, extensionOrigin);
  assert.equal(JSON.parse(decodeURIComponent(upload.headers["x-imprint-file"])).workspace, "reading");

  // Search continues in the dashboard, in the chosen workspace.
  await wd("POST", at("/window"), {handle: popupHandle});
  const before = await handles();
  await run("document.getElementById('query').value = 'planning & decisions'; document.getElementById('search-form').requestSubmit();");
  let search;
  while (!(search = (await handles()).find((handle) => !before.includes(handle)))) await pause(100);
  await wd("POST", at("/window"), {handle: search});
  let address;
  while (!(address = await wd("GET", at("/url"))).includes("/search/")) await pause(100);
  const url = new URL(address);
  assert.equal(url.pathname, "/search/");
  assert.deepEqual(Object.fromEntries(new URLSearchParams(url.hash.slice(1))), {workspace: "reading", q: "planning & decisions"});

  // A page's content script cannot use the add-on's privileged bridge.
  await wd("POST", at("/window"), {handle: popupHandle});
  const bridge = await run(`
    const tab = (await chrome.tabs.query({})).find((candidate) => candidate.url === arguments[0] + "/signed-in");
    const [result] = await chrome.scripting.executeScript({target: {tabId: tab.id}, func: async () => {
      try { return (await chrome.runtime.sendMessage({type: "status"})) === undefined; } catch { return true; }
    }});
    return result.result;`, origin);
  assert.equal(bridge, true);
  void articleHandle;
  console.log("Packaged Firefox add-on: discovery, workspace choice, signed-in page and file capture, full-page stitching, upload, search handoff and bridge isolation passed.");
} finally {
  if (session) await wd("DELETE", `/session/${session}`).catch(() => {});
  driver.kill();
  api.close();
  await rm(workdir, {recursive: true, force: true});
}
