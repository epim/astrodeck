// lastFrame.ts - the result card's promote state (D-SES-4).
//
// Its own module rather than a block inside `captureGate.ts` because that file
// is PURE (gate inputs in, sentences out, no React and no fetch) and this one
// is the opposite: it holds the buffer read, the write, the three refusals and
// the lock. Folding them together would put a fetch behind every gate call the
// capture screen makes.
//
// WHAT IT IS FOR. `POST /api/capture` decides at shutter time whether a frame
// is kept. When the answer was "no" and the frame turns out to be worth
// keeping, the rig is still holding the pixels - `GET /api/capture/last` says
// so and `POST /api/capture/last/save` writes them. Everything below exists to
// make that offer only when it is real.
//
// THE FETCH KEY IS THE CARD'S MOUNT, deliberately. `CaptureScreen` renders the
// result card only while `lastShot && !arm.inFlight`, and clears `lastShot` on
// every press of the shutter, so the card is unmounted and remounted once per
// landed shot. That mount IS "a capture just landed", which is exactly when the
// buffer changed. A future refactor that keeps the card mounted across shots
// must give it a React `key` per shot, or this hook will describe the previous
// frame; there is no other signal on the wire that a new one arrived.
//
// DEGRADING TO WHAT SHIPPED. An engine older than this feature answers a plain
// 404 with no `code` from both routes. That is not "the frame is gone", it is
// "this rig cannot promote", and the two want opposite screens: `route` goes to
// `"absent"`, `offered` stays false, and the card keeps today's RE-SHOOT AND
// SAVE. A button that offers to save through a route that does not exist is the
// defect class this wave is closing, not a graceful fallback.
//
// A READ THAT FAILED FOR ANY OTHER REASON leaves `route` at `"unknown"`, which
// renders the same shipped face. The difference matters for the copy, not the
// controls: we know an older engine cannot do this, and we do not know that
// about a rig whose link blipped.

import { useCallback, useEffect, useRef, useState } from "react";
import {
  getLastFrame, isPromoteRouteMissing, promoteLastFrame, PromoteRefused,
  type LastFrame, type PromotedFrame,
} from "../../../../api/capture";
import { useLock } from "../../../lib/gateHook";

/** Shown when the buffer answered "nothing held" to a press. The second
 *  sentence is the fact that makes it actionable: the rig keeps ONE frame per
 *  camera, so there is nothing older to go back to. */
export const NOTHING_TO_PROMOTE_NOTE =
  "That frame is no longer buffered - the rig keeps only the last unsaved "
  + "frame per camera. Re-shoot it to keep it.";

/** The second press. Not an apology: the operator has what they asked for, and
 *  the sentence says why pressing again would not add a copy. */
export const ALREADY_SAVED_NOTE =
  "Already saved - a second press would not write a second copy.";

/** The interlock fired: the card was showing an older frame than the rig holds.
 *  The card re-reads the buffer, so the next press is about the frame on screen
 *  rather than the one that was. */
export const FRAME_MOVED_NOTE =
  "The rig is holding a newer frame than this card was showing - the card has "
  + "reloaded it. Press SAVE TO GALLERY again to keep the one it has now.";

/** The `extra` lock reason, in `gate.ts`'s lower-case voice so it reads the
 *  same as "the rig is not reachable" in the same slot. */
export const NOT_BUFFERED_REASON = "the rig is no longer holding that frame";

/** Whether the engine on the other end has the promote routes at all.
 *  `"unknown"` is the honest state before the first answer, and after one that
 *  failed for a reason that says nothing about the engine. */
export type PromoteRoute = "unknown" | "present" | "absent";

