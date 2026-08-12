// TonightTimeline.tsx — tonight as a picture, drawn from the server's TIMES.
//
// `flows/tonight.py` says it in capitals: "WHAT THIS RETURNS IS TIMES, NOT
// PIXELS". It hands back absolute unix seconds and refuses to decide layout, so
// everything below the axis rule in this file is a RENDERING decision and is
// marked as one. The contract's §G-18 is still open on five of them; none of
// them is closed here. What is NOT a decision: every instant drawn comes from a
// field the server returned, and a field it did not return draws nothing. The
// prototype's fabricated night (a hardcoded 20:00→05:00 axis, five equal target
// blocks, a moon bar that starts at a fixture minute and runs to the edge) is
// deliberately not ported — an operator cannot tell an invented ephemeris from
// a measured one, which is the whole reason this panel exists.
//
// TEXT IS NEVER INSIDE THE SVG. README §7 is emphatic and harness gate 4 exists
// to catch it: SVG text measures fine in a DOM dump and paints nothing. Every
// label lives in an absolutely-positioned HTML layer over the drawing.

import type { JSX } from "react";
import { fmtTime } from "../../lib/visibility";

// ─────────────────────────────────────────────────────────── payload shapes
// The normalised slice of GET /api/flows/{id}/tonight that this tab consumes,
// per contract §E.5. TonightPanel owns the reading; these are the shapes it
// produces. Everything nullable is nullable BECAUSE the server can answer null
// there (an unparseable flats window, a moon that never crosses the horizon
// inside the search span, a target with no coordinates).

export interface TonightWindow {
  start_unix: number;
  end_unix: number;
}

export interface TonightNight {
  dusk_unix: number | null;
  dawn_unix: number | null;
  dark_start_unix: number | null;
  dark_end_unix: number | null;
}

export interface TonightFlats {
  start_unix: number | null;
  end_unix: number | null;
}

export interface TonightMoon {
  illumination: number | null;
  rise_unix: number | null;
  set_unix: number | null;
}

export interface TonightTarget {
  label: string;
  window: TonightWindow | null;
  /** `[t_unix, altitude_deg]` samples, dusk→dawn, from `targets[].curve`. */
  curve: [number, number][];
  meridian_flip_unix: number | null;
}

export interface TonightTimelineProps {
  night: TonightNight;
  flats: TonightFlats | null;
  moon: TonightMoon | null;
  targets: TonightTarget[];
}

// ────────────────────────────────────────────────────────── drawing constants
// All from contract §C.11, which lifted them from the prototype's SVG. They are
// user-space units inside the 1000×150 viewBox, not pixels.

export const TL_W = 1000;
export const TL_H = 150;
const AXIS_Y = 118;
const TICK_Y1 = 114;
const TICK_Y2 = 122;
/** The band that carries twilight, flats and every target block. */
const BAND_Y = 36;
const BAND_H = 70;
const MOON_Y = 22;
const MOON_H = 8;
const DASH_Y1 = 28;
const DASH_Y2 = 118;
const RECT_R = 3;

const OP_TWILIGHT = 0.08;
const OP_FLATS = 0.4;
const OP_MOON = 0.25;
const OP_TARGET = 0.16;

/** Altitude → y across the band. 0°→bottom, 90°→top, matching the plot in
 *  `lib/visibility.ts` (ALT_MAX = 90) so the two altitude charts in this app
 *  read the same way. The prototype drew a fixed decorative quadratic instead;
 *  the server returns the real curve, and §E.5 lists it as consumed here. */
const ALT_MAX = 90;

/** A pure LAYOUT gutter at each end of the axis, as a fraction of the night's
 *  length — the same kind of thing as `geometry.ts`'s FIT_PAD. Without it the
 *  DUSK and DAWN labels, which are centred on their own dashed rules, sit half
 *  off the edge of the label layer. It moves no drawn instant: it only decides
 *  where the picture stops. §G-18(a) is still open on the axis rule itself. */
const TL_PAD_FRAC = 0.03;

export type TlTone = "accent" | "sky" | "faint" | "warn" | "good" | "ink";

/** Tone → the token, never a hex. Inline because these are SVG paint and CSS
 *  `color` values, not Tailwind utilities. */
