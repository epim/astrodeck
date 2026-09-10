// quickNightArc.tsx - HOW LONG, drawn on the target's own path to dawn
// (hub-sky plan D.3, design README section 3, screenshot 05).
//
// ONE CONTROL, NOT TWO. The README lists "HOW LONG: 1 · 2 · 3 · 4 h · until dawn
// as one dial", and the prototype draws the same choice as a handle on the night
// arc. Both are here as one thing: the handle drags, and it SNAPS to the same
// five stops, so a drag and a tap can never disagree about what was chosen.
//
// EVERY LAYER IS A MEASUREMENT, not decoration:
//   the curve      the target's altitude from now to dawn, from /api/visibility
//   the red parts  where that curve is under the horizon YOU drew
//   the amber band forecast cloud inside the window - where the flow will hold
//   the dashed line the 25 degree floor
//   the filled box the window the handle has chosen
//
// WHAT IT WILL NOT DRAW. With no visibility answer there is no curve, and none
// is invented: the chart renders its frame, its floor line and the sentence
// saying the ephemeris has not come back. A plausible arc over a target nobody
// computed is exactly the shape of a screen that lies.

import { useRef, type JSX, type PointerEvent as ReactPointerEvent } from "react";
import type { VisibilityNight } from "../../../../types";

export const ARC_W = 340;
export const ARC_H = 120;
const X0 = 10;
const X1 = 330;
const TOP = 8;
const BASE = 104;

export interface ArcSample {
  /** Hours from now. */
  h: number;
  alt: number;
  /** Under the horizon the site actually has - drawn red, not merely low. */
  below: boolean;
}

export interface ArcCurve {
  id: string;
  name: string;
  color: string;
  samples: ArcSample[];
}

export interface ArcHold {
  /** Hours from now. */
  from: number;
  to: number;
}

export function arcX(hours: number, span: number): number {
  if (!(span > 0)) return X0;
  const f = Math.max(0, Math.min(1, hours / span));
  return X0 + f * (X1 - X0);
}

export function arcY(alt: number): number {
  const a = Math.max(0, Math.min(90, alt));
  return BASE - (a * (BASE - TOP)) / 90;
}

/**
 * The target's arc, from the ephemeris the server already computed.
 *
 * Samples before now are dropped rather than compressed to the left edge: the
 * chart is a picture of the time still available, and an hour that has gone is
 * not a choice anybody can make.
 */
export function curveFromNight(
  night: VisibilityNight | null,
  nowMs: number,
  horizonMinDeg: number,
  id: string,
  name: string,
  color: string,
): ArcCurve | null {
  if (!night || !Array.isArray(night.samples) || night.samples.length === 0) return null;
  const nowS = nowMs / 1000;
  const limit = Number.isFinite(horizonMinDeg) ? horizonMinDeg : 0;
  const samples: ArcSample[] = [];
  for (const s of night.samples) {
    if (s.t_unix < nowS) continue;
    samples.push({ h: (s.t_unix - nowS) / 3600, alt: s.alt, below: s.alt < limit });
  }
  return samples.length > 1 ? { id, name, color, samples } : null;
}

/** Hours from now to the end of astronomical darkness, or null when the site
 *  has none tonight (a high-latitude summer is a real answer, not a gap). */
export function hoursToDawn(night: VisibilityNight | null, nowMs: number): number | null {
  const end = night?.dark_end_unix;
  if (typeof end !== "number") return null;
  const h = (end * 1000 - nowMs) / 3600_000;
  return h > 0 ? h : null;
}

/** Contiguous runs of `below` samples, as their own polylines: a curve that
 *  simply changed colour mid-segment would blend the two states on the joining
 *  line, and "behind the trees" is the half an operator has to see. */
function maskRuns(curve: ArcCurve, span: number): string[] {
  const out: string[] = [];
  let run: string[] = [];
  for (const s of curve.samples) {
    if (s.below) {
      run.push(`${arcX(s.h, span).toFixed(1)},${arcY(s.alt).toFixed(1)}`);
    } else if (run.length > 1) {
      out.push(run.join(" "));
      run = [];
    } else {
      run = [];
    }
  }
  if (run.length > 1) out.push(run.join(" "));
  return out;
}

export interface NightArcProps {
  curves: ArcCurve[];
  holds: ArcHold[];
  /** Hours the handle has selected. */
  hours: number;
  /** The full width of the chart in hours (now to dawn, or 8 with no dawn). */
  span: number;
  floorDeg: number;
  nowLabel: string;
  dawnLabel: string;
  /** Null while the ephemeris has not answered; the chart then says so. */
  emptyNote: string | null;
  onHours: (hours: number) => void;
  lockedReason?: string | null;
  onExplain?: (reason: string) => void;
}

