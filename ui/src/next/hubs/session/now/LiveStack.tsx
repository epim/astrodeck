// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
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
// A CHANNEL IS A DIFFERENT PICTURE, NOT A TINT (D-SES-1). Picking Ha in the
// strip changes the URL this <img> asks for; the server renders that channel's
// own accumulator. An <img> cannot read a status code, so a refusal arrives as
// `onError`, and it is CAUGHT: the picture falls back to the composite, the
// badge stops saying "Ha only", and a sentence says which of the two is on
// screen. A silent fallback would leave the badge and the picture disagreeing,
// which is the exact defect the per-channel route was added to end. WHICH
// sentence is decided by `channelFallbackNote` from the stack's own per-channel
// frame count - `onError` does not carry a status code, and "nothing stacked in
// Ha yet" over 42 banked Ha subs is a false statement about the night.
//
// THE EMPTY FACE CARRIES THE COST OF THE PRESS. Switching the stack on used to
// mean "from the next frame", so arming it at 2am showed two of the night's
// ninety subs. The backfill folds the earlier accepted subs in, and its count is
// ON THE LABEL because that pass reads every one of those frames off disk: an
// informed press, not a surprise. The label may take two lines rather than cut
// the count off with an ellipsis.
//
// THE EMPTY FACE IS LAID OUT, NOT LAID OVER (#468). The chips and the corner
// readouts are drawn over a PICTURE, which is what they describe. With nothing
// stacked there is no picture, and the chips said only what the card's own
// heading says ("0 subs", "nothing stacked yet", and a stretch for an image
// that is not there), so they are not drawn. The card and the corner readouts
// sit in the frame's flow, which grows to hold them: centred in a fixed 180 px
// frame, the card in the desktop column was taller than its box, and the chips
// at the top printed over its heading and the first line of its body.
//
// A MOSAIC HAS ONE STACK PER PANEL AND THE ROW OF CHIPS ABOVE THE PICTURE IS HOW
// YOU CHOOSE (#172). The server keeps each panel's pixels across visits, so the
// stack no longer restarts at every hop; the chips name the panels with their
// sub counts, and the picture follows the latest panel until one is pinned. The
// row is drawn ONLY when there is more than one panel: with one it would say
// what the badge already says, and every one-target night looks as it always
// did. Pinning a panel changes the URL the picture is fetched from AND the
// status the screen reads (see stackView.ts), so the badge and the channel strip
// describe the panel on screen. A panel the server released to stay under its
// memory budget is said once under the picture, because the count in its chip
// restarted from zero and nothing else on the screen would say why.

import { useState, type CSSProperties, type JSX } from "react";

import { backfillLabel, fmtIntegration, sessionStackImageUrl } from "../../../../api/sessionStack";
import { modeLabel } from "../../../../components/preview/SessionStack";
import { accessPhrase, useCanControlCapture } from "../../../../lib/caps";
import { fmtClock } from "../../../../lib/eta";
import { fmtCount } from "../../../../lib/gallery";
import { usePreview, useSeq, useStore, useTelemetryStale, useWsPhase } from "../../../../store";
import { ActionButton, Bar, Chip, EmptyCard, Mono } from "../../../ui";
import { explainLock } from "../../../shell/explain";
import { phaseOf, phaseWord } from "./phase";
import { latestPanel, panelLabels, useStackView, useSessionStackStatus } from "./stackView";
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

/** A button label allowed onto a second line. `.nx-btn-label` never wraps and
 *  cuts with an ellipsis, which over "1 ALREADY CAPTURED" would cut the one
 *  number the label is there to carry. */
const WRAP_LABEL: CSSProperties = {
  display: "block", whiteSpace: "normal", lineHeight: 1.3, textAlign: "center",
};

/** A count of subs and its noun: "1 sub", "1,284 subs". Shared by every
 *  surface that counts subs on the Now screen and the desktop column, and by
 *  the FILES sheet: "FILES · 1 SUBS" (#468) is the tell of a number nobody
 *  looked at. It lives here, and not in the FILES sheet, because that sheet is
 *  loaded on demand and this module is already on the first paint. */
export function subsPhrase(n: number): string {
  return `${fmtCount(n)} ${n === 1 ? "sub" : "subs"}`;
}

/** Before the first `GET /api/sequence/stack` answers there is no stack state,
 *  and "LIVE STACK IS OFF" is a claim about the rig, not about the request. */
