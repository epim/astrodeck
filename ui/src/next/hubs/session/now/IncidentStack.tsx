// IncidentStack.tsx - the incident card, and the ones underneath it.
//
// The README shows ONE card. The engine can raise several at once (a cloud hold
// during a cooler gate on a phone that has lost the link), and swallowing the
// rest would be a loss - so the most severe one is the card, and the others are
// a row that expands. Nothing is hidden; the screen just refuses to be four
// cards tall on a phone.
//
// EVERY ACTION IS GATED BY THE SAME HELPER THE REST OF THE APP USES, and the
// gate is called as a PURE function per action rather than as a hook: an
// incident's action list changes length between renders, and a hook per action
// would be a hook called conditionally.
//
// STOP THE RUN IS TWO TAPS. `IncidentCard` renders plain buttons, so the arm
// lives here: the first press swaps the label and starts a 3 s window, and only
// a second press inside it posts. The design's rule, on the design's control.

import { useEffect, useRef, useState, type JSX } from "react";

import { ApiError } from "../../../../api";
import { lockReason } from "../../../lib/gate";
import type { Capability } from "../../../../types";
import {
  useEquipConnected, useFrameSettings, usePrincipal, useStatus, useStore, useWsPhase,
} from "../../../../store";
import { IncidentCard, Pill } from "../../../ui";
import { NxIcon, type NxIconName } from "../../../icons";
import { explainLock } from "../../../shell/explain";
import {
  ARMED_ACTION_IDS, INCIDENT_ARM_LABEL, INCIDENT_ARM_MS, runIncidentAction, specFor,
} from "./incidentActions";
import { useNowIncidents } from "./useNowIncidents";

/** Kind -> glyph. The prototype's tiles are not in `next/icons.tsx`'s union, so
 *  each kind takes the closest glyph that already exists and is drawn in the
 *  same idiom; `stall` takes the pulse waveform, as the plan asks. */
const GLYPH: Record<string, NxIconName> = {
  cloud: "weather",
  safety: "safety",
  link: "refresh",
  solve: "search",
  af: "focuser",
  guide: "guider",
  disk: "layers",
  cooler: "temp",
  stall: "monitor",
};

export function IncidentStack({ compact = false }: { compact?: boolean }): JSX.Element | null {
  const { incidents, nowMs, dismissed, dismiss, refineCtx } = useNowIncidents();
  const [expanded, setExpanded] = useState(false);
  const [armed, setArmed] = useState<string | null>(null);
  const armTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (armTimer.current) clearTimeout(armTimer.current); }, []);

  const principal = usePrincipal();
  const status = useStatus();
  const equipConnected = useEquipConnected();
  const wsPhase = useWsPhase();
  const focusFrame = useFrameSettings("focus");
  const weatherIgnored = refineCtx.weatherIgnored;

  if (incidents.length === 0) return null;

  const gate = { principal, status, equipConnected, wsPhase };
  const lockedFor = (id: string): string | null => {
    const s = specFor(id);
    if (!s) return null;
    // A nav or a local dismissal needs nothing from the rig; locking those
    // would tell a viewer they may not read the safety sheet.
    if (s.kind === "nav" || s.kind === "local" || s.kind === "client") {
      // `wsPhase: "up"` deliberately: opening the Safety sheet or the trash is
      // still possible with the rig unreachable, and telling the operator it is
      // not would be the wrong sentence at exactly the wrong moment.
      return s.cap
        ? lockReason({ cap: s.cap as Capability }, { ...gate, wsPhase: "up" as const })
        : null;
    }
    const first = lockReason(
      { cap: s.cap as Capability | undefined, needsRole: s.needsRole, busyLane: s.busyLane }, gate);
    if (first) return first;
    return s.busyLane2 ? lockReason({ busyLane: s.busyLane2 }, gate) : null;
  };

  const fire = (kind: string) => (id: string) => {
    if (ARMED_ACTION_IDS.includes(id) && armed !== id) {
      setArmed(id);
      if (armTimer.current) clearTimeout(armTimer.current);
      armTimer.current = setTimeout(() => { armTimer.current = null; setArmed(null); }, INCIDENT_ARM_MS);
      return;
    }
    if (armTimer.current) { clearTimeout(armTimer.current); armTimer.current = null; }
    setArmed(null);
    void runIncidentAction(id, kind, {
      focusFrame: {
        exposure_s: focusFrame.exposure_s, gain: focusFrame.gain, binning: focusFrame.binning,
      },
      coolerTargetC: refineCtx.coolerTargetC,
      onDismiss: dismiss,
      weatherIgnored,
    }).catch((e: unknown) => {
      // The server's own message, not a generic one: "no guider connected" and
      // "a capture is already running" are different nights.
      const detail = e instanceof ApiError || e instanceof Error ? e.message : String(e);
      useStore.getState().enqueueToast({ level: "error", title: "That did not land", detail });
    });
  };

  // The arm swaps the LABEL, so the button says what the second press will do.
  const withArm = (inc: (typeof incidents)[number]) => (armed
    ? { ...inc, actions: inc.actions.map((a) => (a.id === armed ? { ...a, label: INCIDENT_ARM_LABEL } : a)) }
    : inc);

  const top = incidents[0];
  const rest = incidents.slice(1);
  const shown = expanded ? incidents : [top];

  return (
    <div
      data-testid="now-incidents"
      data-count={incidents.length}
      style={{ display: "flex", flexDirection: "column", gap: 8 }}
    >
      {shown.map((inc) => (
        // The WAIT / ACKNOWLEDGE / CONTINUE press is "I have read this", and
        // what it buys is the card standing down visually. The incident itself
        // stays - the sky has not cleared because somebody pressed a button.
        <div key={inc.kind} data-read={dismissed[inc.kind] ? "true" : undefined}
          style={{ opacity: dismissed[inc.kind] ? 0.72 : 1 }}>
          <IncidentCard
            incident={withArm(inc)}
            nowMs={nowMs}
            glyph={<NxIcon name={GLYPH[inc.kind] ?? "info"} size={compact ? 16 : 18} />}
            lockedFor={lockedFor}
            onExplain={explainLock}
            onAction={fire(inc.kind)}
            data-testid={`incident-${inc.kind}`}
          />
        </div>
      ))}
      {rest.length > 0 && (
        <div style={{ display: "flex", gap: 6 }}>
          <Pill
            tone="warn"
            onClick={() => setExpanded((v) => !v)}
            ariaLabel={expanded ? "Show only the most severe incident"
              : `Show ${rest.length} more incident${rest.length === 1 ? "" : "s"}`}
            data-testid="incidents-more"
          >
            {expanded ? "SHOW ONLY THE WORST" : `${rest.length} MORE`}
          </Pill>
        </div>
      )}
    </div>
  );
}
