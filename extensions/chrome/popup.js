import {extractCapture} from "./capture.js";
import {requestAPI} from "./client.js";
import {decodedFile, googleExport, meetingTitle, readTabFile} from "./files.js";
import {send, showError} from "./ui.js";

const $ = (id) => document.getElementById(id);
const uploadPage = location.search === "?upload";
const captureActions = ["page", "selection", "url", "file", "screenshot", "upload", "attachment", "record", "search"];
let capture;
let activeTab;
let exportTarget;
let fileLimit = 0;
let extensions = [];
let apiSettings;
let connected = false;
let jobTimer;
let latestPoll = 0;

function setBusy(busy) {
  for (const id of captureActions) $(id).disabled = busy || !connected;
  for (const id of ["file", "screenshot", "upload", "attachment"]) $(id).disabled ||= !fileLimit;
  $("save").disabled = busy || !connected;
  $("workspace").disabled = busy || !connected;
}

function showMessage(id, message) {
  $(id).classList.remove("error");
  $(id).textContent = message;
}

async function pollJob() {
  clearTimeout(jobTimer);
  const poll = ++latestPoll;
  try {
    const job = await send("job");
    if (!job || poll !== latestPoll) return;
    showMessage("job", ({
      queued: "Queued in Imprint.",
      running: "Indexing in Imprint…",
      done: "Indexed in Imprint.",
      failed: "Indexing failed. Open Imprint’s queue for details.",
      cancelled: "Capture cancelled.",
    })[job.status] || job.status);
    if (["queued", "running"].includes(job.status)) jobTimer = setTimeout(pollJob, 1500);
  } catch (error) {
    if (poll === latestPoll) showError($("job"), error);
  }
}

function previewDocument(doc, index) {
  const title = document.createElement("input");
  title.id = `title-${index}`;
  title.value = doc.title;
  title.maxLength = 1000;
  title.required = true;
  title.addEventListener("input", () => { doc.title = title.value; });
  const titleLabel = document.createElement("label");
  titleLabel.htmlFor = title.id;
  titleLabel.textContent = "Title";

  const source = document.createElement("p");
  source.className = "source";
  source.textContent = doc.url;

  const text = document.createElement("textarea");
  text.id = `text-${index}`;
  text.value = doc.text;
  text.rows = 5;
  text.minLength = 10;
  text.maxLength = 800000;
  text.required = true;
  text.addEventListener("input", () => { doc.text = text.value; });
  const textLabel = document.createElement("label");
  textLabel.htmlFor = text.id;
  textLabel.textContent = "Content to save";
  $("preview").append(titleLabel, title, source, textLabel, text);
}

function previewFile(file, metadata, imageURL) {
  if (!file.size || file.size > fileLimit) throw new Error("Choose a non-empty file within Imprint’s upload limit.");
  if (!extensions.some((suffix) => metadata.filename.toLowerCase().endsWith(suffix))) {
    throw new Error("This attachment type is unsupported. Choose a PDF, Office export, text file, or image.");
  }
  const name = document.createElement("p");
  name.className = "source";
  name.textContent = metadata.filename;
  $("preview").append(name);
  if (imageURL) {
    const img = document.createElement("img");
    img.className = "screenshot";
    img.src = imageURL;
    img.alt = "Visible tab to send for text recognition";
    $("preview").append(img);
  }
  capture = {file, metadata};
}

async function previewTabFile(tab) {
  let result;
  try {
    [result] = await chrome.scripting.executeScript({
      target: {tabId: tab.id}, func: readTabFile, args: [exportTarget?.url ?? null, tab.url, extensions, fileLimit],
    });
  } catch {
    throw new Error("Chrome cannot read this viewer directly. Download the file, then use Upload file.");
  }
  const data = result?.result;
  if (data?.error) throw new Error(data.error);
  if (!data?.base64) throw new Error("The file is empty or unavailable. Download it, then use Upload file.");
  const metadata = exportTarget || {filename: data.filename, source_url: data.url};
  const {filename, source_url, title = filename, kind = "file"} = metadata;
  previewFile(decodedFile(data.base64), {filename, source_url, title, kind});
}

async function previewScreenshot(tab) {
  const screenshot = await chrome.tabs.captureVisibleTab(tab.windowId, {format: "png"});
  const [current] = await chrome.tabs.query({active: true, currentWindow: true});
  if (tab.id !== current?.id || tab.url !== current.url) throw new Error("The tab changed. Try again.");
  previewFile(decodedFile(screenshot.split(",")[1], "image/png"), {
    filename: "screenshot.png", title: (tab.title || "Tab screenshot").slice(0, 1000),
    ...(tab.url?.startsWith("http") ? {source_url: tab.url} : {}), kind: "screenshot",
  }, screenshot);
}

