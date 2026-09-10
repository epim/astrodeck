// stackView.ts - the live stack's SERVER state (polled once) and the two
// DISPLAY choices that ride on top of it (channel tint, stretch).
//
// ONE POLLER. `components/preview/SessionStack.tsx` self-subscribes and polls;
// the Now screen needs the same numbers in two places (the picture and the
// channel strip), and two pollers would be two requests every ten seconds all
// night for one number that changes once per exposure. The cadence itself is
// transcribed, not invented: 10 s while the stack is on, 1.5 s while a backfill
// is reading (a progress counter that updates every ten seconds is a progress
// counter nobody believes), and NOTHING while it is off.
//
// STRETCH IS A DISPLAY CONTROL AND IT DOES NOT TOUCH `store.stretch`
// (deviation D5). That slice is the LIVE PREVIEW's black/mid/white transfer
// function; it applies to the per-frame path and hijacking it would silently
// change the Inspect sheet's histogram. SOFT/AUTO/HARD is a CSS filter over one
// JPEG, so it is kept per phone in localStorage, wrapped in try/catch, and the
// screen renders correctly with nothing stored.
//
// THE CHANNEL CHIP IS A TINT, NOT A FETCH (deviation D4). The stack preview
// route takes `size` and `seq` and nothing else - there is no per-channel image
// on the server - so picking Ha greyscales the composite and tints it, and the
// badge says exactly that. The honesty line under the strip is not optional.

import { useEffect, useState } from "react";
import {
  backfillSessionStack, getSessionStack, resetSessionStack, startSessionStack,
  stopSessionStack, type SessionStackStatus,
} from "../../../../api/sessionStack";

// --------------------------------------------------------------- stretch

export type StretchMode = "SOFT" | "AUTO" | "HARD";
export const STRETCH_KEY = "astrodeck-next-stretch";

/** logic.js `stretchOpts` / `stackFilter`, transcribed. */
export const STRETCH_FILTER: Record<StretchMode, string> = {
  SOFT: "contrast(.9) brightness(.85)",
  AUTO: "none",
  HARD: "contrast(1.35) brightness(1.25)",
};

function readStretch(): StretchMode {
  try {
    const v = localStorage.getItem(STRETCH_KEY);
    if (v === "SOFT" || v === "AUTO" || v === "HARD") return v;
  } catch { /* private window, blocked storage: AUTO is the right default */ }
  return "AUTO";
}

let stretch: StretchMode = readStretch();
let channel: string | null = null;
const viewListeners = new Set<() => void>();

function publishView(): void { for (const fn of viewListeners) fn(); }

export interface StackView {
  stretch: StretchMode;
  setStretch: (m: StretchMode) => void;
  /** The filter name being shown alone, or null for the combined composite. */
  channel: string | null;
  setChannel: (f: string | null) => void;
  /** The CSS `filter` for the composite image. */
  cssFilter: string;
}

export function useStackView(): StackView {
  const [, bump] = useState(0);
  useEffect(() => {
    const fn = () => bump((n) => n + 1);
    viewListeners.add(fn);
    return () => { viewListeners.delete(fn); };
  }, []);

  const parts: string[] = [];
  if (channel) parts.push("grayscale(1)");
  if (STRETCH_FILTER[stretch] !== "none") parts.push(STRETCH_FILTER[stretch]);

  return {
    stretch,
    channel,
    cssFilter: parts.length ? parts.join(" ") : "none",
    setStretch: (m) => {
      stretch = m;
      try { localStorage.setItem(STRETCH_KEY, m); } catch { /* nothing to persist to */ }
      publishView();
    },
    setChannel: (f) => { channel = f; publishView(); },
  };
}

export function resetStackViewForTests(): void {
  stretch = "AUTO";
  channel = null;
  viewListeners.clear();
}

// ------------------------------------------------------------ server state

const POLL_MS = 10_000;
const BACKFILL_POLL_MS = 1_500;

interface StackState {
  status: SessionStackStatus | null;
  busy: boolean;
  error: string | null;
}

let stack: StackState = { status: null, busy: false, error: null };
const stackListeners = new Set<() => void>();
let poller: ReturnType<typeof setInterval> | null = null;
let mounted = 0;

function publishStack(next: StackState): void {
  stack = next;
  for (const fn of stackListeners) fn();
}

async function refresh(): Promise<void> {
  try {
    const s = await getSessionStack();
    publishStack({ ...stack, status: s, error: null });
  } catch (e) {
    // A failed poll is not worth a red panel: the run is unaffected and the next
    // tick is ten seconds away. It is only SAID when there is nothing at all to
    // show, so nobody stares at a stale picture that looks live.
    publishStack({ ...stack, error: (e as Error).message });
  }
}

function schedule(): void {
  if (poller) { clearInterval(poller); poller = null; }
  if (mounted === 0) return;
  if (!stack.status?.enabled) return;
  const ms = stack.status.backfill?.running ? BACKFILL_POLL_MS : POLL_MS;
  poller = setInterval(() => { void refresh().then(schedule); }, ms);
}

export interface StackRead extends StackState {
  start: (withEarlier: boolean) => void;
  backfill: () => void;
  stop: () => void;
  reset: () => void;
}

export function useSessionStackStatus(): StackRead {
  const [, bump] = useState(0);
  useEffect(() => {
    const fn = () => bump((n) => n + 1);
    stackListeners.add(fn);
    mounted += 1;
    if (mounted === 1) void refresh().then(schedule);
    return () => {
      stackListeners.delete(fn);
      mounted -= 1;
      if (mounted === 0 && poller) { clearInterval(poller); poller = null; }
    };
  }, []);

  const act = (fn: () => Promise<SessionStackStatus>) => {
    publishStack({ ...stack, busy: true });
    void fn().then(
      (s) => { publishStack({ status: s, busy: false, error: null }); schedule(); },
      (e: Error) => { publishStack({ ...stack, busy: false, error: e.message }); },
    );
  };

  return {
    ...stack,
    start: (withEarlier: boolean) => act(() => startSessionStack(withEarlier)),
    backfill: () => act(backfillSessionStack),
    stop: () => act(stopSessionStack),
    reset: () => act(resetSessionStack),
  };
}

export function resetSessionStackStateForTests(): void {
  if (poller) { clearInterval(poller); poller = null; }
  mounted = 0;
  stack = { status: null, busy: false, error: null };
  stackListeners.clear();
}
