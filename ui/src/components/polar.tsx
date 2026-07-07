/** Polar alignment bullseye reticle (HERO 2 — the TPPA wizard's spatial view).
 *  Center = the true pole; the dot is the mount's axis; the vector is the skew.
 *  Rings are the arcminute TIER thresholds the verdict is built on — 2′ (stop /
 *  excellent) and 10′ (the keep-going boundary) — so the ring you are inside IS
 *  the verdict, read spatially. The vector CONVERGES toward center as the user
 *  turns the knobs: this is one of the two sanctioned motion moments (spec §0.6),
 *  so readings are eased between (a real tween of successive server readings, NOT
 *  a canned demo loop) and the whole thing snaps to the final state under
 *  prefers-reduced-motion.
 *
 *  Everything is token-driven (no hardcoded hex) so it redshifts in .night and
 *  lightens on the light ground with no component change. */
import { useEffect, useRef, useState } from "react";

/* Knob-direction hint. The native engine emits an authoritative knob label
   (astro-tppa error_det → knob_label): which way to physically turn each bolt.
   NINA/sim don't, so we fall back to the signed error's sense. Exported so the
   Align view's stat row and this reticle decode identically (one source). */
export type KnobDir =
  | "up" | "down" | "left_west" | "left_east" | "right_west" | "right_east";

export interface Hint {
  arrow: string; // ◀ ▶ ▲ ▼
  text: string; // "turn W" | "raise" | …
}

export function knobHint(
  dir: KnobDir | null | undefined,
  signedArcmin: number,
  axis: "az" | "alt",
): Hint | null {
  if (axis === "alt") {
    // authoritative native label, else the signed error's sense
    if (dir === "up") return { arrow: "▲", text: "raise" };
    if (dir === "down") return { arrow: "▼", text: "lower" };
    if (dir === "left_west" || dir === "left_east" || dir === "right_west" || dir === "right_east")
      return null; // wrong-axis label; ignore
    if (!Number.isFinite(signedArcmin) || signedArcmin === 0) return null;
    return signedArcmin > 0 ? { arrow: "▼", text: "lower" } : { arrow: "▲", text: "raise" };
  }
  // azimuth
  if (dir === "left_west") return { arrow: "◀", text: "turn W" };
  if (dir === "left_east") return { arrow: "◀", text: "turn E" };
  if (dir === "right_west") return { arrow: "▶", text: "turn W" };
  if (dir === "right_east") return { arrow: "▶", text: "turn E" };
  if (dir === "up" || dir === "down") return null; // wrong-axis label
  if (!Number.isFinite(signedArcmin) || signedArcmin === 0) return null;
  return signedArcmin < 0
    ? { arrow: "◀", text: "turn E" }
    : { arrow: "▶", text: "turn W" };
}

/* Verdict tier from total error (spec §HERO2): <2′ excellent · 2–10′ good ·
   >10′ keep going. Drives the reticle ZONE hue (tokened good/warn/bad) and the
   panel's verdict text; the error vector/dot/hints stay --accent (the "guide
   star") so the mark survives night mode where the hue collapses. Exported so
   the panel verdict and the reticle zones agree on the tier. */
export type PolarTier = "excellent" | "good" | "keepgoing";
export function polarTier(total: number): PolarTier {
  return total < 2 ? "excellent" : total < 10 ? "good" : "keepgoing";
}

// Zoom-out ladder: the 2′/10′ tier rings stay fixed until the error exceeds the
// 10′ boundary, then the boundary steps out to a nice round arcmin so a huge
// error (e.g. 45′) still reads on-screen with the tier rings nested inside.
const _CEIL_LADDER = [10, 15, 20, 30, 50, 75, 100, 150, 200, 300];
function boundaryArcmin(total: number): number {
  const want = total * 1.08;
  for (const s of _CEIL_LADDER) if (s >= want) return s;
  return _CEIL_LADDER[_CEIL_LADDER.length - 1];
}

/** prefers-reduced-motion, live. Under reduce we snap to final readings (spec
 *  §0.6: show the final state, no convergence animation). */
function usePrefersReducedMotion(): boolean {
  const [reduce, setReduce] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduce(mq.matches);
    const on = () => setReduce(mq.matches);
    mq.addEventListener?.("change", on);
    return () => mq.removeEventListener?.("change", on);
  }, []);
  return reduce;
}