export const UNANSWERED_HINT =
  "The rig has not answered about the stack yet, so whether it is already "
  + "building a picture is unknown. This is not the off state.";

export const UNANSWERED_START_REASON =
  "The rig has not said yet whether the stack is already running. "
  + "This clears when the first status update arrives.";

/** The sentence under a channel that fell back to the composite.
 *
 *  AN `<img>` ERROR IS NOT A 404. `onError` fires for a channel the server has
 *  no accumulator for, and equally for a dropped link, a truncated JPEG or a
 *  render that timed out. The strip already knows which of those is even
 *  possible, because the stack publishes a frame count per channel: zero frames
 *  is the one case where "nothing stacked yet" is a fact. With 42 Ha subs banked
 *  it told the operator the night had shot nothing - on the screen whose whole
 *  job is to say what the night has shot. */
export function channelFallbackNote(name: string, frames: number): string {
  return frames > 0
    ? `Could not load the ${name} frame (${subsPhrase(frames)} ${frames === 1 ? "is" : "are"} stacked) - showing the combined picture.`
    : `Nothing stacked in ${name} yet - showing the combined picture.`;
}

/** The sentence under the picture when the server released panels to stay under
 *  its memory budget. Null when it released none. */
export function evictedNote(names: readonly string[] | undefined): string | null {
  if (!names || names.length === 0) return null;
  return `${names.join(", ")} ${names.length === 1 ? "was" : "were"} released to stay `
    + "under the memory budget; a panel starts again from zero when the run returns to it.";
}

