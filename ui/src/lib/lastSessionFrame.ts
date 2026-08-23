// lib/lastSessionFrame.ts — WHICH already-saved frame may stand in for the live
// one on the Capture stage, and when nothing may.
//
// THE GAP. The stage paints from `previews`, an in-memory ring filled only by
// WebSocket preview events received during THIS browser session. Open the
// Capture tab five minutes into a run and the ring is empty, so the stage shows
// the AstroDeck logo — for up to a full sub (180 s on a normal night) while the
// rig is imaging and has forty frames on disk. The rig has the picture; the
// browser has simply never been told about it.
//
// WHY THE RULE IS NARROW. The obvious fix — paint the newest file on disk — is
// worse than the bug. A frame from last week sitting where the live view goes
// is a lie about what the camera is doing right now, and the logo at least
// promises nothing. So a candidate has to pass BOTH tests below and anything
// else falls back to the empty state. Every ambiguity resolves towards the
// logo: a false negative costs one sub of waiting, a false positive costs
// trust in the stage.
//
// Store-free and React-free (and no `window` at module load, so it is loadable
// under `tsx` in Node): the hook that fetches lives in
// components/preview/useLastSessionFrame.ts and this is the half that decides.

import type { GalleryFrame, SequenceState } from "../types";
import { viewWidthFor } from "./frameView";

/**
 * Is the rig still imaging?
 *
 * "aborting" is in it for the reason SequenceState documents: abort awaits the
 * whole wind-down (~210 s worst case), so a client that treats it as terminal
 * blanks the stage over a rig that is still moving.
 *
 * "holding" is in it because a cloud hold is not a stop. sequence/engine.py
 * promotes any routine `running` publish to "holding" while the hold is up, so
 * the ONE field every client keys off changes under a run that is still
 * probing the sky on a timer and shooting hold darks. Leaving it out gave the
 * logo to a live run — and gave a DIFFERENT answer depending on when you
 * arrived, because a stand-in already on the stage when the hold began stayed
 * there. Two screens for one rig state is the worse half of that bug.
 *
 * "nina_native" is deliberately OUT. NINA is driving and the target it is
 * shooting is not published to us, so the target test below could never pass —
 * including it would only buy a library walk whose result is always refused.
 *
 * NOT the same predicate as SequenceRunStrip's, which still omits "holding" and
 * so still vanishes during a hold. That is its own defect in its own file.
 */
export function runIsLive(seq: SequenceState | null | undefined): boolean {
  const s = seq?.state;
  return s === "running" || s === "paused" || s === "holding" || s === "aborting";
}

/**
 * Target identity, loosely.
 *
 * The two names being compared come from different places and are written by
 * different hands: `sequence.target` is what the plan says, `frame.target` is
 * the FITS OBJECT header (falling back to the folder name). "M 31", "M31" and
 * "m-31" are one object, and a strict compare would refuse the stand-in on a
 * space. Loose in the direction that can only ADD candidates of the same
 * object, never of a different one — there is no pair of distinct targets that
 * collapses to the same key.
 */
export function targetKey(name: string | null | undefined): string {
  return String(name ?? "").toLowerCase().replace(/[^a-z0-9]+/g, "");
}

/**
 * How stale a frame may be and still be "the session that is running".
 *
 * A frame from the run in flight is at most one sub plus the engine's
 * between-frame work old, and even the expensive version of that — a meridian
 * flip, a re-centring solve and an autofocus back to back, then a 600 s sub —
 * is minutes. 45 minutes is slack on that, not a fit.
 *
 * It also has to be short enough to refuse the frame this whole module exists
 * to refuse. Last night's is a day out; an earlier run the same evening is the
 * only real contest, and 45 minutes puts most of those on the right side of the
 * line. The cost of being wrong the strict way is one sub of logo.
 *
 * KNOWN EDGE: a run PAUSED for longer than this shows the logo again, because
 * its newest frame really is that old. That is the safe direction and it is not
 * silently wrong — the stage says "No capture yet", which is true of this
 * browser session.
 */
export const SESSION_FRAME_MAX_AGE_S = 45 * 60;

