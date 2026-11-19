import {extractCapture} from "./capture.js";
import {requestAPI} from "./client.js";
import {decodedFile, googleExport, meetingTitle, readTabFile} from "./files.js";
import {captureFullPage} from "./screenshot.js";
import {friendlyMessage, send, showError, unreachable} from "./ui.js";

const $ = (id) => document.getElementById(id);
const uploadPage = location.search === "?upload";
const tabModes = ["page", "selection", "url", "file", "screenshot", "fullpage"];
const fileModes = new Set(["file", "screenshot", "fullpage"]);
const reviewHeadings = {
  page: "Review page text", selection: "Review selection", url: "Review link", file: "Review file",
  screenshot: "Review screenshot", fullpage: "Review full-page screenshot", attachment: "Review upload",
};
const defaultHints = Object.fromEntries(["page", "selection", "url", "file", "ocr", "upload"]
  .map((id) => [id, $(`${id}-hint`).textContent]));
let capture;
let activeTab;
let exportTarget;
let tab = {chip: "", recommended: "", reasons: {}, meeting: false};
let fileLimit = 0;
let extensions = [];
let apiSettings;
let connected = false;
let busy = false;
let returnFocus;
let jobTimer;
let retryTimer;
let latestPoll = 0;

function safeURL(value) {
  try {
    return new URL(value);
  } catch {
    return null;
  }
}

// What this tab supports, decided from its address alone: nothing runs in the
// page until an action is chosen.
function describeTab(current) {
  const url = safeURL(current?.url);
  const info = {chip: "Web page", recommended: "page", reasons: {}, meeting: false};
  if (!url || !["http:", "https:"].includes(url.protocol)) {
    for (const mode of tabModes) info.reasons[mode] = "Chrome doesn’t let extensions read this page";
    return {...info, chip: "Browser page", recommended: "upload", blocked: true};
  }
  if (exportTarget) {
    const name = exportTarget.kind === "google-doc" ? "Google Doc" : "Google Sheet";
    info.reasons.page = info.reasons.selection = `Use ${name} above to save all of it`;
    return {...info, chip: name, recommended: "file"};
  }
  if (url.hostname === "docs.google.com") {
    info.reasons.page = info.reasons.selection = "Google’s editors hide their text; use a screenshot";
    return {...info, chip: "Google Docs", recommended: "ocr"};
  }
  if (url.hostname === "mail.google.com") {
    info.reasons.page = info.reasons.selection = "Gmail messages can’t be saved as text yet";
    info.reasons.url = "Imprint can’t sign in to Gmail";
    return {...info, chip: "Gmail", recommended: "ocr"};
  }
  if (meetingTitle(current || {})) return {...info, chip: "Meeting", recommended: "", meeting: true};
  if (/\.pdf$/i.test(url.pathname)) return {...info, chip: "PDF", recommended: "file"};
  return info;
}

function render() {
  const off = busy || !connected;
  const noFiles = connected && !fileLimit && "Update Imprint to save files from Chrome";
  for (const mode of tabModes) {
    $(mode).disabled = off || Boolean(tab.reasons[mode] || (fileModes.has(mode) && noFiles));
  }
  for (const id of ["page", "selection", "url", "file"]) {
    $(`${id}-hint`).textContent = tab.reasons[id] || (id === "file" && noFiles) || defaultHints[id];
  }
  $("ocr-hint").textContent = tab.reasons.screenshot || noFiles || defaultHints.ocr;
  $("ocr").classList.toggle("disabled", $("screenshot").disabled && $("fullpage").disabled);
  $("upload").disabled = $("attachment").disabled = off || !fileLimit;
  $("upload-hint").textContent = noFiles || defaultHints.upload;
  for (const id of ["search", "record", "workspace"]) $(id).disabled = off;
  $("project").disabled = busy;
  $("save").disabled = off;
  for (const id of [...tabModes.filter((mode) => !["screenshot", "fullpage"].includes(mode)), "ocr", "upload"]) {
    $(id).classList.toggle("recommended", tab.recommended === id && !uploadPage);
  }
  $("record").classList.toggle("recommended", tab.meeting);
  // On Chrome's own pages only Upload applies; one line says why.
  $("tab-blocked").hidden = !tab.blocked || uploadPage;
  for (const id of ["page", "selection", "url", "file", "ocr"]) $(id).hidden = Boolean(tab.blocked);
}

function setFeedback(id, message, {spinner = false} = {}) {
  $(id).classList.remove("error");
  $(id).classList.toggle("busy", spinner);
  $(id).textContent = message;
}

