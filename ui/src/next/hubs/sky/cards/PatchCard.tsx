// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
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
//
// A NULL PATCH HAS TWO CAUSES, AND THE CARD IS TOLD WHICH (#544, the #503
// residual). `useSkyModel` answers no patch when the view is aimed below the
// horizon, and also whenever it has no coordinates to place the sky with: no
// saved site (a default site's placeholder 0,0) or a role the site's position is
// withheld from. The card used to read every null patch as the first, so a
// fresh rig's finder, aimed at 45 degrees, said "BELOW HORIZON", "0 of 0 targets
// clear and in reach" and "aim above the horizon" about a sky nobody placed.
// `placementNote` is the model's own answer to "why is nothing placed", and with
// it set the card prints no status word and no count it did not compute, and the
// button's reason is the one that applies: the no-site fix, or the role
// sentence, since a viewer cannot set a site.

import type { JSX } from "react";
import { Card, honestPress, lockedAttrs, lockedClass } from "../../../ui";
import { SkyGlyph } from "./glyphs";
import { NO_COORDS_NOTE, type PatchModel } from "../finder";

const MONO = "'IBM Plex Mono', ui-monospace, monospace";
const DISPLAY = "'Chakra Petch', system-ui, sans-serif";

export interface PatchCardProps {
  patch: PatchModel | null;
  /** In-reach count, or null when nothing was placed to count (#544): the
   *  "N of M" line is then not printed. */
  reachCount: number | null;
  targetCount: number;
  /** The model's `placementNote`: null when the sky is placed, else why it is
   *  not (`NO_SITE_NOTE` or `NO_COORDS_NOTE`). With it set a null patch is a
   *  sky nobody placed, not one aimed below the horizon (#544). */
  placementNote: string | null;
  onImagePatch: (patch: PatchModel) => void;
  /** FREE-ROAM: open FRAME mode on this patch, with no catalogued object.
   *  Survey imagery of an uncatalogued patch had no door in the new UI at all
   *  (review #29), and this is the one the design already has a card for. */
  onFramePatch: (patch: PatchModel) => void;
  /** The kept free-roam framing for THIS patch, or null. Without it, FRAME
   *  HERE > DONE leaves no trace anywhere on the screen and reads as a control
   *  that did nothing - which is the defect class this whole review is about. */
  framed: string | null;
  onAdjustFrame: () => void;
  onClearFrame: () => void;
  onCoords: () => void;
  imageReason: string | null;
  onExplain: (reason: string) => void;
}

export const FRAME_HERE_NEEDS_PATCH =
  "No position under the reticle - set a site in Settings, or aim above the horizon.";

/** Why IMAGE THIS PATCH has nothing to send, by why there is no patch (#544). */
export const PATCH_NEEDS_SITE = "Set a site first - a patch of sky has no coordinates without one.";
export const PATCH_HIDDEN_FOR_ROLE =
  "The site's position is hidden for this role, so this patch has no coordinates to send.";
export const PATCH_BELOW_HORIZON = "Aim above the horizon - a patch below it cannot be imaged.";

