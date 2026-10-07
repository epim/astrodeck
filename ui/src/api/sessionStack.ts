// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// api/sessionStack.ts: the run's colour composite (server imaging/sessionstack.py).
//
// The monitor page has always shown the LAST SUB: one frame, one filter, and
// whatever the sky did in those five minutes. This is the other half: every
// frame the sequence has accepted, stacked per filter and composited into one
// colour image, so the question "is this actually going to be a picture?" has
// an answer before the night ends.
//
// Four routes, one image. The status route is the cheap one and carries `seq`,
// which changes only when a frame has actually landed; the image URL is built
// from that, so the browser refetches when there is something new and not on a
// timer.

import { api } from "../api";
import { u } from "../lib/base";

/** One filter's contribution to the composite. */
export interface SessionStackChannel {
  /** Composite channel the filter folded onto: R/G/B/L/Ha/Oiii/Sii. */
  channel: string;
  frames: number;
  integrated_s: number;
  /** Subs the stacker itself refused (no stars, drifted too far). */
  rejected: number;
}

/** How the "stack the subs I already shot" pass is getting on.
 *
 *  Switching the stack on used to mean "from the next frame", so arming it at
 *  2am showed two of the night's ninety subs. The backfill walks the run's own
 *  ledger and folds the earlier ACCEPTED ones in; it reads and registers every
 *  one off disk, so on a full night it is minutes of work and needs a counter
 *  rather than a spinner. */
export interface SessionStackBackfill {
  running: boolean;
  /** Frames this pass set out to read. */
  total: number;
  /** Frames considered so far = added + skipped + failed. `done === total`
   *  is completion whatever happened to each frame. */
  done: number;
  added: number;
  /** Already in the stack - the live path or an earlier pass took them. */
  skipped: number;
  /** Unreadable, or refused by the stacker (no stars, drifted off field). */
  failed: number;
  /** The channel the frame in hand landed on, for a live caption. */
  channel: string;
  /** Set when the PASS died; one bad frame only bumps `failed`. */
  error: string;
  started_ts: number | null;
  finished_ts: number | null;
  /** Subs of this run that are not in the stack yet - what pressing it now
   *  would read. Recomputed on every poll from the in-memory ledger. */
  available: number;
}

/** One mosaic panel's stack. The server keeps one picture per panel and keeps
 *  it across visits (#172); a one-target night has exactly one of these. */
export interface SessionStackPanel {
  /** What to send back as `?panel=`: the target's id, or its name when the
   *  frames arrived without one. Opaque to the client. */
  key: string;
  /** The name to show. Not unique: two targets can share one. */
  target: string;
  /** Subs stacked in this panel, counted as the top-level `frames` is. */
  frames: number;
  integrated_s: number;
  /** The stack's change counter at this panel's last frame. Counters never
   *  repeat across panels, so the panel with the highest one is the latest. */
  seq: number;
}

export interface SessionStackStatus {
  enabled: boolean;
  /** What the frames are of. Empty before the first accepted frame. */
  target: string;
  /** Bumps on every accepted frame. The client's "there is a new picture" signal. */
  seq: number;
  channels: SessionStackChannel[];
  frames: number;
  integrated_s: number;
  rejected: number;
  /** "rgb" | "narrowband" | "mono" | null (nothing stacked yet). */
  mode: string | null;
  /** Binning applied before accumulating (memory policy; 2 or more). */
  downsample: number;
  has_image: boolean;
  render_age_s: number | null;
  backfill: SessionStackBackfill;
  /** Every panel the stack holds, in the order they were first seen, whichever
   *  panel the other fields describe. Optional: an older server sends none, and
   *  a client must read that as "one panel, no chips". */
  panels?: SessionStackPanel[];
  /** Names of panels the server released to stay under its memory budget. A
   *  name here can also be in `panels`: that panel came back and started again
   *  from zero. */
  evicted?: string[];
}

/** The stack's status. Every field but `panels` and `evicted` describes ONE
 *  panel: the one named, or the latest when none is. The panel is appended to
 *  the query only when given, so the plain request is the one this app has
 *  always made. */
export const getSessionStack = (panel?: string | null): Promise<SessionStackStatus> =>
  api.get<SessionStackStatus>(
    "/api/sequence/stack" + (panel ? `?panel=${encodeURIComponent(panel)}` : ""));

/** Switch the stack on. `backfill` also folds in the subs this run has already
 *  accepted - off by default on the server, because it is minutes of disk on a
 *  full night and a switch must not do that unasked. */
export const startSessionStack = (backfill = false): Promise<SessionStackStatus> =>
  api.post<SessionStackStatus>(
    `/api/sequence/stack/start${backfill ? "?backfill=true" : ""}`);

