import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

/** An item a write could not change, and why. */
export type Failure = { key: string; error: string };

/** One write: `kind` applied to the items named by `keys`. */
export interface WriteOp<K extends string> {
  readonly id: number;
  readonly kind: K;
  readonly keys: string[];
  /** Sends only the keys that succeeded in this earlier write: an undo's
   * inverse never runs for an item whose write failed. */
  readonly after?: WriteOp<K>;
  state: "queued" | "sending" | "done" | "cancelled";
  ok: Set<string>;
}

type Settled = (ok: string[], failed: Failure[], skipped: string[]) => void;

/** Review decisions as a queue of writes (ADR 0136): one request in flight,
 * because concurrent moves would collide on the ingestion lock (ADR 0064);
 * adjacent writes of one kind merge into one request of at most `chunk`
 * items; a write not yet sent can be taken back without a request. Nothing
 * waits on a timer: a write leaves as soon as it is first in line. */
export class WriteQueue<K extends string> {
  private ops: { op: WriteOp<K>; settled: Settled }[] = [];
  private serial = 0;
  private busy = false;
  private listeners = new Set<() => void>();
  private drained = new Set<() => void>();
  private count = 0;

  private send: (kind: K, keys: string[], keepalive: boolean) => Promise<Failure[]> = async () => [];

  constructor(private chunk = 10) {}

  /** How writes reach the server: the page's latest sender. */
  sendWith(send: (kind: K, keys: string[], keepalive: boolean) => Promise<Failure[]>) {
    this.send = send;
  }

  /** Queue `kind` for `keys`, in requests of at most `chunk` items. */
  enqueue(kind: K, keys: string[], settled: Settled, after?: WriteOp<K>): WriteOp<K>[] {
    const made: WriteOp<K>[] = [];
    for (let start = 0; start < keys.length; start += this.chunk) {
      const op: WriteOp<K> = { id: ++this.serial, kind, keys: keys.slice(start, start + this.chunk), after, state: "queued", ok: new Set() };
      this.ops.push({ op, settled });
      made.push(op);
    }
    this.changed();
    void this.pump();
    return made;
  }

  /** Take back a write that has not left; false once it has. */
  cancel(op: WriteOp<K>): boolean {
    const index = this.ops.findIndex((entry) => entry.op === op);
    if (index < 0 || op.state !== "queued") return false;
    this.ops.splice(index, 1);
    op.state = "cancelled";
    this.changed();
    return true;
  }

  /** Keys with a write waiting or in flight. */
  unsettled(): Set<string> {
    return new Set(this.ops.flatMap((entry) => entry.op.keys));
  }

  /** Items waiting or in flight, for "Saving 3…". */
  readonly pending = () => this.count;

  readonly subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };

  /** Called each time the last write has settled. */
  onDrain(listener: () => void) {
    this.drained.add(listener);
    return () => { this.drained.delete(listener); };
  }

  /** The page is going away: send what waits, with keepalive requests that
   * outlive it, and forget it. */
  flush() {
    // In order, so an undo's inverse leaves after what it reverses; one
    // whose write is still in flight has no known result and stays.
    const waiting = this.ops.filter((entry) => entry.op.state === "queued" && entry.op.after?.state !== "sending");
    this.ops = this.ops.filter((entry) => !waiting.includes(entry));
    for (const { op } of waiting) {
      const keys = op.after ? op.keys.filter((key) => op.after!.ok.has(key)) : op.keys;
      op.state = "done";
      op.ok = new Set(keys); // Unknown now; what depends on it is sent too.
      if (keys.length) void this.send(op.kind, keys, true).catch(() => {});
    }
    this.changed();
  }

  private changed() {
    this.count = this.ops.reduce((n, entry) => n + entry.op.keys.length, 0);
    this.listeners.forEach((listener) => listener());
  }

  private take() {
    const first = this.ops[0];
    const batch = [first];
    let size = first.op.keys.length;
    for (const entry of this.ops.slice(1)) {
      if (entry.op.kind !== first.op.kind || size + entry.op.keys.length > this.chunk
          || (entry.op.after && batch.some((held) => held.op === entry.op.after))) break;
      batch.push(entry);
      size += entry.op.keys.length;
    }
    return batch;
  }

  private async pump() {
    if (this.busy) return;
    this.busy = true;
    try {
      while (this.ops.length) {
        const batch = this.take();
        batch.forEach(({ op }) => { op.state = "sending"; });
        const sent = batch.map(({ op }) => (op.after ? op.keys.filter((key) => op.after!.ok.has(key)) : op.keys));
        const keys = [...new Set(sent.flat())];
        let failures: Failure[] = [];
        if (keys.length) {
          try {
            failures = await this.send(batch[0].op.kind, keys, false);
          } catch (error) {
            const message = error instanceof Error ? error.message : String(error);
            failures = keys.map((key) => ({ key, error: message }));
          }
        }
        const failed = new Map(failures.map((failure) => [failure.key, failure.error]));
        this.ops = this.ops.filter((entry) => !batch.includes(entry));
        this.changed();
        batch.forEach(({ op, settled }, index) => {
          op.ok = new Set(sent[index].filter((key) => !failed.has(key)));
          op.state = "done";
          settled([...op.ok], sent[index].filter((key) => failed.has(key)).map((key) => ({ key, error: failed.get(key)! })),
                  op.keys.filter((key) => !sent[index].includes(key)));
        });
      }
    } finally {
      this.busy = false;
    }
    this.drained.forEach((listener) => listener());
  }
}