const TONE_VAR: Record<TlTone, string> = {
  accent: "var(--accent)",
  sky: "var(--sky)",
  faint: "var(--text-faint)",
  warn: "var(--warn)",
  good: "var(--good)",
  ink: "var(--text)",
};

export interface TlRect {
  key: string;
  x: number; y: number; w: number; h: number;
  tone: TlTone;
  opacity: number;
}
export interface TlDash { key: string; x: number; tone: TlTone }
export interface TlPath { key: string; d: string }
export interface TlLabel {
  key: string;
  leftPct: number;
  topPct: number;
  text: string;
  tone: TlTone;
}

export interface TlGeometry {
  /** Axis bounds in unix seconds — exported so a test can pin the rule. */
  t0: number;
  t1: number;
  rects: TlRect[];
  /** Hour-tick x positions (the short marks straddling the axis rule). */
  hourTicks: number[];
  dashes: TlDash[];
  paths: TlPath[];
  labels: TlLabel[];
}

const isNum = (v: number | null | undefined): v is number =>
  typeof v === "number" && Number.isFinite(v);

/** The whole timeline as data, so the rule that places each element can be
 *  tested without a DOM. Returns null when the server gave no usable
 *  dusk→dawn span — which is a real answer, not an error, and the component
 *  says so rather than drawing an axis nobody measured. */