function setConnection(state, label, title = "") {
  $("connection").dataset.state = state;
  $("connection-text").textContent = label;
  $("connection").title = title;
}

function element(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
}

function sourceLine(text, href = text) {
  const line = element("p", {className: "source", title: href});
  line.append(element("span", {textContent: text}));
  return line;
}

function field(id, labelText, input) {
  input.id = id;
  const label = element("label", {htmlFor: id, textContent: labelText});
  return [label, input];
}

function titleField(index, value, onInput) {
  const input = element("input", {value, maxLength: 1000, required: true, spellcheck: false});
  input.addEventListener("input", () => onInput(input.value));
  return field(`title-${index}`, "Title", input);
}

const formatter = new Intl.NumberFormat();
const words = (text) => (text.match(/\S+/g) || []).length;
const countText = (text) => `${formatter.format(words(text))} words · ${formatter.format(text.length)} characters`;

function previewDocument(doc, index) {
  const text = element("textarea", {value: doc.text, rows: 8, minLength: 10, maxLength: 800000, required: true});
  const count = element("p", {className: "count", textContent: countText(doc.text)});
  text.addEventListener("input", () => {
    doc.text = text.value;
    count.textContent = countText(text.value);
  });
  $("preview").append(
    ...titleField(index, doc.title, (value) => { doc.title = value; }),
    sourceLine(doc.url),
    ...field(`text-${index}`, "Content to save", text), count,
  );
}

