export function apiBase(value) {
  const url = new URL(value);
  if (url.protocol !== "http:" || url.hostname !== "127.0.0.1" || url.username || url.password || url.pathname !== "/" || url.search || url.hash) {
    throw new Error("Use http://127.0.0.1:8420 (or another local Imprint port).");
  }
  return url.origin;
}

export function trustedSender(sender, extensionId) {
  return sender.id === extensionId &&
    ["popup.html", "options.html"].some((page) => sender.url === `chrome-extension://${extensionId}/${page}`);
}

export async function requestAPI(settings, path, body, fetcher = fetch) {
  const base = apiBase(settings.base || "http://127.0.0.1:8420");
  const response = await fetcher(base + "/api/browser/local/" + path, {
    method: body === undefined ? "GET" : "POST",
    headers: {"X-Imprint-Extension-Id": settings.extensionId,
      ...(body === undefined ? {} : {"Content-Type": "application/json"})},
    ...(body === undefined ? {} : {body: JSON.stringify(body)}),
    credentials: "omit", redirect: "error", cache: "no-store", signal: AbortSignal.timeout(10000),
  });
  if (response.status === 404 && path.startsWith("jobs/")) {
    throw new Error("This capture job is no longer available.");
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
