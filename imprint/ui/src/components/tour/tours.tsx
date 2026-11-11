"use client";

import { usePathname } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";
import { toast } from "sonner";
import { Check, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useRecorder } from "@/lib/recorder";
import { onTourRequest, useSeenTours } from "@/lib/tour-state";
import { stepsIn, tourAt, TOURS, WELCOME, type Tour, type TourStep } from "@/lib/tours";
import { setTutorials, setUiMode, useUiMode, type UiMode } from "@/lib/ui-mode";
import { cn } from "@/lib/utils";

/** The tutorials (ADR 0183): the welcome on a new installation, then each
 * page's few steps the first time it opens. A step points at a control by its
 * `data-tour` name; the page stays usable under it, and a tutorial never
 * starts over a dialog or during a recording. */

type Running = { tour: Tour; steps: TourStep[]; index: number; path: string };
type View = { x: number; y: number; w: number; h: number; found: boolean; vw: number; vh: number; cw: number; ch: number; covered: boolean };

const GAP = 12;
const EDGE = 12;
const PAD = 6;
/** How long a page may take to show a tour's first control before the tour waits for the next visit. */
const READY_MS = 8000;

function control(name: string): HTMLElement | null {
  const element = document.querySelector<HTMLElement>(`[data-tour="${name}"]`);
  return element && element.getClientRects().length > 0 ? element : null;
}

/** A dialog or sheet of the page is open: tutorials wait behind it. */
function covered() {
  return !!document.querySelector('[role="dialog"]:not([data-tour-card]), [role="alertdialog"]');
}

/** The steps, of either dashboard, whose control is on the page now. */
function present(tour: Tour) {
  return tour.steps.filter((step) => !step.target || control(step.target));
}

