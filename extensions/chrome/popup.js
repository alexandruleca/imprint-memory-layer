import {extractCapture} from "./capture.js";
import {send, showError} from "./ui.js";

const $ = (id) => document.getElementById(id);
const captureActions = ["page", "selection", "url"];
let capture;
let connected = false;
let jobTimer;
let latestPoll = 0;

function setBusy(busy) {
  for (const id of captureActions) $(id).disabled = busy || !connected;
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

async function preview(mode) {
  setBusy(true);
  showMessage("feedback", "Reading the current tab…");
  $("capture-form").hidden = true;
  $("preview").replaceChildren();
  capture = undefined;
  try {
    const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
    if (!tab?.id || !/^https?:\/\//i.test(tab.url || "")) {
      throw new Error("Open an HTTP or HTTPS page to capture this tab.");
    }
    if (mode === "url") {
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
    $("project").value = settings.project;
    $("workspace").replaceChildren(...status.workspaces.map((name) => new Option(name, name)));
    $("workspace").value = status.workspaces.includes(settings.workspace) ? settings.workspace : status.active;
    connected = Boolean($("workspace").value);
    if (!connected) throw new Error("No active workspace. Create or select one in Imprint.");
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
for (const mode of captureActions) $(mode).addEventListener("click", () => preview(mode));
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
    const payload = {...capture, project, workspace};
    if (new TextEncoder().encode(JSON.stringify(payload)).length > 1000000) {
      throw new Error("Capture is too large. Select a smaller passage.");
    }
    await send("save", {capture: payload});
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
