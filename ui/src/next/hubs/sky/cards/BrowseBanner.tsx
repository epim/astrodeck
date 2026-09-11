// BrowseBanner.tsx - the dashed card that appears when no rig is connected
// (hub-sky plan A.2).
//
// It is not a warning and it does not offer to dismiss itself. Everything the
// Sky hub does WITHOUT a rig - the finder, the ranked list, the dome, the
// horizon - still works, and the banner's whole job is to say which one thing
// does not, before the user presses it and finds out. That is why the copy names
// IMAGE THIS specifically rather than saying "some features are unavailable".

import type { JSX } from "react";
import { Card } from "../../../ui";
import { nav } from "../../../router";

export function BrowseBanner(): JSX.Element {
  return (
    <Card tone="dashed" padding={0} className="nx-sky-browse" data-testid="sky-browse">
      <button
        type="button"
        onClick={() => nav.go("/settings/general/setup")}
        style={{
          width: "100%",
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "8px 12px",
          border: 0,
          background: "transparent",
          color: "var(--text)",
          textAlign: "left",
          cursor: "pointer",
          minHeight: 44,
        }}
      >
        <span
          aria-hidden="true"
          style={{
            width: 8,
            height: 8,
            borderRadius: "50%",
            background: "var(--text-faint)",
            flexShrink: 0,
          }}
        />
        <span
          style={{
            flex: 1,
            fontFamily: "'IBM Plex Mono', ui-monospace, monospace",
            fontSize: 10.5,
            lineHeight: 1.4,
            color: "var(--text-dim)",
          }}
        >
          <span style={{ color: "var(--text)" }}>Browsing.</span> No rig connected - the
          sky, the dome and the list all work; IMAGE THIS needs a rig.
        </span>
        <span
          style={{
            fontFamily: "'Chakra Petch', system-ui, sans-serif",
            fontWeight: 600,
            fontSize: 10,
            letterSpacing: ".12em",
            color: "var(--accent)",
            whiteSpace: "nowrap",
          }}
        >
          SET UP ›
        </span>
      </button>
    </Card>
  );
}
