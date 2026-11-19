import {send, showError} from "./ui.js";

const $ = (id) => document.getElementById(id);

try {
  const settings = await send("settings");
  $("base").value = settings.base;
} catch (error) {
  showError($("feedback"), error);
} finally {
  $("base").disabled = false;
  $("save").disabled = false;
}

$("connect").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("save").disabled = true;
  try {
    const status = await send("connect", {base: $("base").value});
    $("feedback").textContent = `Connected · ${status.workspaces.length} local workspace(s)`;
  } catch (error) {
    showError($("feedback"), error);
  } finally {
    $("save").disabled = false;
  }
});
