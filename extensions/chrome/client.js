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
  if (!settings.token) throw new Error("Pair with Imprint in extension settings first.");
  const response = await fetcher(base + "/api/browser/" + path, {
    method: body === undefined ? "GET" : "POST",
    headers: {"Authorization": `Bearer ${settings.token}`, "X-Imprint-Extension-Id": settings.extensionId,
      ...(body === undefined ? {} : {"Content-Type": "application/json"})},
    ...(body === undefined ? {} : {body: JSON.stringify(body)}),
    credentials: "omit", redirect: "error", cache: "no-store", signal: AbortSignal.timeout(10000),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || "Imprint request failed.");
  return result;
}
