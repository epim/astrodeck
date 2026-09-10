// PatchCard.tsx - the card that replaces the lock card when nothing catalogued
// is under the reticle (hub-sky plan A.8).
//
// The title is the prototype's "NO CATALOGUE TARGET HERE" and not the README's
// "NOTHING IN THE RETICLE" (plan H.5). The two cannot both be right on one card:
// this card offers IMAGE THIS PATCH, and a button that images what is in the
// reticle contradicts a title saying there is nothing in it. There IS something
// there - a patch of sky with real coordinates - and the only thing that is
// silent about it is the catalogue.
//
// The patch line prints the RA/Dec the button will send, because that is the one
// fact a picture of empty sky cannot carry and the one the FITS header will be
// filed under.

import type { JSX } from "react";
import { Card, honestPress, lockedAttrs, lockedClass } from "../../../ui";
import type { PatchModel } from "../finder";

const MONO = "'IBM Plex Mono', ui-monospace, monospace";
const DISPLAY = "'Chakra Petch', system-ui, sans-serif";

export interface PatchCardProps {
  patch: PatchModel | null;
  reachCount: number;
  targetCount: number;
  onImagePatch: (patch: PatchModel) => void;
  onCoords: () => void;
  imageReason: string | null;
  onExplain: (reason: string) => void;
}

export function PatchCard({
  patch,
  reachCount,
  targetCount,
  onImagePatch,
  onCoords,
  imageReason,
  onExplain,
}: PatchCardProps): JSX.Element {
  // No longitude means no local sidereal time, so the reticle has no RA to
  // report and the button has nothing to send. Saying so beats a button that
  // posts a position derived from a site nobody set.
  const reason = patch == null ? "Set a site first - a patch of sky has no coordinates without one." : imageReason;

  return (
    <Card tone="dashed" className="nx-sky-patch" data-testid="sky-patch">
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 10 }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
            <div style={{ fontFamily: DISPLAY, fontWeight: 600, fontSize: 11, letterSpacing: ".14em", color: "var(--text-dim)" }}>
              NO CATALOGUE TARGET HERE
            </div>
            <div style={{ fontSize: 12, color: "var(--text-faint)", lineHeight: 1.5 }}>
              {reachCount} of {targetCount} targets clear and in reach · tap a label to jump
            </div>
          </div>
          <div style={{ fontFamily: MONO, fontSize: 10, color: patch?.color ?? "var(--text-faint)", textAlign: "right", whiteSpace: "nowrap" }}>
            {patch?.statusTxt ?? "BELOW HORIZON"}
          </div>
        </div>

        <div style={{ display: "flex", gap: 8 }}>
          <button
            type="button"
            data-testid="sky-patch-image"
            className={lockedClass(reason, "nx-sky-patch-cta")}
            onClick={honestPress(reason, onExplain, () => { if (patch) onImagePatch(patch); })}
            {...lockedAttrs(reason)}
            style={{
              flex: 1, minWidth: 0, height: 50, borderRadius: 12,
              border: "1px solid var(--accent)",
              background: "color-mix(in srgb, var(--accent) 12%, transparent)",
              color: "var(--accent)", fontFamily: DISPLAY, fontWeight: 600,
              fontSize: 11, letterSpacing: ".12em", cursor: "pointer",
              padding: "0 8px", display: "flex", flexDirection: "column",
              alignItems: "center", justifyContent: "center", gap: 2,
            }}
          >
            <span>IMAGE THIS PATCH</span>
            <span style={{ fontFamily: MONO, fontWeight: 400, fontSize: 10, letterSpacing: ".04em", opacity: 0.85 }}>
              {patch ? `${patch.raStr} ${patch.decStr}` : "aim above the horizon"}
            </span>
          </button>
          <button
            type="button"
            data-testid="sky-patch-coords"
            onClick={onCoords}
            style={{
              width: 100, height: 50, borderRadius: 12,
              border: "1px solid var(--line-bright)", background: "var(--bg-raise)",
              color: "var(--text)", fontFamily: DISPLAY, fontWeight: 600,
              fontSize: 10, letterSpacing: ".12em", cursor: "pointer",
            }}
          >
            RA / DEC
          </button>
        </div>
      </div>
    </Card>
  );
}
