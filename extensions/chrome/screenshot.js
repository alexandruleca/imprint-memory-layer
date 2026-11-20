import {decodedFile} from "./files.js";

// A full-page screenshot without the debugger permission: scroll the tab one
// viewport at a time, capture each with captureVisibleTab and stitch them on a
// canvas (ADR 0191). Chrome allows two captures a second.
const CAPTURE_INTERVAL = 550;
// Bounds the canvas (4 bytes a pixel) and stays under Chrome's 32,767 px side.
export const MAX_PIXELS = 48_000_000;
export const MAX_HEIGHT = 32_000;

// Serialized by executeScript. It runs in the tab's isolated world, which keeps
// its state between calls; if the popup closes mid-capture, the watchdog puts
// the page back as it was.
export function fullPageStep(action, top = 0) {
  const state = globalThis.__imprintFullPage ||= {hidden: [], origin: null, timer: 0};
  const restore = () => {
    clearTimeout(state.timer);
    for (const [element, value, priority] of state.hidden) element.style.setProperty("visibility", value, priority);
    state.hidden = [];
    if (state.origin) window.scrollTo({left: state.origin[0], top: state.origin[1], behavior: "instant"});
    state.origin = null;
  };
  if (action === "restore") return void restore();
  if (action === "start") restore();
  clearTimeout(state.timer);
  state.timer = setTimeout(restore, 5000);
  if (action === "start") {
    state.origin = [scrollX, scrollY];
    const root = document.scrollingElement || document.documentElement;
    return {width: innerWidth, height: innerHeight, total: Math.max(root.scrollHeight, document.body?.scrollHeight || 0)};
  }
  // Fixed banners would cover the text under them and repeat in every slice;
  // sticky headers sit in place in the first slice and repeat after it.
  const positions = action === "scroll-first" ? ["fixed"] : action === "scroll-rest" ? ["fixed", "sticky"] : [];
  if (positions.length) {
    const hidden = new Set(state.hidden.map(([element]) => element));
    for (const element of document.querySelectorAll("body *")) {
      if (hidden.has(element) || !positions.includes(getComputedStyle(element).position)) continue;
      state.hidden.push([element, element.style.getPropertyValue("visibility"), element.style.getPropertyPriority("visibility")]);
      element.style.setProperty("visibility", "hidden", "important");
    }
  }
  window.scrollTo({top, behavior: "instant"});
  // Two frames let the page paint; a hidden tab gets no frames, hence the timer.
  return new Promise((done) => {
    requestAnimationFrame(() => requestAnimationFrame(() => done(scrollY)));
    setTimeout(() => done(scrollY), 150);
  });
}

const pause = (ms) => new Promise((done) => setTimeout(done, ms));

async function captureTab(tab) {
  for (let attempt = 0; ; attempt++) {
    // Pages that track the section in view rewrite the fragment while scrolling.
    const [current] = await chrome.tabs.query({active: true, currentWindow: true});
    if (current?.id !== tab.id || current.url?.split("#")[0] !== tab.url?.split("#")[0]) {
      throw new Error("The tab changed. Try again.");
    }
    try {
      return await chrome.tabs.captureVisibleTab(tab.windowId, {format: "png"});
    } catch (error) {
      if (attempt > 2 || !/MAX_CAPTURE_VISIBLE_TAB_CALLS_PER_SECOND/.test(error.message)) throw error;
      await pause(CAPTURE_INTERVAL);
    }
  }
}

async function bitmap(dataURL) {
  return createImageBitmap(decodedFile(dataURL.split(",")[1], "image/png"));
}

// Returns {blob, width, height, truncated, scrolls}; onProgress(done, total).
export async function captureFullPage(tab, onProgress = () => {}) {
  // Arguments cross as JSON, which has no undefined.
  const run = async (action, top = 0) => {
    const [result] = await chrome.scripting.executeScript({target: {tabId: tab.id}, func: fullPageStep, args: [action, top]});
    return result?.result;
  };
  let page;
  try {
    page = await run("start");
  } catch {
    throw new Error("The browser can’t capture this page. Try Visible for the visible area.");
  }
  try {
    const scrolls = page.total > page.height + 1;
    const slices = [];
    let canvas;
    let scale = 1;
    let limit = page.height;
    let bottom = 0;
    let count = 1;
    for (let index = 0; index < count; index++) {
      const started = Date.now();
      const top = scrolls ? await run(index === 0 ? "scroll-first" : index === 1 ? "scroll-rest" : "scroll", index * page.height) : 0;
      const image = await bitmap(await captureTab(tab));
      if (!canvas) {
        scale = image.width / page.width;
        limit = Math.min(page.total, Math.floor(MAX_HEIGHT / scale), Math.floor(MAX_PIXELS / (image.width * scale)));
        count = scrolls ? Math.ceil(limit / page.height) : 1;
        canvas = new OffscreenCanvas(image.width, Math.round((scrolls ? limit : page.height) * scale));
        const context = canvas.getContext("2d");
        context.fillStyle = "#fff";
        context.fillRect(0, 0, canvas.width, canvas.height);
      }
      canvas.getContext("2d").drawImage(image, 0, Math.round(top * scale));
      image.close();
      bottom = Math.max(bottom, top + page.height);
      slices.push(top);
      onProgress(index + 1, count);
      if (index + 1 < count) await pause(Math.max(0, CAPTURE_INTERVAL - (Date.now() - started)));
    }
    // The page can end up shorter than measured; drop the empty rows.
    const height = Math.min(canvas.height, Math.round(bottom * scale));
    let output = canvas;
    if (height < canvas.height) {
      output = new OffscreenCanvas(canvas.width, height);
      output.getContext("2d").drawImage(canvas, 0, 0);
    }
    return {
      blob: await output.convertToBlob({type: "image/png"}),
      width: output.width, height: output.height,
      cssHeight: Math.round(output.height / scale), truncated: scrolls && page.total > limit, scrolls,
    };
  } finally {
    await run("restore").catch(() => {});
  }
}
