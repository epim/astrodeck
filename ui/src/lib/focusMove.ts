// focusMove.ts — what the Focuser panel says while a commanded move is in flight.
//
// Why this exists: on 2026-07-31 the user typed 22000 into "Go to position",
// pressed Go, and NOTHING happened on screen. Not an error, not a spinner, not
// a target — the panel looked exactly as it had before the tap. The move was in
// fact being refused by the focuser's firmware, but the UI could not have shown
// that either way, because POST /api/focuser/move returns `{started}` the
// instant the task is spawned and the panel drew no distinction between "sent"
// and "happening".
//
// So: hold the commanded target, watch the live position and the device's own
// `moving` flag, and say which of the three states we are in — under way,
// arrived, or sent-and-nothing-moved.

/** A move this session asked for. `from` is the position when we asked, kept
 *  so the readout can show how far it has actually got. */
export interface FocuserCommand {
  target: number;
  /** ms epoch when Go (or a nudge) was tapped. */
  startedAt: number;
  from: number;
}

export interface MoveProgress {
  text: string;
  tone: "info" | "warn" | "good";
  /** Nothing further to wait for — the move arrived or demonstrably isn't
   *  happening. The caller uses this to stop ticking a clock. */
  settled: boolean;
}

/** Same tolerance the EAF driver treats as arrival (devices/backends/zwo_usb.py
 *  ARRIVAL_TOLERANCE_STEPS). A focuser that reports 21999 for a commanded 22000
 *  has arrived; saying otherwise would leave the panel waiting forever. */
export const ARRIVAL_TOLERANCE_STEPS = 2;

/** How long the position may sit unchanged, with the device not reporting
 *  motion, before we call it stalled. The status poll is 2s (hub._status_loop),
 *  so this is three polls — long enough that a slow motor start or one dropped
 *  poll can't cry wolf, short enough to beat a person's "did that work?". */
export const STALL_GRACE_MS = 6000;

/**
 * The line to print under the Go button, or null when there is nothing to say.
 *
 * `moving` is the device's own report and may be undefined — plenty of backends
 * cannot answer (Focuser.is_moving defaults False). That is why an unchanging
 * position is the OTHER half of the test: a move that is genuinely happening
 * shows it in the numbers whether or not the driver has a flag for it.
 *
 * @param progressAt ms epoch when `pos` last changed. The stall clock runs from
 *   the later of this and `cmd.startedAt`, so a long multi-thousand-step move
 *   that is visibly progressing never trips the warning.
 */
export function moveProgress(
  cmd: FocuserCommand | null,
  pos: number | null | undefined,
  moving: boolean | undefined,
  now: number,
  progressAt: number,
): MoveProgress | null {
  if (!cmd || pos == null) return null;

  if (Math.abs(pos - cmd.target) <= ARRIVAL_TOLERANCE_STEPS) {
    return { text: `at ${cmd.target}`, tone: "good", settled: true };
  }

  const underWay = { text: `→ ${cmd.target} · at ${pos}`, tone: "info" as const, settled: false };
  if (moving) return underWay;

  const still = now - Math.max(cmd.startedAt, progressAt);
  if (still < STALL_GRACE_MS) return underWay;

  return {
    // Both numbers, always: "it didn't move" is not actionable, "it stopped at
    // 360 when you asked for 22000" points straight at the travel limit.
    text: `not moving — stopped at ${pos}, asked for ${cmd.target}`,
    tone: "warn",
    settled: true,
  };
}
