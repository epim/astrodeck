// LiveStack.tsx - the picture the night is actually making.
//
// The last-sub tile answers "is the rig still working". This answers "is this
// going to be a picture", which is the question the person who set an alarm for
// 3am has. The ENGINE is the existing `api/sessionStack.ts` module and the
// server's own compositor; what is rebuilt here is the CHROME, because the
// README specifies this screen and the legacy component's Panel/Stat/Toggle
// furniture is a different screen's.
//
// `modeLabel` is IMPORTED from the legacy component rather than re-written: it
// is the one sentence that lets a viewer looking at a teal image find out why it
// is teal, and two copies of it would eventually disagree.
//
// THE LINK LOST OVERLAY IS DRIVEN BY THE SAME PREDICATE AS THE PILL. If the
// picture said "retrying link" while the header said CAPTURING, one of them
// would be lying and the operator would have no way to tell which.
//
// THE EMPTY FACE CARRIES THE COST OF THE PRESS. Switching the stack on used to
// mean "from the next frame", so arming it at 2am showed two of the night's
// ninety subs. The backfill folds the earlier accepted subs in, and its count is
// ON THE LABEL because that pass reads every one of those frames off disk: an
// informed press, not a surprise.

import type { CSSProperties, JSX } from "react";

import { backfillLabel, fmtIntegration, sessionStackImageUrl } from "../../../../api/sessionStack";
import { modeLabel } from "../../../../components/preview/SessionStack";
import { accessPhrase, useCanControlCapture } from "../../../../lib/caps";
import { fmtClock } from "../../../../lib/eta";
import { usePreview, useSeq, useStore, useTelemetryStale, useWsPhase } from "../../../../store";
import { ActionButton, Bar, EmptyCard, Mono } from "../../../ui";
import { explainLock } from "../../../shell/explain";
import { filterColor } from "./filters";
import { phaseOf, phaseWord } from "./phase";
import { useStackView, useSessionStackStatus } from "./stackView";
import { useSubFrame } from "./useSubFrame";
import { useNowIncidents } from "./useNowIncidents";

const PILL: CSSProperties = {
  padding: "4px 8px", borderRadius: 999,
  background: "rgba(6,7,11,.75)", border: "1px solid var(--line)",
  fontFamily: '"IBM Plex Mono", monospace', fontSize: 10,
  letterSpacing: ".08em", whiteSpace: "nowrap",
};

const CORNER: CSSProperties = {
  padding: "5px 9px", borderRadius: 10,
  background: "rgba(6,7,11,.75)", border: "1px solid var(--line)",
  fontFamily: '"IBM Plex Mono", monospace', fontSize: 11,
};

