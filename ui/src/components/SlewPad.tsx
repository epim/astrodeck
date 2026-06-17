// SlewPad.tsx — predictable fixed-rate slew + tap-to-pulse nudge (touch spec §4).
//
// THE revised headline control. Replaces the old 3-chip "slow/med/fast" pad and
// its ▲▼◀▶ glyphs. Key behaviors (all from the critique-resolution log):
//   - N/S/E/W labelled arrows, >=56px (`tap-lg`) gloved targets, one `--tap-gap`.
//   - setPointerCapture binds the slew to the finger; stop fires ONLY on
//     pointerup/pointercancel/lostpointercapture (NO onPointerLeave — R3).
//   - touch-action:none on the pad so a vertical drag never scrolls `main` (R25).
//   - Rate selector occupies the CENTER cell (was STOP); persistent segmented
//     pulse / 8× / 0.5°/s with a growing speed glyph (shape, not color — R21).
//   - STOP is a SEPARATE full-width bar below the grid: solid --bad, ■ motif,
//     1-tap, categorically distinct from any HoldButton (R9/R10).
//   - reverse-RA / reverse-Dec toggles persisted via store.setTouch (R7).
//   - keyboard nudge fallback (Enter/Space = one pulse at the selected rate — R19).
//   - client alt-guard: below MIN_SLEW_ALT_DEG the pad auto-stops + flashes
//     "below horizon limit" (R30).
//   - NINA mode: hold disabled (NINA move_axis raises); tap = small relative GOTO;
//     a one-line note instead of a dead pad (R8).
//   - global safety: blur / visibilitychange(hidden) / setLocked(true) forceStop.
//
// Consumes: SlewController + haptics + Icon (icons.tsx) + Toggle (ui.tsx).

import { useEffect, useMemo, useRef, useState } from "react";
import type {
  PointerEvent as RPointerEvent,
  KeyboardEvent as RKeyboardEvent,
  MouseEvent as RMouseEvent,
} from "react";
import { api } from "../api";
import { useStatus, useStore } from "../store";
import { Toggle } from "./ui";
import { Icon } from "./icons";
import {
  SlewController,
  SLEW_RATES,
  MIN_SLEW_ALT_DEG,
  rateGlyph,
  type SlewState,
  type Axis,
  type Dir,
} from "../lib/slewController";
import { haptics } from "../lib/haptics";
import { useTouchSettings, useSetTouch, useLocked } from "../lib/touchStore";
import { useCanControlMount } from "../lib/caps";

const axisLabelText: Record<string, string> = {
  "dec:1": "north",
  "dec:-1": "south",
  "ra:1": "east",
  "ra:-1": "west",
};
const arrowGlyph: Record<string, string> = {
  "dec:1": "▲",
  "dec:-1": "▼",
  "ra:1": "▶",
  "ra:-1": "◀",
};
const cardinal: Record<string, string> = {
  "dec:1": "N",
  "dec:-1": "S",
  "ra:1": "E",
  "ra:-1": "W",
};

function key(axis: Axis, dir: Dir): string {
  return `${axis}:${dir}`;
}

