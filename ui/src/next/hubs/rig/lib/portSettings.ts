// portSettings.ts - the per-port policies the POWER sheet edits (D-RIG-5, and
// the port half of D-RIG-3), and the one place that decides what a port row is
// allowed to claim.
//
// THE PROTECTION DECISION MOVED SERVER-SIDE. Until S7h the rule "mount, camera
// and USB are locked while a session runs" lived in the browser, as a regex over
// the port's label (`sheets/power.tsx`'s `SESSION_CRITICAL`). That made the lock
// advice rather than enforcement - curl, a second tab on an older build or a
// script could cut power to the mount mid-sequence and the engine would do it -
// and it made two copies of one rule, which are two rules. `power_guard.py` now
// holds the pattern BYTE-IDENTICAL (`power_guard.py:67`) as the DEFAULT rather
// than the rule, and every port row arrives carrying the server's own answer in
// `protected_now`. This module reads that answer; it does not recompute it.
//
// THE REGEX SURVIVES AS EXACTLY ONE THING: the fallback for an engine that
// predates S7h/S7L and sends no annotation at all. A port that was locked
// yesterday must not become tappable today because the decision moved, so an
// unannotated row keeps the shipped behaviour AND the shipped sentence - the old
// sentence, not the new one, because the new one ends "clear the protection for
// this port in Power settings" and on that engine there is nothing to clear.
//
// THE TRI-STATE IS THE POINT, AND IT IS WHY THE CONTROL IS NOT A SWITCH.
// `protect_during_run` is `null` / `true` / `false` and the three do not
// collapse (`power_guard.py:16-27`):
//
//     null   nobody has said - follow the port's NAME, and keep following it
//            through a rename on the box
//     true   protected, whatever it is called
//     false  not protected, whatever it is called
//
// "unset" adapts to a rename; "the operator said no" is a decision that has to
// survive one. Collapsing them into one boolean is the single failure this
// module's tests exist to catch: it looks right on screen and it silently
// re-protects a port somebody deliberately opened the next time the box's labels
// change.

import { ApiError } from "../../../../api";
import { apiErrorPayload } from "../../../../lib/apiError";
import type { SwitchPortSettings } from "../../../../api/power";
import type { SwitchPort } from "../../../../types";

/** The name heuristic, byte-identical to `power_guard.py:67` and to what this
 *  UI has shipped since wave 1. USED ONLY as the fallback for an engine that
 *  sends no `protected_now` - see the module header. */
export const SESSION_CRITICAL = /mount|camera|usb/i;

/** The engine's refusal, verbatim from `power_guard.py:73-75`, so a tap never
 *  has to be refused to learn why it would be.
 *
 *  A SECOND COPY OF A SERVER STRING, deliberately, and the smallest one that
 *  works: the sentence has to be on screen BEFORE the press, and the only other
 *  way to have it there is to press and read the 409. When a 409 does arrive,
 *  the WIRE's sentence is what gets shown - `switchRefusal` below returns it
 *  verbatim and never substitutes this - so the two can only disagree in the
 *  pre-press hint, and the engine always gets the last word. */
export function protectedRefusal(name: string): string {
  return `${name} is protected while a run is live: switching it now would cut `
    + "power to something the sequence is using. Stop the run, or clear the "
    + "protection for this port in Power settings.";
}

/** The sentence for the LEGACY path only: an engine with no per-port store, so
 *  the only way out is to stop the run. This is the wording that shipped. */
export function legacyLockReason(name: string): string {
  return `${name} is locked while a run is going. Stop the run on Session - Now first.`;
}

/** Does this engine annotate its port rows (S7h + S7L landed)?
 *
 *  `in`, not a truthiness or null test: `protect_during_run` is legitimately
 *  `null` and `protected_now`/`follow_dew` are legitimately `false`. Every one
 *  of those is an ANSWER; only an absent key is silence. `annotate` sets all
 *  three together, so one of them decides for all three. */
export function isAnnotated(port: SwitchPort): boolean {
  return "protected_now" in port;
}

/** Why this port's on/off control is locked, or null.
 *
 *  Capability and role are NOT decided here - they are `useLock`'s, and they
 *  come first (`gate.ts`'s priority: link -> cap -> role -> lane -> extra). This
 *  is the `extra`, the last stop, so the caller composes it as
 *  `capReason ?? portLockReason(...)` and the documented order is preserved. */
export function portLockReason(port: SwitchPort, runActive: boolean): string | null {
  if (isAnnotated(port)) {
    // The server has already ANDed its answer with the run - and with a PAUSE,
    // on purpose: a paused run still owns the camera and still intends to
    // continue, and cutting the mount's power under it loses the alignment the
    // resume needs (`power_guard.py:254-266`). Do not re-AND it here.
    return port.protected_now ? protectedRefusal(port.name) : null;
  }
  return runActive && SESSION_CRITICAL.test(port.name)
    ? legacyLockReason(port.name)
    : null;
}

/** The three stops of the PROTECT DURING RUN control. Strings because
 *  `Segmented` keys and compares by value, and `null` is not a key. */
export type ProtectStop = "name" | "on" | "off";

/** `protect_during_run` -> the stop to show. An unannotated row has no stored
 *  value at all and renders as BY NAME, which is what it is behaving as. */
export function protectStop(port: SwitchPort): ProtectStop {
  const v = port.protect_during_run;
  if (v === true) return "on";
  if (v === false) return "off";
  return "name";
}

/** The stop the user picked -> the value to SEND. `null` for BY NAME is a real
 *  value ("nobody has said"), not an omission, and `putSwitchPortSettings`
 *  sends it as one. Returning `undefined` here would land on "unchanged" and
 *  the control would silently do nothing. */