/** A decision the page can take back: what each key was before it. */
interface Action<K extends string, S extends string> {
  kind: K;
  from: Map<string, S>;
  ops: WriteOp<K>[];
  /** What the page said it did, for the undo's toast. */
  note: string;
}

export interface DecisionOptions<K extends string, S extends string> {
  send: (kind: K, keys: string[], keepalive: boolean) => Promise<Failure[]>;
  /** The state a key takes from a write of `kind`. */
  result: (kind: K) => S;
  /** The writes that take a key decided by `kind` back to `from`, as near
   * as the server allows (a move back is undone by reopening and moving). */
  inverse: (kind: K, from: S) => K[];
  /** Every write settled: time to read the server again. */
  onDrain: () => void;
  /** Some keys failed; their rows are back with the error. */
  onFailed: (kind: K, failed: Failure[]) => void;
}

/** Optimistic decisions over a WriteQueue: the page shows `overrides` over
 * what the server last returned, failed keys come back with their error,
 * and `undo` takes back the last decision, cancelling what has not left
 * and sending the inverse of what has. */
export function useDecisions<K extends string, S extends string>(options: DecisionOptions<K, S>) {
  const latest = useRef(options);
  const [queue] = useState(() => new WriteQueue<K>());
  useEffect(() => {
    latest.current = options;
    queue.sendWith(options.send);
  });
  const [overrides, setOverrides] = useState<ReadonlyMap<string, S>>(() => new Map());
  const [errors, setErrors] = useState<ReadonlyMap<string, { kind: K; error: string }>>(() => new Map());
  const history = useRef<Action<K, S>[]>([]);
  const pending = useSyncExternalStore(queue.subscribe, queue.pending, () => 0);

  useEffect(() => queue.onDrain(() => latest.current.onDrain()), [queue]);
  useEffect(() => {
    const leave = () => queue.flush();
    window.addEventListener("pagehide", leave);
    return () => window.removeEventListener("pagehide", leave);
  }, [queue]);

  const settled = useCallback((kind: K): Settled => (ok, failed, skipped) => {
    const later = queue.unsettled();
    const dropped = [...failed.map((f) => f.key), ...skipped].filter((key) => !later.has(key));
    if (dropped.length) setOverrides((prev) => { const next = new Map(prev); dropped.forEach((key) => next.delete(key)); return next; });
    if (failed.length) {
      setErrors((prev) => { const next = new Map(prev); failed.forEach((f) => next.set(f.key, { kind, error: f.error })); return next; });
      latest.current.onFailed(kind, failed);
    }
  }, [queue]);

  const set = useCallback((keys: Iterable<string>, state: (key: string) => S) => {
    const list = [...keys];
    setOverrides((prev) => { const next = new Map(prev); list.forEach((key) => next.set(key, state(key))); return next; });
    setErrors((prev) => {
      if (!list.some((key) => prev.has(key))) return prev;
      const next = new Map(prev); list.forEach((key) => next.delete(key)); return next;
    });
  }, []);

  /** Apply `kind` to `keys`, each now in the state `from` gives. */
  const decide = useCallback((kind: K, keys: string[], from: Map<string, S>, note = "") => {
    if (!keys.length) return;
    const ops = queue.enqueue(kind, keys, settled(kind));
    set(keys, () => latest.current.result(kind));
    history.current = [...history.current.slice(-49), { kind, from, ops, note }];
  }, [queue, set, settled]);

  /** Take back the last decision; returns its keys, or null when none. */
  const undo = useCallback((): { kind: K; keys: string[]; from: Map<string, S>; note: string } | null => {
    const action = history.current.at(-1);
    if (!action) return null;
    history.current = history.current.slice(0, -1);
    const state = new Map<string, S>();
    for (const op of action.ops) {
      if (queue.cancel(op)) {
        op.keys.forEach((key) => state.set(key, action.from.get(key)!));
        continue;
      }
      const byFrom = new Map<S, string[]>();
      op.keys.forEach((key) => byFrom.set(action.from.get(key)!, [...(byFrom.get(action.from.get(key)!) ?? []), key]));
      for (const [from, keys] of byFrom) {
        const kinds = latest.current.inverse(action.kind, from);
        let after: WriteOp<K> = op;
        for (const kind of kinds) after = queue.enqueue(kind, keys, settled(kind), after)[0];
        keys.forEach((key) => state.set(key, kinds.length ? latest.current.result(kinds.at(-1)!) : from));
      }
    }
    set(state.keys(), (key) => state.get(key)!);
    return { kind: action.kind, keys: action.ops.flatMap((op) => op.keys), from: action.from, note: action.note };
  }, [queue, set, settled]);

  /** Write `kind` again for a key whose write failed. */
  const retry = useCallback((key: string, from: S) => {
    const failed = errors.get(key);
    if (failed) decide(failed.kind, [key], new Map([[key, from]]));
  }, [decide, errors]);

  /** After the server was read again: keep only what is still on its way. */
  const prune = useCallback(() => {
    const waiting = queue.unsettled();
    setOverrides((prev) => {
      const next = new Map([...prev].filter(([key]) => waiting.has(key)));
      return next.size === prev.size ? prev : next;
    });
  }, [queue]);

  return { overrides, errors, pending, decide, undo, retry, prune };
}
