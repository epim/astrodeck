// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// PanelPicker.tsx - the row of chips that chooses which mosaic panel a session
// stack surface shows, and the hook that holds the choice (#781, #1113).
//
// A mosaic night keeps one stack per panel on the server. A surface that reads
// "the stack" with no panel is shown whichever panel the latest frame fed, and
// flips at every hop of the rotation. Every surface that draws a stack takes
// its panel from here: the Now screen's LiveStack, the Gallery's
// SessionStackPanel, the classic Monitor's SessionStack and the Rig inspect
// sheet. The pinned choice is ONE value, held in stackView.ts, so a panel
// pinned on one surface is the panel the others show: the rig has one stack,
// and the operator is looking at one panel of it.
//
// The chip row is drawn ONLY when there is more than one panel: with one it
// would say what the surface's own badge already says, and every one-target
// night looks as it always did.
//
// The classic root and the new UI both mount this, and only the new UI loads
// `next.css`, so the chips carry their own style, from the colour variables
// both roots define.

import { useEffect, type CSSProperties, type JSX } from "react";

import type { SessionStackPanel } from "../api/sessionStack";
import { latestPanel, panelLabels, useStackView } from "../next/hubs/session/now/stackView";

export interface PanelPin {
  /** The pinned panel's key (`status.panels[].key`), or null to follow the
   *  latest. It goes on the wire as `?panel=`. */
  panel: string | null;
  setPanel: (key: string | null) => void;
}

/** The pinned panel, shared across surfaces. A pin on a panel the stack no
 *  longer holds (the server released it for memory, or the stack was reset) is
 *  dropped here, so the surface asks for the latest instead of for a panel that
 *  reads as empty. `panels` is the status's whole list, which describes the
 *  stack whichever panel the rest of the status is for; undefined (an older
 *  server) keeps the pin. */
export function usePanelPin(panels: readonly SessionStackPanel[] | undefined): PanelPin {
  const { panel, setPanel } = useStackView();
  useEffect(() => {
    if (panel !== null && panels !== undefined && !panels.some((p) => p.key === panel)) {
      setPanel(null);
    }
  }, [panel, panels, setPanel]);
  return { panel, setPanel };
}

const ROW: CSSProperties = { display: "flex", gap: 6, overflowX: "auto", minWidth: 0 };

function chipStyle(lit: boolean): CSSProperties {
  return {
    display: "inline-flex", alignItems: "center", gap: 6, flexShrink: 0,
    minHeight: 44, padding: "0 12px", borderRadius: 999, cursor: "pointer",
    border: `1px solid ${lit ? "var(--accent)" : "var(--line)"}`,
    background: lit ? "var(--accent-fill)" : "transparent",
    color: lit ? "var(--accent)" : "var(--text)",
    fontFamily: '"IBM Plex Mono", monospace', fontSize: 11, letterSpacing: ".06em",
    whiteSpace: "nowrap",
  };
}

/** One chip per panel, named with its sub count. The lit chip is the pinned
 *  panel, or the latest while following; pressing the pinned chip lets go. */
export function PanelPicker({ panels, panel, onPick, testId = "panel-picker" }: {
  panels: readonly SessionStackPanel[];
  panel: string | null;
  onPick: (key: string | null) => void;
  testId?: string;
}): JSX.Element | null {
  if (panels.length < 2) return null;
  const labels = panelLabels(panels);
  const lit = panel ?? latestPanel(panels)?.key ?? null;
  return (
    <div data-testid={testId} role="group" aria-label="Mosaic panels" style={ROW}>
      {panels.map((p, i) => (
        <button
          key={p.key}
          type="button"
          aria-pressed={lit === p.key}
          data-testid={`panel-chip-${p.key}`}
          style={chipStyle(lit === p.key)}
          onClick={() => onPick(panel === p.key ? null : p.key)}
        >
          <span data-part="name">{labels[i]}</span>
          <span data-part="count" style={{ opacity: 0.7 }}>{p.frames}</span>
        </button>
      ))}
    </div>
  );
}
