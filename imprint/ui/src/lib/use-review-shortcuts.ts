import { useEffect, useRef, type RefObject } from "react";
import { useStoredFlag } from "@/lib/use-stored";

/** Handlers by key: "j", "J", "Enter", "?", and "mod+z" for Ctrl+Z or ⌘Z. */
export type ShortcutMap = Partial<Record<string, (event: KeyboardEvent) => void>>;

const TYPING = 'input:not([type="checkbox"]):not([type="radio"]), textarea, select, [contenteditable=""], [contenteditable="true"], [role="combobox"], [role="textbox"]';
const OVERLAY = '[role="dialog"], [role="alertdialog"], [role="menu"], [role="listbox"]';
const OPEN = ["dialog", "alert-dialog", "sheet", "popover", "dropdown-menu", "select"]
  .map((slot) => `[data-slot="${slot}-content"][data-open]`).join(", ");
// Keys that mean something else on a button or a link: only on a row, or
// when nothing in particular has focus.
const ROW_ONLY = new Set(["Enter", "ArrowDown", "ArrowUp"]);

/** Whether single-key shortcuts are on in this browser (WCAG 2.1.4). */
export function useShortcutsSwitch() {
  return useStoredFlag("imprint.review.shortcuts", true);
}

/** Keyboard shortcuts for a review page. They act while focus is inside
 * `root`, or on nothing in particular, and never while typing in a field,
 * with a dialog, menu, popover or select open, or with Ctrl, Alt or ⌘ held
 * (except Ctrl+Z / ⌘Z). Single-key shortcuts follow the per-browser switch;
 * Escape and Ctrl+Z always work. */
export function useReviewShortcuts(root: RefObject<HTMLElement | null>, keys: ShortcutMap) {
  const [enabled] = useShortcutsSwitch();
  const latest = useRef(keys);
  useEffect(() => { latest.current = keys; });

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.defaultPrevented || event.isComposing) return;
      const target = event.target instanceof HTMLElement ? event.target : document.body;
      if (target.closest(TYPING) || target.closest(OVERLAY) || document.querySelector(OPEN)) return;
      const inside = root.current?.contains(target) || target === document.body || target.id === "main-content";
      if (!inside) return;
      const mod = event.ctrlKey || event.metaKey;
      let name = event.key;
      if (mod && !event.altKey && name.toLowerCase() === "z" && !event.shiftKey) name = "mod+z";
      else if (mod || event.altKey) return;
      if (!enabled && name !== "Escape" && name !== "mod+z") return;
      if (ROW_ONLY.has(name) && !(target.matches("[data-review-row]") || target === document.body || target.id === "main-content")) return;
      const handler = latest.current[name];
      if (!handler) return;
      event.preventDefault();
      handler(event);
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [root, enabled]);
}