async function preview(mode) {
  setBusy(true);
  showMessage("feedback", "Reading the current tab…");
  $("capture-form").hidden = true;
  $("preview").replaceChildren();
  capture = undefined;
  try {
    const tab = activeTab;
    const web = /^https?:\/\//i.test(tab?.url || "");
    if (mode === "attachment") {
      const [file] = $("attachment").files;
      if (!file) return;
      previewFile(file, {filename: file.name});
    } else if (mode === "screenshot" && tab?.id != null) {
      await previewScreenshot(tab);
    } else if (tab?.id == null || !web) {
      throw new Error("Open an HTTP or HTTPS page to capture it, or choose Upload file.");
    } else if (mode === "file") {
      await previewTabFile(tab);
    } else if (mode === "url") {
      capture = {url: tab.url};
      const source = document.createElement("p");
      source.className = "source";
      source.textContent = tab.url;
      $("preview").append(source);
    } else {
      const [result] = await chrome.scripting.executeScript({target: {tabId: tab.id}, func: extractCapture, args: [mode]});
      if (result?.error || result?.result?.error) throw new Error(result.error?.message || result.result?.error || "Page extraction failed.");
      const documents = result?.result;
      if (!Array.isArray(documents) || !documents.length) {
        throw new Error("Could not extract this page. Try selecting text or another tab.");
      }
      capture = {documents};
      documents.forEach(previewDocument);
    }
    showMessage("feedback", "");
    $("capture-form").hidden = false;
  } catch (error) {
    showError($("feedback"), error);
  } finally {
    setBusy(false);
  }
}

async function connect() {
  $("retry").hidden = true;
  showMessage("connection", "Connecting to local Imprint…");
  try {
    const [settings, status] = await Promise.all([send("settings"), send("status")]);
    apiSettings = {base: settings.base, extensionId: chrome.runtime.id};
    fileLimit = status.max_file_bytes || 0;
    extensions = status.file_extensions || [];
    $("attachment").accept = extensions.join(",");
    $("project").value = settings.project;
    $("workspace").replaceChildren(...status.workspaces.map((name) => new Option(name, name)));
    $("workspace").value = status.workspaces.includes(settings.workspace) ? settings.workspace : status.active;
    connected = Boolean($("workspace").value);
    if (!connected) throw new Error("No active workspace. Create or select one in Imprint.");
    if (!uploadPage) {
      [activeTab] = await chrome.tabs.query({active: true, currentWindow: true});
      if (activeTab?.url) exportTarget = googleExport(activeTab.url, activeTab.title);
      if (exportTarget) $("file").textContent = exportTarget.kind === "google-doc" ? "Save Google Doc" : "Save Google Sheet";
    }
    showMessage("connection", "Connected to local Imprint");
    $("connection").classList.add("connected");
    setBusy(false);
    await pollJob();
  } catch (error) {
    showError($("connection"), error);
    $("retry").hidden = false;
  }
}

$("settings").addEventListener("click", () => chrome.runtime.openOptionsPage());
$("retry").addEventListener("click", connect);
for (const mode of ["page", "selection", "url", "file", "screenshot"]) $(mode).addEventListener("click", () => preview(mode));
$("attachment").addEventListener("change", () => preview("attachment"));
$("upload").addEventListener("click", () => chrome.tabs.create({url: chrome.runtime.getURL("popup.html?upload")}));
if (uploadPage) {
  document.body.classList.add("upload-page");
  $("capture-heading").textContent = "Upload to Imprint";
  $("tab-actions").hidden = true;
  $("tab-hint").hidden = true;
  $("upload-picker").hidden = false;
  $("dashboard-actions").hidden = true;
}

async function openDashboard(path, params) {
  try {
    const url = new URL(path, apiSettings.base);
    url.hash = new URLSearchParams({workspace: $("workspace").value, ...params});
    await chrome.tabs.create({url: url.href});
  } catch (error) {
    showError($("dashboard-feedback"), error);
  }
}

$("search-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const q = $("query").value.trim();
  if (q) void openDashboard("/search/", {q});
});
$("record").addEventListener("click", () => {
  void openDashboard("/recordings/", {title: meetingTitle(activeTab || {}), project: $("project").value.trim()});
});
$("workspace").addEventListener("change", async () => {
  try {
    await send("workspace", {workspace: $("workspace").value});
  } catch (error) {
    showError($("feedback"), error);
  }
});
$("capture-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!capture) return;
  setBusy(true);
  try {
    const project = $("project").value.trim();
    if (!project) throw new Error("Enter a project name.");
    const workspace = $("workspace").value;
    if (capture.file) {
      const result = await requestAPI(apiSettings, "files", {
        file: capture.file, metadata: {...capture.metadata, project, workspace},
      });
      await send("fileJob", {id: result.job_id, base: apiSettings.base, project});
      $("attachment").value = "";
    } else {
      const payload = {...capture, project, workspace};
      if (new TextEncoder().encode(JSON.stringify(payload)).length > 1000000) {
        throw new Error("Capture is too large. Select a smaller passage.");
      }
      await send("save", {capture: payload});
    }
    capture = undefined;
    $("capture-form").hidden = true;
    $("preview").replaceChildren();
    showMessage("feedback", `Sent to ${workspace}.`);
    await pollJob();
  } catch (error) {
    showError($("feedback"), error);
  } finally {
    setBusy(false);
  }
});

await connect();
