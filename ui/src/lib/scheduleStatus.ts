// scheduleStatus.ts — pure plain-language copy for the Monitor/Sequence "why
// nothing is happening" line (NOV-8). Turns the engine's sequence.schedule /
// sequence.live blocks into ONE calm sentence, shared by MonitorView (prominent
// header banner) and SequenceView (ScheduleChip). No React, no DOM — unit-tested
// via `npx tsx`. The server `reason` strings are terse (schedule.py gating_status:
// "below start altitude (30 deg)"); novice copy is composed HERE off `state` +
// the numeric fields, extracting the gate degrees from `reason` when present.
//
// That composition knows a CLOSED LIST of waits: an altitude gate and a window
// that has not opened. A cause newer than the list must not be mapped onto an
// older one (#488: a mosaic's meridian wait read as the window). So the
// meridian wait has its own sentence, and any other reason prints the server's
// own words; neither carries a time (spec 6.9).

import type { SequenceState } from "../types";
import { fmtTime } from "./visibility";

export type ScheduleTone = "calm" | "warn";
export interface ScheduleStatus {
  tone: ScheduleTone;
  text: string;
}

/** Plain-language coarse wait: "under a minute" / "47 min" / "1 hr 12 min".
 *  (fmtDuration would emit "47m 0s" — wrong for a calm, novice-facing sentence.) */
export function fmtWaitApprox(seconds: number): string {
  const t = Math.max(0, Math.round(seconds));
  if (t < 60) return "under a minute";
  if (t < 3600) return `${Math.round(t / 60)} min`;
  const h = Math.floor(t / 3600);
  const m = Math.round((t % 3600) / 60);
  return m > 0 ? `${h} hr ${m} min` : `${h} hr`;
}

/** Extract the start-gate degrees from a server reason string, or null.
 *  "below start altitude (30 deg)" -> 30; "never rises above 30 deg" -> 30. */
export function parseGateDeg(reason: string): number | null {
  const m = /(\d+(?:\.\d+)?)\s*deg/.exec(reason);
  return m ? Number(m[1]) : null;
}

/** The line for a mosaic held on the meridian rule (spec 5.7, 5.10). No
 *  countdown and no clock, ever: the crossing it waits for is set by the site,
 *  and a time beside it would give a viewer the site's longitude (6.9). */
export const MERIDIAN_WAIT_TEXT = "Waiting for the meridian, so the mosaic changes pier side once.";

/** The engine's meridian wait (engine.py `_publish_group_wait`: "the mosaic
 *  waits for the meridian, so it changes pier side once"). Read off the words,
 *  because the schedule block is all this formatter is handed; no other wait
 *  the engine publishes names the meridian. */
function isMeridianWait(reason: string): boolean {
  return /\bmeridian\b/i.test(reason);
}

function waitingText(
  schedule: NonNullable<SequenceState["schedule"]>,
  nowUnix: number,
): string {
  const { reason, eta_s, start_ts } = schedule;
  // A MERIDIAN WAIT READS AS ONE (#488). It used to fall through to the window
  // sentence below, so a mosaic held for the crossing said "Waiting for the
  // observing window to open." with the window wide open, pointing the operator
  // at the schedule rather than the flip rule. Asked first, before any time is
  // worked out, so no number the block carries can reach the line.
  if (isMeridianWait(reason)) return MERIDIAN_WAIT_TEXT;

  // Resolve the countdown + absolute clock. Clock case (window not open yet):
  // start_ts is in the future -> a LIVE remaining that ticks down. Altitude case
  // (engine.py case 4): start_ts already passed -> fall back to eta_s + now.
  let remaining: number;
  let clockUnix: number | null;
  if (start_ts != null && start_ts > nowUnix) {
    remaining = start_ts - nowUnix;
    clockUnix = start_ts;
  } else if (eta_s > 0) {
    remaining = eta_s;
    clockUnix = nowUnix + eta_s;
  } else {
    remaining = 0;
    clockUnix = null;
  }

  const gate = parseGateDeg(reason);
  const isAltitude = /altitude/i.test(reason) || (start_ts != null && start_ts <= nowUnix);
  let lead: string;
  if (isAltitude) {
    lead = gate != null
      ? `Waiting for your target to rise above ${gate}°`
      : `Waiting for your target to rise high enough`;
  } else if (start_ts != null && start_ts > nowUnix) {
    // Only a window that has NOT opened is worded as the window (#488).
    lead = `Waiting for the observing window to open`;
  } else {
    // ANY OTHER REASON IS THE SERVER'S OWN WORDS (#488): a limit, a panel set
    // aside for now, a moon or hour-angle constraint. Mapping them onto the
    // window sentence made the line false for every cause newer than it. And
    // no time beside them: this formatter cannot tell whether an unknown
    // wait's time is set by the site (the hour-angle one's is, by the
    // longitude), and every wait the engine added since publishes none
    // (`_publish_group_wait`: eta_s 0).
    const words = reason.trim().replace(/[.\s]+$/, "");
    return words ? `Waiting: ${words}.` : "Waiting.";
  }

  if (clockUnix != null) {
    return `${lead} at ${fmtTime(clockUnix)}, about ${fmtWaitApprox(remaining)}.`;
  }
  if (remaining > 0) {
    return `${lead}, about ${fmtWaitApprox(remaining)}.`;
  }
  return `${lead}.`;
}

export function formatScheduleStatus(
  schedule: SequenceState["schedule"] | undefined | null,
  live: SequenceState["live"] | undefined | null,
  nowUnix: number,
): ScheduleStatus | null {
  // Schedule states dominate — they explain why imaging isn't running at all.
  if (schedule) {
    switch (schedule.state) {
      case "window_closed":
        return { tone: "warn", text: "Tonight's observing window has closed for this target." };
      case "never_rises": {
        const gate = parseGateDeg(schedule.reason);
        return {
          tone: "warn",
          text: gate != null
            ? `This target never rises above ${gate}° from your site tonight.`
            : `This target never rises high enough from your site tonight.`,
        };
      }
      case "waiting":
        return { tone: "calm", text: waitingText(schedule, nowUnix) };
      case "ready":
        break; // fall through to the meridian heads-up
    }
  }

  // Meridian flip heads-up — only mid-imaging (schedule ready/absent). The engine
  // attaches meridian_eta_s ONLY within plan.meridian_flip_warn_min and always
  // > 0 (engine.py _live_block), so no client lead gate is needed.
  const m = live?.meridian_eta_s;
  if (m != null && Number.isFinite(m)) {
    return {
      tone: "calm",
      text: m > 0
        ? `Meridian flip in ${fmtWaitApprox(m)} — imaging will pause briefly.`
        : `Meridian flip starting — imaging will pause briefly.`,
    };
  }

  return null;
}