/**
 * How far AHEAD of "now" a frame may be stamped before we stop believing the
 * clock. `server_now_ms` rides the status publish, so a frame written since the
 * last one is legitimately a couple of seconds in the future; a minute of slack
 * covers that and any small skew. Past that, the two clocks disagree by an
 * amount we cannot reason about, and guessing is not better than the logo.
 */
const FUTURE_SLACK_S = 60;

/**
 * The clock to measure `ts` against, in unix seconds.
 *
 * `frame.ts` is stamped by the RIG. Comparing it against the BROWSER's clock
 * compares two clocks that a relay-connected user has no reason to expect to
 * agree — and the rigs this runs on are appliances that can come up before NTP
 * settles. `progress.server_now_ms` is the rig's own epoch at the last publish,
 * so when it is there the comparison is one clock against itself.
 *
 * WHERE IT IS NOT THERE. `server_now_ms` has a single producer — compute_eta()
 * — and sequence/engine.py merges the ETA block only under
 * `if self.running and not self._aborting`, so an ABORTING run (which runIsLive
 * accepts on purpose) publishes progress with no rig clock in it and this falls
 * back to the browser's. That is the safe direction: a browser clock behind the
 * rig's makes frames look older and fails closed to the logo. Do not read the
 * fallback as "rare" — it is guaranteed for the whole ~210 s wind-down.
 *
 * AND WHERE IT IS FROZEN. This is a reading of a clock, not a measurement of
 * one: if the websocket dies mid-run the store keeps the last `sequence` it was
 * sent (store.ts's "sequence" event is the only writer), so `server_now_ms` and
 * the newest frame's `ts` freeze together and the age between them stays small
 * forever. The age gate cannot see that; the LINK-DOWN gate in
 * useLastSessionFrame is what stops it, and it has to stay there.
 */
function sessionNowS(seq: SequenceState | null | undefined, clientNowMs: number): number {
  const srv = seq?.progress?.server_now_ms;
  const ms = typeof srv === "number" && Number.isFinite(srv) && srv > 0 ? srv : clientNowMs;
  return ms / 1000;
}

/**
 * The newest frame in `frames` that belongs to the run in flight, or null.
 *
 * Null is the ordinary answer and never an error: an idle rig, a run that has
 * not written a frame yet, a run whose target we do not know, a listing whose
 * newest entry is last week's. Every one of those means "show the empty state".
 *
 * No frame_type filter, on purpose. Darks and flats are written under their own
 * target/folder, so the target test already excludes them; a rule that also
 * demanded "Light" would silently refuse a run whose header AstroDeck could not
 * read, for no gain.
 */
export function pickSessionFrame(
  frames: readonly GalleryFrame[] | null | undefined,
  seq: SequenceState | null | undefined,
  clientNowMs: number,
): GalleryFrame | null {
  if (!runIsLive(seq)) return null;
  const want = targetKey(seq?.target);
  // An untargeted run matches nothing. "" == "" would otherwise make every
  // header-unreadable frame in the library a candidate for every run.
  if (!want) return null;

  const nowS = sessionNowS(seq, clientNowMs);
  let best: GalleryFrame | null = null;
  for (const f of frames ?? []) {
    if (!f) continue;
    const ts = f.ts;
    if (typeof ts !== "number" || !Number.isFinite(ts)) continue;
    if (!f.path) continue;
    if (targetKey(f.target) !== want) continue;
    if (nowS - ts > SESSION_FRAME_MAX_AGE_S) continue;
    if (ts - nowS > FUTURE_SLACK_S) continue;
    // The server sorts newest first, but this is the thing standing between a
    // stale file and the live view — it does its own arithmetic.
    if (!best || ts > best.ts) best = f;
  }
  return best;
}

/**
 * The chip over the stand-in.
 *
 * Two facts, both of which the operator cannot get from the picture itself:
 * that it is NOT the live frame, and when it was taken — a 40-second-old
 * stand-in and a 40-minute-old one look identical and mean very different
 * things about the run.
 *
 * `local_clock` is the OBSERVATORY's clock, sent by the server. Formatting `ts`
 * here would print the viewer's timezone over a rig in another one; see the
 * note on GalleryFrame.local_clock.
 */
