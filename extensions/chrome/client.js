export function apiBase(value) {
  const url = new URL(value);
  if (url.protocol !== "http:" || url.hostname !== "127.0.0.1" || url.username || url.password || url.pathname !== "/" || url.search || url.hash) {
    throw new Error("Use http://127.0.0.1:8420 (or another local Imprint port).");
  }
  return url.origin;
}

// The host of the extension's own origin: the extension ID in Chrome
// (chrome-extension://<id>/), a per-install UUID in Firefox (moz-extension://<uuid>/).
// Imprint compares it with the Origin of each submission.
export function originHost(root) {
  return new URL(root).host;
}

// root is chrome.runtime.getURL(""); sender.id is the extension ID in both browsers.
export function trustedSender(sender, extensionId, root) {
  return sender.id === extensionId &&
    ["popup.html", "popup.html?upload", "options.html"].some((page) => sender.url === root + page);
}

export async function requestAPI(settings, path, body, fetcher = fetch) {
  const base = apiBase(settings.base || "http://127.0.0.1:8420");
  const isFile = path === "files";
  const metadata = isFile ? encodeURIComponent(JSON.stringify(body.metadata)) : "";
  if (metadata.length > 8192) throw new Error("This file’s name or source URL is too long.");
  const response = await fetcher(base + "/api/browser/local/" + path, {
    method: body === undefined ? "GET" : "POST",
    headers: {"X-Imprint-Extension-Id": settings.extensionId,
      ...(body === undefined ? {} : {"Content-Type": isFile ? "application/octet-stream" : "application/json"}),
      ...(isFile ? {"X-Imprint-File": metadata} : {})},
    ...(body === undefined ? {} : {body: isFile ? body.file : JSON.stringify(body)}),
    credentials: "omit", redirect: "error", cache: "no-store", signal: AbortSignal.timeout(isFile ? 60000 : 10000),
  });
  if (response.status === 404 && path.startsWith("jobs/")) {
    throw Object.assign(new Error("This capture job is no longer available."), {gone: true});
  }
  const result = await response.json().catch(() => null);
  if (path === "status" && (response.status === 404 || result?.detail === "Extension is not paired with this Imprint")) {
    throw new Error("Update the local Imprint backend to use automatic workspace discovery.");
  }
  if (!response.ok) throw new Error(result?.detail || `Imprint request failed (HTTP ${response.status}).`);
  if (!result || (path === "status" && !Array.isArray(result.workspaces))) {
    throw new Error("The local address did not return an Imprint capture response. Check the address in Settings.");
  }
  return result;
}
