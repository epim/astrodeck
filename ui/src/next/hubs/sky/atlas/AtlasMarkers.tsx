// AtlasMarkers.tsx - tonight's ranked targets, and the reticle, drawn over the
// atlas canvas.
//
// WHY THE FINDER'S LIST AND NOT THE CANVAS'S OWN. `SkyCanvas` already annotates
// the patch it is showing from `GET /api/catalog/region` - every catalogued
// object in view, glyph by type, with a label budget. That answers "what is
// here". It cannot answer "what is worth pointing at tonight", because nothing
// in that payload knows this site's horizon, this hour's cloud, or how high the
// object gets before dawn. `useSkyModel.targets` is that second list - the same
// ranked, lens-filtered, cloud-and-horizon-decorated set the schematic MAP
// draws - so the atlas is populated by the TARGETS rather than by the
// catalogue, and the two cannot disagree about which is which.
//
// ONE OBJECT, ONE LABEL. A ranked target is drawn here and is filtered out of
// the rows handed to `SkyCanvas` (`SkyHub`'s `atlasRows`), so nothing on this
// canvas carries two names. Everything the ranking does NOT cover is still the
// canvas's own, tap included.
//
// THE PILL IS THE FINDER'S, deliberately: ring, then name and altitude in the
// design's own label pill (`finder/SkyView.tsx`'s markers, screenshot
// 01-sky-finder). The same object has to read the same way in both modes, or
// switching modes becomes a re-learning exercise.
//
// POINTER RULES. The overlay is `pointer-events: none` and each pill turns them
// back on for itself, so dragging the sky still works everywhere between the
// labels, and a tap that lands on a pill never reaches the canvas underneath
// (which is what keeps a target tap out of the aim-anywhere path).

import type { JSX } from "react";
import { NxIcon, type NxIconName } from "../../../icons";
import { skyToBox } from "./aim";
import type { PatchModel, SkyKind, SkyTarget } from "../finder";

/** How far outside the square a marker may sit and still be drawn, CSS px. A
 *  pill whose anchor is just off-frame still has most of its text on screen,
 *  and cutting at the exact edge makes labels pop in and out during a pan. */
const MARGIN_PX = 28;

/** The reticle square's edge, CSS px - the finder's own 46 px lock radius as a
 *  box, so the thing you aim with is the same size in both modes. */
const RETICLE_PX = 46;

export interface AtlasMarkersProps {
  /** The square canvas, measured: its offset inside the positioned wrapper and
   *  its CSS edge. Zero means it has not been measured yet (or is not laid out
   *  at all, as in jsdom) - the markers still render, so the tree is testable,
   *  but nothing pretends to know where they are. */
  box: { left: number; top: number; size: number };
  centre: { ra_hours: number; dec_deg: number };
  fovDeg: number;
  /** Ranked and lens-filtered: `useSkyModel.targets`, exactly as MAP draws. */
  targets: SkyTarget[];
  /** The finder's lock, which gets the ring. Null while the reticle is on a
   *  patch the catalogue is silent about. */
  lockId: string | null;
  kindIcon: Record<SkyKind, NxIconName>;
  /** Where the finder is aimed, in RA/Dec, or null when there is no aim to draw
   *  (no site, or the reticle is below the horizon). */
  aim: PatchModel | null;
  onPick: (t: SkyTarget) => void;
}

export function AtlasMarkers({
  box, centre, fovDeg, targets, lockId, kindIcon, aim, onPick,
}: AtlasMarkersProps): JSX.Element {
  const size = box.size;
  const place = (p: { ra_hours: number; dec_deg: number }): { x: number; y: number } | null => {
    const at = skyToBox(p, centre, fovDeg, size);
    if (at == null) return size > 0 ? null : { x: 0, y: 0 };
    if (at.x < -MARGIN_PX || at.x > size + MARGIN_PX) return null;
    if (at.y < -MARGIN_PX || at.y > size + MARGIN_PX) return null;
    return at;
  };

  const aimAt = aim ? place(aim) : null;

  return (
    <div
      className="nx-atlas-markers"
      style={{ left: box.left, top: box.top, width: size, height: size }}
      data-testid="atlas-markers"
    >
      {aim && aimAt && (
        <div
          className="nx-atlas-reticle"
          data-testid="atlas-reticle"
          style={{
            left: aimAt.x, top: aimAt.y, width: RETICLE_PX, height: RETICLE_PX,
            borderColor: aim.color,
          }}
          aria-hidden
        />
      )}

      {targets.map((t) => {
        const at = place(t);
        if (at == null) return null;
        const locked = t.id === lockId;
        return (
          <button
            key={t.id}
            type="button"
            className="nx-atlas-marker"
            data-atlas-marker={t.id}
            data-atlas-locked={locked ? "true" : undefined}
            // The pill says name and altitude; the accessible name adds what
            // the colour carries for everyone else - clear, clouded, behind a
            // tree - because hue is never allowed to be the only channel.
            aria-label={`${t.name}, altitude ${Math.round(t.altNow)} degrees, ${t.statusTxt}`}
            style={{ left: at.x, top: at.y }}
            onPointerDown={(e) => e.stopPropagation()}
            onClick={() => onPick(t)}
          >
            <span
              className="nx-atlas-marker-ring"
              style={{
                borderColor: t.color,
                boxShadow: `0 0 12px color-mix(in srgb, ${t.color} 40%, transparent)`,
              }}
            />
            <span className="nx-atlas-marker-pill" style={{ color: t.color }}>
              <NxIcon name={kindIcon[t.kind]} size={11} />
              {t.name}
              <span className="nx-atlas-marker-alt">{Math.round(t.altNow)}°</span>
            </span>
          </button>
        );
      })}
    </div>
  );
}

export default AtlasMarkers;
