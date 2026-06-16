// useWakeLock.ts — screen wake-lock hook (touch spec §8.3, R13).
//
// CRITICAL decoupling (R13): the wake lock is requested ONLY while a sequence is
// running OR `monitorAwake` is explicitly on — NEVER tied to `locked`. Locking to
// pocket the phone must let the screen SLEEP; coupling wake-lock to the lock would
// burn battery in a pocket. So this hook reads the two real "keep the screen on"
// signals and ignores `locked` entirely.
//
// Fully guarded: `navigator.wakeLock` is absent on iOS Safari < 16.4 and many
// in-app WebViews. Every path is a silent no-op when unsupported — no throw, no
// console noise, no dead UI.

import { useEffect, useRef } from "react";
import { useStore } from "../store";
import { useMonitorAwake } from "./touchStore";

// Minimal structural types so we don't depend on lib.dom's WakeLock typings being
// present in every toolchain (older @types/* lack them).
interface WakeLockSentinelLike {
  released: boolean;
  release(): Promise<void>;
  addEventListener(type: "release", cb: () => void): void;
}
interface WakeLockLike {
  request(type: "screen"): Promise<WakeLockSentinelLike>;
}

function getWakeLock(): WakeLockLike | null {
  if (typeof navigator === "undefined") return null;
  const wl = (navigator as unknown as { wakeLock?: WakeLockLike }).wakeLock;
  return wl && typeof wl.request === "function" ? wl : null;
}

/**
 * Hold a screen wake lock while `active`. Re-acquires automatically after the OS
 * drops the lock on tab-hide/return (the documented WakeLock lifecycle). Releases
 * on deactivate and unmount. Safe to call unconditionally — it does nothing where
 * the API is missing.
 */
export function useWakeLock(active: boolean): { supported: boolean } {
  const sentinel = useRef<WakeLockSentinelLike | null>(null);
  const wantRef = useRef(active);
  wantRef.current = active;
  const supported = getWakeLock() != null;

  useEffect(() => {
    const wl = getWakeLock();
    if (!wl) return; // unsupported -> silent no-op
    let cancelled = false;
    // F-wakelock: debounce the on-visible re-acquire. The OS can fire `release`
    // then `visibilitychange` in quick succession on tab return; a raw re-acquire
    // races the release handler (and some engines reject a request issued in the
    // same frame as the release). Coalesce bursts into a single deferred acquire.
    let revisitTimer: ReturnType<typeof setTimeout> | null = null;
    const VISIBLE_REACQUIRE_DEBOUNCE_MS = 300;

    const acquire = async () => {
      if (sentinel.current && !sentinel.current.released) return;
      try {
        const s = await wl.request("screen");
        if (cancelled || !wantRef.current) {
          // state changed while awaiting — release immediately.
          void s.release().catch(() => {});
          return;
        }
        sentinel.current = s;
        // The OS releases the lock when the page is hidden; re-acquire on return
        // (visibilitychange handler below) — also clear our ref when it fires.
        s.addEventListener("release", () => {
          if (sentinel.current === s) sentinel.current = null;
        });
      } catch {
        /* user-gesture / permission / power-save rejection — silent */
      }
    };

    const release = () => {
      const s = sentinel.current;
      sentinel.current = null;
      if (s && !s.released) void s.release().catch(() => {});
    };

    const onVisible = () => {
      if (document.visibilityState !== "visible" || !wantRef.current) return;
      if (revisitTimer != null) clearTimeout(revisitTimer);
      revisitTimer = setTimeout(() => {
        revisitTimer = null;
        // re-check liveness at fire time — visibility/intent may have flipped back.
        if (!cancelled && wantRef.current && document.visibilityState === "visible") {
          void acquire();
        }
      }, VISIBLE_REACQUIRE_DEBOUNCE_MS);
    };

    if (active) {
      void acquire();
      document.addEventListener("visibilitychange", onVisible);
    } else {
      release();
    }

    return () => {
      cancelled = true;
      if (revisitTimer != null) clearTimeout(revisitTimer);
      document.removeEventListener("visibilitychange", onVisible);
      release();
    };
  }, [active]);

  return { supported };
}

/**
 * App-root convenience: derives `active` from the real signals (sequence running
 * OR monitorAwake) so a caller can just `useMonitorWakeLock()` at the root. Uses
 * narrow store selectors so it doesn't widen App's subscription (R27).
 */
export function useMonitorWakeLock(): { supported: boolean; active: boolean } {
  const seqRunning = useStore(
    (s) => s.sequence.state === "running" || s.sequence.state === "paused",
  );
  const monitorAwake = useMonitorAwake();
  const active = seqRunning || monitorAwake;
  const { supported } = useWakeLock(active);
  return { supported, active };
}
