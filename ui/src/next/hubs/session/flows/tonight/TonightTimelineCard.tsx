// TonightTimelineCard.tsx - tonight as a picture, drawn from the server's
// TIMES (parity row A19).
//
// THE RULE THAT PLACES EVERY MARK IS NOT REBUILT. `timelineGeometry`, `TL_W`,
// `TL_H`, `moonPercent` and the `TlRect`/`TlDash`/`TlPath`/`TlLabel` shapes are
// imported from `components/flows/TonightTimeline.tsx`, which exports them: it
// is the rule that decides the axis span, the twilight bands, the moon bar, the
// per-target block and its altitude arc, and a second copy of it in this file
// would be a second night. Only the SVG chrome below is new.
//
// TEXT IS NEVER INSIDE THE SVG. SVG text measures fine in a DOM dump and paints
// nothing under `preserveAspectRatio="none"`, so every label lives in an
// absolutely-positioned HTML layer over the drawing - which is also why the
// `<svg>` is `aria-hidden` and the STORY tab is the accessible reading of the
// same night.

import { useId, type JSX } from "react";

import {
  TL_H, TL_W, moonPercent, timelineGeometry,
  type TonightTimelineProps,
} from "../../../../../components/flows/TonightTimeline";
import { Mono } from "../../../../ui";
import { TL_TONE_VAR } from "./tonightModel";

// Drawing constants, user-space units inside the 1000x150 viewBox. Private to
// the legacy component (the geometry it exports carries the y/height of every
// rect already), so the four that place the axis rule, its ticks and the dashed
// verticals are transcribed here rather than re-derived from the rects.
const AXIS_Y = 118;
const TICK_Y1 = 114;
const TICK_Y2 = 122;
const DASH_Y1 = 28;
const DASH_Y2 = 118;
const RECT_R = 3;
// The mosaic band's hatch (#189 S3), transcribed with the rest: the legacy
// file keeps its drawing constants module-private. `timelineGeometry` marks the
// band's rect `shape: "hatch"`, and that mark is what this chrome draws from.
const HATCH_GAP = 5;
const HATCH_W = 1.4;

/** One legend entry: a coloured swatch AND the words. Shape plus text, never
 *  hue alone - under `:root.night` every token collapses toward the same red.
 *  A "hatch" swatch is drawn inline, diagonal lines in the tone and an outline,
 *  so it reads as the band's own shape whatever the stylesheet says. */
function LegendItem({ tone, shape, children }: {
  tone: string;
  shape: "block" | "bar" | "dash" | "hatch";
  children: string;
}): JSX.Element {
  const paint = TL_TONE_VAR[tone];
  return (
    <span className="nx-tn-legend-item" data-legend={shape === "hatch" ? "mosaic-band" : undefined}>
      <span
        className="nx-tn-legend-swatch"
        data-shape={shape}
        style={shape === "hatch"
          ? {
              background: `repeating-linear-gradient(45deg, ${paint} 0 1px, transparent 1px 3px)`,
              boxShadow: `inset 0 0 0 1px ${paint}`,
            }
          : { background: paint }}
        aria-hidden="true"
      />
      {children}
    </span>
  );
}