export function LiveStack({ height = 250 }: { height?: number }): JSX.Element {
  const { status, busy, error, start } = useSessionStackStatus();
  const { channel, cssFilter, stretch } = useStackView();
  const seq = useSeq();
  const preview = usePreview();
  const wsPhase = useWsPhase();
  const telemetryStale = useTelemetryStale();
  const wsLastEvent = useStore((s) => s.wsLastEvent);
  const focusRunning = useStore((s) => s.focus?.state === "running");
  const busyLanes = useStore((s) => s.status?.busy_lanes ?? EMPTY_LANES);
  const canCapture = useCanControlCapture();
  const { incidents } = useNowIncidents();
  const sub = useSubFrame(seq.progress, false);

  // Exactly the incident-3 predicate, so the overlay and the pill agree.
  const linkLost = wsPhase !== "up" || telemetryStale;

  const frames = status?.frames ?? 0;
  const hasImage = Boolean(status?.has_image && frames > 0);
  const available = status?.backfill?.available ?? 0;
  const bfLine = backfillLabel(status?.backfill);

  const stretchWord = stretch.toLowerCase();
  const modeBadge = channel
    ? `${channel} only · ${status ? channelFrames(status, channel) : 0} subs · ${stretchWord} stretch`
    : status && Array.isArray(status.channels)
      ? `${modeLabel(status)} · ${stretchWord} stretch`
      : "nothing stacked yet";

  const phase = phaseOf({
    state: seq.state,
    detail: seq.detail,
    scheduleState: seq.schedule?.state ?? null,
    busyLanes,
    focusRunning,
    frameStartedAtMs: seq.progress?.frame_started_at_ms ?? null,
    incident: incidents.length ? { pill: incidents[0].pill, color: incidents[0].color } : null,
  });

  const currentLine = seq.state === "complete"
    ? `complete · ${seq.progress?.frames_done ?? 0} subs`
    : seq.detail && /\[\d+\/\d+\]/.test(seq.detail)
      ? seq.detail
      : `${phaseWord(phase)}...`;

  const hfr = preview?.hfr;
  const hfrLine = typeof hfr === "number" ? hfr.toFixed(2) : "-";

  const startReason = canCapture ? null : `Stacking needs ${accessPhrase("control.capture")}.`;

  return (
    <div data-testid="now-live-stack" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div
        style={{
          position: "relative", height, flexShrink: 0, borderRadius: 16,
          overflow: "hidden", border: "1px solid var(--line)", background: "#000",
        }}
      >
        {hasImage && status ? (
          <img
            src={sessionStackImageUrl(status.seq, 1200)}
            alt={`Live stack of ${status.target || "tonight's target"}`}
            data-testid="live-stack-image"
            style={{
              position: "absolute", inset: 0, width: "100%", height: "100%",
              objectFit: "cover", opacity: 0.9, filter: cssFilter, transition: "filter .4s",
            }}
          />
        ) : (
          <div style={{
            position: "absolute", inset: 10, display: "flex",
            alignItems: "center", justifyContent: "center",
          }}>
            <EmptyCard
              title={status?.enabled ? "NOTHING STACKED YET" : "LIVE STACK IS OFF"}
              hint={status?.enabled
                ? "The first accepted sub of the run makes the first picture."
                : "Every sub the run accepts is stacked per filter and composited into colour."}
              action={
                <ActionButton
                  kind="primary"
                  onPress={() => start(true)}
                  busy={busy}
                  lockedReason={startReason}
                  onExplain={explainLock}
                  data-testid="live-stack-start"
                >
                  {available > 0
                    ? `STACK TONIGHT'S SUBS · ${available} ALREADY CAPTURED`
                    : "STACK TONIGHT'S SUBS"}
                </ActionButton>
              }
              data-testid="live-stack-empty"
            />
          </div>
        )}

        {/* The tint layer for a single-channel view. It is a PRESENTATION of the
            colour composite, and the badge plus the line under the strip say so:
            there is no per-channel image on the server to fetch. */}
        {channel && hasImage && (
          <div
            aria-hidden="true"
            style={{
              position: "absolute", inset: 0, background: filterColor(channel),
              mixBlendMode: "color", pointerEvents: "none",
            }}
          />
        )}

        {linkLost && (
          <div
            data-testid="live-stack-linklost"
            style={{
              position: "absolute", inset: 0, background: "rgba(6,7,11,.55)",
              display: "flex", flexDirection: "column", alignItems: "center",
              justifyContent: "center", gap: 6, textAlign: "center", padding: 8,
            }}
          >
            <Mono size={10.5}>
              LAST UPDATE {wsLastEvent ? fmtClock(wsLastEvent) : "unknown"}
            </Mono>
            <Mono size={10.5} tone="dim">the rig keeps imaging · retrying link...</Mono>
          </div>
        )}

        <div style={{
          position: "absolute", left: 10, top: 10, display: "flex", gap: 6, flexWrap: "wrap",
        }}>
          <span style={PILL} data-testid="live-stack-badge">
            LIVE STACK · {frames} subs · {fmtIntegration(status?.integrated_s ?? 0)}
          </span>
          <span style={{ ...PILL, color: "var(--text-dim)" }} data-testid="live-stack-mode">
            {modeBadge}
          </span>
        </div>

        <div style={{
          position: "absolute", left: 10, right: 10, bottom: 10,
          display: "flex", justifyContent: "space-between", alignItems: "flex-end", gap: 8,
        }}>
          <span style={CORNER} data-testid="live-stack-current">{currentLine}</span>
          <span style={{ ...CORNER, color: "var(--text-dim)" }}>
            HFR <span style={{ color: "var(--text)" }}>{hfrLine}</span>
          </span>
        </div>

        <div style={{ position: "absolute", left: 0, right: 0, bottom: 0 }}>
          <Bar value={sub.fraction ?? 0} height={3} label="Current sub" />
        </div>
      </div>

      {bfLine && <Mono size={10} tone="dim">{bfLine}</Mono>}
      {error && !status && (
        <Mono size={10} tone="warn">Could not read the stack: {error}</Mono>
      )}
    </div>
  );
}

const EMPTY_LANES: string[] = [];

function channelFrames(status: { channels?: { channel: string; frames: number }[] }, name: string): number {
  const hit = (status.channels ?? []).find((c) => c.channel.toLowerCase() === name.toLowerCase());
  return hit?.frames ?? 0;
}
