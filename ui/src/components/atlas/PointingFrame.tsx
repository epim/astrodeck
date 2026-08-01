// PointingFrame — where the telescope is ACTUALLY pointing, drawn on the Atlas.
//
// Pure SVG geometry, like FovOverlay: NO text inside the scaled viewBox (spec
// §6 C3-A2 — every label on this canvas is real CSS px on the HTML layer), and
// every stroke carries the black `.svg-halo` underlay. All the math lives in
// lib/atlasFov.ts; this file only chooses ink.
//
// TWO CLAIMS, BOTH LEGIBLE AT ONCE. FovOverlay draws where you INTEND to point
// (a continuous thin rectangle + centre cross, var(--accent)); this draws where
// the tube IS. They must never be mistaken for each other — and in night mode
// they cannot be told apart by hue at all: --good is #ff3333 against --accent
// #ff3a3a, a 1.0x distinction (see the palette note in index.css: "any status
// that must survive night mode needs a non-hue channel"). So the live footprint
// is separated by SHAPE, which survives the red filter, a mono screen and a
// colour-blind eye:
//   • dashed edges, not continuous;
//   • solid corner brackets — a viewfinder read nothing else on this canvas has;
//   • a reticle (ring + dot) on the optical axis, where the planned box has a
//     plain cross.
//
// The three ways this can be honestly incomplete each get their own drawing,
// because they have different fixes (lib/atlasFov.ts pointingFov):
//   • no outline, no disc, just the reticle  -> position known, frame size isn't
//     (optics unset);
//   • dashed CIRCLE + reticle                -> position known, camera ANGLE
//     isn't (no rotator, or one that was never synced to the sky). The circle is
//     the one the sensor fits inside whatever its angle really is — the honest
//     shape for "somewhere in here", where a rectangle would be a claim nobody
//     measured;
//   • nothing at all                         -> no mount is reporting.
// The sentence that names which of these is happening rides on the HTML layer
// (SkyCanvas), because it has to be readable text, not a shape.

import { memo, type JSX } from "react";
import type { PointingReadout, ViewPoint } from "../../lib/atlasFov";

export interface PointingFrameProps {
  /** viewBox edge length (square). SkyCanvas uses 1000. */
  view: number;
  /** The live readout — pure telemetry, computed by lib/atlasFov.pointingFov. */
  readout: PointingReadout;
}

/** A stroke from `a` a fraction of the way toward `b` — one arm of a corner
 *  bracket. Fraction, not a fixed length, so the bracket stays proportional at
 *  every zoom instead of swallowing a small frame whole. */
function arm(a: ViewPoint, b: ViewPoint, frac: number): string {
  const x = a.x + (b.x - a.x) * frac;
  const y = a.y + (b.y - a.y) * frac;
  return `M ${a.x.toFixed(2)} ${a.y.toFixed(2)} L ${x.toFixed(2)} ${y.toFixed(2)}`;
}

export const PointingFrame = memo(function PointingFrame(
  props: PointingFrameProps,
): JSX.Element | null {
  const { view, readout } = props;
  const { axis, outline, discRPx, moving } = readout;
  if (!axis) return null;

  // Off-canvas is normal — the footprint is glued to the sky, so panning away
  // scrolls it out of view like any other object. Skip the draw once it is well
  // clear: handing the renderer a path millions of units wide is pointless work,
  // not a picture.
  //
  // This is an OPTIMISATION and nothing more. It used to be written as the
  // safety net for a scope on the far side of the sky, on the assumption that
  // such a pointing "projects near the projection's blow-up" — false. The
  // gnomonic blows up at 90° and then FOLDS BACK, so the far hemisphere lands
  // inside the canvas, mirrored, with the antipode exactly at the centre; a
  // screen-distance cull can never catch that. The real guard is angular and
  // lives in lib/atlasFov (TAN_HORIZON_DEG), which hands us a null axis for
  // anything past the horizon. What survives to here is the near-90° case:
  // correctly-directed coordinates that are merely enormous.
  const stray = Math.max(Math.abs(axis.x - view / 2), Math.abs(axis.y - view / 2));
  if (!Number.isFinite(stray) || stray > view * 1.5) return null;

  const ink = "var(--good)";
  // Moving is a fourth non-hue channel: the reticle breathes only while the
  // hardware itself reports motion. motion-safe so a reduced-motion user gets a
  // still frame rather than an animation they asked not to see.
  const pulse = moving ? "motion-safe:animate-pulse" : "";

  return (
    <g aria-hidden>
      {outline && (
        <g>
          <path
            d={`M ${outline.map((p) => `${p.x.toFixed(2)} ${p.y.toFixed(2)}`).join(" L ")} Z`}
            className="svg-halo"
            fill="none"
            stroke={ink}
            strokeWidth={1.5}
            strokeDasharray="5 6"
            opacity={0.95}
          />
          {/* corner brackets — the shape channel. Each corner gets an arm along
              both of its edges, so the reading survives a 90° rotation and a
              frame drawn on top of the planned box. */}
          <path
            d={outline
              .map((p, i) => {
                const next = outline[(i + 1) % outline.length];
                const prev = outline[(i + outline.length - 1) % outline.length];
                return `${arm(p, next, 0.22)} ${arm(p, prev, 0.22)}`;
              })
              .join(" ")}
            className="svg-halo"
            fill="none"
            stroke={ink}
            strokeWidth={3}
            strokeLinecap="butt"
          />
        </g>
      )}

      {/* angle-unknown disc: the circle the sensor fits inside at ANY angle. */}
      {discRPx != null && (
        <circle
          cx={axis.x}
          cy={axis.y}
          r={discRPx}
          className="svg-halo"
          fill="none"
          stroke={ink}
          strokeWidth={1.5}
          strokeDasharray="7 9"
          opacity={0.9}
        />
      )}

      {/* the optical axis itself — drawn in every state, including the one where
          it is all we know. */}
      <g className={pulse}>
        <circle
          cx={axis.x}
          cy={axis.y}
          r={9}
          className="svg-halo"
          fill="none"
          stroke={ink}
          strokeWidth={2}
        />
        <circle cx={axis.x} cy={axis.y} r={2.5} className="svg-halo" fill={ink} />
      </g>
    </g>
  );
});
