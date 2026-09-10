// api/power.ts: the power box, and the two policies attached to each of its
// ports (D-RIG-3 / D-RIG-5). Server: server/astrodeck/power_guard.py, routed in
// api/app.py's switch block.
//
// THE PROTECTION DECISION MOVED SERVER-SIDE, and that is what these wrappers
// exist to consume. The UI has shipped a `/mount|camera|usb/i` name heuristic
// since wave 1; the engine had none, so a port the UI would not let you tap
// could still be cut by anything that was not this UI, and two copies of a rule
// are two rules. `power_guard.py:67` now holds that pattern BYTE-IDENTICAL, as
// the DEFAULT rather than the rule, and every port row comes back carrying the
// server's own answer.
//
// THE TRI-STATE IS THE POINT. `protect_during_run` is `null` / `true` / `false`
// and the three do not collapse: `null` is "nobody has said", which follows the
// port's name and KEEPS following it through a rename on the box; `false` is
// "the operator said no", a decision that has to survive one. A settings toggle
// that cannot tell them apart cannot show what it is about to change - which is
// why `putSwitchPortSettings` below goes to some trouble to send `null` as a
// value and to send nothing at all as silence.

import { api } from "../api";
import type { SwitchPort } from "../types";

/** `GET /api/switch/ports` (`view.status`) - every port, ANNOTATED.
 *
 *  The rows carry `protect_during_run` (the stored tri-state), `protected_now`
 *  (the effective answer, already ANDed with whether a run is live) and
 *  `follow_dew`. Read `protected_now` to decide whether a tap will be refused;
 *  read `protect_during_run` only in the settings row that edits it.
 *
 *  A run counts as live THROUGH A PAUSE, on purpose (`power_guard.py:254-266`):
 *  a paused run still owns the camera and still intends to continue, and cutting
 *  the mount's power under it loses the alignment the resume needs. */
export const getSwitchPorts = (): Promise<SwitchPort[]> =>
  api.get<SwitchPort[]>("/api/switch/ports");

/** `POST /api/switch/set` (`control.power`) - operate one port. Answers with the
 *  full annotated list, so a caller replaces its rows with the response rather
 *  than patching one in.
 *
 *  409 `code: "port_protected"` when the port is protected and a run is live,
 *  raised BEFORE the write, so nothing has happened when it arrives. The body
 *  carries `port_id` and `port_name` beside the code, and `ApiError.message` is
 *  the server's one sentence (`power_guard.py:73-75`) - show THAT verbatim. It
 *  names the port, says what switching it would actually do, and gives BOTH ways
 *  out, because "stop the run" is not an acceptable answer to somebody whose dew
 *  port is called "USB DEW" and was never session-critical in the first place.
 *
 *  Deliberately NOT on the relay fence: operating a power box remotely is the
 *  product. Only the settings route below is LAN-only. */
export const setSwitchPort = (id: number, value: number): Promise<SwitchPort[]> =>
  api.post<SwitchPort[]>("/api/switch/set", { port_id: id, value });

/** What `putSwitchPortSettings` may change. ABSENT MEANS UNCHANGED - see the
 *  function, where that is enforced rather than merely documented. */
export interface SwitchPortSettings {
  /** `null` is a REAL VALUE ("follow the port's name"), not an omission. */
  protect_during_run?: boolean | null;
  follow_dew?: boolean;
}

/** `PUT /api/switch/ports/{id}` (`config.safety`, LAN-ONLY) - one port's
 *  protection and dew policy. Answers with the full annotated list.
 *
 *  IT SENDS ONLY THE KEYS THE CALLER PASSED, and that is load-bearing rather
 *  than tidy. The server reads presence off `model_fields_set`, so an absent key
 *  means UNCHANGED - and `protect_during_run: null` is one of its three real
 *  values. A spread over a defaults object would therefore FORGE a decision: a
 *  caller editing only `follow_dew` would send `protect_during_run: null` as
 *  well and silently un-pin a port somebody had deliberately protected, with
 *  nothing on screen to show for it. There is no value a caller could send to
 *  mean "leave it alone", so the key has to be missing.
 *
 *  `in` rather than a truthiness test, because the caller's intent is which KEYS
 *  they wrote and two of the three values are falsy: `false` and `null` are both
 *  decisions, and a truthiness test would drop the two that matter and keep only
 *  the one that is already the name heuristic's default. (`JSON.stringify` then
 *  drops an explicitly-`undefined` value, which lands on the same "unchanged" as
 *  an omitted key - the honest answer for a caller who had no value.)
 *
 *  LAN-only because it decides which ports the ENGINE refuses during a run -
 *  protection policy, the same reasoning as `/api/locations`. The fence matches
 *  by prefix, which catches this PUT and deliberately misses
 *  `POST /api/switch/set`. 422 on an unknown port id. */
export function putSwitchPortSettings(
  id: number,
  settings: SwitchPortSettings,
): Promise<SwitchPort[]> {
  const body: Record<string, unknown> = {};
  if ("protect_during_run" in settings) {
    body.protect_during_run = settings.protect_during_run;
  }
  if ("follow_dew" in settings) body.follow_dew = settings.follow_dew;
  return api.put<SwitchPort[]>(`/api/switch/ports/${id}`, body);
}