export function protectPatch(stop: ProtectStop): SwitchPortSettings {
  return { protect_during_run: stop === "on" ? true : stop === "off" ? false : null };
}

/** What the current stop MEANS for this port, in one line, said differently for
 *  each of the three so they cannot be mistaken for each other on screen. */
export function protectLine(port: SwitchPort): string {
  const stop = protectStop(port);
  if (stop === "on") return "protected whatever this port is called";
  if (stop === "off") return "not protected whatever this port is called";
  return SESSION_CRITICAL.test(port.name)
    ? "matched by name - protected"
    : "matched by name - not protected";
}

/** The three stops, in the order they read as a scale: the default first, then
 *  the two decisions that override it. `NOT PROTECTED`, not `OPEN`: on a power
 *  sheet "open" is an open circuit, which is what this control does NOT mean. */
export const PROTECT_STOPS: { value: ProtectStop; label: string }[] = [
  { value: "name", label: "BY NAME" },
  { value: "on", label: "PROTECTED" },
  { value: "off", label: "NOT PROTECTED" },
];

/** The third state, stated once under the control. Without it BY NAME reads as
 *  a default rather than as a choice, and the choice is the whole feature. */
export const BY_NAME_NOTE =
  "Left on BY NAME, this follows the port's label: rename it on the box and the "
  + "protection follows.";

/** What FOLLOW DEW will actually do to THIS port.
 *
 *  In the port's OWN units, never a percentage: `dew.scale_to_port` maps the
 *  loop's 0-100 onto whatever range the port reports, and a Pegasus UPB dew
 *  channel is an 8-bit register (0..255). Printing "62 %" beside a control that
 *  writes 158 is a second unit for one number. A boolean port has no levels at
 *  all, and the same scaling with `_round_half_up` puts it on from half power
 *  up - that is a fact about this port, not a warning. */
export function followDewNote(port: SwitchPort): string {
  if (port.is_boolean) {
    return "the dew loop switches this port on from half power up - it has no levels";
  }
  const unit = port.unit.trim();
  const range = `${port.min} and ${port.max}${unit ? ` ${unit}` : ""}`;
  return `the dew loop sets this port between ${range}, scaled from the dew margin`;
}

/** The one-line summary on the collapsed settings group, so the two policies
 *  are readable without opening it. */
export function settingsSummary(port: SwitchPort): string {
  const stop = protectStop(port);
  const protect = stop === "on" ? "protected"
    : stop === "off" ? "not protected"
      : SESSION_CRITICAL.test(port.name) ? "by name - protected" : "by name - not protected";
  return port.follow_dew ? `${protect} · follows dew` : protect;
}

/** The row's sub-line while the port is protected RIGHT NOW: says which of the
 *  two decisions locked it, because "why is this one locked" is the question
 *  the name heuristic used to leave unanswerable. */
export function protectedSub(port: SwitchPort): string {
  return port.protect_during_run === true
    ? "protected during the run - set for this port"
    : "protected during the run - matched by name";
}

/** The row's sub-line for a port the dew loop is driving. A level set by hand
 *  is not ignored and is not a mistake: `POST /api/switch/set` calls
 *  `dew_controller.note_manual`, which hands the port back to the operator
 *  until the override expires. Saying so is the difference between a control
 *  that looks like it fights the loop and one that is understood to win. */
export function followDewSub(): string {
  return "follows the dew margin - a level set by hand pauses the loop";
}

/** LAN-only, so the relay gets a 403 rather than a write (`s7l-patches.md` From
 *  S7h patch 2 adds `/api/switch/ports` to the fence, which `startswith` catches
 *  while deliberately missing `POST /api/switch/set` - operating a power box
 *  remotely is the product, deciding what the ENGINE refuses is not). */
export const RELAY_SETTINGS_NOTE =
  "Port protection is set at the rig, not over the relay - open AstroDeck on the "
  + "observatory network to change it.";

/** The sentence to show when `PUT /api/switch/ports/{id}` fails. The relay
 *  fence gets its own, because "this security-sensitive operation is LAN-only"
 *  names neither the port nor the way out. */
export function settingsRefusal(err: unknown): string {
  if (err instanceof ApiError && err.code === "local_only") return RELAY_SETTINGS_NOTE;
  return err instanceof Error ? err.message : "could not save this port's settings";
}

/** The sentence to show when `POST /api/switch/set` is refused.
 *
 *  BY CODE, never by matching the message: the wire's `detail` is the engine's
 *  own sentence and is the whole answer - it names the port, says what
 *  switching it would do, and gives BOTH ways out. Returned VERBATIM, which is
 *  why the caller must not put it through `showToast` (that runs `humanizeLog`,
 *  which truncates at 137 characters and this sentence is longer). `null` means
 *  this was not a protection refusal and the caller's ordinary error path owns
 *  it. */
export function switchRefusal(err: unknown): string | null {
  if (!(err instanceof ApiError) || err.code !== "port_protected") return null;
  const payload = apiErrorPayload(err.body);
  const name = typeof payload?.port_name === "string" ? payload.port_name : null;
  // `err.message` IS the server's detail (`parseApiError` pulls it out of the
  // nested shape). The port name is used only when the engine sent a code with
  // no sentence, which no shipped engine does but a proxy could.
  return err.message || (name ? protectedRefusal(name) : null);
}

/** Merge a queued settings patch with a newer one. Both keys are independent
 *  and ABSENT MEANS UNCHANGED, so a spread is exactly right: the newer patch
 *  wins on the keys it carries and says nothing about the others. */
export function mergeSettings(
  older: SwitchPortSettings, newer: SwitchPortSettings,
): SwitchPortSettings {
  return { ...older, ...newer };
}
