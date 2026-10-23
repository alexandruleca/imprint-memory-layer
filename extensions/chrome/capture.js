// This function is serialized by executeScript. Keep every dependency inside it.
export function extractCapture(mode = "page", account = "") {
  try {
  const clean = (text) => (text || "").replace(/\u00a0/g, " ").replace(/\n{3,}/g, "\n\n").trim();
  const visible = (element) => {
    const style = getComputedStyle(element);
    return element.getClientRects().length > 0 && style.display !== "none" && style.visibility !== "hidden";
  };
  if (!["http:", "https:"].includes(location.protocol)) {
    throw new Error("Open an ordinary web page to capture its content.");
  }
  if (document.contentType === "application/pdf") {
    throw new Error("PDF capture is planned. Download the PDF and ingest it with Imprint for now.");
  }
  const gmail = location.hostname === "mail.google.com";
  if (gmail && !account.trim()) throw new Error("Enter this Gmail account's email in extension settings first.");
  const common = {url: location.href, title: document.title.slice(0, 1000) || location.hostname, account: gmail ? account.trim() : ""};
  if (mode === "selection") {
    const selection = window.getSelection();
    const anchor = selection?.anchorNode?.parentElement;
    if (anchor?.closest('input, textarea, [contenteditable="true"], [role="textbox"]')) {
      throw new Error("Select text outside a form or draft editor.");
    }
    const text = clean(selection?.toString());
    if (text.length < 10) throw new Error("Select at least 10 characters on the page first.");
    return [{...common, kind: "selection", text}];
  }
  if (gmail) {
    const main = document.querySelector('[role="main"]');
    const subject = clean(main?.querySelector("h2.hP")?.innerText) || common.title;
    const documents = [];
    const seen = new Set();
    for (const container of main?.querySelectorAll("[data-legacy-message-id]") || []) {
      const body = container.querySelector(".a3s");
      if (!body || !visible(body) || body.closest('[contenteditable="true"], [role="textbox"]')) continue;
      const messageId = container.getAttribute("data-legacy-message-id");
      if (!messageId || seen.has(messageId)) continue;
      const sender = container.querySelector(".gD[email]");
      const date = container.querySelector(".g3");
      const text = clean(body.innerText);
      if (text.length < 10) continue;
      seen.add(messageId);
      documents.push({...common, kind: "gmail", message_id: messageId, title: subject.slice(0, 1000),
        text: [sender ? `From: ${sender.getAttribute("email")}` : "", date ? `Date: ${date.getAttribute("title") || date.innerText}` : "", "", text].filter(Boolean).join("\n")});
    }
    if (!documents.length) throw new Error("Open an email and expand the messages to save. Gmail inbox capture is unavailable.");
    return documents;
  }
  if (location.hostname === "docs.google.com") {
    throw new Error("Google Docs needs an export adapter. Export the document and ingest it with Imprint for now.");
  }
  const root = document.querySelector("article") || document.querySelector("main") || document.querySelector('[role="main"]') || document.body;
  const lines = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let node;
  let size = 0;
  while ((node = walker.nextNode())) {
    const parent = node.parentElement;
    if (!parent || parent.closest('script, style, noscript, nav, header, footer, aside, input, textarea, select, button, [hidden], [aria-hidden="true"], [contenteditable="true"], [role="textbox"]') || !visible(parent)) continue;
    const text = clean(node.textContent);
    if (text) {
      lines.push(text);
      size += text.length + 1;
      if (size > 800000) throw new Error("Page is too large. Select a smaller passage to save.");
    }
  }
  const text = lines.join("\n");
  if (text.length < 10) throw new Error("No readable page text found. Try selecting a passage instead.");
  return [{...common, kind: "page", text}];
  } catch (error) {
    return {error: error.message};
  }
}
