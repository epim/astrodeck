// LayersPopover.tsx - the three overlay toggles under the layers button
// (hub-sky plan A.6).
//
// ARCHITECTURE.md section 5 names layers as one of the two popovers in the app
// and gives it the `Popover` primitive, so that is what it uses - outside tap,
// Escape and viewport clamping come with it, and the design's hand-placed
// `right:60px; top:90px` box becomes an anchored one. The plan wrote the
// absolute placement because `next/ui/Popover.tsx` did not exist when it was
// written; it does now.
//
// THE NOTE IS THE POINT. Three switches say what CAN be drawn; the sentence
// under them says why what is drawn looks the way it does - no weather access,
// no horizon marked at this site, no wind in the forecast. Those are three
// different absences with three different fixes, and collapsing them into a
// greyed-out switch would leave the user toggling something that was never
// going to draw anything.

import type { JSX, RefObject } from "react";
import { Popover, Switch } from "../../../ui";
import { NxIcon, type NxIconName } from "../../../icons";
import { SkyGlyph } from "./glyphs";

export type LayerKey = "clouds" | "horizon" | "wind";

const ROWS: { key: LayerKey; label: string; icon: NxIconName }[] = [
  { key: "clouds", label: "Cloud deck", icon: "weather" },
  { key: "horizon", label: "Horizon", icon: "horizon" },
  { key: "wind", label: "Wind + drift", icon: "wind" },
];

export interface LayersPopoverProps {
  open: boolean;
  anchorRef: RefObject<HTMLElement | null>;
  onClose: () => void;
  layers: Record<LayerKey, boolean>;
  onToggle: (key: LayerKey, on: boolean) => void;
  /**
   * The atlas's survey imagery, when the atlas is the thing behind this
   * popover. Absent in the schematic finder, which has no imagery to draw.
   *
   * It is a fourth OVERLAY rather than a setting of its own: it answers the
   * same question the other three do - what is painted on this device's sky -
   * and it belongs under the same stack icon. It is NOT one of `LayerKey`'s
   * three, because those are `useSkyModel`'s own state and this one is the
   * hub's; see `finder/prefs.ts setLayers` for how one key survives two owners.
   */
  survey?: { on: boolean; onToggle: (on: boolean) => void } | null;
  /** One sentence: the model's `layersNote`, already chosen for this principal
   *  and this site. */
  note: string;
  /** The wind feed's own absence, when there is one. */
  windNote: string | null;
  /** Set when this principal cannot see weather at all - the two weather rows
   *  then render honest-disabled with it. */
  weatherReason: string | null;
  onExplain: (reason: string) => void;
}

export function LayersPopover({
  open,
  anchorRef,
  onClose,
  layers,
  onToggle,
  survey,
  note,
  windNote,
  weatherReason,
  onExplain,
}: LayersPopoverProps): JSX.Element | null {
  return (
    <Popover open={open} anchorRef={anchorRef} onClose={onClose} align="end" data-testid="sky-layers">
      <div
        style={{
          padding: "2px 6px 4px",
          fontFamily: "'Chakra Petch', system-ui, sans-serif",
          fontWeight: 600,
          fontSize: 10,
          letterSpacing: ".2em",
          color: "var(--text-faint)",
        }}
      >
        OVERLAYS
      </div>
      {survey && (
        <div
          data-layer-row="survey"
          style={{ display: "flex", alignItems: "center", gap: 8, padding: "0 6px", minHeight: 44 }}
        >
          <span style={{ display: "flex", color: "var(--text-dim)", flexShrink: 0 }}>
            <SkyGlyph name="atlas" size={18} />
          </span>
          <Switch
            checked={survey.on}
            onChange={survey.onToggle}
            label="Survey imagery"
            // The note is the reason to turn it OFF, which is the half nobody
            // guesses: off is not a dimmer, it stops the fetching.
            note="off draws the markers and the reticle only, and fetches no tiles"
            className="nx-sky-layer-switch"
          />
        </div>
      )}
      {ROWS.map((row) => {
        const reason = row.key === "horizon" ? null : weatherReason;
        return (
          <div
            key={row.key}
            data-layer-row={row.key}
            style={{ display: "flex", alignItems: "center", gap: 8, padding: "0 6px", minHeight: 44 }}
          >
            <span style={{ display: "flex", color: "var(--text-dim)", flexShrink: 0 }}>
              <NxIcon name={row.icon} size={18} />
            </span>
            <Switch
              checked={layers[row.key]}
              onChange={(v) => onToggle(row.key, v)}
              label={row.label}
              lockedReason={reason}
              onExplain={onExplain}
              className="nx-sky-layer-switch"
            />
          </div>
        );
      })}
      <div style={{ padding: "6px 6px 4px", fontSize: 10, lineHeight: 1.45, color: "var(--text-faint)" }}>
        {note}
      </div>
      {windNote && (
        <div
          data-testid="sky-layers-wind-note"
          style={{ padding: "0 6px 6px", fontSize: 10, lineHeight: 1.45, color: "var(--text-faint)" }}
        >
          {windNote}
        </div>
      )}
    </Popover>
  );
}
