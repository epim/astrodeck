// LensDial.tsx - the radial kind filter behind the funnel button (plan A.6).
//
// It is an OVERLAY, not a `Popover`. A popover anchored to a 44 px button would
// put a 300 px stage against one corner of the finder; the design centres the
// dial over the whole screen because the count in the middle - "11 IN REACH" -
// is the answer the dial exists to give, and it has to be readable at arm's
// length while the phone is pointed at the sky.
//
// Two things every button here carries that a plain toggle would not:
//
//   1. The COUNT is what turning the kind back on would give you, so it is
//      computed with the lens ignored. A hidden kind reading 0 would be a filter
//      that argues for leaving itself off.
//   2. HOLD opens the kind's explanation. Tap and hold are different verbs on
//      the same target, so the press timer is cancelled by a move as well as by
//      a release - otherwise a scroll that starts on a glyph teaches instead of
//      scrolling.
//
// z-index 28 puts it over the hub body and UNDER the sheet layer (30) and the
// toasts (45): opening the tonight list from the centre button must cover the
// dial, and a locked press must be able to say why over the top of it.

import { useEffect, useRef, type JSX } from "react";
import type { SkyKind } from "../finder";
import { NxIcon, type NxIconName } from "../../../icons";
import {
  LENS_FLOOR_CHIP,
  LENS_FOOTER,
  LENS_COUNT_NOUN,
  LENS_HOLD_MS,
  LENS_LEARN,
  LENS_OVERLAY_NOTE,
  LENS_RING_D,
  LENS_STAGE_PX,
  lensSeats,
} from "./lens";

const MONO = "'IBM Plex Mono', ui-monospace, monospace";
const DISPLAY = "'Chakra Petch', system-ui, sans-serif";

export interface LensDialProps {
  kinds: readonly SkyKind[];
  lens: Record<SkyKind, boolean>;
  counts: Record<SkyKind, number>;
  icons: Record<SkyKind, NxIconName>;
  reachCount: number;
  floorOnly: boolean;
  onToggle: (kind: SkyKind, on: boolean) => void;
  onFloorOnly: (v: boolean) => void;
  onLearn: (kind: SkyKind, text: string) => void;
  /** The centre button: closes the dial and opens the ranked list. */
  onTonight: () => void;
  onClose: () => void;
}

