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

/** Why a second move is refused while one is in flight.
 *
 *  Not merely advice: POST /api/focuser/move `_spawn`s the `focuser` lane
 *  WITHOUT replace=True, so the server answers a second one with a 409
 *  (api/app.py `_spawn`). Naming Halt matters — it is the only way to end a
 *  move early, and it is the button directly beside the one being refused. */
export const MOVE_IN_FLIGHT_REASON =
  "The focuser is still moving — wait for it to arrive, or press Halt";

/** Why a move is refused between the tap and the server's answer to it.
 *
 *  A DIFFERENT window from the one above, and it needs its own sentence
 *  because nothing has happened on the rig yet. `cmd` is armed only once the
 *  POST resolves (moveTo's ordering, so a refused move is never drawn as a
 *  real one), which leaves the round trip itself unguarded and unnarrated:
 *  normally ~40ms, but a busy rig or a poor link makes it seconds, and in that
 *  window the panel looked exactly as it had before the tap — the 2026-07-31
 *  complaint, reintroduced at a smaller scale. Two taps inside it both reach
 *  /api/focuser/move and the second is a 409 off the `focuser` lane. */
export const MOVE_SENDING_REASON =
  "Sending that move to the focuser — wait for the rig to answer";

/** How long an ARRIVED move's line stays up before the command is retired.
 *  Long enough to read "at 22000" after looking away at the image. */
export const ARRIVED_LINGER_MS = 8_000;

/** …and a refused one's. Much longer, because "not moving — stopped at 360,
 *  asked for 22000" is the fault report this whole narrator exists for, and it
 *  is read minutes after the tap that caused it. */
export const STALL_LINGER_MS = 90_000;

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

/**
 * When to forget a commanded move — 0 for "now", a delay in ms, or null to
 * keep holding it.
 *
 * WHY A COMMAND MUST BE RETIRED AT ALL. `cmd` is the only thing that makes the
 * position numbers mean anything, and it used to live until the next Go. So
 * every LATER motion of the focuser — an autofocus sweep, a coarse walk, the
 * sequencer's own refocus, another client — was narrated as that finished
 * command: first "→ 22000 · at 14475" for a move nobody asked for, and then,
 * six seconds after the sweep parked somewhere else, the orange "not moving —
 * stopped at 18300, asked for 22000". A fault report about a move that
 * succeeded twenty minutes earlier, on a screen whose one job is to tell a
 * refused move from a working one.
 *
 * A settled move lingers first — the line it is showing was worth printing —
 * unless somebody else has taken the focuser, in which case there is nothing
 * honest left to say about our command at all.
 */
export function retireAfterMs(p: {
  /** moveProgress().settled — arrived, or demonstrably not happening. */
  settled: boolean;
  /** …and its tone, so a refusal outstays a confirmation. */
  tone: MoveProgress["tone"] | null;
  /** A sweep or a coarse walk is driving the focuser (the `autofocus` lane).
   *  Retires the command whether or not it settled: the numbers are the
   *  sweep's now, and narrating them against our target is how "→ 22000"
   *  appears over a move nobody asked for. */
  sweepOwnsFocuser: boolean;
  /** A sequence is running. It refocuses on its own cadence, so a finished
   *  CONFIRMATION must not be left lying around for its next move to be
   *  reported as — but it does NOT cancel one in flight (nudging the focuser
   *  during a run is allowed, and a nudge with no narration is the exact
   *  failure this file exists to fix) and it does NOT cancel a REFUSAL. */
  sequenceRunning: boolean;
}): number | null {
  if (p.sweepOwnsFocuser) return 0;
  if (!p.settled) return null;
  // TONE BEFORE THE SEQUENCE GATE, and the order is the whole point. A run does
  // not take the focuser away from the operator, so the move the firmware
  // refuses happens under a sequence as readily as without one — and with the
  // gate above this line, "not moving — stopped at 360, asked for 22000" was
  // retired at 0, i.e. cleared in the same commit that first rendered it. The
  // one line this module exists to print, deleted on the frame it appeared,
  // for the whole duration of every run.
  //
  // The confirmation is what a run may take: "at 22000" has been read by then,
  // and the cost of keeping it is the run's own next refocus being narrated as
  // the user's Go (which is what `sweepOwnsFocuser` above catches, but only
  // once the autofocus lane is actually up).
  if (p.tone === "warn") return STALL_LINGER_MS;
  if (p.sequenceRunning) return 0;
  return ARRIVED_LINGER_MS;
}

/**
 * Why re-anchoring is unavailable, or null when it is allowed.
 *
 * Re-anchoring rewrites what every saved focus position MEANS, so a blocked
 * control here has to say why rather than sit greyed out (house rule §11.8).
 */
export function anchorBlocker(p: {
  canFocus: boolean;
  hasFocuser: boolean;
  supported: boolean;
  moving: boolean;
  raw: string;
  max: number | null;
}): string | null {
  if (!p.canFocus) return "Read-only session";
  if (!p.hasFocuser) return "No focuser is connected";
  if (!p.supported) return "This focuser cannot have its position set";
  if (p.moving) return "Wait for the focuser to stop moving";
  const n = Number(p.raw);
  if (p.raw.trim() === "" || !Number.isFinite(n)) return "Type the position number first";
  if (n < 0) return "Position cannot be negative";
  if (p.max != null && n > p.max) return `Position must be ${p.max} or less`;
  return null;
}
