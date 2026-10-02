// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
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

import { useId, useMemo, type JSX } from "react";
import { fmtTime } from "../../lib/visibility";
import { mosaicBand, type TonightMosaicBand } from "../../lib/flowsApi";
import { useStore } from "../../store";

// ─────────────────────────────────────────────────────────── payload shapes
// The normalised slice of GET /api/flows/{id}/tonight that this tab consumes,
// per contract §E.5. TonightPanel owns the reading; these are the shapes it
// produces, all but a mosaic's band, which its reader does not carry and this
// file reads itself (`withMosaicBands`). Everything nullable is nullable
// BECAUSE the server can answer null there (an unparseable flats window, a
// moon that never crosses the horizon inside the search span, a target with
// no coordinates).

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
  /** A mosaic block's peak-altitude spread, its worst and best panel
   *  (`targets[].mosaic.band`, read by `flowsApi.mosaicBand`). Absent on a
   *  single target's row and a pool member's, which is what keeps their
   *  drawing byte for byte what it was before mosaics (#189 S3). */
  band?: TonightMosaicBand | null;
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
/** The mosaic band's hatch. Its lines are the block's own tone at near full
 *  strength: the band is told apart from the block by its SHAPE (hatched and
 *  outlined, against a flat fill), because under the night palette every
 *  token collapses toward one red (spec 2.3). */
const OP_BAND = 0.85;

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
  /** "hatch" for a mosaic's band: diagonal hatch plus an outline, in the same
   *  tone as its block. Absent for every other rect, which is a flat fill. */
  shape?: "hatch";
}
export interface TlDash { key: string; x: number; tone: TlTone }
export interface TlPath { key: string; d: string }
export interface TlLabel {
  key: string;
  leftPct: number;
  topPct: number;
  text: string;
  tone: TlTone;
  /** "end": the label's right edge sits on `leftPct`, so it reads inside the
   *  block it names (a band's label, on the band's right end). Absent: centred
   *  on the point, as every other label is. */
  anchor?: "end";
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

/** Altitude in degrees -> y inside the band, 0 at its foot and 90 at its head,
 *  clamped: the one mapping the altitude arc and the mosaic band share, so a
 *  panel peaking at the centre's transit altitude lands on the arc's crest. */
const altY = (alt: number): number =>
  BAND_Y + (1 - Math.max(0, Math.min(ALT_MAX, alt)) / ALT_MAX) * BAND_H;

/** The words on a mosaic band: which panel peaks lowest and which highest,
 *  in the Plan's own "r-c" names, so the operator knows which edge of the grid
 *  to pull in. One name when the two are the same panel (a grid whose answered
 *  panels all peak alike, or only one answered). */
export function bandText(band: TonightMosaicBand): string {
  return band.worst.panel === band.best.panel
    ? band.worst.panel
    : `${band.worst.panel} low, ${band.best.panel} high`;
}

/** The classic panel's targets with each mosaic row's band filled in from the
 *  Tonight answer in the store.
 *
 *  WHY THIS EXISTS. `TonightPanel.tsx` reads the answer with a module-private
 *  reader that builds each target from four fields and drops the rest, so the
 *  `mosaic` key S3 added never reaches this component through its props. The
 *  band is read here instead, off the SAME payload that reader walked
 *  (`flows.tonight`), with the one shared reader (`flowsApi.mosaicBand`). The
 *  rows are matched by place AND by label, because that reader maps the
 *  answer's targets one to one: a list of another length, or a row whose label
 *  is not the target's, is not the answer these props came from, and nothing
 *  is filled. A target that already carries `band` (a reader that learnt the
 *  key) is left as it is, so this becomes a no-op rather than a second opinion
 *  once the panel's reader carries the band itself.
 *
 *  Returns the SAME array when nothing was filled, so a night with no mosaic
 *  draws from exactly the objects it was handed. */
export function withMosaicBands(
  targets: TonightTarget[], rawTargets: unknown,
): TonightTarget[] {
  if (!Array.isArray(rawTargets) || rawTargets.length !== targets.length) return targets;
  let filled = false;
  const out = targets.map((t, i) => {
    if (t.band !== undefined) return t;
    const raw = rawTargets[i];
    if (raw === null || typeof raw !== "object" || Array.isArray(raw)) return t;
    const r = raw as Record<string, unknown>;
    const label = (typeof r.label === "string" && r.label)
      || (typeof r.name === "string" ? r.name : "");
    if (label !== t.label) return t;
    const band = mosaicBand(r.mosaic);
    if (!band) return t;
    filled = true;
    return { ...t, band };
  });
  return filled ? out : targets;
}

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