export interface LastFrameView {
  route: PromoteRoute;
  /** What the rig says it is holding, once read. */
  frame: LastFrame | null;
  /** True when SAVE TO GALLERY belongs on screen: the route exists and the rig
   *  named a frame. It stays true after a refusal, because a control that
   *  vanishes takes its explanation with it - the reason moves onto the button
   *  instead (`saveReason`). */
  offered: boolean;
  /** The write is in flight. Drives `busy`, never `disabled`. */
  saving: boolean;
  /** The frame is on disk: promoted here, or already there. The card flips to
   *  its saved face and stops offering the press. */
  done: boolean;
  /** The 200's body, when this card is what wrote the file. Null after an
   *  `already_saved`, where the rig knows the file exists and this client never
   *  learned its name - so the card says that rather than inventing a path. */
  saved: PromotedFrame | null;
  /** The one sentence under the buttons: a refusal, or a server message. */
  note: string | null;
  saveReason: string | null;
  onExplain: (reason: string) => void;
  save: (targetOverride?: string) => void;
}

export function useLastFrame(enabled: boolean): LastFrameView {
  const [route, setRoute] = useState<PromoteRoute>("unknown");
  const [frame, setFrame] = useState<LastFrame | null>(null);
  const [gone, setGone] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<PromotedFrame | null>(null);
  const [done, setDone] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  // Read through refs inside `save`: the press must act on what is on screen
  // NOW, and a callback rebuilt on every state change would re-arm the button
  // mid-write.
  const frameRef = useRef<LastFrame | null>(null);
  frameRef.current = frame;
  const doneRef = useRef(false);
  doneRef.current = done;
  // The re-entry guard is a ref rather than `saving`, because two clicks in one
  // tick both run before React flushes any state: the second press would see
  // `saving === false` and post a second write for the same frame.
  const inFlight = useRef(false);

  useEffect(() => {
    if (!enabled) return;
    let live = true;
    void (async () => {
      try {
        const f = await getLastFrame();
        if (!live || !mounted.current) return;
        setFrame(f);
        setRoute("present");
      } catch (e) {
        if (!live || !mounted.current) return;
        setRoute(isPromoteRouteMissing(e) ? "absent" : "unknown");
      }
    })();
    return () => { live = false; };
  }, [enabled]);

  const reload = useCallback(async () => {
    try {
      const f = await getLastFrame();
      if (mounted.current) setFrame(f);
    } catch {
      // The note already says what happened; a failed re-read must not
      // overwrite it with a second, vaguer sentence.
    }
  }, []);

  const save = useCallback((targetOverride?: string) => {
    if (inFlight.current || doneRef.current) return;
    const f = frameRef.current;
    if (!f || !f.available) return;
    inFlight.current = true;
    setSaving(true);
    setNote(null);
    void (async () => {
      try {
        const res = await promoteLastFrame({
          // The interlock, every time: see api/capture.ts's header.
          ...(f.id != null ? { frame_id: f.id } : {}),
          ...(targetOverride ? { target: targetOverride } : {}),
        });
        if (!mounted.current) return;
        setSaved(res);
        setDone(true);
      } catch (e) {
        if (!mounted.current) return;
        if (e instanceof PromoteRefused) {
          // By CODE. The sentences beside these are the server's docstrings and
          // move without notice; the next action each one implies does not.
          if (e.code === "already_saved") {
            setDone(true);
            setNote(ALREADY_SAVED_NOTE);
          } else if (e.code === "nothing_to_promote") {
            setGone(true);
            setNote(NOTHING_TO_PROMOTE_NOTE);
          } else {
            setNote(FRAME_MOVED_NOTE);
            void reload();
          }
        } else {
          // A fault, not a refusal: the server's own sentence is the only thing
          // that knows what went wrong.
          setNote((e as Error).message);
        }
      } finally {
        inFlight.current = false;
        if (mounted.current) setSaving(false);
      }
    })();
  }, [reload]);

  const lock = useLock({
    cap: "control.capture",
    needsRole: "camera",
    // `extra`, not a replacement: the gate's priority order (link -> cap ->
    // role -> lane -> extra) has to stand, or a viewer on a dead link would be
    // told about the buffer instead of the link.
    extra: gone ? NOT_BUFFERED_REASON : null,
  });

  return {
    route,
    frame,
    offered: route === "present" && frame?.available === true && !done,
    saving,
    done,
    saved,
    note,
    saveReason: lock.lockedReason,
    onExplain: lock.onExplain,
    save,
  };
}
