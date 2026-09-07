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
}

export const getSessionStack = (): Promise<SessionStackStatus> =>
  api.get<SessionStackStatus>("/api/sequence/stack");

export const startSessionStack = (): Promise<SessionStackStatus> =>
  api.post<SessionStackStatus>("/api/sequence/stack/start");

export const stopSessionStack = (): Promise<SessionStackStatus> =>
  api.post<SessionStackStatus>("/api/sequence/stack/stop");

export const resetSessionStack = (): Promise<SessionStackStatus> =>
  api.post<SessionStackStatus>("/api/sequence/stack/reset");

/** URL for the composite JPEG.
 *
 *  `seq` is in the query string ONLY as a cache key: the route itself answers
 *  `no-store`, but an <img> whose src never changes is never re-requested at
 *  all, so without this the panel would show the first frame of the run for the
 *  rest of the night. */
export const sessionStackImageUrl = (seq: number, size = 1200): string =>
  u(`/api/sequence/stack/preview.jpg?size=${Math.round(size)}&seq=${seq}`);

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
