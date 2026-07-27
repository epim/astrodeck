// toastQueue.test.ts — UX review #33 regression ("two identical toasts
// sometimes, zero other times").
//
// The safety UNSAFE notice is enqueued sticky (ttl: 0) so a later transient
// toast can't push it off. Two bugs in enqueueToast made that queue behave
// exactly as the reviewer described, and neither was visible from the server:
//
//   two   — the duplicate check only coalesced within a flat 5s of createdAt.
//           A sticky toast never ages out of the screen, so an identical re-trip
//           at +6s failed the age test and enqueued a SECOND identical card.
//   zero  — overflow eviction picked `find(t => t.ttl > 0 && t.kind==='generic')`
//           and fell back to next[0] — the OLDEST — when nothing matched. With
//           the queue full of sticky notices the fallback fired and threw away
//           the UNSAFE toast itself, which is why a DOM query for "UNSAFE:"
//           returned [] on a trip whose banner and pause fired correctly.
//
// Same inline-assert harness + browser stubs as store.test.ts (the store reads
// localStorage/document/window at import time):
//     npx tsx src/__tests__/toastQueue.test.ts

// ------------------------------------------------------------ browser stubs
class MemStorage {
  private m = new Map<string, string>();
  getItem(k: string): string | null {
    return this.m.has(k) ? (this.m.get(k) as string) : null;
  }
  setItem(k: string, v: string): void {
    this.m.set(k, String(v));
  }
  removeItem(k: string): void {
    this.m.delete(k);
  }
  clear(): void {
    this.m.clear();
  }
}
const g = globalThis as unknown as {
  localStorage?: Storage;
  document?: unknown;
  window?: unknown;
};
if (typeof g.localStorage === "undefined") {
  g.localStorage = new MemStorage() as unknown as Storage;
}
if (typeof g.window === "undefined") {
  g.window = { location: { pathname: "/", host: "localhost", protocol: "http:" } };
}
if (typeof g.document === "undefined") {
  const classList = {
    toggle(_c: string, _on?: boolean): void {},
    add(_c: string): void {},
    remove(_c: string): void {},
    contains(_c: string): boolean {
      return false;
    },
  };
  const style = {
    setProperty(_k: string, _v: string): void {},
    getPropertyValue(_k: string): string {
      return "";
    },
  };
  g.document = { documentElement: { classList, style } };
}

const { useStore } = await import("../store");
import type { Toast } from "../types";

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try {
    fn();
    passed++;
  } catch (e) {
    failed++;
    failures.push(`✗ ${name}: ${(e as Error).message}`);
  }
}
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} expected ${String(b)}, got ${String(a)}`);
}

const UNSAFE = "UNSAFE: rain detected";
const toasts = (): Toast[] => useStore.getState().toasts;
const reset = (): void => useStore.setState({ toasts: [] });
/** Backdate every queued toast so the next enqueue looks like a LATER re-trip. */
function age(ms: number): void {
  useStore.setState({
    toasts: toasts().map((t) => ({ ...t, createdAt: t.createdAt - ms })),
  });
}
const unsafe = (): void =>
  useStore.getState().enqueueToast({ level: "error", title: UNSAFE, ttl: 0 });

// ====================================================================
// "two identical toasts sometimes"
// ====================================================================

test("#33: an identical STICKY toast coalesces however long ago it was raised", () => {
  reset();
  unsafe();
  age(60_000); // a minute of rain later, the monitor re-publishes the same edge
  unsafe();
  eq(toasts().length, 1, "still exactly one UNSAFE card");
  eq(toasts()[0].count, 2, "the ×N chip carries the repeat, not a second card");
});

test("#33: a timed toast still coalesces while it is on screen, and not after", () => {
  reset();
  const say = () => useStore.getState().enqueueToast({ level: "info", title: "Site saved" });
  say();
  age(2_000);
  say();
  eq(toasts().length, 1, "within the window: coalesced");
  age(60_000); // long gone from the screen
  say();
  eq(toasts().length, 2, "after it expired: a genuinely new toast");
});

test("#33: different reasons stay separate cards (coalescing is per-title)", () => {
  reset();
  unsafe();
  useStore.getState().enqueueToast({ level: "error", title: "UNSAFE: cloud sensor", ttl: 0 });
  eq(toasts().length, 2, "two distinct conditions, two cards");
});

// ====================================================================
// "zero other times"
// ====================================================================

test("#33: overflow never evicts the sticky UNSAFE toast", () => {
  reset();
  unsafe();
  // Fill and overflow the queue with ordinary transient toasts.
  for (const t of ["one", "two", "three", "four", "five"]) {
    useStore.getState().enqueueToast({ level: "info", title: t });
  }
  assert(toasts().some((t) => t.title === UNSAFE), "UNSAFE survived the flood");
  eq(toasts()[0].title, UNSAFE, "and it is still the oldest/top card");
});

test("#33: overflow with a full queue of sticky notices drops a non-error first", () => {
  reset();
  const stick = (level: "warning" | "error", title: string) =>
    useStore.getState().enqueueToast({ level, title, ttl: 0 });
  stick("error", UNSAFE);
  stick("warning", "Dew heater at 100%");
  stick("warning", "Disk 88 GB free");
  stick("warning", "Guiding degraded"); // overflows TOAST_MAX
  assert(toasts().some((t) => t.title === UNSAFE), "the sticky ERROR is kept");
  assert(!toasts().some((t) => t.title === "Dew heater at 100%"),
         "the oldest sticky non-error is what goes");
});

test("#33: the toast being enqueued is never its own eviction victim", () => {
  reset();
  const stick = (title: string) =>
    useStore.getState().enqueueToast({ level: "error", title, ttl: 0 });
  stick("a"); stick("b"); stick("c");     // queue full of sticky errors
  useStore.getState().enqueueToast({ level: "info", title: "just now" });
  assert(toasts().some((t) => t.title === "just now"),
         "the new arrival is on screen, not silently swallowed");
});

test("#33: the focal sequence toast is never evicted", () => {
  reset();
  useStore.getState().enqueueToast({ level: "error", title: "Sequence failed", kind: "sequence", ttl: 0 });
  for (const t of ["one", "two", "three", "four"]) {
    useStore.getState().enqueueToast({ level: "info", title: t });
  }
  assert(toasts().some((t) => t.kind === "sequence"), "sequence toast survives");
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\ntoastQueue.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
