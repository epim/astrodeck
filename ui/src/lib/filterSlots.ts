// filterSlots.ts — the naming rule behind the blackout checkbox.
//
// The name and the blackout flag were independent fields: you ticked "blackout"
// and then separately typed a name, and nothing stopped the dark slot being
// called "L". That name is not decoration — it lands in the FITS FILTER header
// and in the saved-filename token, so darks would file themselves under
// whatever the slot used to be.

/** The name a blackout slot takes. Upper case because it is a frame-type
 *  token in a filename, and the other tokens are too. */
export const DARK_SLOT_NAME = "DARK";

/** True when `name` is a placeholder rather than something a person chose:
 *  empty, or the "Slot N" the wheel reports when a position is unset. */
export function isPlaceholderName(name: string, index: number): boolean {
  const n = (name ?? "").trim();
  return n === "" || n.toLowerCase() === `slot ${index + 1}`;
}

/**
 * The name after ticking or unticking blackout on slot `index`.
 *
 * Ticking fills in DARK only when the current name is a placeholder — silently
 * overwriting a name somebody deliberately typed is the one variant to avoid,
 * and someone who wants "DARK 2" must be able to keep it.
 *
 * Unticking restores what was there before, because leaving DARK on a slot that
 * now passes light is exactly the mislabel this is meant to prevent.
 */
export function nameForOpaqueToggle(
  current: string,
  index: number,
  nowOpaque: boolean,
  previous: string | undefined,
): string {
  if (nowOpaque) {
    return isPlaceholderName(current, index) ? DARK_SLOT_NAME : current;
  }
  // Coming back off blackout: restore the prior name if we replaced it, else
  // clear our own DARK so the slot does not keep claiming to be one.
  if (current.trim().toUpperCase() === DARK_SLOT_NAME) {
    return previous ?? `Slot ${index + 1}`;
  }
  return current;
}

/* ==========================================================================
   What the Slot button says while a filter change is in flight.

   Why this exists: on 2026-07-31 picking L on the Capture screen looked like
   nothing at all. POST /api/filterwheel/position spawns a background task and
   answers `{started}` immediately, and the only thing on screen that could have
   changed — the slot NAME — does not change until the carousel has finished
   turning, because every backend's get_position reports the OLD slot right up
   to the landing (the Snowflake's banner stream literally goes silent for the
   whole move; ASCOM returns -1, which we clamp to 0).

   Same defect as the focuser Go button, so the same treatment as focusMove.ts:
   hold the slot this session asked for, watch the wheel's own `moving` flag,
   and make "turning" look different from "ignored".
   ========================================================================== */

/** A filter change this session asked for. */
export interface FilterCommand {
  /** 0-based slot the user picked. */
  slot: number;
  /** ms epoch when the pick was tapped. */
  startedAt: number;
  /** Slot the wheel was on when we asked. Load-bearing: it separates "the
   *  carousel never budged" from "the carousel turned and landed somewhere
   *  else", which are different faults with different fixes. */
  from: number;
}

export interface FilterMotion {
  /** What the Slot button should read. The TARGET while a change is in flight,
   *  so the tap has an answer before the wheel lands. */
  summary: string;
  /** Draw the pulsing box. Motion is the primary cue; `summary` carries the
   *  same fact in text, because prefers-reduced-motion kills the animation and
   *  a stilled cue must not be a lost one. */
  pulsing: boolean;
  /** The wheel is not doing what it was told — a sentence, or null. */
  problem: string | null;
}

/**
 * How long a change may sit with the wheel neither reporting motion nor
 * reaching the requested slot before we call it stuck.
 *
 * This clock only ever runs for backends whose `moving` is absent or false —
 * every wheel that can answer (Snowflake, ASCOM/Alpaca, NINA, sim) pulses on
 * its own flag for as long as it genuinely turns. So it is set generously: a
 * full carousel sweep on a slow 8-slot wheel is a few seconds, the status poll
 * that would clear it is 2s, and crying "stuck" at a wheel that is merely slow
 * would be its own lie.
 */
export const FILTER_STUCK_AFTER_MS = 15000;

/** Slot name for display; falls back to the 1-based slot number, never to a
 *  blank, so an unnamed slot is still identifiable on the button. */
export function slotLabel(names: readonly string[], slot: number): string {
  const n = (names[slot] ?? "").trim();
  return n || `#${slot + 1}`;
}

/**
 * @param cmd     the change this session asked for, or null
 * @param position the slot the wheel reports — NOT trustworthy mid-move (see
 *   the header), which is why `moving` is consulted first
 * @param moving  the wheel's own flag; `undefined` = the backend cannot say
 */
export function filterMotion(
  cmd: FilterCommand | null,
  position: number | null | undefined,
  moving: boolean | undefined,
  names: readonly string[],
  now: number,
): FilterMotion {
  const at = position != null && position >= 0 ? slotLabel(names, position) : "—";

  // `moving` outranks the position, deliberately. An ASCOM wheel reports -1
  // while turning and we clamp that to 0, so a move TO slot 0 would otherwise
  // read as "arrived" the instant it was commanded.
  if (moving) {
    return cmd
      ? { summary: `→ ${slotLabel(names, cmd.slot)}`, pulsing: true, problem: null }
      // Nobody on this screen asked, so a sequence step did — and the position
      // is mid-move garbage on at least one backend, so name neither slot.
      : { summary: "turning…", pulsing: true, problem: null };
  }

  if (!cmd) return { summary: at, pulsing: false, problem: null };

  // Arrived: drop every trace of the in-flight state and just be the name. The
  // user asked for exactly this — "Then show the filter name."
  if (position === cmd.slot) {
    return { summary: at, pulsing: false, problem: null };
  }

  // Commanded, not arrived, wheel not claiming motion. For the first few
  // seconds that is indistinguishable from a poll that simply hasn't caught up
  // (2s cadence, and the command was spawned as a background task), so keep
  // pulsing rather than accusing a working wheel.
  if (now - cmd.startedAt < FILTER_STUCK_AFTER_MS) {
    return { summary: `→ ${slotLabel(names, cmd.slot)}`, pulsing: true, problem: null };
  }

  // Both slots, always. "It didn't move" is not actionable; naming where the
  // wheel IS and where it was told to go is, and the two phrasings below point
  // at different faults — a carousel that never budged (power, a seized motor,
  // a command the driver dropped) versus one that turned and mis-seated.
  const want = slotLabel(names, cmd.slot);
  return {
    summary: at,
    pulsing: false,
    problem: position === cmd.from
      ? `wheel did not turn — still on ${at}, asked for ${want}`
      : `wheel stopped on ${at}, not ${want}`,
  };
}