function reducedMotion() {
  return typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** Where the step's card goes: beside its control where there is room, else in the middle. */
function place(view: View): { left: number; top: number } {
  const { x, y, w, h, vw, vh, cw, ch } = view;
  const clampX = (left: number) => Math.min(Math.max(EDGE, left), Math.max(EDGE, vw - cw - EDGE));
  const clampY = (top: number) => Math.min(Math.max(EDGE, top), Math.max(EDGE, vh - ch - EDGE));
  if (!view.found) return { left: clampX((vw - cw) / 2), top: clampY((vh - ch) / 2) };
  const below = y + h + PAD + GAP;
  const above = y - PAD - GAP - ch;
  const right = x + w + PAD + GAP;
  const left = x - PAD - GAP - cw;
  // A tall control at the window's side (the sidebar): the card goes beside it.
  const tall = h > vh * 0.5;
  if (tall && right + cw + EDGE <= vw) return { left: right, top: clampY(y + Math.min(h, vh) / 2 - ch / 2) };
  if (tall && left >= EDGE) return { left, top: clampY(y + Math.min(h, vh) / 2 - ch / 2) };
  if (below + ch + EDGE <= vh) return { left: clampX(x), top: below };
  if (above >= EDGE) return { left: clampX(x), top: above };
  if (right + cw + EDGE <= vw) return { left: right, top: clampY(y) };
  if (left >= EDGE) return { left, top: clampY(y) };
  return { left: clampX(x), top: clampY(below) };
}

function same(a: View | null, b: View) {
  return !!a && a.x === b.x && a.y === b.y && a.w === b.w && a.h === b.h && a.found === b.found
    && a.vw === b.vw && a.vh === b.vh && a.cw === b.cw && a.ch === b.ch && a.covered === b.covered;
}

export function Tours() {
  const path = usePathname().replace(/\/+$/, "") || "/";
  // One per page visit: leaving a page ends its tutorial, and a skip keeps
  // other tutorials from starting only until then.
  return <PageTours key={path} path={path} />;
}

function PageTours({ path }: { path: string }) {
  const ui = useUiMode();
  const recorder = useRecorder();
  const { seen, markSeen } = useSeenTours();
  const [started, setStarted] = useState<Running | null>(null);
  // After a skip nothing else starts by itself until the next page.
  const [quiet, setQuiet] = useState<string | null>(null);
  const [view, setView] = useState<View | null>(null);
  const card = useRef<HTMLDivElement>(null);
  const primary = useRef<HTMLButtonElement>(null);
  const returnTo = useRef<HTMLElement | null>(null);
  const titleId = useId();
  const bodyId = useId();

  // A tour belongs to the page it started on: leaving the page ends it, unseen.
  // Its steps follow the dashboard, which the welcome can switch as it runs.
  const running = started && started.path === path
    ? { ...started, steps: started.steps.filter((candidate) => !candidate.only || candidate.only === ui.mode) } : null;
  const step = running?.steps[Math.min(running.index, running.steps.length - 1)];
  const idle = recorder.phase === "idle";

  function begin(tour: Tour, simple: boolean) {
    const steps = present(tour);
    if (!stepsIn({ ...tour, steps }, simple).length) return false;
    returnTo.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setView(null);
    setStarted({ tour, steps, index: 0, path: location.pathname.replace(/\/+$/, "") || "/" });
    return true;
  }

  // The welcome first, then the open page's tour, each once per browser.
  const next = !ui.settled || !ui.tutorials || running || quiet === path || !idle ? undefined
    : !seen.includes(WELCOME.id) ? WELCOME
      : tourAt(path);
  const due = next && !seen.includes(next.id) ? next : undefined;
  useEffect(() => {
    if (!due) return;
    const since = Date.now();
    const timer = window.setInterval(() => {
      if (Date.now() - since > READY_MS) { window.clearInterval(timer); return; }
      if (document.hidden || covered()) return;
      const first = stepsIn(due, ui.simple)[0];
      if (first?.target && !control(first.target)) return;
      window.clearInterval(timer);
      begin(due, ui.simple);
    }, 300);
    return () => window.clearInterval(timer);
  });

  // The help menu shows a tour again, seen or not, or lets them start again.
  useEffect(() => onTourRequest((request) => {
    if ("resume" in request) { setQuiet(null); return; }
    const tour = request.id === WELCOME.id ? WELCOME : TOURS.find((candidate) => candidate.page === request.id);
    if (!tour || !begin(tour, ui.simple)) toast.message("Nothing to show on this page yet.");
  }));

  function end(how: "done" | "skip" | "off") {
    if (!running) return;
    markSeen(running.tour.id);
    if (how !== "done") setQuiet(running.path);
    setStarted(null);
    returnTo.current?.focus({ preventScroll: true });
    if (how === "off") {
      setTutorials(false)
        .then(() => toast.message("Tutorials are off", { description: "The help button in the top bar shows a page's tutorial and turns them back on." }))
        .catch((error) => toast.error("Could not turn tutorials off", { description: error instanceof Error ? error.message : String(error) }));
    }
  }

  function move(by: number) {
    setStarted((current) => current && { ...current, index: Math.min(Math.max(0, current.index + by), current.steps.length - 1) });
  }

  // Follow the control as the page scrolls, resizes or loads.
  useEffect(() => {
    if (!step) return;
    const element = step.target ? control(step.target) : null;
    if (element) {
      const rect = element.getBoundingClientRect();
      if (rect.top < 0 || rect.bottom > window.innerHeight) {
        element.scrollIntoView({ block: rect.height > window.innerHeight * 0.6 ? "start" : "center", behavior: reducedMotion() ? "auto" : "smooth" });
      }
    }
    let frame = 0;
    const tick = () => {
      const target = step.target ? control(step.target) : null;
      const rect = target?.getBoundingClientRect();
      const size = card.current?.getBoundingClientRect();
      setView((current) => {
        // Behind a dialog the card is not laid out: it keeps the size it had.
        const measured: View = {
          x: Math.round(rect?.left ?? 0), y: Math.round(rect?.top ?? 0), w: Math.round(rect?.width ?? 0), h: Math.round(rect?.height ?? 0),
          found: !!rect, vw: window.innerWidth, vh: window.innerHeight,
          cw: Math.round(size?.width || current?.cw || 0), ch: Math.round(size?.height || current?.ch || 0), covered: covered(),
        };
        return same(current, measured) ? current : measured;
      });
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [step]);

  const shown = !!step && !!view && !view.covered;
  useEffect(() => {
    if (shown) primary.current?.focus({ preventScroll: true });
  }, [shown, step]);

  useEffect(() => {
    if (!shown) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !event.defaultPrevented) { event.preventDefault(); end("skip"); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (!running || !step) return null;
  const index = Math.min(running.index, running.steps.length - 1);
  const last = index === running.steps.length - 1;
  const sheet = !!view && view.vw < 640;
  const position = view && !sheet ? place(view) : null;
  const still = reducedMotion();

  return (
    // Hidden behind a dialog; transparent for the frame before the card is
    // measured. Not `invisible`: the buttons' own transition would delay
    // becoming visible, and the focus that follows would be refused.
    <div className={cn("pointer-events-none fixed inset-0 z-[60]", view?.covered ? "hidden" : !view && "opacity-0")} inert={!shown} data-tour-layer>
      {view?.found ? (
        <div
          aria-hidden
          className={cn("absolute rounded-lg ring-2 ring-primary shadow-[0_0_0_9999px_rgb(0_0_0/0.4)]", !still && "transition-all duration-200")}
          style={{ left: view.x - PAD, top: view.y - PAD, width: view.w + PAD * 2, height: view.h + PAD * 2 }}
        />
      ) : (
        <div aria-hidden className="absolute inset-0 bg-black/40" />
      )}
      <div
        ref={card}
        role="dialog"
        aria-modal="false"
        aria-labelledby={titleId}
        aria-describedby={bodyId}
        data-tour-card
        className={cn(
          "pointer-events-auto absolute flex flex-col gap-3 rounded-xl bg-popover p-4 text-sm text-popover-foreground shadow-lg ring-1 ring-foreground/15",
          sheet ? "inset-x-2 bottom-2" : step.choice ? "w-[26rem] max-w-[calc(100vw-1.5rem)]" : "w-80 max-w-[calc(100vw-1.5rem)]",
          !still && !sheet && "transition-[left,top] duration-200",
        )}
        style={position ?? undefined}
      >
        <div className="flex items-start justify-between gap-3">
          <h2 id={titleId} className="text-base font-semibold leading-snug">{step.title}</h2>
          <Button variant="ghost" size="icon-xs" className="-mt-1 -mr-1 shrink-0 text-muted-foreground" aria-label="Skip this tutorial" onClick={() => end("skip")}>
            <X aria-hidden />
          </Button>
        </div>
        <p id={bodyId} className="text-muted-foreground">{step.body}</p>
        {step.choice === "mode" && <ModeChoice />}
        <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
          <span className="text-xs text-muted-foreground tabular-nums" aria-live="polite">
            {running.steps.length > 1 ? `${index + 1} of ${running.steps.length}` : ""}
          </span>
          <div className="flex items-center gap-2">
            {index > 0
              ? <Button variant="ghost" size="sm" onClick={() => move(-1)}>Back</Button>
              : <Button variant="ghost" size="sm" onClick={() => end("skip")}>Skip</Button>}
            <Button ref={primary} size="sm" onClick={() => (last ? end("done") : move(1))}>{last ? "Done" : "Next"}</Button>
          </div>
        </div>
        {ui.tutorials && (
          <button type="button" onClick={() => end("off")}
            className="self-start rounded-sm text-xs text-muted-foreground underline-offset-2 outline-none hover:text-foreground hover:underline focus-visible:ring-3 focus-visible:ring-ring/50">
            Skip all tutorials
          </button>
        )}
      </div>
    </div>
  );
}

const MODES: { value: UiMode; title: string; detail: string }[] = [
  { value: "simple", title: "Simple", detail: "The everyday pages and a short list of settings, in plain words. Right for most people." },
  { value: "advanced", title: "Advanced", detail: "Every page and every setting, for setting Imprint up in detail and looking into problems." },
];

/** The welcome's choice of dashboard; it applies as it is chosen. */
function ModeChoice() {
  const ui = useUiMode();
  const choose = (mode: UiMode) => {
    setUiMode(mode).catch((error) => toast.error("Could not switch the dashboard", { description: error instanceof Error ? error.message : String(error) }));
  };
  return (
    <div role="group" aria-label="Dashboard" className="grid gap-2">
      {MODES.map(({ value, title, detail }) => {
        const selected = ui.mode === value;
        return (
          <button key={value} type="button" aria-pressed={selected} onClick={() => choose(value)}
            className={cn(
              "flex items-start gap-3 rounded-lg p-3 text-left ring-1 outline-none transition-colors focus-visible:ring-3 focus-visible:ring-ring/50",
              selected ? "bg-muted ring-foreground/40" : "ring-foreground/10 hover:bg-muted/60",
            )}>
            <span className={cn("mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full ring-1", selected ? "bg-primary text-primary-foreground ring-primary" : "ring-foreground/30")}>
              {selected && <Check className="size-3" aria-hidden />}
            </span>
            <span className="min-w-0">
              <span className="block font-medium text-foreground">{title}</span>
              <span className="block text-xs text-muted-foreground">{detail}</span>
            </span>
          </button>
        );
      })}
    </div>
  );
}
