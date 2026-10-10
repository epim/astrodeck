// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// sunWatch.ts - what the sun watch's published state means to the person at the
// rig (#894), as a PURE function. No React, no DOM: npx-tsx testable.
//
// WHY THIS EXISTS. The server's sun watch is the net under a tube the Sun is
// coming TO. It has two states in which it cannot protect the tube, and until
// #894 neither reached a screen:
//
//   * BLIND: it cannot read the mount, so it cannot see the Sun closing on it.
//   * POSITION UNKNOWN (owner ruling 4B, #888): the rig's position latch is
//     set - the mount reports its home position wherever the tube is - so the
//     net judges no approach and parks nothing, because a park is aimed from
//     that position. This is the ORDINARY start of a night after any power-up.
//
// A rig with no alert sink configured showed a quiet UI through both. A third
// state is "off": the net is not running while a mount is connected.
//
// WHAT THE COPY MAY AND MAY NOT SAY.
//
//  * It names what the net cannot do, since when, and the next step. A line
//    that restates what the screen already shows would be slop; the since-time
//    and the step are the information.
//  * It never advises a slew or a go-to while the position is unknown (#850):
//    a move is aimed from a position nobody vouches for. The step is the safe
//    order the server's own alert gives (`mount_offset.POSITION_UNKNOWN_SAFE_ORDER`):
//    TRUST POSITION when the tube is at home, else a pad key held to bring it
//    home by eye first, then TRUST POSITION.
//  * It does not say whether the Sun is up. The state is times and booleans so
//    a viewer can read it; the Sun's height is a computation on the site and
//    the site is not a viewer's. So the step says "if the Sun is up".
//  * Hyphens, never em-dashes: the new UI forbids them.
//
// WHICH ONE APPLIES. The server clears one when the other begins, so at most one
// is true at a time; the check order below is the answer if a frame ever
// carries both: the position latch is the cause, blindness the symptom.

import type { SunWatchState } from "../types";
import { TRUST_POSITION_LABEL } from "./slewController";

export type SunWatchKind = "position_unknown" | "blind" | "off";

export interface SunWatchNotice {
  kind: SunWatchKind;
  /** 2: the tube has no protection from the Sun now (blind, standing down).
   *  1: worth knowing, nothing is unprotected that was not chosen (off). */
  tier: 1 | 2;
  /** Server epoch seconds the state began, or null (off has none). */
  since: number | null;
  /** What the net cannot do, and since when. */
  text: string;
  /** The next step, or null when there is none to give. */
  action: string | null;
}

/** "03:14", the browser's local clock, zero padded. A time of day carries no
 *  site: it is the clock on the wall beside the person reading it. */
export function fmtSinceClock(epochS: number): string {
  const d = new Date(epochS * 1000);
  if (Number.isNaN(d.getTime())) return "--:--";
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

/** "under 1 min", "12 min", "2 h 5 min". Clamped at zero: a browser clock a
 *  little behind the server's must not print a negative age. */
export function fmtElapsed(seconds: number): string {
  const mins = Math.floor(Math.max(0, seconds) / 60);
  if (mins < 1) return "under 1 min";
  if (mins < 120) return `${mins} min`;
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return m === 0 ? `${h} h` : `${h} h ${m} min`;
}

function sinceClause(since: number | null, nowS: number): string {
  if (since == null) return "";
  return ` since ${fmtSinceClock(since)} (${fmtElapsed(nowS - since)})`;
}

const COVER = "Cover the tube if the Sun is up.";

/** The position-unknown step. The same safe order as the mount sheet and the
 *  server's alert; `TRUST_POSITION_LABEL` is the one label both roots share. */
export const SUN_WATCH_POSITION_STEP =
  `${COVER} If the tube is at home, ${TRUST_POSITION_LABEL}. If it is not, hold a `
  + `pad key to bring it home by eye first, then ${TRUST_POSITION_LABEL}.`;

export const SUN_WATCH_BLIND_STEP = `Check the mount's power and cable. ${COVER}`;

/** The notice for a published state, or null when there is nothing to say.
 *
 *  `sw` ABSENT (a server older than #894, or a hub with no net attached) says
 *  nothing: not knowing is not "off", and a banner claiming the net is not
 *  running on the strength of a missing key would be the false alarm that
 *  teaches people to ignore the true one. `mountConnected` only gates "off":
 *  a net that is not running while no mount is connected protects nothing that
 *  is not already unplugged. `nowS` is epoch seconds. */
export function sunWatchNotice(
  sw: SunWatchState | null | undefined,
  mountConnected: boolean,
  nowS: number,
): SunWatchNotice | null {
  if (!sw) return null;
  if (sw.position_unknown) {
    return {
      kind: "position_unknown",
      tier: 2,
      since: sw.position_unknown_since,
      text: `Sun watch is standing down${sinceClause(sw.position_unknown_since, nowS)}: `
        + "the mount's position is unknown, so it will not park the tube.",
      action: SUN_WATCH_POSITION_STEP,
    };
  }
  if (sw.blind) {
    return {
      kind: "blind",
      tier: 2,
      since: sw.blind_since,
      text: `Sun watch is blind${sinceClause(sw.blind_since, nowS)}: `
        + "it cannot read the mount, so it cannot see the Sun closing on the tube.",
      action: SUN_WATCH_BLIND_STEP,
    };
  }
  if (!sw.armed && mountConnected) {
    return {
      kind: "off",
      tier: 1,
      since: null,
      text: "Sun watch is not running: nothing will park the tube if the Sun closes on it.",
      action: null,
    };
  }
  return null;
}
