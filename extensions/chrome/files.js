// Exports use the open tab's Google account, including multi-account URLs.
export function googleExport(value, title = "") {
  const url = new URL(value);
  if (url.origin !== "https://docs.google.com") return null;
  const match = url.pathname.match(/^\/(document|spreadsheets)(\/u\/\d+)?\/d\/([\w-]+)(?:\/|$)/);
  if (!match) return null;
  const [, service, account = "", id] = match;
  const isDoc = service === "document";
  const format = isDoc ? "docx" : "xlsx";
  const canonical = `${url.origin}/${service}/d/${id}/edit`;
  const exportURL = new URL(`${url.origin}/${service}${account}/d/${id}/export`);
  exportURL.searchParams.set("format", format);
  for (const key of ["authuser", "resourcekey"]) {
    if (url.searchParams.has(key)) exportURL.searchParams.set(key, url.searchParams.get(key));
  }
  const name = (title.replace(/\s*[-–—]\s*Google (Docs|Sheets)\s*$/i, "").trim() || (isDoc ? "Google document" : "Google spreadsheet")).slice(0, 180);
  const filename = name.replace(/[<>:"/\\|?*\x00-\x1f]/g, "-") + "." + format;
  return {url: exportURL.href, source_url: canonical, filename, title: name, kind: isDoc ? "google-doc" : "google-sheet"};
}

// Serialized by executeScript and run only on the active tab, in Chrome's
// isolated world. The page's browser session supplies the fetch credentials.
export async function readTabFile(explicitURL, expectedPage, extensions, limit) {
  try {
    const page = new URL(location.href);
    if (page.href !== expectedPage) throw new Error("The tab changed. Open Imprint Capture again.");
    const extension = (url) => url.pathname.match(/\.[a-z\d]+$/i)?.[0].toLowerCase();
    const supported = (url) => extensions.includes(extension(url));
    let url = explicitURL ? new URL(explicitURL) : page;
    if (!explicitURL && !supported(page)) {
      const selection = window.getSelection();
      const link = [document.activeElement, selection?.anchorNode?.parentElement]
        .map((element) => element?.closest("a[href]"))
        .find(Boolean);
      if (link) url = new URL(link.href);
    }
    if (!["http:", "https:"].includes(url.protocol) || url.origin !== page.origin || url.username || url.password) {
      throw new Error("Download this attachment in Chrome, then use Upload file.");
    }
    const response = await fetch(url, {credentials: "same-origin", cache: "no-store", signal: AbortSignal.timeout(30000)});
    const mime = response.headers.get("Content-Type")?.split(";")[0].trim().toLowerCase();
    if (!response.ok || !response.body || mime === "text/html") {
      throw new Error("Chrome could not read the file. Download or export it, then use Upload file.");
    }
    let filename = decodeURIComponent(url.pathname.split("/").pop());
    if (!explicitURL) {
      const disposition = response.headers.get("Content-Disposition") || "";
      const encoded = disposition.match(/(?:^|;)\s*filename\*=UTF-8''([^;]+)/i)?.[1];
      const named = disposition.match(/(?:^|;)\s*filename=(?:"([^"]+)"|([^;]+))/i);
      filename = (encoded ? decodeURIComponent(encoded) : (named?.[1] ?? named?.[2] ?? filename))
        .split(/[\\/]/).at(-1).trim();
      if (!extensions.some((suffix) => filename.toLowerCase().endsWith(suffix))) {
        const types = {
          "application/pdf": ".pdf",
          "text/plain": ".txt",
          "text/markdown": ".md",
          "text/csv": ".csv",
          "image/png": ".png",
          "image/jpeg": ".jpg",
          "image/webp": ".webp",
        };
        const suffix = types[mime];
        if (!extensions.includes(suffix)) throw new Error("Download this attachment, then use Upload file.");
        filename = (filename || "attachment") + suffix;
      }
    }
    if (Number(response.headers.get("Content-Length")) > limit) throw new Error("This file exceeds the local upload limit.");
    const reader = response.body.getReader();
    const chunks = [];
    let total = 0;
    try {
      while (true) {
        const {value, done} = await reader.read();
        if (done) break;
        total += value.length;
        if (total > limit) throw new Error("This file exceeds the local upload limit.");
        chunks.push(value);
      }
    } finally {
      await reader.cancel();
    }
    // Base64 crosses executeScript's JSON boundary. The loop bounds argument size.
    const pieces = [];
    for (const chunk of chunks) {
      for (let start = 0; start < chunk.length; start += 8192) {
        pieces.push(String.fromCharCode(...chunk.subarray(start, start + 8192)));
      }
    }
    return {base64: btoa(pieces.join("")), filename, url: url.href};
  } catch (error) {
    return {error: error.message};
  }
}

export function decodedFile(base64, type = "application/octet-stream") {
  const binary = atob(base64);
  const data = Uint8Array.from(binary, (char) => char.charCodeAt(0));
  return new Blob([data], {type});
}

export function meetingTitle(tab) {
  const url = new URL(tab.url || "about:blank");
  let provider;
  if (url.hostname === "meet.google.com") provider = "Google Meet";
  if (["teams.microsoft.com", "teams.live.com", "teams.cloud.microsoft"].includes(url.hostname)) provider = "Microsoft Teams";
  if (!provider) return "";
  const title = (tab.title || "").replace(/^\(\d+\)\s*/, "").trim();
  const name = title.replace(/^(?:meet|google meet|microsoft teams)\s*[-–—|:]\s*/i, "")
    .replace(/\s*[-–—|]\s*(?:meet|google meet|microsoft teams|teams)$/i, "").trim();
  if (name && !/^(?:meet|google meet|microsoft teams|teams)$/i.test(name)) return name.slice(0, 200);
  const code = url.pathname.match(/\b[a-z]{3}-[a-z]{4}-[a-z]{3}\b/i)?.[0];
  return code ? `Google Meet · ${code}` : provider;
}
