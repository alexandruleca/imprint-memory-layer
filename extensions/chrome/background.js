import {apiBase, originHost, requestAPI, trustedSender} from "./client.js";

// Chrome keeps content scripts out of saved settings; Firefox has no such
// switch, and this extension injects only its own capture functions.
const storageReady = chrome.storage.local.setAccessLevel?.({accessLevel: "TRUSTED_CONTEXTS"}) ?? Promise.resolve();
const root = chrome.runtime.getURL("");
const extensionId = originHost(root);
const defaultBase = "http://127.0.0.1:8420";
// The popup shows the last capture for a day, and offers recent projects.
const JOB_SHOWN_FOR = 24 * 3600 * 1000;
const RECENT_PROJECTS = 6;

const text = (value, limit) => (typeof value === "string" ? value.slice(0, limit) : "");

async function remember(job, message, projects) {
  const project = text(message.project, 128);
  await chrome.storage.local.set({
    lastJob: {...job, title: text(message.title, 300), workspace: text(message.workspace, 128), project, at: Date.now()},
    project,
    projects: [project, ...projects.filter((name) => name !== project)].slice(0, RECENT_PROJECTS),
  });
}

async function handle(message) {
  await storageReady;
  const {settings = {}, workspace, project, lastJob, projects: stored} = await chrome.storage.local.get(
    ["settings", "workspace", "project", "lastJob", "projects"],
  );
  const projects = Array.isArray(stored) ? stored.filter((name) => typeof name === "string") : [];
  const base = settings.base || defaultBase;
  const request = (path, body, address = base) => requestAPI({base: address, extensionId}, path, body);

  switch (message.type) {
    case "settings":
      return {base, workspace, project: project || settings.project || "browser", projects};
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
      const {project: name, workspace: destination} = message.capture;
      await remember({id: result.job_id, base}, {title: message.title, project: name, workspace: destination}, projects);
      return result;
    }
    case "fileJob":
      if (base === message.base && /^[a-f0-9]{32}$/.test(message.id)) {
        await remember({id: message.id, base}, message, projects);
      }
      return {};
    case "job": {
      if (lastJob?.base !== base || !/^[a-f0-9]{32}$/.test(lastJob?.id)) return null;
      if (lastJob.at && Date.now() - lastJob.at > JOB_SHOWN_FOR) {
        await chrome.storage.local.remove("lastJob");
        return null;
      }
      try {
        const status = await request("jobs/" + lastJob.id);
        return {...status, title: lastJob.title || "", workspace: lastJob.workspace || "", at: lastJob.at || 0};
      } catch (error) {
        if (!error.gone) throw error;
        await chrome.storage.local.remove("lastJob");
        return null;
      }
    }
    case "dismissJob":
      await chrome.storage.local.remove("lastJob");
      return {};
    default:
      throw new Error("Unknown extension action.");
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!trustedSender(sender, chrome.runtime.id, root)) return false;
  handle(message).then((data) => sendResponse({ok: true, data}), (error) => sendResponse({ok: false, error: error.message}));
  return true;
});
