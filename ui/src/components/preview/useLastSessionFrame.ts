// useLastSessionFrame.ts — fetch, once per visit, the last frame the running
// sequence actually saved, so the Capture stage has something true to show
// while this browser session waits for its first live preview.
//
// The decision half of this (which frame qualifies, how wide a render to ask
// for, what the chip says) is lib/lastSessionFrame.ts and is tested without a
// browser. What lives here is the part that can only be got wrong in time:
//
//   ONE FETCH PER VISIT. Not a poll, and not one per status frame — but not
//   "once, ever" either, which is what this comment used to claim. App renders
//   only the active view, under `key={view}`, so every trip to the Capture tab
//   is a fresh mount with a fresh `asked` ref: five visits with the ring still
//   empty measure five library walks. That window is only ever open until the
//   first live preview lands (the store's `previews` ring survives navigation),
//   so it is bounded by one sub — but each walk is a full library scan under a
//   lock on the imaging machine (the server's own recorded numbers: 1381 ms
//   cold at 293 frames, projected to minutes at 50k), so it is not free either.
//   Do not "optimise" it with a module-scope cache without also keeping the two
//   things a per-mount fetch gets right for nothing: a run whose first frame
//   was not on disk yet gets another chance on the next visit, and a cached
//   answer cannot go stale between visits.
//
//   IT MUST NEVER FIGHT THE LIVE FRAME. `/api/gallery/frames` walks the library
//   and can take seconds; the sub it is racing may finish first. Three things
//   make that safe, and each of them is a test in
//   __tests__/lastSessionFrameHook.test.tsx: a reply is DROPPED if the caller
//   stopped needing one while it was in flight, the held frame is CLEARED the
//   moment that happens, and there is never a second fetch to bring one back.
//
//   THE VERDICT IS RE-RUN, NOT REMEMBERED. What state holds is the FRAME, and
//   `pickSessionFrame` is applied to it again on every render against the
//   sequence as it is NOW. Holding the finished URL instead was a real bug: a
//   stand-in picked mid-run survived the run ending, survived the plan moving
//   to another target, and survived aging past SESSION_FRAME_MAX_AGE_S — so the
//   stage kept a picture of NGC 6946 up under a completed run, and under an
//   M 31 step, which is the exact "lie about what the camera is doing" the
//   picker exists to refuse. Every refusal in lib/lastSessionFrame.ts is now
//   enforced continuously instead of once, at fetch time.
//
//   A DEAD LINK IS NOT A LIVE RUN. With the websocket down the store keeps
//   serving the last `sequence` it was sent, frozen — including
//   `progress.server_now_ms`. The age gate then compares a frozen clock against
//   a frame stamped just before it froze and sees seconds where hours have
//   passed (measured: 41 s apparent against 10840 s real). Nothing inside the
//   picker can detect that, because BOTH of its numbers are frozen; the only
//   honest signal is the link itself, so `linkDown` gates the fetch. It gates
//   the FETCH and not `enabled` on purpose — flipping `enabled` also runs the
//   clearing effect below while `asked` stays set, so a momentary blip would
//   retire the stand-in for the rest of the mount.
//
//   A FAILURE IS SILENT. A 500, a 403, a timeout: the Capture tab is the screen
//   someone is running a night from, and a decorative fetch has no business
//   putting an error on it. The empty state was the behaviour before this
//   feature existed and it is a fine place to fall back to.
import { useEffect, useMemo, useRef, useState } from "react";
import { listFrames } from "../../api/gallery";
import { u } from "../../lib/base";
import { viewPath } from "../../lib/frameView";
import {
  lastFrameAlt,
  lastFrameLabel,
  pickSessionFrame,
  placeholderViewWidth,
  runIsLive,
} from "../../lib/lastSessionFrame";
import type { GalleryFrame, SequenceState } from "../../types";

/**
 * How many rows of the newest-first listing to consider.
 *
 * The page size does not change what the server does — it walks and caches the
 * whole library either way — it only bounds what comes over the wire, which
 * matters on a relay link. Twelve is enough to see past a handful of rows that
 * are not this run's (a calibration frame written at the end of the last one, a
 * second target's tail) without shipping a 40 KB listing to paint one picture.
 */
const LOOK_BACK = 12;

export interface LastSessionFrame {
  /** Fully built, base-prefixed URL for the high-fidelity render. */
  src: string;
  /** The chip drawn over the stage. */
  label: string;
  alt: string;
}

/**
 * What state holds: the frame ITSELF, plus the render width measured off the
 * stage at the moment the reply landed.
 *
 * The frame rather than the finished URL, so the verdict can be re-run against
 * a moving sequence. The width alongside it because the stage that decided it
 * is not being measured again by the time the URL is rebuilt — and re-measuring
 * on every status frame would let a resize silently change the URL, which is a
 * refetch of the picture nobody asked for.
 */