export function LiveStack({ height = 250 }: { height?: number }): JSX.Element {
  const { status, busy, error, answered, start } = useSessionStackStatus();
  const { channel, cssFilter, stretch, panel, setPanel } = useStackView();
  // Which (seq, channel) pair the server has already refused. Keyed by the pair
  // rather than a bare boolean so a new frame (seq moves) or a different chip
  // asks again by itself: the channel that was empty at 21:10 is the one the
  // run is filling.
  const [missingKey, setMissingKey] = useState<string | null>(null);
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

  // The chip row. `status.panels` is the whole stack's list whichever panel the
  // rest of the status describes; absent from an older server, which reads as
  // one panel and so no row.
  const panels = status?.panels ?? [];
  const labels = panelLabels(panels);
  const showPanelChips = panels.length > 1;
  // Following the latest, the chip lit is the one the latest frame fed.
  const litPanel = panel ?? latestPanel(panels)?.key ?? null;
  const panelWord = (status?.target ?? "") || "tonight's target";
  const evicted = evictedNote(status?.evicted);

  const viewKey = `${status?.seq ?? 0}|${channel ?? ""}|${panel ?? ""}`;
  const channelMissing = channel != null && missingKey === viewKey;
  // What is actually on screen, which is what everything below must describe.
  const shown = channelMissing ? null : channel;

  const stretchWord = stretch.toLowerCase();
  const modeBadge = shown
    ? `${shown} only · ${subsPhrase(status ? channelFrames(status, shown) : 0)} · ${stretchWord} stretch`
    : status && Array.isArray(status.channels)
      ? `${modeLabel(status)} · ${stretchWord} stretch`
      : answered ? "nothing stacked yet" : "reading the stack";

  const phase = phaseOf({
    state: seq.state,
    detail: seq.detail,
    scheduleState: seq.schedule?.state ?? null,
    busyLanes,
    focusRunning,
    frameStartedAtMs: seq.progress?.frame_started_at_ms ?? null,
    incident: incidents.length ? { pill: incidents[0].pill, color: incidents[0].color } : null,
  });

  // An ended run is counted, not trailed off: "stopped..." read as a stop still
  // under way, over a run that had ended with one sub banked.
  const ended = seq.state === "complete" || seq.state === "aborted" || seq.state === "error";
  const currentLine = ended
    ? `${seq.state === "complete" ? "complete" : phaseWord(phase)} · ${subsPhrase(seq.progress?.frames_done ?? 0)}`
    : seq.detail && /\[\d+\/\d+\]/.test(seq.detail)
      ? seq.detail
      : `${phaseWord(phase)}...`;

  const hfr = preview?.hfr;
  const hfrLine = typeof hfr === "number" ? hfr.toFixed(2) : "-";

  const startReason = !canCapture
    ? `Stacking needs ${accessPhrase("control.capture")}.`
    : answered ? null : UNANSWERED_START_REASON;

  // Drawn in both faces: over the picture in its corners, and under the empty
  // card in the frame's flow.
  const corners = (
    <>
      <span style={CORNER} data-testid="live-stack-current">{currentLine}</span>
      <span style={{ ...CORNER, color: "var(--text-dim)" }}>
        HFR <span style={{ color: "var(--text)" }}>{hfrLine}</span>
      </span>
    </>
  );
  const linkLostOverlay = linkLost && (
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
  );
  const subBar = (
    <div style={{ position: "absolute", left: 0, right: 0, bottom: 0 }}>
      <Bar value={sub.fraction ?? 0} height={3} label="Current sub" />
    </div>
  );
  const frameStyle: CSSProperties = {
    position: "relative", flexShrink: 0, borderRadius: 16,
    overflow: "hidden", border: "1px solid var(--line)", background: "#000",
  };

  return (
    <div data-testid="now-live-stack" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {showPanelChips && (
        <div
          data-testid="live-stack-panels"
          role="group"
          aria-label="Mosaic panels"
          style={{ display: "flex", gap: 5, overflowX: "auto", minWidth: 0 }}
        >
          {panels.map((p, i) => (
            <Chip
              key={p.key}
              active={litPanel === p.key}
              count={p.frames}
              onClick={() => setPanel(panel === p.key ? null : p.key)}
              data-testid={`panel-chip-${p.key}`}
            >
              {labels[i]}
            </Chip>
          ))}
        </div>
      )}
      {hasImage && status ? (
        <div data-testid="live-stack-frame" style={{ ...frameStyle, height }}>
          <img
            // The panel rides on the URL ONLY while one is pinned: following
            // the latest is the request this screen has always made.
            src={sessionStackImageUrl(status.seq, 1200, shown ?? undefined, panel ?? undefined)}
            onError={() => { if (channel) setMissingKey(viewKey); }}
            alt={shown
              ? `Live stack of ${panelWord}, ${shown} channel only`
              : `Live stack of ${panelWord}`}
            data-testid="live-stack-image"
            style={{
              position: "absolute", inset: 0, width: "100%", height: "100%",
              objectFit: "cover", opacity: 0.9, filter: cssFilter, transition: "filter .4s",
            }}
          />

          {linkLostOverlay}

          <div style={{
            position: "absolute", left: 10, top: 10, display: "flex", gap: 6, flexWrap: "wrap",
          }}>
            <span style={PILL} data-testid="live-stack-badge">
              LIVE STACK · {subsPhrase(frames)} · {fmtIntegration(status.integrated_s ?? 0)}
            </span>
            <span style={{ ...PILL, color: "var(--text-dim)" }} data-testid="live-stack-mode">
              {modeBadge}
            </span>
          </div>

          <div style={{
            position: "absolute", left: 10, right: 10, bottom: 10,
            display: "flex", justifyContent: "space-between", alignItems: "flex-end", gap: 8,
          }}>
            {corners}
          </div>

          {subBar}
        </div>
      ) : (
        <div
          data-testid="live-stack-frame"
          style={{
            ...frameStyle, minHeight: height, padding: 10,
            display: "flex", flexDirection: "column", justifyContent: "space-between", gap: 8,
          }}
        >
          <EmptyCard
            title={!answered ? "READING THE STACK"
              : status?.enabled ? "NOTHING STACKED YET" : "LIVE STACK IS OFF"}
            hint={!answered
              ? UNANSWERED_HINT
              : status?.enabled
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
                <span style={WRAP_LABEL}>
                  {available > 0
                    ? `STACK TONIGHT'S SUBS · ${available} ALREADY CAPTURED`
                    : "STACK TONIGHT'S SUBS"}
                </span>
              </ActionButton>
            }
            data-testid="live-stack-empty"
          />
          <div style={{
            display: "flex", justifyContent: "space-between", alignItems: "flex-end", gap: 8,
          }}>
            {corners}
          </div>

          {linkLostOverlay}
          {subBar}
        </div>
      )}

      {channelMissing && channel && (
        <span data-testid="channel-missing-note">
          <Mono size={10} tone="warn">
            {channelFallbackNote(channel, status ? channelFrames(status, channel) : 0)}
          </Mono>
        </span>
      )}
      {evicted && (
        <span data-testid="live-stack-evicted">
          <Mono size={10} tone="warn">{evicted}</Mono>
        </span>
      )}
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
