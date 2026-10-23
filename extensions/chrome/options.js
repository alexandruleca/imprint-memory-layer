import {send, showError} from "./ui.js";
const $ = (id) => document.getElementById(id);
$("command").textContent = `python -m imprint.browser_capture ${chrome.runtime.id} --workspace YOUR_WORKSPACE`;
$("revoke").textContent = `python -m imprint.browser_capture ${chrome.runtime.id} --revoke`;
try {
  const settings = await send("settings");
  $("base").value = settings.base;
  if (settings.paired) $("token").placeholder = "Leave empty to keep the saved token";
} catch (error) {showError($("feedback"), error);}
finally {$("save").disabled = false;}
$("connect").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("save").disabled = true;
  try {
    const status = await send("connect", {base: $("base").value, token: $("token").value});
    $("token").value = "";
    $("token").placeholder = "Leave empty to keep the saved token";
    $("feedback").textContent = `Connected to workspace: ${status.workspace}`;
  } catch (error) {showError($("feedback"), error);}
  finally {$("save").disabled = false;}
});
$("disconnect").addEventListener("click", async () => {
  try {
    await send("disconnect");
    $("token").value = "";
    $("token").placeholder = "Paste the token from the command";
    $("feedback").textContent = "Local connection forgotten. Use the revoke command to remove backend access.";
  } catch (error) {showError($("feedback"), error);}
});
