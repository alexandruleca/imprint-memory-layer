import {apiBase, requestAPI, trustedSender} from "./client.js";

// Content scripts never receive the pairing token or the ability to call this bridge.
const storageReady = chrome.storage.local.setAccessLevel({accessLevel: "TRUSTED_CONTEXTS"});

async function handle(message) {
  await storageReady;
  const {settings = {}} = await chrome.storage.local.get("settings");
  const request = (path, body, override = settings) => requestAPI({...override, extensionId: chrome.runtime.id}, path, body);
  switch (message.type) {
    case "settings":
      return {base: settings.base || "http://127.0.0.1:8420", project: settings.project || "browser", paired: Boolean(settings.token)};
    case "connect": {
      const updated = {base: apiBase(message.base), token: message.token.trim() || settings.token,
        project: settings.project || "browser"};
      const status = await request("status", undefined, updated);
      await chrome.storage.local.set({settings: updated});
      await chrome.storage.local.remove("lastJob");
      return status;
    }
    case "disconnect":
      await chrome.storage.local.remove(["settings", "lastJob"]);
      return {};
    case "status":
      return request("status");
    case "save": {
      const result = await request("captures", message.capture);
      await chrome.storage.local.set({lastJob: result.job_id,
        settings: {...settings, project: message.capture.project}});
      return result;
    }
    case "job": {
      const {lastJob} = await chrome.storage.local.get("lastJob");
      if (!lastJob) return null;
      if (!/^[a-f0-9]{32}$/.test(lastJob)) throw new Error("Invalid stored job ID.");
      return request("jobs/" + lastJob);
    }
    default:
      throw new Error("Unknown extension action.");
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!trustedSender(sender, chrome.runtime.id)) return false;
  handle(message).then((data) => sendResponse({ok: true, data}), (error) => sendResponse({ok: false, error: error.message}));
  return true;
});