    // ── a mosaic's band (#189 S3 item 5): a strip across the block's own
    //    window, from its best panel's peak altitude down to its worst's. The
    //    centre's one curve cannot show that one edge of a grid peaks lower
    //    than another; this can. It is drawn from two numbers the server
    //    returned and nothing else: NOT two copies of the centre's curve
    //    shifted by each panel's offset, which would draw altitudes at every
    //    instant that nobody computed (a panel east of the centre also
    //    transits earlier). Same tone as the block, told apart by shape.
    if (t.band) {
      const x = clampX(X(s));
      const w = clampX(X(e)) - x;
      if (w > 0) {
        const top = altY(t.band.best.transit_alt);
        const foot = altY(t.band.worst.transit_alt);
        rects.push({
          key: `band-${i}`, x, y: top, w, h: foot - top,
          tone: "accent", opacity: OP_BAND, shape: "hatch",
        });
        labels.push({
          key: `band-${i}-label`,
          leftPct: ((x + w) / TL_W) * 100,
          topPct: (((top + foot) / 2) / TL_H) * 100,
          text: bandText(t.band),
          tone: "ink",
          anchor: "end",
        });
      }
    }

    // The altitude arc, from the real curve clipped to the window the block
    // draws — the legend ties the two together as one thing.
    const pts = t.curve
      .filter(([ts]) => ts >= s && ts <= e)
      .map(([ts, alt]) => {
        const y = altY(alt);
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

/** The hatch a band is filled with: one diagonal line per tile, in user
 *  units. A RENDERING decision, like every other number below the axis. */
const HATCH_GAP = 5;
const HATCH_W = 1.4;

/** The tones the hatched rects use, in first-seen order: one `<pattern>` each,
 *  because a pattern's stroke is resolved where the pattern is defined, not
 *  where it is used, so a hatch cannot inherit its rect's tone. */
function hatchTones(rects: TlRect[]): TlTone[] {
  const tones: TlTone[] = [];
  for (const r of rects) if (r.shape === "hatch" && !tones.includes(r.tone)) tones.push(r.tone);
  return tones;
}

export function TonightTimeline(props: TonightTimelineProps): JSX.Element {
  // The band's pattern ids must be unique in the document, and a colon is not
  // safe inside `url(#...)`, so React's id is reduced to its word characters.
  const hatchId = `tl-hatch-${useId().replace(/[^A-Za-z0-9_-]/g, "")}`;
  // The mosaic band, off the answer the panel read (see `withMosaicBands`).
  const rawTargets = useStore((s) => s.flows.tonight?.targets);
  const targets = useMemo(
    () => withMosaicBands(props.targets, rawTargets),
    [props.targets, rawTargets],
  );
  const g = timelineGeometry(
    targets === props.targets ? props : { ...props, targets },
  );

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
  const hatched = hatchTones(g.rects);

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
          {/* The mosaic band's hatch, defined only when a band is drawn, so a
              night with no mosaic renders exactly the markup it always did. */}
          {hatched.length > 0 && (
            <defs>
              {hatched.map((tone) => (
                <pattern
                  key={tone}
                  id={`${hatchId}-${tone}`}
                  patternUnits="userSpaceOnUse"
                  width={HATCH_GAP} height={HATCH_GAP}
                  patternTransform="rotate(45)"
                >
                  <line
                    x1={0} y1={0} x2={0} y2={HATCH_GAP}
                    stroke={TONE_VAR[tone]} strokeWidth={HATCH_W}
                  />
                </pattern>
              ))}
            </defs>
          )}
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
          {g.rects.map((r) => (r.shape === "hatch" ? (
            // A mosaic's band: hatched and outlined, in its block's tone. The
            // outline keeps a band a couple of degrees deep visible at all;
            // non-scaling, so the stretched viewBox cannot thicken it.
            <rect
              key={r.key}
              data-shape="hatch"
              x={r.x} y={r.y} width={r.w} height={r.h}
              fill={`url(#${hatchId}-${r.tone})`}
              stroke={TONE_VAR[r.tone]} strokeWidth={1}
              vectorEffect="non-scaling-stroke" opacity={r.opacity}
            />
          ) : (
            <rect
              key={r.key}
              x={r.x} y={r.y} width={r.w} height={r.h} rx={RECT_R}
              fill={TONE_VAR[r.tone]} opacity={r.opacity}
            />
          )))}
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
                transform: l.anchor === "end"
                  ? "translate(-100%,-50%)" : "translate(-50%,-50%)",
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
        {hatched.length > 0 && (
          // Only on a night that draws a band: a legend entry for a mark the
          // picture does not have would send the reader looking for it.
          <span data-legend="mosaic-band">
            <LegendSwatch glyph="▨" tone="accent" /> mosaic panels&apos; peaks, lowest to highest
          </span>
        )}
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