export function timelineGeometry(p: TonightTimelineProps): TlGeometry | null {
  const { night, flats, moon, targets } = p;
  if (!isNum(night.dusk_unix) || !isNum(night.dawn_unix)) return null;
  if (night.dawn_unix <= night.dusk_unix) return null;

  const pad = (night.dawn_unix - night.dusk_unix) * TL_PAD_FRAC;
  const t0 = night.dusk_unix - pad;
  const t1 = night.dawn_unix + pad;
  const span = t1 - t0;

  const X = (t: number) => ((t - t0) / span) * TL_W;
  const clampX = (x: number) => Math.max(0, Math.min(TL_W, x));

  const rects: TlRect[] = [];
  const dashes: TlDash[] = [];
  const paths: TlPath[] = [];
  const labels: TlLabel[] = [];
  const hourTicks: number[] = [];

  /** A band, plus its centred ink label when one was asked for. Drops anything
   *  that clamps to nothing — a zero-width block is a lie about a zero-length
   *  interval. */
  const block = (
    key: string, from: number, to: number, y: number, h: number,
    tone: TlTone, opacity: number, label?: string,
  ) => {
    const x = clampX(X(from));
    const w = clampX(X(to)) - x;
    if (!(w > 0)) return;
    rects.push({ key, x, y, w, h, tone, opacity });
    if (label) {
      labels.push({
        key: `${key}-label`,
        leftPct: ((x + w / 2) / TL_W) * 100,
        topPct: ((y + h / 2) / TL_H) * 100,
        text: label,
        tone: "ink",
      });
    }
  };

  const dash = (key: string, t: number, tone: TlTone, label: string) => {
    if (!isNum(t) || t < t0 || t > t1) return;
    const x = clampX(X(t));
    dashes.push({ key, x, tone });
    labels.push({
      key: `${key}-label`,
      leftPct: (x / TL_W) * 100,
      // 6% — the prototype's dashed-tick label row, above the band.
      topPct: 6,
      text: label,
      tone,
    });
  };

  // ── hour ticks: every whole local hour inside the span. The prototype
  //    stepped 60 min from a hardcoded 20:00; whole hours off the operator's
  //    own clock is the same idea against real times.
  const firstHour = new Date(t0 * 1000);
  firstHour.setMinutes(0, 0, 0);
  let hour = firstHour.getTime() / 1000;
  if (hour < t0) hour += 3600;
  // A night is under 24 h everywhere the sun sets; the cap is a runaway guard,
  // not a rule about the sky.
  for (let i = 0; hour <= t1 && i < 48; i++, hour += 3600) {
    const x = clampX(X(hour));
    hourTicks.push(x);
    labels.push({
      key: `hour-${hour}`,
      leftPct: (x / TL_W) * 100,
      topPct: 88,
      text: fmtTime(hour),
      tone: "faint",
    });
  }

  // ── twilight, both ends. The server returns dusk (sun at the autorun
  //    twilight angle), astronomical dark start/end, and dawn; the two twilight
  //    bands are exactly the gaps between them. The prototype ran the left band
  //    off the edge of its invented axis and started the right one at dawn,
  //    which left `dark_end_unix` — returned, and the honest right-hand
  //    boundary — drawn nowhere (§E.5).
  if (isNum(night.dark_start_unix)) {
    block("tw-l", night.dusk_unix, night.dark_start_unix,
          BAND_Y, BAND_H, "sky", OP_TWILIGHT);
  }
  if (isNum(night.dark_end_unix)) {
    block("tw-r", night.dark_end_unix, night.dawn_unix,
          BAND_Y, BAND_H, "sky", OP_TWILIGHT);
  }

  // ── the moon-up bar. §G-18(b): `visibility._moon_rise_set` only searches
  //    inside [dark_start, dark_end], so a moon already up at dark start comes
  //    back with BOTH crossings null while the story says "up all night" —
  //    indistinguishable, here, from a moon that never rose. Drawing the bar
  //    full width in that case would assert a moon-up night nobody measured, so
  //    with neither crossing known nothing is drawn. One crossing IS an answer:
  //    a known rise with no set means it did not set before the span ended.
  if (moon && (isNum(moon.rise_unix) || isNum(moon.set_unix))) {
    block("moon", isNum(moon.rise_unix) ? moon.rise_unix : t0,
          isNum(moon.set_unix) ? moon.set_unix : t1,
          MOON_Y, MOON_H, "faint", OP_MOON);
  }

  // ── the dusk-flats window. Null start/end means the node's window text did
  //    not name two sun altitudes; the story row says so in words, and here it
  //    simply draws nothing.
  if (flats && isNum(flats.start_unix) && isNum(flats.end_unix)) {
    block("flats", flats.start_unix, flats.end_unix,
          BAND_Y, BAND_H, "sky", OP_FLATS, "FLATS");
  }

  // ── one block per target that actually gets a window, at its own times.
  //    A target the server could not place (`resolved: false`, empty curve) has
  //    no window and therefore no block — it is listed in the story instead.
  targets.forEach((t, i) => {
    if (!t.window) return;
    const { start_unix: s, end_unix: e } = t.window;
    if (!isNum(s) || !isNum(e)) return;
    block(`tgt-${i}`, s, e, BAND_Y, BAND_H, "accent", OP_TARGET, t.label);

    // The altitude arc, from the real curve clipped to the window the block
    // draws — the legend ties the two together as one thing.
    const pts = t.curve
      .filter(([ts]) => ts >= s && ts <= e)
      .map(([ts, alt]) => {
        const y = BAND_Y + (1 - Math.max(0, Math.min(ALT_MAX, alt)) / ALT_MAX) * BAND_H;
        return `${clampX(X(ts)).toFixed(1)} ${y.toFixed(1)}`;
      });
    if (pts.length >= 2) paths.push({ key: `arc-${i}`, d: `M${pts.join("L")}` });
  });

  // ── the dashed instants. §G-18(c) is open on whether DUSK means
  //    `dusk_unix` or `window_start_unix` (dusk plus the node's offset); the
  //    tick is placed on the field that carries the same name, and nothing is
  //    drawn for the other one rather than guessing which the label meant.
  dash("dusk", night.dusk_unix, "accent", "DUSK");
  if (isNum(night.dark_start_unix)) {
    dash("dark", night.dark_start_unix, "faint", "DARK");
  }
  if (moon && isNum(moon.rise_unix)) {
    const pct = isNum(moon.illumination)
      ? ` ${Math.round(moon.illumination * 100)}%` : "";
    dash("moonrise", moon.rise_unix, "faint", `☾${pct}`);
  }
  targets.forEach((t, i) => {
    // Only for a target that gets a window, exactly as the story does: a pool
    // candidate that never places has a meridian crossing and no night in which
    // to cross it.
    if (t.window && isNum(t.meridian_flip_unix)) {
      dash(`flip-${i}`, t.meridian_flip_unix, "warn", "FLIP");
    }
  });
  dash("dawn", night.dawn_unix, "good", "DAWN");

  return { t0, t1, rects, hourTicks, dashes, paths, labels };
}

