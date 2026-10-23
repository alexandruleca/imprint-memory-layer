// This function is serialized by executeScript. Keep every dependency inside it.
export function extractCapture(mode = "page") {
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
      throw new Error("PDF capture is coming soon. Download the PDF and ingest it with Imprint for now.");
    }
    if (location.hostname === "mail.google.com") throw new Error("Gmail capture is coming soon. Web pages and selected text on other sites are available now.");
    if (location.hostname === "docs.google.com") throw new Error("Google Docs & Sheets capture is coming soon. Export the document and ingest it with Imprint for now.");
    const common = {url: location.href, title: document.title.slice(0, 1000) || location.hostname};
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