export function lastFrameLabel(f: GalleryFrame): string {
  const clock = (f.local_clock || "").trim();
  return clock ? `Last saved frame, ${clock} — not live` : "Last saved frame — not live";
}

/** Alt text: what the picture IS, for someone who cannot see it. */
export function lastFrameAlt(f: GalleryFrame): string {
  const clock = (f.local_clock || "").trim();
  const target = (f.target || "").trim();
  return `Last saved frame${target ? ` of ${target}` : ""}${clock ? `, ${clock}` : ""}` +
    " — already captured, not the live view";
}

/**
 * Stage width, in CSS px, to assume when the browser has not laid the stage out
 * yet. Only reached before first layout (or under a test harness with no layout
 * engine at all) — a real stage has a width by the time the fetch resolves.
 *
 * 640 rather than something small: the failure to avoid is asking for fewer
 * pixels than the screen will show, and over-asking costs one cached render.
 */
const UNMEASURED_STAGE_CSS_W = 640;

/**
 * The widest frame the height bound below is allowed to assume.
 *
 * The stand-in's aspect ratio is unknown at request time — that is the whole
 * reason this function exists instead of `neededDeviceWidth` — so bounding by
 * height needs an assumption, and the assumption has to be one that cannot
 * UNDER-ask for a real frame. Every sensor this runs against is between 1:1 and
 * 3:2 (IMX533 1.00, IMX571/455/294 1.50); 2:1 is a full stop past the widest of
 * them. A frame wider than 2:1 — a stitched panorama dropped into the capture
 * library by hand — would get a slightly soft stand-in for one sub. Nothing the
 * sequencer writes is that shape.
 */
const MAX_STANDIN_ASPECT = 2;

/**
 * How wide a render to ask `/api/gallery/view` for, given the stage's measured
 * CSS box and the display's device-pixel ratio.
 *
 * NOT the gallery thumbnail, and the owner has ruled on exactly this: "for the
 * gallery - the thumbnail size I specified is ok. I did not make any
 * declaration for capture previews. those need to be high fidelity." The tile's
 * 256 px is a SCANNING size; this picture is standing in for the frame someone
 * judges focus from.
 *
 * BOUNDED BY BOTH AXES, because the empty-state stage is not square-ish: it is
 * `W x 380` (PreviewStage gives it `minHeight: 380`, no aspect-ratio, and both
 * its children are absolutely positioned, so 380 is the height at every
 * viewport), and the picture is `object-contain` inside it. On any stage wider
 * than ~570 CSS px the picture is therefore HEIGHT-limited, and sizing off the
 * width alone over-asked by up to 4.8x in bytes — measured: a 1090x380 stage on
 * a 2x screen asked for the 2560 rung (603 KB) to paint 1138 device px, which
 * the 1280 rung (126 KB) covers exactly. Every rung is a separate disk-cache
 * key AND a separate cold render (gallery.PRECOMPUTE_WIDTHS is (256,), so no
 * view rung is ever pre-warmed), and the extra payload crosses a relay link
 * while the rig is imaging.
 *
 * Still over-asking, deliberately, just by less: the bound uses the widest
 * plausible frame rather than the real one, so it rounds UP to the same rung a
 * 1.5:1 frame would need and never below it. `cssHeight <= 0` (before first
 * layout, or a harness with no layout engine) drops the height term entirely
 * rather than guessing one — the failure to avoid is asking for fewer pixels
 * than the screen will show.
 */
export function placeholderViewWidth(cssWidth: number, cssHeight: number, dpr: number): number {
  const w = Number.isFinite(cssWidth) && cssWidth > 0 ? cssWidth : UNMEASURED_STAGE_CSS_W;
  const h = Number.isFinite(cssHeight) && cssHeight > 0 ? cssHeight : 0;
  const ratio = Number.isFinite(dpr) && dpr > 1 ? dpr : 1;
  const fitted = h > 0 ? Math.min(w, h * MAX_STANDIN_ASPECT) : w;
  return viewWidthFor(Math.ceil(fitted * ratio));
}
