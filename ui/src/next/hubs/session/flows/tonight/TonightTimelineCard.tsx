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

import type { JSX } from "react";

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

/** One legend entry: a coloured swatch AND the words. Shape plus text, never
 *  hue alone - under `:root.night` every token collapses toward the same red. */
function LegendItem({ tone, shape, children }: {
  tone: string;
  shape: "block" | "bar" | "dash";
  children: string;
}): JSX.Element {
  return (
    <span className="nx-tn-legend-item">
      <span
        className="nx-tn-legend-swatch"
        data-shape={shape}
        style={{ background: TL_TONE_VAR[tone] }}
        aria-hidden="true"
      />
      {children}
    </span>
  );
}

export function TonightTimelineCard(props: TonightTimelineProps): JSX.Element {
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
          {g.rects.map((r) => (
            <rect
              key={r.key}
              x={r.x} y={r.y} width={r.w} height={r.h} rx={RECT_R}
              fill={TL_TONE_VAR[r.tone]} opacity={r.opacity}
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
              }}
            >
              {l.text}
            </div>
          ))}
        </div>
      </div>

      <div className="nx-tn-legend">
        <LegendItem tone="accent" shape="block">imaging window + altitude arc</LegendItem>
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
