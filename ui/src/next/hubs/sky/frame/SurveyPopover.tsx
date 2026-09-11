// SurveyPopover.tsx - which survey, how bright, and survey-or-schematic
// (hub-sky plan C's `mode` row, review #31).
//
// All three were unreachable. `framing.survey` had no writer in the new UI,
// `prefs.setFrameMode` and `prefs.setSurveyBright` were exported and never
// called, and `SkyHub` read both with a `useState` that had no setter - while
// `FrameHost`'s own header said "the schematic fallback stays the user's
// explicit choice". It was nobody's choice; it was whatever was in localStorage
// from a build that could still write it.
//
// A POPOVER RATHER THAN A ROW because FRAME mode already carries a framing card,
// a tools row and (for a mosaic) a night summary under a 370 px finder. These
// three are set once a session and read never, so they earn a button, not a
// permanent 120 px of the screen.
//
// THE ONLINE-ONLY SURVEYS ARE LOCKED, NOT HIDDEN (`SurveyControls.tsx:88-96`
// disables the same two options): a rig with the offline pack and no internet
// still has to be told that DSS2 red exists and what it would need.

import type { JSX, RefObject } from "react";
import { Popover, Segmented, Switch } from "../../../ui";

export interface SurveyOption {
  id: string;
  label: string;
  /** True when the tiles only exist upstream, so `config.survey.online_fetch`
   *  has to be on for it to draw anything. */
  needsOnline: boolean;
}

/** The Atlas's own list (`SurveyControls.tsx:17-22`), minus its "schematic"
 *  row: schematic is a MODE here, not a survey, so it is the switch below
 *  rather than a fourth entry that silently means something different. */
export const SURVEYS: readonly SurveyOption[] = [
  { id: "CDS/P/DSS2/color", label: "DSS2 COLOUR", needsOnline: false },
  { id: "CDS/P/DSS2/red", label: "DSS2 RED", needsOnline: true },
  { id: "CDS/P/2MASS/color", label: "2MASS", needsOnline: true },
];

export const ONLINE_ONLY_REASON =
  "This survey is fetched from the internet - turn on online fetch in Settings > Sky pack.";

export const SCHEMATIC_NOTE =
  "Schematic draws the catalogue's own shapes instead of photographs. It needs no "
  + "survey at all, so it always works - and it cannot show you a nebula's real extent.";

export const BRIGHT_MIN = 0.08;
export const BRIGHT_MAX = 1;

export interface SurveyPopoverProps {
  open: boolean;
  anchorRef: RefObject<HTMLElement | null>;
  onClose: () => void;
  survey: string;
  mode: "survey" | "schematic";
  brightness: number;
  onlineFetch: boolean;
  onSurvey: (id: string) => void;
  onMode: (mode: "survey" | "schematic") => void;
  onBrightness: (v: number) => void;
  onExplain: (reason: string) => void;
}

export function SurveyPopover({
  open, anchorRef, onClose, survey, mode, brightness, onlineFetch,
  onSurvey, onMode, onBrightness, onExplain,
}: SurveyPopoverProps): JSX.Element | null {
  return (
    <Popover open={open} anchorRef={anchorRef} onClose={onClose} align="end" data-testid="sky-survey-pop">
      <div
        style={{
          padding: "2px 6px 4px",
          fontFamily: "'Chakra Petch', system-ui, sans-serif",
          fontWeight: 600, fontSize: 10, letterSpacing: ".2em", color: "var(--text-faint)",
        }}
      >
        SURVEY
      </div>

      <div style={{ padding: "0 6px" }}>
        <Segmented<string>
          options={SURVEYS.map((s) => ({
            value: s.id,
            label: s.label,
            lockedReason: s.needsOnline && !onlineFetch ? ONLINE_ONLY_REASON : null,
          }))}
          value={survey}
          onChange={onSurvey}
          label="Which survey the framing view draws"
          onExplain={onExplain}
          data-testid="sky-survey-pick"
        />
      </div>

      <div style={{ display: "flex", alignItems: "center", padding: "0 6px", minHeight: 44 }}>
        <Switch
          checked={mode === "schematic"}
          onChange={(v) => onMode(v ? "schematic" : "survey")}
          label="Schematic instead"
          note={SCHEMATIC_NOTE}
          data-testid="sky-frame-mode"
        />
      </div>

      <label style={{ display: "flex", flexDirection: "column", gap: 4, padding: "6px 6px 8px" }}>
        <span
          style={{
            fontFamily: "'Chakra Petch', system-ui, sans-serif",
            fontWeight: 600, fontSize: 10, letterSpacing: ".14em", color: "var(--text-faint)",
          }}
        >
          {`IMAGE BRIGHTNESS · ${Math.round(brightness * 100)}%`}
        </span>
        <input
          type="range"
          min={BRIGHT_MIN}
          max={BRIGHT_MAX}
          step={0.02}
          value={brightness}
          onChange={(e) => onBrightness(Number(e.target.value))}
          aria-label="Survey image brightness"
          data-testid="sky-survey-bright"
          style={{ width: "100%", height: 44, accentColor: "var(--accent)" }}
        />
        <span style={{ fontSize: 10, lineHeight: 1.45, color: "var(--text-faint)" }}>
          Dims the photographs only, not the panels or the reticle - so a dark-adapted eye
          can still read the framing over a bright field.
        </span>
      </label>
    </Popover>
  );
}
