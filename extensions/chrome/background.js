import {apiBase, requestAPI, trustedSender} from "./client.js";

const storageReady = chrome.storage.local.setAccessLevel({accessLevel: "TRUSTED_CONTEXTS"});
const defaultBase = "http://127.0.0.1:8420";

async function handle(message) {
  await storageReady;
  const {settings = {}, workspace, project, lastJob} = await chrome.storage.local.get(
    ["settings", "workspace", "project", "lastJob"],
  );
  const base = settings.base || defaultBase;
  const request = (path, body, address = base) => requestAPI({base: address, extensionId: chrome.runtime.id}, path, body);

  switch (message.type) {
    case "settings":
      return {base, workspace, project: project || settings.project || "browser"};
    case "connect": {
      const address = apiBase(message.base);
      const status = await request("status", undefined, address);
      await chrome.storage.local.set({settings: {base: address}});
      return status;
    }
    case "status":
      return request("status");
    case "workspace":
      await chrome.storage.local.set({workspace: message.workspace});
      return {};
    case "save": {
      const result = await request("captures", message.capture);
      await chrome.storage.local.set({lastJob: {id: result.job_id, base}, project: message.capture.project});
      return result;
    }
    case "fileJob":
      if (base === message.base && /^[a-f0-9]{32}$/.test(message.id)) {
        await chrome.storage.local.set({lastJob: {id: message.id, base}, project: message.project});
      }
      return {};
    case "job":
      if (lastJob?.base !== base || !/^[a-f0-9]{32}$/.test(lastJob?.id)) return null;
      return request("jobs/" + lastJob.id);
    default:
      throw new Error("Unknown extension action.");
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!trustedSender(sender, chrome.runtime.id)) return false;
  handle(message).then((data) => sendResponse({ok: true, data}), (error) => sendResponse({ok: false, error: error.message}));
  return true;
});