/** Ease the (az, alt) point from the last reading to the new one over ~600ms so
 *  the vector visibly CONVERGES between successive plate-solve updates instead
 *  of teleporting. This is a tween of REAL readings, not a scripted loop: each
 *  new prop pair starts a fresh ease from wherever the dot currently sits. Snaps
 *  instantly when animation is off (reduced motion / no rAF). */
function useEasedPoint(az: number, alt: number, animate: boolean): [number, number] {
  const [pt, setPt] = useState<[number, number]>([az, alt]);
  const cur = useRef<[number, number]>([az, alt]);
  const raf = useRef<number | null>(null);
  useEffect(() => {
    if (!animate || typeof requestAnimationFrame === "undefined") {
      cur.current = [az, alt];
      setPt([az, alt]);
      return;
    }
    const from = cur.current;
    const to: [number, number] = [az, alt];
    const t0 = (typeof performance !== "undefined" ? performance.now() : Date.now());
    const dur = 600;
    const tick = (now: number) => {
      const raw = Math.min(1, (now - t0) / dur);
      const e = 1 - Math.pow(1 - raw, 3); // easeOutCubic — quick then settling
      const p: [number, number] = [
        from[0] + (to[0] - from[0]) * e,
        from[1] + (to[1] - from[1]) * e,
      ];
      cur.current = p;
      setPt(p);
      if (raw < 1) raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => {
      if (raf.current != null) cancelAnimationFrame(raf.current);
    };
  }, [az, alt, animate]);
  return pt;
}

export function PolarReticle({
  az,
  alt,
  azDir,
  altDir,
  active = true,
}: {
  az: number;
  alt: number;
  azDir?: KnobDir | null;
  altDir?: KnobDir | null;
  /** false while idle/measuring (no fitted error yet): draw the empty target. */
  active?: boolean;
}) {
  const reduce = usePrefersReducedMotion();
  const [eAz, eAlt] = useEasedPoint(az, alt, active && !reduce);

  const size = 380, cx = size / 2, cy = size / 2, R = 165;
  const total = Math.hypot(eAz, eAlt);

  // Fixed 2′/10′ tier scale; only zoom OUT past 10′ so the tier rings keep their
  // meaning (a shrinking ring would make "2′" a moving target).
  const smax = boundaryArcmin(total);
  const k = R / smax;

  // clamp the dot to the boundary so a large error still points the right way
  const rawD = Math.hypot(eAz * k, eAlt * k);
  const scale = rawD > R ? R / rawD : 1;
  const dx = cx + eAz * k * scale;
  const dy = cy - eAlt * k * scale;

  // rings: the two tier thresholds, plus the zoom boundary when it exceeds 10′.
  const rings: { r: number; boundary: boolean }[] = [
    { r: 2, boundary: smax === 2 },
    { r: 10, boundary: smax === 10 },
  ];
  if (smax > 10) rings.push({ r: smax, boundary: true });

  // Halo so labels stay legible over rings/vector in day AND night (paint a --bg
  // stroke UNDER the fill).
  const labelHalo = {
    paintOrder: "stroke" as const,
    stroke: "var(--bg)",
    strokeWidth: 3,
    strokeLinejoin: "round" as const,
  };

  const azHint = active ? knobHint(azDir, az, "az") : null;
  const altHint = active ? knobHint(altDir, alt, "alt") : null;

  // vector/dot glide: the eased point already moves smoothly; a short CSS
  // transition on the dot's radius keeps the settle from snapping.
  const dotTrans = reduce ? undefined : "r 200ms ease-out";

  return (
    <svg viewBox={`0 0 ${size} ${size}`} className="w-full mx-auto block instr-fit"
      style={{ aspectRatio: "1 / 1", maxWidth: "338px" }}
      role="img"
      aria-label={
        active
          ? `Polar error ${total.toFixed(1)} arcminutes${azHint ? `, azimuth ${azHint.text}` : ""}${altHint ? `, altitude ${altHint.text}` : ""}`
          : "Polar alignment reticle — no measurement yet"
      }>
      <defs>
        <marker id="pa-arrow" markerWidth="7" markerHeight="7" refX="5" refY="3" orient="auto">
          <path d="M0,0 L6,3 L0,6 Z" fill="var(--accent)" />
        </marker>
      </defs>

      {/* tier zones — bad annulus (outside 10′), good disk (inside 2′). Clamped
          to the boundary so they never spill past the outer ring. Subtle. */}
      <circle cx={cx} cy={cy} r={R} fill="var(--bad)" fillOpacity={0.05} />
      <circle cx={cx} cy={cy} r={Math.min(10, smax) * k} fill="var(--warn)" fillOpacity={0.05} />
      <circle cx={cx} cy={cy} r={Math.min(2, smax) * k} fill="var(--good)" fillOpacity={0.1} />

      {/* tier rings (2′ accent-target, 10′ boundary, + zoom boundary if any) */}
      {rings.map(({ r, boundary }) => (
        <circle key={r} cx={cx} cy={cy} r={r * k} fill="none"
          stroke={r === 2 ? "var(--accent)" : "var(--line-bright)"}
          strokeWidth={boundary ? 1.2 : r === 2 ? 1.2 : 0.8}
          strokeOpacity={r === 2 ? 0.7 : 1}
          strokeDasharray={boundary ? "" : "2 5"} />
      ))}

      {/* crosshair */}
      <line x1={cx - R} y1={cy} x2={cx + R} y2={cy} stroke="var(--line)" />
      <line x1={cx} y1={cy - R} x2={cx} y2={cy + R} stroke="var(--line)" />

      {/* tier labels along the top vertical, echoing the verdict tiers */}
      <text x={cx + 6} y={cy - 2 * k - 4} fill="var(--accent)" fontSize={11}
        fontFamily="IBM Plex Mono" style={labelHalo}>2′ stop</text>
      <text x={cx + 6} y={cy - 10 * k + 13} fill="var(--text)" fontSize={11}
        fontFamily="IBM Plex Mono" style={labelHalo}>10′ keep going</text>
      {smax > 10 && (
        <text x={cx + 6} y={cy - smax * k + 13} fill="var(--text-dim)" fontSize={10}
          fontFamily="IBM Plex Mono" style={labelHalo}>{smax}′</text>
      )}

      {/* orientation axis labels (kept minimal so the knob hints stand out) */}
      <text x={cx - R + 2} y={cy - 5} fill="var(--text-dim)" fontSize={10} fontFamily="IBM Plex Mono"
        letterSpacing="2" textAnchor="start" style={labelHalo}>AZ E</text>
      <text x={cx} y={cy + R - 1} fill="var(--text-dim)" fontSize={10} fontFamily="IBM Plex Mono"
        letterSpacing="2" textAnchor="middle" style={labelHalo}>ALT −</text>

      {/* knob-direction hints — the actionable "which way" cue, from the native
          engine's knob labels (fallback: the error's sign). Top = altitude bolt,
          right = azimuth bolt, matching the design reference. */}
      {altHint && (
        <text x={cx} y={cy - R + 1} fill="var(--accent)" fontSize={11} fontFamily="IBM Plex Mono"
          fontWeight={600} textAnchor="middle" style={labelHalo}>
          {altHint.arrow} ALT {altHint.text}
        </text>
      )}
      {azHint && (
        <text x={cx + R - 2} y={cy + 13} fill="var(--accent)" fontSize={11} fontFamily="IBM Plex Mono"
          fontWeight={600} textAnchor="end" style={labelHalo}>
          {azHint.arrow} AZ {azHint.text}
        </text>
      )}

      {/* skew vector + error dot (converges toward center as the user adjusts) */}
      {active && total > 0.02 && (
        <>
          <line x1={cx} y1={cy} x2={dx} y2={dy} stroke="var(--accent)" strokeWidth={2} markerEnd="url(#pa-arrow)" filter="drop-shadow(0 0 4px var(--glow))" />
          <circle cx={dx} cy={dy} r={7} fill="var(--bg)" stroke="var(--accent)" strokeWidth={2} filter="drop-shadow(0 0 4px var(--glow))"
            style={{ transition: dotTrans }} />
          <circle cx={dx} cy={dy} r={2.5} fill="var(--accent)" style={{ transition: dotTrans }} />
        </>
      )}

      {/* true-pole target */}
      <circle cx={cx} cy={cy} r={3} fill="none" stroke="var(--good)" strokeWidth={1.2} />
      <circle cx={cx} cy={cy} r={1} fill="var(--good)" />
    </svg>
  );
}
