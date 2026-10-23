import {extractCapture} from "./capture.js";
import {send, showError} from "./ui.js";

const $ = (id) => document.getElementById(id);
let documents = [];
let account = "";
let polling = false;
$("settings").addEventListener("click", () => chrome.runtime.openOptionsPage());

async function pollJob() {
  if (polling) return;
  polling = true;
  try {
    const job = await send("job");
    if (!job) return;
    $("job").textContent = ({queued: "Capture queued. It will continue if you close this popup.", running: "Indexing capture…", done: "Capture indexed in Imprint.", failed: "Indexing failed. Check this job in Imprint's queue for details.", cancelled: "Capture cancelled."})[job.status] || job.status;
    if (["queued", "running"].includes(job.status)) setTimeout(pollJob, 1500);
  } catch (error) {
    showError($("job"), error);
  } finally {
    polling = false;
  }
}

async function preview(mode) {
  $("feedback").textContent = "Reading the current tab…";
  $("capture-form").hidden = true;
  documents = [];
  try {
    const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
    const results = await chrome.scripting.executeScript({target: {tabId: tab.id}, func: extractCapture, args: [mode, account]});
    if (results[0]?.error) throw new Error(results[0].error.message || "Page extraction failed.");
    if (results[0]?.result?.error) throw new Error(results[0].result.error);
    documents = results[0]?.result;
    if (!Array.isArray(documents) || !documents.length) throw new Error("Could not extract this page. Try selecting text or another tab.");
    const bytes = new TextEncoder().encode(JSON.stringify({documents, project: $("project").value})).length;
    if (bytes > 950000 || documents.length > 50) throw new Error("Capture is too large. Select a smaller passage or expand fewer emails.");
    $("preview").replaceChildren();
    for (const [index, doc] of documents.entries()) {
      const label = document.createElement("label");
      const input = document.createElement("input");
      input.id = `title-${index}`;
      input.value = doc.title;
      input.maxLength = 1000;
      input.required = true;
      input.addEventListener("input", () => {doc.title = input.value;});
      label.htmlFor = input.id;
      label.textContent = doc.kind === "gmail" ? "Email subject" : "Title";
      const source = document.createElement("p");
      source.className = "source";
      source.textContent = doc.url + (doc.account ? `\nAccount: ${doc.account}` : "");
      const textLabel = document.createElement("label");
      const text = document.createElement("textarea");
      text.id = `text-${index}`;
      text.value = doc.text;
      text.rows = 9;
      text.minLength = 10;
      text.maxLength = 800000;
      text.required = true;
      text.addEventListener("input", () => {doc.text = text.value;});
      textLabel.htmlFor = text.id;
      textLabel.textContent = "Content to save";
      $("preview").append(label, input, source, textLabel, text);
    }
    $("capture-form").hidden = false;
    $("feedback").textContent = `${documents.length} document(s). Review the text, then save.`;
  } catch (error) {showError($("feedback"), error);}
}

$("page").addEventListener("click", () => preview("page"));
$("selection").addEventListener("click", () => preview("selection"));
$("capture-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("save").disabled = true;
  try {
    if (!$("project").value.trim()) throw new Error("Enter a project name.");
    await send("save", {capture: {project: $("project").value.trim(), documents}});
    $("capture-form").hidden = true;
    $("feedback").textContent = "Sent to local Imprint.";
    await pollJob();
  } catch (error) {showError($("feedback"), error);}
  finally {$("save").disabled = false;}
});

try {
  const settings = await send("settings");
  account = settings.account;
  $("project").value = settings.project;
  const status = await send("status");
  $("connection").textContent = `Connected · workspace: ${status.workspace}`;
  $("page").disabled = false;
  $("selection").disabled = false;
  await pollJob();
} catch (error) {showError($("connection"), error);}
