// StallStrip.tsx - "is anything still landing on disk?", and what to do when
// the answer is no.
//
// TWO CLOCKS, ON PURPOSE (#206, `inventory-session-monitor.md` §4.8):
//
//   frameAgeMs   a preview JPEG arrived here. A claim about PICTURES. It drives
//                the LIVE badge on the last-frame tile.
//   captureAgeS  the RIG's own frames_done advanced. A claim about the CAMERA.
//                It drives this alarm, and only this one.
//
// "Conflating them made a slow relay dropping preview JPEGs read as 'CAPTURE
// STALLED' over a perfectly healthy camera." This file reads `liveness.capture`
// and never `liveness.frame`; the LIVE badge is the last-frame tile's business.
//
// `stallLevel()` gates strictly on `state === "running"` - a fix for a COMPLETE
// run showing "CAPTURE STALLED?" for minutes. That gate is not relaxed here.

import type { JSX } from "react";
import { useLiveness, useLogs, useSeq } from "../../../../store";
import { stallLevel } from "../../../../lib/eta";
import { diagnoseFailure, runFailureLog } from "../../../../lib/troubleshoot";
import { fmtLogTime } from "../../../../lib/logFormat";
import { fmtDuration } from "../../../lib/format";
import { ActionButton, Card, Mono } from "../../../ui";
import { nav } from "../../../router";

/** The `help` sheet, deep-linked at a topic (plan §1: `#/settings/help?topic=`).
 *  `nav` replaces `store.openHelp(topic)` in the new UI; the legacy deep-link
 *  keeps working through `legacyBridge.ts`. */
export function openHelpTopic(topic: string): void {
  nav.go(`/settings/help?topic=${encodeURIComponent(topic)}`);
}

export function StallStrip({ nowMs }: { nowMs: number }): JSX.Element | null {
  const seq = useSeq();
  const liveness = useLiveness();

  const captureAgeS = liveness.capture != null ? (nowMs - liveness.capture) / 1000 : null;
  if (captureAgeS == null) return null;

  const curExp = seq.progress?.current_exposure_s ?? 0;
  const level = stallLevel(seq.state, captureAgeS, curExp);
  const hard = level === "red";
  const soft = level === "amber";

  // A stall has no `detail` of its own; when the engine HAS named the step that
  // is wedged, that name is what has a guide behind it. No name, no button -
  // there is no page to open, and a "How to fix" that opens the index would be
  // a promise the screen cannot keep.
  const diag = hard ? diagnoseFailure(seq.detail, { state: seq.state }) : null;

  return (
    <div
      data-testid="monitor-stall"
      data-level={level}
      style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}
    >
      <Mono size={11} tone={hard ? "bad" : soft ? "warn" : "dim"}>
        {hard && "CAPTURE STALLED? "}
        last frame {fmtDuration(captureAgeS)} ago
      </Mono>
      {diag?.topic && (
        <ActionButton
          kind="secondary"
          onPress={() => openHelpTopic(diag.topic as string)}
          data-testid="stall-help"
        >
          HOW TO FIX
        </ActionButton>
      )}
    </div>
  );
}

/** The end-of-run card. A run the operator held ABORT to stop is NOT a fault:
 *  `diagnoseFailure` discriminates on `detail === "sequence aborted"` (exact,
 *  and deliberately NOT `end_reason`, "because the engine's state dict merges
 *  and never clears `end_reason` between runs"), and the user-abort branch gets
 *  no fix line, no Help deep-link, no red, and no log excerpt hunting for a
 *  cause that never was. */
export function FailureCard({ runStartedAtS }: { runStartedAtS: number | null }): JSX.Element | null {
  const seq = useSeq();
  const logs = useLogs();
  const state = seq.state;
  if (state !== "error" && state !== "aborted") return null;

  const progress = seq.progress;
  const diag = diagnoseFailure(seq.detail, {
    state,
    framesDone: progress?.frames_done,
    framesTotal: progress?.frames_total,
  });
  const byUser = !!diag.userInitiated;
  const lines = byUser ? [] : runFailureLog(logs, runStartedAtS);

  return (
    <Card tone={byUser ? "default" : "accent"} padding={12} data-testid="monitor-failure">
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <span style={{
          fontFamily: '"Chakra Petch", sans-serif', fontWeight: 600, fontSize: 12,
          letterSpacing: ".12em", textTransform: "uppercase",
          color: byUser ? "var(--text)" : "var(--bad)",
        }}>
          {byUser ? diag.title : state === "error" ? "Sequence failed" : "Sequence aborted"}
        </span>
        <span style={{ fontSize: 12.5, lineHeight: 1.5, color: "var(--text-dim)" }}>
          {diag.cause}{diag.fix ? ` ${diag.fix}` : ""}
        </span>
        {lines.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            {lines.map((l, i) => (
              <Mono key={`${l.ts}-${i}`} size={10} tone="dim">
                {fmtLogTime(l.ts)} {l.data.message}
              </Mono>
            ))}
          </div>
        )}
        {diag.topic && (
          <ActionButton
            kind="secondary"
            onPress={() => openHelpTopic(diag.topic as string)}
            data-testid="failure-help"
          >
            HOW TO FIX
          </ActionButton>
        )}
      </div>
    </Card>
  );
}
