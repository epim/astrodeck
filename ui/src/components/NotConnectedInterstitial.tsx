// NotConnectedInterstitial.tsx — the "connect equipment first" interstitial for
// gated views (onboarding spec §7a). Rendered by equipment-dependent views when
// !equipConnected (the gating signal is the sticky equipConnected flag, NOT
// wsConnected — a WS drop shows the reconnecting banner, never this).
//
// One model only: a ghost nav glyph + headline + per-view one-liner + a single
// "Go to Rig" primary. NO "Connect Simulator Rig" here (resolves E18) — the sim
// rig lives only on the Rig page, labelled Demo / Simulator.

import type { JSX } from "react";
import { useStore } from "../store";
import { Icon, type IconName } from "./icons";
import type { ViewName } from "../types";

// Per-view ghost icon + one-liner. Icons come from the Batch-1 icon module.
const VIEW_META: Partial<Record<ViewName, { icon: IconName; line: string }>> = {
  capture: { icon: "capture", line: "Connect a camera to start capturing frames." },
  focus: { icon: "focus", line: "Connect a focuser and camera to run autofocus." },
  mount: { icon: "mount", line: "Connect a telescope to slew, center, and track." },
  polar: { icon: "align", line: "Connect a telescope to run polar alignment." },
  guide: { icon: "guide", line: "Connect a guider to start guiding." },
  power: { icon: "power", line: "Connect a power/switch device to control ports." },
  // NOTE: `sequence`/`monitor` are intentionally absent — those views are not
  // gated by this interstitial, so entries here would be dead config.
};

export function NotConnectedInterstitial({ view }: { view: ViewName }): JSX.Element {
  const setView = useStore((s) => s.setView);
  const meta = VIEW_META[view] ?? { icon: "rig" as IconName, line: "Connect your equipment to use this view." };

  return (
    <div className="empty-state view-enter" role="status">
      <span className="empty-ghost" aria-hidden>
        <Icon name={meta.icon} size={40} strokeWidth={1} />
      </span>
      <div className="panel-title !text-ink">Connect equipment first</div>
      <p className="text-xs text-dim max-w-[40ch]">{meta.line}</p>
      <div className="mt-2">
        <button
          type="button"
          className="btn btn-accent min-h-11"
          onClick={() => setView("connect")}
        >
          <Icon name="rig" size={14} className="inline -mt-0.5 mr-1.5" />
          Go to Rig
        </button>
      </div>
    </div>
  );
}

export default NotConnectedInterstitial;