export function TonightTimelineCard(props: TonightTimelineProps): JSX.Element {
  // Pattern ids must be unique in the document, and a colon is not safe
  // inside `url(#...)`, so React's id is reduced to its word characters. Not
  // `nx-` prefixed: it is an element id, and the area's `nx-*` names are its
  // classes, each owed a rule in tonight.css (r7Css.test.ts).
  const hatchId = `tonight-hatch-${useId().replace(/[^A-Za-z0-9_-]/g, "")}`;
  const g = timelineGeometry(props);

  if (!g) {
    // A real answer, not an error: the server gave no usable dusk-to-dawn span,
    // and an empty frame would read as "no data yet". Saying WHICH two instants
    // are missing is what sends the operator to the site settings.
    return (
      <p className="nx-tn-note" data-testid="tonight-timeline">
        This night has no dusk and dawn to lay a timeline between. The STORY tab
        carries whatever the server could still say about it.
      </p>
    );
  }

  const pct = moonPercent(props.moon);
  const hatched: string[] = [];
  for (const r of g.rects) {
    if (r.shape === "hatch" && !hatched.includes(r.tone)) hatched.push(r.tone);
  }

  return (
    <div className="nx-tn-stack" data-testid="tonight-timeline">
      <div className="nx-tn-tl">
        <svg
          viewBox={`0 0 ${TL_W} ${TL_H}`}
          width="100%"
          preserveAspectRatio="none"
          className="nx-tn-tl-svg"
          aria-hidden="true"
          focusable="false"
        >
          {/* The mosaic band's hatch, one pattern per tone a band uses (a
              pattern's stroke resolves where it is defined, not where it is
              used), and none at all on a night with no mosaic. */}
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
                    stroke={TL_TONE_VAR[tone]} strokeWidth={HATCH_W}
                  />
                </pattern>
              ))}
            </defs>
          )}
          {/* Draw order: axis, hour ticks, blocks, altitude arcs, dashed
              verticals - later marks have to read over earlier ones. */}
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
            // A mosaic's band: hatched and outlined in its block's own tone,
            // told apart from the flat block by shape. The outline keeps a
            // band a couple of degrees deep visible; non-scaling, so the
            // stretched viewBox cannot thicken it.
            <rect
              key={r.key}
              data-shape="hatch"
              x={r.x} y={r.y} width={r.w} height={r.h}
              fill={`url(#${hatchId}-${r.tone})`}
              stroke={TL_TONE_VAR[r.tone]} strokeWidth={1}
              vectorEffect="non-scaling-stroke" opacity={r.opacity}
            />
          ) : (
            <rect
              key={r.key}
              x={r.x} y={r.y} width={r.w} height={r.h} rx={RECT_R}
              fill={TL_TONE_VAR[r.tone]} opacity={r.opacity}
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
              stroke={TL_TONE_VAR[d.tone]} strokeWidth={1} strokeDasharray="3 3"
            />
          ))}
        </svg>

        <div className="nx-tn-tl-labels">
          {g.labels.map((l) => (
            <div
              key={l.key}
              className="nx-tn-tl-label"
              style={{
                left: `${l.leftPct}%`,
                top: `${l.topPct}%`,
                color: TL_TONE_VAR[l.tone],
                // A band's label ends at the band's right end, inside the
                // block it names; every other label keeps the stylesheet's
                // centring, so its style is what it always was.
                ...(l.anchor === "end" ? { transform: "translate(-100%, -50%)" } : {}),
              }}
            >
              {l.text}
            </div>
          ))}
        </div>
      </div>

      <div className="nx-tn-legend">
        <LegendItem tone="accent" shape="block">imaging window + altitude arc</LegendItem>
        {hatched.length > 0 && (
          // Only on a night that draws a band: a legend entry for a mark the
          // picture does not have would send the reader looking for it.
          <LegendItem tone="accent" shape="hatch">mosaic panels&apos; peaks, lowest to highest</LegendItem>
        )}
        <LegendItem tone="sky" shape="block">twilight / flats</LegendItem>
        <LegendItem tone="warn" shape="dash">meridian flip</LegendItem>
        <LegendItem tone="faint" shape="bar">
          {pct === null ? "moon up" : `moon up (${pct}%)`}
        </LegendItem>
      </div>

      <Mono size={10} tone="dim">
        {/* The axis is dusk to dawn with a layout gutter at each end; saying so
            stops the first and last hour tick reading as the night's edges. */}
        Axis runs dusk to dawn. A target with no window drew no block - the STORY
        tab says which and why.
      </Mono>
    </div>
  );
}

export default TonightTimelineCard;