/** The moon's illuminated fraction as a whole percent, or null. The design's
 *  legend reads "moon up (71%)"; 71 is a fixture and `moon.illumination` is the
 *  real source (§E.5). With no moon block in the payload the parenthetical is
 *  simply absent — never a stand-in number. */
export function moonPercent(moon: TonightMoon | null): number | null {
  if (!moon || typeof moon.illumination !== "number") return null;
  if (!Number.isFinite(moon.illumination)) return null;
  return Math.round(moon.illumination * 100);
}

function LegendSwatch({ glyph, tone }: { glyph: string; tone: TlTone }) {
  return <span style={{ color: TONE_VAR[tone] }}>{glyph}</span>;
}

export function TonightTimeline(props: TonightTimelineProps): JSX.Element {
  const g = timelineGeometry(props);

  if (!g) {
    // A degraded state the design does not cover (§G-18(d)). Saying which two
    // instants are missing beats an empty frame that reads as "no data yet".
    return (
      <p className="text-[12px] leading-[1.5] text-dim [text-wrap:pretty]">
        This night has no dusk and dawn to lay a timeline between. The STORY tab
        carries whatever the server could still say about it.
      </p>
    );
  }

  const pct = moonPercent(props.moon);

  return (
    <>
      <div className="relative">
        {/* aria-hidden: every glyph a reader needs is in the HTML layer below,
            and the STORY tab is the same night in sentences. */}
        <svg
          viewBox={`0 0 ${TL_W} ${TL_H}`}
          width="100%"
          preserveAspectRatio="none"
          className="block"
          aria-hidden
          focusable="false"
        >
          {/* Draw order is the contract's: axis, hour ticks, blocks, arcs,
              dashed verticals. */}
          <line
            x1={0} y1={AXIS_Y} x2={TL_W} y2={AXIS_Y}
            stroke="var(--line-bright)" strokeWidth={1}
          />
          {g.hourTicks.map((x) => (
            <line
              key={`ht-${x}`}
              x1={x} x2={x} y1={TICK_Y1} y2={TICK_Y2}
              stroke="var(--line-bright)" strokeWidth={1}
            />
          ))}
          {g.rects.map((r) => (
            <rect
              key={r.key}
              x={r.x} y={r.y} width={r.w} height={r.h} rx={RECT_R}
              fill={TONE_VAR[r.tone]} opacity={r.opacity}
            />
          ))}
          {g.paths.map((p) => (
            <path
              key={p.key}
              d={p.d}
              fill="none"
              stroke="color-mix(in srgb, var(--accent) 60%, transparent)"
              strokeWidth={1.3}
            />
          ))}
          {g.dashes.map((d) => (
            <line
              key={d.key}
              x1={d.x} x2={d.x} y1={DASH_Y1} y2={DASH_Y2}
              stroke={TONE_VAR[d.tone]} strokeWidth={1} strokeDasharray="3 3"
            />
          ))}
        </svg>

        <div className="absolute inset-0 pointer-events-none">
          {g.labels.map((l) => (
            <div
              key={l.key}
              className="absolute font-mono text-[9.5px] leading-none whitespace-nowrap"
              style={{
                left: `${l.leftPct}%`,
                top: `${l.topPct}%`,
                transform: "translate(-50%,-50%)",
                color: TONE_VAR[l.tone],
              }}
            >
              {l.text}
            </div>
          ))}
        </div>
      </div>

      <div className="mt-2.5 flex gap-3.5 flex-wrap font-mono text-[9.5px] text-faint">
        <span><LegendSwatch glyph="■" tone="accent" /> imaging window + altitude arc</span>
        <span><LegendSwatch glyph="■" tone="sky" /> twilight / flats</span>
        <span><LegendSwatch glyph="┆" tone="warn" /> meridian flip</span>
        <span>
          <LegendSwatch glyph="▬" tone="faint" /> moon up
          {pct === null ? "" : ` (${pct}%)`}
        </span>
      </div>
    </>
  );
}

export default TonightTimeline;