export default function SlewPad() {
  const status = useStatus();
  const showToast = useStore((s) => s.showToast);
  const touch = useTouchSettings();
  const setTouch = useSetTouch();
  const locked = useLocked();
  // VIEWER-READ-ONLY (W2.5): a viewer lacks control.mount, so the pad is inert for
  // them — folded into padDisabled below so every press handler no-ops and the
  // arrows render dimmed. Any stray /api/mount/* 403 is swallowed by the existing
  // best-effort .catch() on the panic/stop posts (never toasts at the viewer).
  const canMount = useCanControlMount();

  const [rateIdx, setRateIdx] = useState(1); // local UI state (transient, not global)
  const [slewState, setSlewState] = useState<SlewState>({
    mode: "idle",
    axis: null,
    dir: null,
    rate: SLEW_RATES[1],
  });

  // F-A4 (multi-touch / S2): the controller is a single-axis model — a second
  // concurrent press would overwrite curAxis and strand the first axis until the
  // 1.2s deadman. We bind the slew to ONE pointerId; any other pointer is ignored
  // while we're already holding. Null when no press owns the pad.
  const activePointerId = useRef<number | null>(null);

  const m = status?.mount;
  const mode = status?.mode;
  const isNina = mode === "nina";
  const noMount = !m;
  const parked = !!m?.parked;
  // !canMount makes the pad read-only for viewers (controls inert, not 403-on-tap).
  const padDisabled = noMount || parked || !canMount;

  // Keep mutable refs the controller closures read so we never rebuild it per render.
  const rateIdxRef = useRef(rateIdx);
  rateIdxRef.current = rateIdx;
  const touchRef = useRef(touch);
  touchRef.current = touch;
  const altRef = useRef<number | null>(m?.alt ?? null);
  altRef.current = m?.alt ?? null;
  const ninaRef = useRef(isNina);
  ninaRef.current = isNina;

  // One controller for the pad's lifetime (testable logic lives in slewController.ts).
  const ctrl = useMemo(
    () =>
      new SlewController({
        getRate: () => SLEW_RATES[rateIdxRef.current],
        reverseRa: () => touchRef.current.reverseRa,
        reverseDec: () => touchRef.current.reverseDec,
        getAlt: () => altRef.current,
        isNina: () => ninaRef.current,
        postMove: async (axis, rateDegS) => {
          await api.post("/api/mount/move", { axis, rate_deg_s: rateDegS });
        },
        postNudge: async (axis, dir) => {
          if (ninaRef.current) {
            // NINA: no manual pulse path -> a small relative GOTO from current pos.
            const cur = useStore.getState().status?.mount;
            if (!cur) return;
            const DELTA_DEG = 0.25; // small, predictable centering nudge
            const ra_hours =
              axis === "ra"
                ? cur.ra_hours +
                  ((touchRef.current.reverseRa ? -1 : 1) * dir * DELTA_DEG) / 15
                : cur.ra_hours;
            const dec_deg =
              axis === "dec"
                ? cur.dec_deg + (touchRef.current.reverseDec ? -1 : 1) * dir * DELTA_DEG
                : cur.dec_deg;
            await api.post("/api/mount/goto", { ra_hours, dec_deg, center: false });
          } else {
            // pulse-guide-sized fixed move: drive the rate briefly, then zero it.
            const rate = SLEW_RATES[0].rateDegS || 0.0334;
            const sign = (axis === "ra" ? touchRef.current.reverseRa : touchRef.current.reverseDec)
              ? -1
              : 1;
            await api.post("/api/mount/move", { axis, rate_deg_s: sign * dir * rate });
            const ms = SLEW_RATES[0].pulseMs ?? 250;
            await new Promise((r) => setTimeout(r, ms));
            await api.post("/api/mount/move", { axis, rate_deg_s: 0 });
          }
        },
        onStateChange: setSlewState,
        onError: (e) => {
          showToast("error", (e as Error)?.message ?? "mount move failed");
          haptics.error();
        },
      }),
    // ctrl is stable; closures read refs. showToast is stable from zustand.
    [showToast],
  );

  // --- global safety effect: blur / hidden / unmount -> forceStop + panic stop ---
  // F-S8: the client forceStop posts a best-effort rate-0 per axis, but a single
  // dropped POST would leave the mount moving with the tab gone. So these panic
  // paths ALSO fire the authoritative /api/mount/stop (abort + zero BOTH axes),
  // exactly as the STOP bar and store.setLocked already do. Fire-and-forget: we're
  // tearing down, there's no UI left to toast an error to.
  useEffect(() => {
    const panicStop = () => {
      ctrl.forceStop();
      api.post("/api/mount/stop").catch(() => {});
    };
    const onVis = () => {
      if (document.visibilityState === "hidden") panicStop();
    };
    window.addEventListener("blur", panicStop);
    document.addEventListener("visibilitychange", onVis);
    return () => {
      window.removeEventListener("blur", panicStop);
      document.removeEventListener("visibilitychange", onVis);
      panicStop();
    };
  }, [ctrl]);

  // Locking mid-hold must stop the slew (setLocked also forceStops in the store,
  // but the pad's own controller instance needs to release too). NOTE: the lock
  // path's authoritative /api/mount/stop is already covered by store.setLocked —
  // do NOT duplicate it here (F-S8 note).
  useEffect(() => {
    if (locked) ctrl.forceStop();
  }, [locked, ctrl]);

  // F-S7: a pad that transitions to parked/disconnected mid-hold must release the
  // slew, mirroring the `locked` effect above. Bounded by the deadman either way,
  // but this stops it immediately on the client and drops any pending keepalive.
  useEffect(() => {
    if (padDisabled) {
      activePointerId.current = null;
      ctrl.forceStop();
    }
  }, [padDisabled, ctrl]);

  const curRate = SLEW_RATES[rateIdx];
  const holdDisabledForRate = curRate.rateDegS <= 0 || isNina;

  const handlers = (axis: Axis, dir: Dir) => ({
    onPointerDown: (e: RPointerEvent) => {
      if (padDisabled) return;
      // F-A4: reject a SECOND concurrent press while a hold is live (or another
      // pointer already owns the pad). A multi-touch second press would overwrite
      // the single-axis controller state and strand the first axis. The first
      // pointer keeps the slew; the second is a no-op.
      if (activePointerId.current != null || ctrl.isHolding()) return;
      activePointerId.current = e.pointerId;
      try {
        (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
      } catch {
        /* capture unsupported — pointerup still fires on the element */
      }
      ctrl.beginPress(axis, dir);
    },
    onPointerUp: (e: RPointerEvent) => {
      if (padDisabled) return;
      // Ignore an up from any pointer that doesn't own the active press.
      if (activePointerId.current !== e.pointerId) return;
      activePointerId.current = null;
      try {
        (e.currentTarget as HTMLElement).releasePointerCapture?.(e.pointerId);
      } catch {
        /* ok */
      }
      ctrl.endPress(axis, dir);
    },
    onPointerCancel: (e: RPointerEvent) => {
      if (activePointerId.current !== e.pointerId) return;
      activePointerId.current = null;
      ctrl.forceStop();
    },
    onLostPointerCapture: (e: RPointerEvent) => {
      if (activePointerId.current !== e.pointerId) return;
      activePointerId.current = null;
      ctrl.forceStop();
    },
    // F-ctxmenu: a desktop right-click during a hold would otherwise pop the
    // context menu and swallow the pointerup → a slew with no release. Suppress it.
    onContextMenu: (e: RMouseEvent) => e.preventDefault(),
    // NO onPointerLeave (R3) — capture keeps the slew bound; up/cancel/lost stop it.
    onKeyDown: (e: RKeyboardEvent) => {
      if (padDisabled) return;
      if (e.key === "Enter" || e.key === " " || e.key === "Spacebar") {
        e.preventDefault();
        ctrl.tapNudge(axis, dir); // keyboard = one pulse at the selected rate (R19)
      }
    },
    tabIndex: padDisabled ? -1 : 0,
    role: "button",
    "aria-label": `slew ${axisLabelText[key(axis, dir)]}`,
    "aria-disabled": padDisabled || undefined,
  });

  const activeKey =
    slewState.mode === "holding" && slewState.axis && slewState.dir
      ? key(slewState.axis, slewState.dir)
      : null;

  const Arrow = ({ axis, dir }: { axis: Axis; dir: Dir }) => {
    const k = key(axis, dir);
    const active = activeKey === k;
    return (
      <button
        {...handlers(axis, dir)}
        className={`tap-lg min-h-[56px] min-w-[56px] btn !p-0 flex flex-col items-center justify-center gap-0.5
          ${active ? "!border-accent !text-accent bg-accent/10" : ""}
          ${padDisabled ? "opacity-40" : ""}`}
      >
        <span className="font-display text-sm leading-none">{cardinal[k]}</span>
        <span className="text-[10px] leading-none text-dim" aria-hidden>
          {arrowGlyph[k]}
        </span>
      </button>
    );
  };

  // ----------------------------------------------------------------- feedback line
  const below = slewState.mode === "belowHorizon";
  const holding = slewState.mode === "holding";
  const glyph = rateGlyph(curRate.id);
  const rateLabel = curRate.id === "pulse" ? "tap to nudge" : `${curRate.label}`;
  // F-B1: the POLITE region carries rate/hold status ONLY. The below-horizon
  // auto-stop is a motion-stop announcement that must NOT queue behind polite
  // speech, so it lives in a SEPARATE assertive region below (the glyph itself is
  // aria-hidden — encoded by shape, read by the assertive text).
  const politeFeedback = holding
    ? `HOLD · ${curRate.rateDegS.toFixed(2)}°/s`
    : below
      ? "" // assertive region owns the below-horizon announcement
      : rateLabel;

  // F-B1: assertive announcements for motion-STOP events (below-horizon auto-stop
  // and the STOP-bar press) — mirrors haptics so a screen-reader user is told the
  // mount halted, never silently. Set imperatively on the STOP press; derived for
  // the below-horizon trip.
  const [stopAnnounce, setStopAnnounce] = useState("");
  const assertiveMsg = below ? "BELOW HORIZON LIMIT — all motion stopped" : stopAnnounce;

  return (
    <div
      className="select-none"
      style={{ touchAction: "none" }} /* R25 — no scroll hijack on a vertical drag */
    >
      {/* parked / no-mount states */}
      {noMount ? (
        <p className="text-sm text-[color:var(--text-dim2,var(--text-dim))] text-center py-6">
          connect a mount to slew
        </p>
      ) : parked ? (
        <div className="flex flex-col items-center gap-3 py-6">
          <p className="text-sm text-[color:var(--text-dim2,var(--text-dim))] text-center">
            mount is parked — unpark to slew
          </p>
          <button
            className="btn tap min-h-[44px]"
            disabled={!canMount}
            onClick={() =>
              api.post("/api/mount/unpark").catch((e) => {
                if ((e as { status?: number })?.status !== 403) showToast("error", (e as Error).message);
              })
            }
          >
            Unpark
          </button>
        </div>
      ) : (
        <>
          {/* viewer read-only note: the pad below renders but every control is
              inert (W2.5 — disabled, never 403-on-tap). */}
          {!canMount && (
            <p className="text-[12px] text-warn text-center mb-2 tracking-wide">
              View only — slewing needs operator or admin access.
            </p>
          )}
          {/* ---- pad grid: N on top, W [rate] E, S on bottom (R-§4.4) ---- */}
          <div
            className="grid grid-cols-3 mx-auto max-w-[260px] place-items-center"
            style={{ gap: "var(--tap-gap, 8px)" }}
          >
            <span />
            <Arrow axis="dec" dir={1} />
            <span />

            <Arrow axis="ra" dir={-1} />
            {/* center cell: rate selector (replaces STOP-in-center, R10) */}
            <div
              className="flex flex-col items-stretch gap-1 w-full"
              role="radiogroup"
              aria-label="Slew rate"
            >
              {SLEW_RATES.map((r, i) => (
                <button
                  key={r.id}
                  role="radio"
                  aria-checked={i === rateIdx}
                  onClick={() => setRateIdx(i)} /* NO haptic on rate change (R26) */
                  className={`tap min-h-[44px] btn !py-1 !px-1 !text-[11px] inline-flex items-center justify-center gap-1
                    ${i === rateIdx ? "!border-accent !text-accent bg-accent/10" : ""}`}
                >
                  <span className="font-display tracking-wide">{r.label}</span>
                  <span aria-hidden className="text-[9px] opacity-80">
                    {rateGlyph(r.id)}
                  </span>
                </button>
              ))}
            </div>
            <Arrow axis="ra" dir={1} />

            <span />
            <Arrow axis="dec" dir={-1} />
            <span />
          </div>

          {/* ---- live feedback line (shape + text, not color — R21) ---- */}
          {/* POLITE region: rate / hold status only (F-B1). */}
          <p
            className={`mt-2 text-center mono text-[14px] ${
              below ? "text-bad alert-pulse" : holding ? "text-accent" : "text-dim"
            }`}
            aria-live="polite"
          >
            {/* below-horizon: glyph + visible label here are aria-hidden; the
                assertive region below speaks the stop so it never queues. */}
            {below ? (
              <span aria-hidden>▣ BELOW HORIZON LIMIT — stopped</span>
            ) : (
              <>
                {politeFeedback}{" "}
                <span aria-hidden>{glyph}</span>
              </>
            )}
          </p>
          {/* ASSERTIVE region: motion-STOP announcements (F-B1) — below-horizon
              auto-stop + STOP-bar press. Visually hidden; SR-only. */}
          <p role="alert" aria-live="assertive" className="sr-only">
            {assertiveMsg}
          </p>
          {holdDisabledForRate && !below && (
            <p className="text-center text-[12px] text-[color:var(--text-dim2,var(--text-dim))] mt-0.5">
              {isNina
                ? "NINA: arrows do fine nudges; use catalog GOTO for big moves"
                : "tap to nudge (hold disabled at this rate)"}
            </p>
          )}

          {/* ---- STOP bar: full-width, solid, ■ motif, 1-tap (R9/R10) ---- */}
          <button
            disabled={!canMount}
            onClick={() => {
              if (!canMount) return; // viewer: nothing to stop, control is read-only
              haptics.stop();
              ctrl.forceStop();
              activePointerId.current = null;
              // F-B1: assertive announce, mirroring haptics.stop(). Re-arm each
              // press (clear then set) so a second STOP still re-announces.
              setStopAnnounce("");
              requestAnimationFrame(() => setStopAnnounce("All motion stopped"));
              // Best-effort: a viewer can never reach here, but a 403 from any
              // race is swallowed silently (never a toast at a read-only user).
              api.post("/api/mount/stop").catch((e) => {
                if ((e as { status?: number })?.status !== 403) showToast("error", (e as Error).message);
              });
            }}
            aria-label="Stop all mount motion"
            className={`mt-3 w-full min-h-[56px] flex items-center justify-center gap-2
              font-display tracking-[0.2em] text-[14px] text-black/90
              bg-bad border border-bad active:translate-y-px ${!canMount ? "opacity-40" : ""}`}
          >
            <Icon name="stop" size={18} className="!text-black/90" />
            STOP
          </button>

          {/* ---- reverse-axis toggles (R7) ---- */}
          <div className="flex items-center justify-center gap-5 mt-3 flex-wrap">
            <label className="flex items-center gap-2 text-xs">
              <Toggle
                checked={touch.reverseRa}
                onChange={(v) => setTouch({ reverseRa: v })}
                label="Reverse RA direction"
              />
              <span className="text-[color:var(--text-dim2,var(--text-dim))]">reverse RA</span>
            </label>
            <label className="flex items-center gap-2 text-xs">
              <Toggle
                checked={touch.reverseDec}
                onChange={(v) => setTouch({ reverseDec: v })}
                label="Reverse Dec direction"
              />
              <span className="text-[color:var(--text-dim2,var(--text-dim))]">reverse Dec</span>
            </label>
          </div>
          <p className="text-center text-[12px] text-[color:var(--text-dim2,var(--text-dim))] mt-1.5 max-w-[260px] mx-auto">
            moves wrong way? toggle reverse — direction depends on pier side &amp; image
            orientation
          </p>
          {m && m.alt < MIN_SLEW_ALT_DEG + 5 && (
            <p className="text-center text-[12px] text-warn mt-1">
              near horizon ({m.alt.toFixed(0)}°) — slew auto-stops below {MIN_SLEW_ALT_DEG}°
            </p>
          )}
        </>
      )}
    </div>
  );
}
