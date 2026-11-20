import {send, showError} from "./ui.js";

const $ = (id) => document.getElementById(id);
const defaultBase = "http://127.0.0.1:8420";

$("version").textContent = `Version ${chrome.runtime.getManifest().version}`;

try {
  const settings = await send("settings");
  $("base").value = settings.base;
} catch (error) {
  showError($("feedback"), error);
} finally {
  for (const id of ["base", "save", "reset"]) $(id).disabled = false;
}

chrome.commands?.getAll?.().then((commands) => {
  const shortcut = commands.find((command) => command.name === "_execute_action")?.shortcut;
  $("shortcut").textContent = shortcut ? `Current: ${shortcut}` : "None set";
}, () => {});

// Firefox opens its shortcut settings itself; Chrome has a page for them.
$("shortcuts").addEventListener("click", () => (chrome.commands?.openShortcutSettings
  ? chrome.commands.openShortcutSettings()
  : chrome.tabs.create({url: "chrome://extensions/shortcuts"})));
$("reset").addEventListener("click", () => {
  $("base").value = defaultBase;
  $("connect").requestSubmit();
});

$("connect").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("save").disabled = true;
  $("feedback").classList.remove("error", "success");
  $("feedback").textContent = "Checking…";
  try {
    const status = await send("connect", {base: $("base").value});
    const count = status.workspaces.length;
    $("feedback").classList.add("success");
    $("feedback").textContent = `Connected · ${count} local workspace${count === 1 ? "" : "s"}`;
  } catch (error) {
    showError($("feedback"), error);
  } finally {
    $("save").disabled = false;
  }
});
