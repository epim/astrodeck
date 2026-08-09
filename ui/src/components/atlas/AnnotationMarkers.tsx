// AnnotationMarkers — the catalogued sky, drawn as geometry.
//
// Sits inside SkyCanvas's existing <svg viewBox="0 0 1000 1000"> (layer 3),
// AFTER the planned FOV box and BEFORE the live pointing footprint. That order
// is the canvas's existing rule, stated where PointingFrame is mounted: live
// telemetry is drawn last so the truth is never hidden under the intention.
// Catalogue markers are backdrop — they say what is out there, not what the rig
// is doing — so they go under both.
//
// TYPE IS CARRIED BY SHAPE, NOT COLOUR. The app is used in the dark and the
// survey layer under these markers has a red CSS filter on it; the same rule is
// already written into the canvas's own gesture chip ("word + glyph, never
// colour alone"). The shapes are the ones printed charts use, so anyone holding
// one already knows them:
//
//     ellipse      galaxy
//     square       nebula (emission / reflection / planetary / dark / remnant)
//     circle       cluster, and anything else with an extent
//     filled dot   star, sized by magnitude
//     ringed dot   Sun, Moon, planet
//
// No text lives in here: the viewBox is scaled, and text inside it becomes 4px
// on a phone. Labels are real CSS px in SkyCanvas's label layer.

import type { JSX } from "react";
import type { PlacedMarker } from "../../lib/skyMarkers";

export interface AnnotationMarkersProps {
  markers: PlacedMarker[];
  /** id of the object whose card is open, drawn with a selection ring. */
  selectedId?: string | null;
}

function shape(m: PlacedMarker, key: string): JSX.Element {
  const { x, y, r, glyph } = m;
  switch (glyph) {
    case "galaxy":
      // Charts draw a galaxy as an ellipse. We hold no position angle or axis
      // ratio for these objects (the catalog carries one size, not two), so
      // the ellipse is drawn unrotated and modestly flattened: it says
      // "galaxy" without claiming an orientation nobody measured.
      return <ellipse key={key} cx={x} cy={y} rx={r} ry={r * 0.62} />;
    case "nebula":
      return (
        <rect key={key} x={x - r} y={y - r} width={r * 2} height={r * 2} />
      );
    case "star":
      return <circle key={key} cx={x} cy={y} r={r} className="fill-current" />;
    case "body":
      return (
        <g key={key}>
          <circle cx={x} cy={y} r={r} className="fill-current" />
          <circle cx={x} cy={y} r={r + 4} fill="none" />
        </g>
      );
    default:
      return <circle key={key} cx={x} cy={y} r={r} />;
  }
}

export function AnnotationMarkers({
  markers, selectedId = null,
}: AnnotationMarkersProps): JSX.Element | null {
  if (markers.length === 0) return null;
  return (
    <g data-role="annotation-markers" aria-hidden>
      {/* Extended outlines are dashed so they read as "this object's angular
          extent", not as a drawn boundary of anything real — the same visual
          grammar FovOverlay uses for the object-size ellipse. */}
      <g
        className="svg-halo"
        stroke="var(--accent-dim)"
        fill="none"
        strokeWidth={1.4}
        opacity={0.85}
        vectorEffect="non-scaling-stroke"
      >
        {markers
          .filter((m) => m.extended)
          .map((m) => (
            <g key={`ext-${m.row.id}`} strokeDasharray="6 5">
              {shape(m, `s-${m.row.id}`)}
            </g>
          ))}
        {markers
          .filter((m) => !m.extended)
          .map((m) => shape(m, `p-${m.row.id}`))}
      </g>
      {/* Selection ring: which marker the open card is about. Drawn as a ring
          rather than a colour change, for the red-filter reason above. */}
      {selectedId != null &&
        markers
          .filter((m) => m.row.id === selectedId)
          .map((m) => (
            <circle
              key={`sel-${m.row.id}`}
              cx={m.x}
              cy={m.y}
              r={Math.max(m.r + 7, 13)}
              fill="none"
              stroke="var(--accent)"
              strokeWidth={2}
              vectorEffect="non-scaling-stroke"
            />
          ))}
    </g>
  );
}

export default AnnotationMarkers;
