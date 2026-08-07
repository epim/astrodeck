/* ActivityRing — the app's one in-flight-operation ring (2026-08-07).

   Two modes, distinct by SHAPE and not hue (night mode turns every token red):

     fill  — a ring that closes over `seconds`, for operations whose duration
             is KNOWN (an exposure: the ring closing is the shutter closing).
             `elapsedS` lets a ring that mounts mid-operation start mid-fill
             (a sequence frame already 40 s into 120 s) via a negative
             animation-delay — the CSS clock and the server's agree.
     orbit — a short arc that circles, for operations whose duration is
             UNKNOWABLE (ASTAP, a measurement) — an indeterminate spinner
             that never claims a progress nobody measured.

   The word beneath is the operation's own state and is REQUIRED: under
   prefers-reduced-motion both animations still (index.css) and the word
   carries everything. Deliberately not a live region — callers that narrate
   already have an aria-live chip, and a second region would say it twice.

   Extracted from PolarSolveRing so the goto strip, the AF chip and the
   sequence strip cannot each grow a slightly different ring. The r=16
   circumference (100.53) is PINNED in index.css's solve-ring-fill keyframes;
   change both or neither. */
import type { JSX } from "react";

const R = 16;
const C = 2 * Math.PI * R; // 100.53 — pinned in index.css solve-ring-fill

export default function ActivityRing({ mode, seconds = 1, elapsedS = 0, word, resetKey }: {
  mode: "fill" | "orbit";
  /** fill mode: total seconds the ring takes to close. */
  seconds?: number;
  /** fill mode: seconds already elapsed when this mounted (starts mid-fill). */
  elapsedS?: number;
  /** The state, as the word printed beneath the ring. */
  word: string;
  /** Changing this remounts the SVG so a fill restarts from zero. */
  resetKey?: string | number;
}): JSX.Element {
  return (
    <div className="flex flex-col items-center gap-0.5 pointer-events-none"
      data-activity-ring={mode}>
      <svg
        key={resetKey ?? mode}
        viewBox="0 0 40 40" width={40} height={40} aria-hidden
        className={`-rotate-90 ${mode === "orbit" ? "solve-ring-spin" : ""}`}
      >
        {/* the track: always a full faint circle, so a stilled
            (reduced-motion) or just-started ring reads as an instrument */}
        <circle cx="20" cy="20" r={R} fill="none"
          stroke="var(--line-bright)" strokeWidth={3} />
        {mode === "fill" ? (
          <circle cx="20" cy="20" r={R} fill="none"
            className="solve-ring-fill"
            stroke="var(--accent)" strokeWidth={3} strokeLinecap="butt"
            strokeDasharray={C.toFixed(2)}
            style={{
              strokeDashoffset: C,
              animationDuration: `${Math.max(0.2, seconds)}s`,
              animationDelay: elapsedS > 0 ? `-${elapsedS}s` : undefined,
            }}
          />
        ) : (
          <circle cx="20" cy="20" r={R} fill="none"
            stroke="var(--accent)" strokeWidth={3} strokeLinecap="butt"
            strokeDasharray={`${(C * 0.28).toFixed(2)} ${C.toFixed(2)}`}
          />
        )}
      </svg>
      <span className="text-[10px] tracking-widest uppercase text-dim leading-none">
        {word}
      </span>
    </div>
  );
}