export function NightArc({
  curves, holds, hours, span, floorDeg, nowLabel, dawnLabel, emptyNote,
  onHours, lockedReason = null, onExplain,
}: NightArcProps): JSX.Element {
  const boxRef = useRef<HTMLDivElement>(null);

  const setFromClientX = (clientX: number): void => {
    const el = boxRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    if (!rect.width) return;
    // The SVG scales to the box, so the pointer has to be converted back into
    // viewBox units before it means anything in hours.
    const vx = ((clientX - rect.left) / rect.width) * ARC_W;
    const f = (vx - X0) / (X1 - X0);
    onHours(Math.max(0, Math.min(1, f)) * span);
  };

  const down = (e: ReactPointerEvent<HTMLDivElement>): void => {
    if (e.button !== 0) return;
    if (lockedReason) { onExplain?.(lockedReason); return; }
    setFromClientX(e.clientX);
    const move = (ev: globalThis.PointerEvent): void => setFromClientX(ev.clientX);
    const up = (): void => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    // A gesture the browser claims for scrolling never fires pointerup, so
    // without this the move listener outlives the drag and the handle follows
    // the finger across the rest of the page.
    window.addEventListener("pointercancel", up);
  };

  const handleX = arcX(hours, span);
  const primary = curves[0] ?? null;
  const handleAlt = primary
    ? (primary.samples.reduce<ArcSample | null>(
        (best, s) => (best == null || Math.abs(s.h - hours) < Math.abs(best.h - hours) ? s : best),
        null,
      )?.alt ?? 45)
    : 45;

  return (
    <div
      ref={boxRef}
      data-testid="quick-arc"
      onPointerDown={down}
      style={{
        position: "relative",
        border: "1px solid rgba(0,210,255,.35)",
        borderRadius: 14,
        background: "var(--bg, #06070B)",
        overflow: "hidden",
        touchAction: "pan-y",
        cursor: lockedReason ? "default" : "ew-resize",
        userSelect: "none",
      }}
    >
      <svg
        viewBox={`0 0 ${ARC_W} ${ARC_H}`}
        style={{ width: "100%", height: "auto", display: "block" }}
        role="img"
        aria-label={`Night arc: ${nowLabel} to ${dawnLabel}, window ends at the handle`}
      >
        <rect x={X0} y={TOP} width={Math.max(0, handleX - X0)} height={BASE - TOP}
          fill="rgba(0,210,255,.09)" data-testid="quick-arc-window" />
        {holds.map((h, i) => (
          <rect
            key={`hold-${i}`}
            x={arcX(h.from, span)}
            y={TOP}
            width={Math.max(2, arcX(h.to, span) - arcX(h.from, span))}
            height={BASE - TOP}
            fill="rgba(255,180,84,.14)"
          />
        ))}
        <line x1={X0} y1={arcY(floorDeg)} x2={X1} y2={arcY(floorDeg)}
          stroke="rgba(255,180,84,.5)" strokeDasharray="2 3" />
        <line x1={X0} y1={BASE} x2={X1} y2={BASE} stroke="rgba(140,160,220,.35)" />
        {curves.map((c) => (
          <polyline
            key={c.id}
            data-testid="quick-arc-curve"
            points={c.samples.map((s) => `${arcX(s.h, span).toFixed(1)},${arcY(s.alt).toFixed(1)}`).join(" ")}
            fill="none"
            stroke={c.color}
            strokeWidth={2}
            strokeLinejoin="round"
            opacity={curves.length > 1 ? 0.85 : 1}
          />
        ))}
        {curves.flatMap((c) =>
          maskRuns(c, span).map((pts, i) => (
            <polyline key={`${c.id}-mask-${i}`} points={pts} fill="none"
              stroke="#ff5470" strokeWidth={3.5} strokeLinecap="round" />
          )),
        )}
        <rect x={handleX} y={TOP} width={1.5} height={BASE - TOP} fill="#00D2FF" />
        <circle cx={handleX} cy={arcY(handleAlt)} r={9} fill="#06070B" stroke="#00D2FF" strokeWidth={2} />
        <circle cx={handleX} cy={arcY(handleAlt)} r={3} fill="#00D2FF" />
      </svg>
      <span style={{ position: "absolute", left: 8, top: 6, fontSize: 10, letterSpacing: ".1em" }}
        className="nx-mono">{nowLabel}</span>
      <span style={{ position: "absolute", right: 8, top: 6, fontSize: 10, letterSpacing: ".1em" }}
        className="nx-mono">{dawnLabel}</span>
      {emptyNote != null && (
        <span
          data-testid="quick-arc-empty"
          style={{
            position: "absolute", left: 0, right: 0, top: "44%", textAlign: "center",
            padding: "0 24px", fontSize: 11, lineHeight: 1.4, color: "var(--text-3, #7683a5)",
          }}
        >
          {emptyNote}
        </span>
      )}
    </div>
  );
}