export function PatchCard({
  patch,
  reachCount,
  targetCount,
  placementNote,
  onImagePatch,
  onFramePatch,
  framed,
  onAdjustFrame,
  onClearFrame,
  onCoords,
  imageReason,
  onExplain,
}: PatchCardProps): JSX.Element {
  // No longitude means no local sidereal time, so the reticle has no RA to
  // report and the button has nothing to send. Saying so beats a button that
  // posts a position derived from a site nobody set. WHICH sentence is the
  // cause's (#544): "set a site" was said to a viewer, who cannot, and to a
  // placed sky aimed below the horizon, which needs no site.
  const unplaced = placementNote != null;
  const noPatchReason = !unplaced
    ? PATCH_BELOW_HORIZON
    : placementNote === NO_COORDS_NOTE ? PATCH_HIDDEN_FOR_ROLE : PATCH_NEEDS_SITE;
  const reason = patch == null ? noPatchReason : imageReason;
  // A status word only for a patch that was judged, or for a placed sky aimed
  // below the horizon, which is what the word says. None for an unplaced sky.
  const status = patch?.statusTxt ?? (unplaced ? null : "BELOW HORIZON");
  // FRAME HERE needs a POSITION, not a camera: it fetches survey imagery and
  // commands nothing, so a viewer with no capture access can still use it and
  // the only thing that can stop it is a reticle with no coordinates at all.
  const frameReason = patch == null ? FRAME_HERE_NEEDS_PATCH : null;

  return (
    <Card tone="dashed" className="nx-sky-patch" data-testid="sky-patch">
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 10 }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
            <div style={{ fontFamily: DISPLAY, fontWeight: 600, fontSize: 11, letterSpacing: ".14em", color: "var(--text-dim)" }}>
              NO CATALOGUE TARGET HERE
            </div>
            {reachCount != null && (
              <div data-testid="sky-patch-count" style={{ fontSize: 12, color: "var(--text-faint)", lineHeight: 1.5 }}>
                {reachCount} of {targetCount} targets clear and in reach · tap a label to jump
              </div>
            )}
          </div>
          {status != null && (
            <div
              data-testid="sky-patch-status"
              style={{ fontFamily: MONO, fontSize: 10, color: patch?.color ?? "var(--text-faint)", textAlign: "right", whiteSpace: "nowrap" }}
            >
              {status}
            </div>
          )}
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
              {patch ? `${patch.raStr} ${patch.decStr}` : unplaced ? "no position to send" : "aim above the horizon"}
            </span>
          </button>
        </div>

        {framed && (
          <div
            data-testid="sky-patch-framed"
            style={{
              display: "flex", alignItems: "center", gap: 8,
              padding: "6px 10px", borderRadius: 10,
              border: "1px solid color-mix(in srgb, var(--accent) 35%, transparent)",
              background: "color-mix(in srgb, var(--accent) 6%, transparent)",
            }}
          >
            <span style={{ color: "var(--accent)", display: "flex" }}>
              <SkyGlyph name="frame" size={14} />
            </span>
            <span style={{ fontFamily: MONO, fontSize: 10.5, color: "var(--text)", flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {framed}
            </span>
            <button
              type="button"
              onClick={onAdjustFrame}
              data-testid="sky-patch-frame-adjust"
              style={{
                height: 26, padding: "0 9px", borderRadius: 8,
                border: "1px solid var(--line-bright)", background: "var(--bg-raise)",
                color: "var(--text)", fontFamily: DISPLAY, fontWeight: 600,
                fontSize: 10, letterSpacing: ".12em", cursor: "pointer",
              }}
            >
              ADJUST
            </button>
            <button
              type="button"
              aria-label="remove framing"
              data-testid="sky-patch-frame-clear"
              onClick={onClearFrame}
              style={{
                width: 26, height: 26, borderRadius: 8,
                border: "1px solid var(--line-bright)", background: "var(--bg-raise)",
                color: "var(--text-dim)", fontFamily: MONO, fontSize: 12, cursor: "pointer",
              }}
            >
              ×
            </button>
          </div>
        )}

        <div style={{ display: "flex", gap: 8 }}>
          <button
            type="button"
            data-testid="sky-patch-frame"
            className={lockedClass(frameReason, "nx-sky-patch-frame")}
            onClick={honestPress(frameReason, onExplain, () => { if (patch) onFramePatch(patch); })}
            {...lockedAttrs(frameReason)}
            style={{
              flex: 1, minWidth: 0, height: 44, borderRadius: 12,
              border: "1px solid var(--line-bright)", background: "var(--bg-raise)",
              color: "var(--text)", fontFamily: DISPLAY, fontWeight: 600,
              fontSize: 10, letterSpacing: ".12em", cursor: "pointer",
              display: "flex", flexDirection: "column",
              alignItems: "center", justifyContent: "center", gap: 1,
            }}
          >
            <span>{framed ? "REFRAME HERE" : "FRAME HERE"}</span>
            <span style={{ fontFamily: MONO, fontWeight: 400, fontSize: 9, letterSpacing: ".02em", color: "var(--text-faint)" }}>
              survey imagery, no object needed
            </span>
          </button>
          <button
            type="button"
            data-testid="sky-patch-coords"
            onClick={onCoords}
            style={{
              width: 100, height: 44, borderRadius: 12,
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
