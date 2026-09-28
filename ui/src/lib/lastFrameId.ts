// lastFrameId.ts - which frame the Monitor's LAST FRAME tile shows (#399).
//
// The tile has two sources for "the newest frame". The live `preview` event
// arrives over the socket once per frame, and the `preview_id` in
// GET /api/monitor/snapshot is the server's own answer. On a phone over the
// relay the live event is the one that goes missing: a reconnect never
// restored it, and the relay's overflow policy could drop it. So the tile took
// the live event first and the snapshot only as a cold-load fallback, and a
// page that missed one event showed NO FRAME YET (or an older frame) for as
// long as nothing new arrived, while `frames_done` kept climbing beside it.
//
// Preview ids only ever increase on the server (hub.preview_seq), so the newer
// of the two is always the truthful one, whichever path delivered it.

/** The id to show: the newer of the live preview's id and the id the last
 *  snapshot reported. `null` only when neither source has ever named a frame. */
export function newestPreviewId(
  live: number | null | undefined,
  cold: number | null | undefined,
): number | null {
  const a = typeof live === "number" && Number.isFinite(live) ? live : null;
  const b = typeof cold === "number" && Number.isFinite(cold) ? cold : null;
  if (a == null) return b;
  if (b == null) return a;
  return Math.max(a, b);
}

/** Whether the tile's frame is the live preview itself, so the live event's
 *  HFR, star count and LIVE badge describe what is on screen. When the
 *  snapshot named a newer frame than the last live event, the live event's
 *  numbers belong to a different, older frame and must not be shown beside it. */
export function showingLivePreview(
  shown: number | null,
  live: number | null | undefined,
): boolean {
  return shown != null && typeof live === "number" && shown === live;
}