interface HeldFrame {
  frame: GalleryFrame;
  width: number;
}

export function useLastSessionFrame(opts: {
  /** True only while the stage has NOTHING of its own to paint. */
  enabled: boolean;
  sequence: SequenceState;
  /** The websocket is down: everything in `sequence` is a memory, not a
   *  reading. No fetch while this is true — see the header. */
  linkDown: boolean;
  /** The element the stage fills — measured to size the render request. */
  stageRef: { current: HTMLElement | null };
}): LastSessionFrame | null {
  const { enabled, sequence, linkDown, stageRef } = opts;
  const [held, setHeld] = useState<HeldFrame | null>(null);
  /** The fetch has been started. Set before the await, so a re-render mid-flight
   *  cannot start a second one — and never reset, which is what makes "one
   *  fetch" mean once per mount rather than once per run state. */
  const asked = useRef(false);

  // The sequence object is replaced on every 2 s status frame, so it cannot be
  // an effect dependency without re-running the effect every two seconds. It is
  // read through a ref instead — and this effect is declared FIRST so the ref
  // is current by the time the one below runs.
  const seqRef = useRef(sequence);
  useEffect(() => { seqRef.current = sequence; });

  useEffect(() => {
    if (enabled) return;
    // Something real is on the stage. DROP the stand-in rather than merely stop
    // returning it: the store's auth gate clears `previews` back to [] on a
    // re-auth, so a caller really can go from "has a live frame" back to "has
    // nothing" with the run still going — and a picture from before that run's
    // first frame reappearing hours later would be the oldest thing on screen.
    setHeld(null);
  }, [enabled]);

  const live = runIsLive(sequence);
  const target = sequence.target ?? "";

  useEffect(() => {
    if (!enabled || asked.current) return;
    if (!live || !target) return;
    // `linkDown` is a dependency, so a link that drops and comes back inside
    // one mount still gets its fetch when it returns.
    if (linkDown) return;
    asked.current = true;
    let cancelled = false;
    void (async () => {
      try {
        const listing = await listFrames({ limit: LOOK_BACK });
        // The race guard. `enabled` is one of this effect's dependencies, so
        // the first live preview tears the effect down and sets `cancelled`;
        // a reply arriving after that is thrown away rather than pushed into
        // state that the clear above has already been through.
        if (cancelled) return;
        const pick = pickSessionFrame(listing?.frames ?? [], seqRef.current, Date.now());
        if (!pick) return;
        // Measured HERE, not at mount: the stage has been laid out by now, and
        // its box is what decides whether the picture is sharp — and how many
        // bytes of it cross the link. BOTH axes: the empty stage is wide and
        // 380 tall, so on most screens the contained picture is height-limited
        // and a width-only request over-asks by multiples.
        const box = stageRef.current;
        const width = placeholderViewWidth(
          box?.clientWidth ?? 0,
          box?.clientHeight ?? 0,
          typeof window === "undefined" ? 1 : window.devicePixelRatio || 1,
        );
        setHeld({ frame: pick, width });
      } catch {
        // Silent by design — see the header. The stage keeps its empty state.
      }
    })();
    return () => { cancelled = true; };
  }, [enabled, live, target, linkDown, stageRef]);

  // THE VERDICT, RE-RUN. `sequence` is a new object on every status frame, so
  // this recomputes about every two seconds — a one-element loop and a couple
  // of string compares, standing between the operator and a picture that would
  // otherwise outlive the run it was picked for. Feeding the held frame back
  // through the SAME picker is what makes "the run ended", "the plan moved on"
  // and "it aged out" one mechanism rather than three special cases each of
  // which has to be remembered separately.
  //
  // `enabled` is checked here as well as in the clearing effect above. React
  // runs passive effects after paint, so there is one real render between a
  // live frame arriving and the clear, and that render must not hand a stand-in
  // back. No test can isolate that render (act() flushes effects before it
  // returns), so this is an untested seam guard and not a pinned invariant —
  // deliberately kept, but do not read it as covered.
  return useMemo(() => {
    if (!enabled || !held) return null;
    if (!pickSessionFrame([held.frame], sequence, Date.now())) return null;
    return {
      // `mtime` busts the BROWSER cache the same way the gallery's tiles do:
      // the render carries max-age and re-capturing to a path would otherwise
      // keep showing the old bytes for an hour.
      src: u(viewPath(held.frame.path, held.width, held.frame.mtime)),
      label: lastFrameLabel(held.frame),
      alt: lastFrameAlt(held.frame),
    };
  }, [enabled, held, sequence]);
}