export function LensDial({
  kinds,
  lens,
  counts,
  icons,
  reachCount,
  floorOnly,
  onToggle,
  onFloorOnly,
  onLearn,
  onTonight,
  onClose,
}: LensDialProps): JSX.Element {
  const seats = lensSeats(kinds);
  const held = useRef<{ timer: ReturnType<typeof setTimeout>; fired: boolean } | null>(null);

  // A hold timer must never outlive the dial: the dial closes on the tap that
  // opens a brief, and a timer still running then fires setState into a tree
  // that is gone.
  useEffect(() => () => { if (held.current) clearTimeout(held.current.timer); }, []);

  const startHold = (kind: SkyKind) => {
    if (held.current) clearTimeout(held.current.timer);
    const state = { timer: setTimeout(() => { state.fired = true; onLearn(kind, LENS_LEARN[kind]); }, LENS_HOLD_MS), fired: false };
    held.current = state;
  };
  const endHold = (): boolean => {
    const h = held.current;
    held.current = null;
    if (!h) return false;
    clearTimeout(h.timer);
    return h.fired;
  };

  return (
    <div
      data-testid="sky-lens"
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 28,
        background: "rgba(6,7,11,.78)",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 14,
        cursor: "pointer",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{ position: "relative", width: LENS_STAGE_PX, height: LENS_STAGE_PX, cursor: "default" }}
      >
        <div
          aria-hidden="true"
          style={{
            position: "absolute", left: "50%", top: "50%",
            width: LENS_RING_D, height: LENS_RING_D, borderRadius: "50%",
            border: "1px dashed color-mix(in srgb, var(--accent) 30%, transparent)",
            transform: "translate(-50%,-50%)",
          }}
        />

        <button
          type="button"
          data-testid="sky-lens-centre"
          onClick={onTonight}
          style={{
            position: "absolute", left: "50%", top: "50%",
            width: 104, height: 104, borderRadius: "50%",
            transform: "translate(-50%,-50%)",
            border: "1px solid color-mix(in srgb, var(--accent) 50%, transparent)",
            background: "var(--bg-raise)", color: "var(--text)",
            display: "flex", flexDirection: "column",
            alignItems: "center", justifyContent: "center", gap: 2,
            cursor: "pointer", boxShadow: "0 0 30px rgba(0,210,255,.18)",
          }}
        >
          <span style={{ fontFamily: MONO, fontSize: 26, color: "var(--accent)", lineHeight: 1 }}>
            {reachCount}
          </span>
          <span style={{ fontFamily: DISPLAY, fontWeight: 600, fontSize: 10, letterSpacing: ".18em", color: "var(--text-dim)" }}>
            IN REACH
          </span>
          <span style={{ fontFamily: MONO, fontSize: 10, color: "var(--accent)" }}>tonight list ›</span>
        </button>

        {seats.map((seat) => {
          const on = lens[seat.kind] !== false;
          return (
            <button
              key={seat.kind}
              type="button"
              data-lens-kind={seat.kind}
              data-lens-on={on ? "true" : "false"}
              aria-label={`${seat.label}, ${counts[seat.kind] ?? 0} ${LENS_COUNT_NOUN[seat.kind]}`}
              aria-pressed={on}
              onPointerDown={() => startHold(seat.kind)}
              onPointerUp={endHold}
              onPointerLeave={endHold}
              onPointerCancel={endHold}
              onClick={() => { if (endHold()) return; onToggle(seat.kind, !on); }}
              style={{
                position: "absolute", left: seat.x, top: seat.y,
                width: 56, height: 56, borderRadius: "50%",
                border: `1px solid ${on ? "var(--accent)" : "var(--line)"}`,
                background: on ? "color-mix(in srgb, var(--accent) 14%, transparent)" : "var(--bg-raise)",
                color: on ? "var(--accent)" : "var(--text-faint)",
                display: "flex", flexDirection: "column",
                alignItems: "center", justifyContent: "center", gap: 1,
                cursor: "pointer",
                boxShadow: on ? "0 0 16px rgba(0,210,255,.25)" : "none",
              }}
            >
              <NxIcon name={icons[seat.kind]} size={20} />
              <span style={{ fontFamily: MONO, fontSize: 10, lineHeight: 1 }}>{counts[seat.kind] ?? 0}</span>
            </button>
          );
        })}

        {seats.map((seat) => (
          <div
            key={`l-${seat.kind}`}
            aria-hidden="true"
            style={{
              position: "absolute", left: seat.lx, top: seat.ly,
              transform: "translate(-50%,-50%)",
              fontFamily: DISPLAY, fontWeight: 600, fontSize: 10, letterSpacing: ".14em",
              color: lens[seat.kind] !== false ? "var(--accent)" : "var(--text-faint)",
              pointerEvents: "none", whiteSpace: "nowrap",
            }}
          >
            {seat.label}
          </div>
        ))}
      </div>

      <div
        onClick={(e) => e.stopPropagation()}
        style={{ display: "flex", gap: 6, flexWrap: "wrap", justifyContent: "center", cursor: "default", padding: "0 16px" }}
      >
        <button
          type="button"
          data-testid="sky-lens-floor"
          aria-pressed={floorOnly}
          onClick={() => onFloorOnly(!floorOnly)}
          style={{
            height: 36, padding: "0 12px", borderRadius: 999,
            border: `1px solid ${floorOnly ? "var(--warn)" : "var(--line)"}`,
            background: floorOnly ? "color-mix(in srgb, var(--warn) 14%, transparent)" : "transparent",
            color: floorOnly ? "var(--warn)" : "var(--text-faint)",
            fontFamily: MONO, fontSize: 10.5, cursor: "pointer",
          }}
        >
          {LENS_FLOOR_CHIP}
        </button>
        <span style={{ height: 36, display: "flex", alignItems: "center", fontFamily: MONO, fontSize: 10, color: "var(--text-faint)" }}>
          {LENS_OVERLAY_NOTE}
        </span>
      </div>

      <div style={{ fontFamily: MONO, fontSize: 10, color: "var(--text-faint)", padding: "0 16px", textAlign: "center" }}>
        {LENS_FOOTER}
      </div>
    </div>
  );
}