/** Catch an ALREADY-RUNNING stack up with the run's earlier subs. Separate from
 *  `start` so reaching it does not mean switching off and on again, which would
 *  throw away everything stacked since. */
export const backfillSessionStack = (): Promise<SessionStackStatus> =>
  api.post<SessionStackStatus>("/api/sequence/stack/backfill");

export const stopSessionStack = (): Promise<SessionStackStatus> =>
  api.post<SessionStackStatus>("/api/sequence/stack/stop");

export const resetSessionStack = (): Promise<SessionStackStatus> =>
  api.post<SessionStackStatus>("/api/sequence/stack/reset");

/** URL for the composite JPEG, or for ONE CHANNEL of it (D-SES-1).
 *
 *  `seq` is in the query string ONLY as a cache key: the route itself answers
 *  `no-store`, but an <img> whose src never changes is never re-requested at
 *  all, so without this the panel would show the first frame of the run for the
 *  rest of the night.
 *
 *  `channel` asks the server for that channel's OWN accumulator instead of the
 *  composite (`imaging/sessionstack.py:858-935 channel_preview`). It replaces
 *  the client-side tint, which only ever coloured the composite and so showed
 *  the same picture under three different labels.
 *
 *  THE PARAMETER IS APPENDED ONLY WHEN A CHANNEL IS GIVEN, so the composite URL
 *  stays byte-identical to the one this app has always built. An empty
 *  `?channel=` would mean the same thing to the server and a different thing to
 *  every browser cache, and re-fetching every stack image on every client is a
 *  real cost for a parameter that says nothing.
 *
 *  PASS THE STACKER'S OWN KEY (`status.channels[].channel`: R/G/B/L/Ha/Oiii/Sii),
 *  not the wheel's filter name. The route does resolve an operator's name
 *  through `channel_for` ("H-alpha" -> "Ha", and the resolved key rides back on
 *  `X-Stack-Channel`), but a filter with no mapping folds onto L - so a chip
 *  built from a wheel name can 404 or, worse, quietly show L's picture.
 *
 *  404 when nothing is stacked in that channel yet, with its own body text. An
 *  `<img>` cannot read a status code, so a caller has to notice through
 *  `onError` and must NOT silently fall back to the composite: the badge and the
 *  picture would then disagree, which is the defect this parameter exists to
 *  end.
 *
 *  `panel` asks for one mosaic panel's own stack (#172) instead of the latest
 *  panel's. It follows the channel rule exactly: APPENDED ONLY WHEN GIVEN, so
 *  the URL of a client that follows the latest panel (and every one-target
 *  night) stays byte-identical to the one this app has always built. Pass the
 *  panel's KEY (`status.panels[].key`), and the `seq` of THAT panel's status:
 *  the cache key has to move when this panel's picture does, not when some
 *  other panel's frame lands. */
export const sessionStackImageUrl = (
  seq: number,
  size = 1200,
  channel?: string,
  panel?: string,
): string =>
  u(`/api/sequence/stack/preview.jpg?size=${Math.round(size)}&seq=${seq}`
    + (channel ? `&channel=${encodeURIComponent(channel)}` : "")
    + (panel ? `&panel=${encodeURIComponent(panel)}` : ""));

/** "3h 12m" / "48m" / "40s": integration time, at the precision it is worth. */
export function fmtIntegration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

/** What the composite is made of, for a caption: "R·G·B" / "Ha·Oiii". */
export function channelSummary(channels: SessionStackChannel[]): string {
  return channels.map((c) => c.channel).join("·");
}

/** The backfill in one line, or null when there is nothing to say.
 *
 *  Three states worth distinguishing and one that is not: reading (a count, so
 *  a stalled pass is visible as a number that stops moving), finished with a
 *  tally, and stopped early. A pass that added everything it read says so
 *  briefly and then has nothing more to contribute - the frame count and the
 *  channel list above it are the real result. */
export function backfillLabel(b: SessionStackBackfill | undefined): string | null {
  if (!b) return null;
  if (b.running) {
    const of = b.total ? ` of ${b.total}` : "";
    return `Stacking earlier subs: ${b.done}${of}`;
  }
  if (b.error) {
    // "stopped" is the plain "you switched it off or reset it" case and the
    // sentence already says so; anything else is a reason worth printing.
    const why = b.error === "stopped" ? "" : ` (${b.error})`;
    return `Earlier subs: stopped at ${b.done} of ${b.total}${why}`;
  }
  if (!b.total) return null;
  const parts = [`${b.added} earlier sub${b.added === 1 ? "" : "s"} stacked`];
  if (b.skipped) parts.push(`${b.skipped} already in`);
  if (b.failed) parts.push(`${b.failed} unusable`);
  return parts.join(" · ");
}