function bytes(size) {
  return size < 1024 * 1024 ? `${formatter.format(Math.ceil(size / 1024))} KB` : `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function previewFile(file, metadata, image) {
  if (!file.size || file.size > fileLimit) {
    throw new Error(`Choose a non-empty file up to ${bytes(fileLimit)}, Imprint’s upload limit.`);
  }
  if (!extensions.some((suffix) => metadata.filename.toLowerCase().endsWith(suffix))) {
    throw new Error("This file type isn’t supported. Choose a PDF, Office export, text file or image.");
  }
  metadata.title ||= metadata.filename;
  $("preview").append(...titleField(0, metadata.title, (value) => { metadata.title = value; }));
  $("preview").append(sourceLine(`${metadata.filename} · ${bytes(file.size)}`, metadata.source_url || metadata.filename));
  if (image) {
    const img = element("img", {src: image.url, alt: image.alt});
    $("preview").append(element("div", {className: "shot", tabIndex: 0}, img));
    for (const note of image.notes || []) $("preview").append(element("p", {className: `shot-note ${note.warning ? "warning" : ""}`, textContent: note.text}));
  }
  capture = {file, metadata};
}

async function previewTabFile(current) {
  let result;
  try {
    [result] = await chrome.scripting.executeScript({
      target: {tabId: current.id}, func: readTabFile, args: [exportTarget?.url ?? null, current.url, extensions, fileLimit],
    });
  } catch {
    throw new Error("Chrome can’t read this viewer directly. Download the file, then use Upload a file.");
  }
  const data = result?.result;
  if (data?.error) throw new Error(data.error.replace("use Upload file", "use Upload a file"));
  if (!data?.base64) throw new Error("The file is empty or unavailable. Download it, then use Upload a file.");
  const metadata = exportTarget || {filename: data.filename, source_url: data.url};
  const {filename, source_url, title = filename, kind = "file"} = metadata;
  previewFile(decodedFile(data.base64), {filename, source_url, title, kind});
}

function sameTab(current, latest) {
  if (current.id !== latest?.id || current.url !== latest.url) throw new Error("The tab changed. Try again.");
}

function screenshotMetadata(current, filename) {
  return {
    filename, title: (current.title || "Tab screenshot").slice(0, 1000),
    ...(current.url?.startsWith("http") ? {source_url: current.url} : {}), kind: "screenshot",
  };
}

async function previewScreenshot(current) {
  const screenshot = await chrome.tabs.captureVisibleTab(current.windowId, {format: "png"});
  sameTab(current, (await chrome.tabs.query({active: true, currentWindow: true}))[0]);
  previewFile(decodedFile(screenshot.split(",")[1], "image/png"), screenshotMetadata(current, "screenshot.png"),
    {url: screenshot, alt: "Visible tab to send for text recognition", notes: [{text: "The visible part of the page."}]});
}

async function previewFullPage(current) {
  const shot = await captureFullPage(current, (done, total) => {
    setFeedback("feedback", total > 1 ? `Capturing the page… ${done} of ${total}` : "Capturing the page…", {spinner: true});
  });
  let {blob} = shot;
  let filename = "full-page.png";
  // A long page can exceed the upload limit as PNG; JPEG keeps text legible.
  if (blob.size > fileLimit && extensions.includes(".jpg")) {
    setFeedback("feedback", "Compressing the screenshot…", {spinner: true});
    const image = await createImageBitmap(blob);
    const canvas = new OffscreenCanvas(image.width, image.height);
    canvas.getContext("2d").drawImage(image, 0, 0);
    blob = await canvas.convertToBlob({type: "image/jpeg", quality: 0.85});
    filename = "full-page.jpg";
  }
  if (blob.size > fileLimit) throw new Error("This page is too long to save as one screenshot. Use Visible, or Page text.");
  const notes = [{text: `Full page · ${formatter.format(shot.cssHeight)} px tall`}];
  if (shot.truncated) notes.push({warning: true, text: "The page is longer than one capture allows; only its top part is included."});
  if (!shot.scrolls) notes.push({warning: true, text: "This page scrolls inside a panel, so only the visible area was captured."});
  previewFile(new Blob([blob], {type: blob.type}), screenshotMetadata(current, filename),
    {url: URL.createObjectURL(blob), alt: "Full page to send for text recognition", notes});
}

function openReview(mode, trigger) {
  returnFocus = trigger;
  $("review-heading").textContent = reviewHeadings[mode];
  setFeedback("review-feedback", "");
  // The destination moves next to Save, so it can still change while reviewing.
  $("capture-form").insertBefore($("destination"), $("save-bar"));
  $("home").hidden = true;
  $("review").hidden = false;
  document.body.classList.add("reviewing");
  ($("preview").querySelector("textarea, input") || $("save")).focus();
}

function closeReview() {
  for (const img of $("preview").querySelectorAll("img")) if (img.src.startsWith("blob:")) URL.revokeObjectURL(img.src);
  $("preview").replaceChildren();
  capture = undefined;
  $("review").hidden = true;
  $("home").insertBefore($("destination"), $("job-card"));
  $("home").hidden = false;
  document.body.classList.remove("reviewing");
  if (returnFocus && !returnFocus.disabled) returnFocus.focus();
}

async function preview(mode, chosen) {
  const trigger = mode === "attachment" ? $("drop-zone") : $(mode);
  busy = true;
  render();
  setFeedback("feedback", mode === "url" ? "" : "Reading the tab…", {spinner: true});
  $("preview").replaceChildren();
  capture = undefined;
  try {
    const current = activeTab;
    if (mode === "attachment") {
      if (!chosen) return setFeedback("feedback", "");
      previewFile(chosen, {filename: chosen.name || "pasted-image.png"});
    } else if (tab.reasons[mode] || current?.id == null) {
      throw new Error(tab.reasons[mode] || "Open a web page to capture it, or upload a file.");
    } else if (mode === "screenshot") {
      await previewScreenshot(current);
    } else if (mode === "fullpage") {
      await previewFullPage(current);
    } else if (mode === "file") {
      await previewTabFile(current);
    } else if (mode === "url") {
      capture = {url: current.url};
      $("preview").append(sourceLine(current.url),
        element("p", {className: "hint", textContent: "Imprint downloads this address itself, without your browser’s sign-ins. For a page behind a login, use Page text."}));
    } else {
      const [result] = await chrome.scripting.executeScript({target: {tabId: current.id}, func: extractCapture, args: [mode]});
      if (result?.error || result?.result?.error) throw new Error(result.error?.message || result.result?.error || "Page extraction failed.");
      const documents = result?.result;
      if (!Array.isArray(documents) || !documents.length) {
        throw new Error("Couldn’t read this page. Select a passage, or try a screenshot.");
      }
      capture = {documents};
      documents.forEach(previewDocument);
    }
    setFeedback("feedback", "");
    openReview(mode, trigger);
  } catch (error) {
    showError($("feedback"), error);
  } finally {
    busy = false;
    render();
  }
}

function since(at) {
  const minutes = Math.round((Date.now() - at) / 60000);
  if (!at || minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  return `${Math.round(minutes / 60)} h ago`;
}

const jobIcons = {done: "#i-check", failed: "#i-alert", cancelled: "#i-close", error: "#i-alert"};

function showJob(status, {title = "", message, action, at} = {}) {
  $("job-card").hidden = false;
  $("job-card").dataset.status = status;
  const icon = jobIcons[status];
  $("job-card").querySelector(".job-icon").innerHTML = icon ? `<svg class="icon"><use href="${icon}"/></svg>` : "";
  $("job-title").textContent = title || "Last capture";
  $("job").textContent = message;
  $("job-open").hidden = !action;
  if (action) {
    $("job-open").replaceChildren(action.label, element("span", {className: "visually-hidden", textContent: " (opens a tab)"}));
    $("job-open").onclick = action.run;
  }
  if (at) $("job").title = new Date(at).toLocaleString();
}

async function pollJob() {
  clearTimeout(jobTimer);
  const poll = ++latestPoll;
  try {
    const job = await send("job");
    if (poll !== latestPoll) return;
    if (!job) {
      $("job-card").hidden = true;
      return;
    }
    const where = job.workspace ? ` in ${job.workspace}` : "";
    const finished = job.ended_at ? job.ended_at * 1000 : job.at;
    const states = {
      queued: {message: "Queued in Imprint"},
      running: {message: "Indexing in Imprint…"},
      done: {message: `Indexed in Imprint · ${since(finished)}`,
        action: job.title && {label: "View", run: () => openDashboard("/search/", {q: job.title}, job.workspace || $("workspace").value)}},
      failed: {message: "Indexing failed", action: {label: "Open queue", run: () => openDashboard("/queue/")}},
      cancelled: {message: `Capture cancelled${where}`},
    };
    showJob(job.status in states ? job.status : "queued", {title: job.title, at: finished, ...(states[job.status] || {message: job.status})});
    if (["queued", "running"].includes(job.status)) jobTimer = setTimeout(pollJob, 1500);
  } catch (error) {
    if (poll === latestPoll) showJob("error", {message: friendlyMessage(error.message)});
  }
}

function showOffline(error) {
  const message = error.message || "";
  const noWorkspace = error.noWorkspace;
  const outdated = /Update the local Imprint backend/.test(message);
  const away = unreachable(message);
  $("offline-title").textContent = noWorkspace ? "Create a workspace first" : outdated ? "Update Imprint"
    : away ? "Imprint isn’t running" : "Can’t connect to Imprint";
  const address = apiSettings?.base && apiSettings.base !== "http://127.0.0.1:8420" ? ` Looking at ${apiSettings.base.replace("http://", "")}.` : "";
  $("offline-text").textContent = noWorkspace ? "Imprint has no workspace yet. Create one in the dashboard, then try again."
    : outdated ? "This Imprint is too old for Capture. Install the latest release, then try again."
      : away ? `Open the Imprint app on this computer. Capture connects by itself.${address}` : friendlyMessage(message);
  $("open-imprint").hidden = !noWorkspace;
  $("offline").hidden = false;
  $("home").hidden = true;
  $("dashboard-actions").hidden = true;
  setConnection("offline", "Offline", message);
  if (away) retryTimer = setTimeout(connect, 3000);
}

async function connect() {
  clearTimeout(retryTimer);
  if ($("offline").hidden) setConnection("connecting", "Connecting…");
  try {
    const [settings, status] = await Promise.all([send("settings"), send("status")]);
    apiSettings = {base: settings.base, extensionId: chrome.runtime.id};
    fileLimit = status.max_file_bytes || 0;
    extensions = status.file_extensions || [];
    $("attachment").accept = extensions.join(",");
    if (!connected) $("project").value = settings.project;
    $("recent-projects").replaceChildren(...(settings.projects || []).map((name) => new Option(name)));
    $("workspace").replaceChildren(...status.workspaces.map((name) => new Option(name, name)));
    $("workspace").value = status.workspaces.includes(settings.workspace) ? settings.workspace : status.active;
    connected = Boolean($("workspace").value);
    if (!connected) throw Object.assign(new Error("No active workspace."), {noWorkspace: true});
    $("offline").hidden = true;
    $("home").hidden = !$("review").hidden;
    $("dashboard-actions").hidden = uploadPage;
    setConnection("connected", "Connected", `Connected to local Imprint at ${settings.base.replace("http://", "")}`);
    render();
    await pollJob();
  } catch (error) {
    connected = false;
    if (!apiSettings) apiSettings = await send("settings").then((settings) => ({base: settings.base, extensionId: chrome.runtime.id}), () => undefined);
    render();
    showOffline(error);
  }
}

async function openDashboard(path, params = {}, workspace = $("workspace").value) {
  try {
    const url = new URL(path, apiSettings.base);
    if (path !== "/queue/" && path !== "/") url.hash = new URLSearchParams({workspace, ...params});
    await chrome.tabs.create({url: url.href});
  } catch (error) {
    showError($("dashboard-feedback"), error);
  }
}

function captureTitle() {
  if (capture.file) return capture.metadata.title || capture.metadata.filename;
  if (capture.url) return activeTab?.title || capture.url;
  return capture.documents[0]?.title || "";
}

$("settings").addEventListener("click", () => chrome.runtime.openOptionsPage());
$("change-address").addEventListener("click", () => chrome.runtime.openOptionsPage());
$("retry").addEventListener("click", connect);
$("open-imprint").addEventListener("click", () => apiSettings && chrome.tabs.create({url: new URL("/", apiSettings.base).href}));
for (const mode of tabModes) $(mode).addEventListener("click", () => preview(mode));
$("attachment").addEventListener("change", () => {
  const [file] = $("attachment").files;
  $("attachment").value = "";
  void preview("attachment", file);
});
$("upload").addEventListener("click", () => chrome.tabs.create({url: chrome.runtime.getURL("popup.html?upload")}));
$("back").addEventListener("click", closeReview);
$("job-dismiss").addEventListener("click", async () => {
  clearTimeout(jobTimer);
  latestPoll++;
  $("job-card").hidden = true;
  await send("dismissJob").catch(() => {});
});
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
document.addEventListener("keydown", (event) => {
  const typing = event.target.closest?.("input, textarea, select");
  if (!$("review").hidden) {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      $("capture-form").requestSubmit();
    } else if (event.key === "Escape") {
      event.preventDefault();
      closeReview();
    }
  } else if (event.key === "/" && !typing && !$("dashboard-actions").hidden) {
    event.preventDefault();
    $("query").focus();
  }
});
$("capture-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!capture || busy) return;
  busy = true;
  render();
  $("save").textContent = "Saving…";
  setFeedback("review-feedback", "");
  try {
    const project = $("project").value.trim();
    if (!project) throw new Error("Enter a project name.");
    const workspace = $("workspace").value;
    const title = captureTitle();
    if (capture.file) {
      const result = await requestAPI(apiSettings, "files", {
        file: capture.file, metadata: {...capture.metadata, project, workspace},
      });
      await send("fileJob", {id: result.job_id, base: apiSettings.base, project, workspace, title});
    } else {
      const payload = {...capture, project, workspace};
      if (new TextEncoder().encode(JSON.stringify(payload)).length > 1000000) {
        throw new Error("This capture is too large. Select a smaller passage.");
      }
      await send("save", {capture: payload, title});
    }
    closeReview();
    setFeedback("feedback", `Sent to ${workspace} › ${project}.`);
    const {projects = []} = await send("settings").catch(() => ({}));
    $("recent-projects").replaceChildren(...projects.map((name) => new Option(name)));
    await pollJob();
  } catch (error) {
    showError($("review-feedback"), error);
  } finally {
    busy = false;
    $("save").textContent = "Save to Imprint";
    render();
  }
});

if (uploadPage) {
  document.body.classList.add("upload-page");
  document.title = "Upload to Imprint";
  $("capture-heading").textContent = "Upload to Imprint";
  $("capture-heading").classList.remove("visually-hidden");
  for (const id of ["tab-actions", "tab-context", "dashboard-actions"]) $(id).hidden = true;
  $("upload-picker").hidden = false;
  const zone = $("drop-zone");
  const accepting = () => connected && !busy && $("review").hidden;
  for (const type of ["dragenter", "dragover"]) {
    document.addEventListener(type, (event) => {
      if (!event.dataTransfer?.types.includes("Files")) return;
      event.preventDefault();
      zone.classList.toggle("dragging", accepting());
    });
  }
  document.addEventListener("dragleave", (event) => {
    if (!event.relatedTarget) zone.classList.remove("dragging");
  });
  document.addEventListener("drop", (event) => {
    event.preventDefault();
    zone.classList.remove("dragging");
    const [file] = event.dataTransfer?.files || [];
    if (file && accepting()) void preview("attachment", file);
  });
  document.addEventListener("paste", (event) => {
    const [file] = event.clipboardData?.files || [];
    if (file && accepting()) {
      event.preventDefault();
      void preview("attachment", file);
    }
  });
} else {
  try {
    [activeTab] = await chrome.tabs.query({active: true, currentWindow: true});
  } catch {
    activeTab = undefined;
  }
  if (activeTab?.url) exportTarget = googleExport(activeTab.url, activeTab.title);
  tab = describeTab(activeTab);
  $("tab-kind").textContent = tab.chip;
  $("tab-title").textContent = activeTab?.title || activeTab?.url || "This tab";
  $("tab-title").title = activeTab?.url || "";
  if (exportTarget) {
    const doc = exportTarget.kind === "google-doc";
    $("file-label").textContent = doc ? "Google Doc" : "Google Sheet";
    defaultHints.file = doc ? "Exported as DOCX with your Google sign-in" : "Exported as XLSX, every sheet included";
  } else if (tab.chip === "PDF") {
    defaultHints.file = "Saves this PDF with your browser’s sign-in";
  }
  render();
}

await connect();
