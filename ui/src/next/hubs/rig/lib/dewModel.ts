// dewModel.ts - what the dew loop is doing, as ONE value two panels render
// (D-RIG-3, task T-U7b-6).
//
// THE MARGIN, NOT THE HUMIDITY. Relative humidity is a statement about the AIR:
// how close it is to saturation at its own temperature. What fogs an objective
// is the GLASS, which radiates to a clear sky and sits below air temperature
// all night, so the quantity that decides whether the optic dews is how many
// degrees the surface has left before it reaches the dew point - air
// temperature minus dew point. That difference is the whole input to the
// server's ramp (`server/astrodeck/dew.py:1-12`) and the only number this
// module prints beside a heater level.
//
// FIVE STATES, AND THE TWO THAT LOOK ALIKE. `status.dew` is ABSENT on an engine
// that has no loop at all, and `null` on one whose loop exists but has not
// ticked yet (`server/astrodeck/hub.py:6642-6657` publishes the key only when
// the snapshot exists; `dew.py:591-615` returns None before the first tick, and
// its docstring says why that is not an empty dict). The two are different
// things to show - "this rig cannot do it" against "ask again in two minutes" -
// so `dewView` answers `absent` for one and `idle` for the other, and the
// camera sheet renders no control for the first and a locked one for the
// second. Collapsing them is how a UI grows a switch bound to a field that does
// not exist.
//
// ABSENT READINGS ARE NOT ZERO. `api/redact.py:151-173` DELETES `margin_c`,
// `temp_c` and `dewpoint_c` and NULLS `power_pct` for a principal without
// `view.weather`, while leaving `enabled`, `following`, `override_until_ts`,
// `reason` and `ports` in place. So a viewer's node is a loop that is genuinely
// following, with no numbers behind it: `?? 0` anywhere below would print a
// heater at 0% and a margin at the dew point - two alarming numbers nobody
// measured. Every reading here is `?? null` and the copy says the readings need
// the capability rather than pretending the loop is idle.
//
// `override_until_ts` IS NULL TWICE. Nothing is overridden, and an override
// configured never to expire (`manual_override_s: 0` is infinity, not zero -
// `dew.py:486-490`). JSON has no infinity to put there, so the two are told
// apart by `following` plus `reason`, which says which in words: `paused` is
// the one with a timestamp in the future, `waiting` is a hand-set level with no
// expiry, and both carry the server's sentence verbatim.

import type { DewConfig, DewStatus } from "../../../../types";
import { accessPhrase } from "../../../../lib/caps";

/** `DewConfig`'s server defaults (`server/astrodeck/config.py:1044-1063`), used
 *  ONLY as the base for a wholesale write when the engine answered `GET
 *  /api/config` without a `dew` block. Never rendered as if it were live. */
export const DEW_DEFAULTS: DewConfig = {
  enabled: false,
  margin_full_c: 1,
  margin_off_c: 5,
  min_power: 0,
  max_power: 100,
  camera_window: true,
  manual_override_s: 7200,
  interval_s: 120,
};

/** The lock reason for the window between the loop being constructed and its
 *  first tick. It resolves itself within one `interval_s`, and saying so is the
 *  difference between a control that is broken and one that is early. */
export const DEW_NOT_TICKED =
  "the dew loop has not ticked yet - it reads the weather once an interval";

/** Why a role sees a following loop with no numbers on it. Built from
 *  `accessPhrase` rather than spelled out, so it can never drift from the
 *  reason the same principal gets when it presses a locked control. */
export function dewHiddenNote(): string {
  return `the margin and the heater level need ${accessPhrase("view.weather")}`;
}

/** The two-threshold explanation, which is the whole reason this is a ramp and
 *  not a switch (`dew.py:14-27`, `config.py:1037-1042`). */
export const DEW_RAMP_NOTE =
  "Two margins, not one, and the gap between them IS the hysteresis. At or below FULL the "
  + "heater runs at MAX; at or above OFF it falls back to MIN; in between the power ramps "
  + "linearly. One threshold would make the heater a switch, and a switch fed a noisy weather "
  + "sample spends the night at 0 and 100 and never at the 40 that would have held the glass.";

/** What the readings mean once they are on screen. */
export interface DewLive {
  /** The loop's own sentence for this tick, VERBATIM - it is digit-free by
   *  construction so it survives redaction (`dew.py:309-316`). */
  reason: string;
  /** 0..100, or null: redacted for this role, or nothing commanded yet. */
  power: number | null;
  /** Air temperature minus dew point in C, or null: absent (redacted) or no
   *  weather reading this tick. NEVER 0 for either. */
  margin: number | null;
  /** True when this principal does not hold `view.weather`, so the two numbers
   *  above are null BECAUSE OF THE ROLE and not because the loop is idle. */
  readingsHidden: boolean;
  /** How many switch ports follow the loop. Survives redaction, so it is
   *  printable for every role. */
  ports: number;
}

export type DewView =
  | { kind: "absent" }
  | { kind: "idle" }
  | ({ kind: "off" } & DewLive)
  | ({ kind: "waiting" } & DewLive)
  | ({ kind: "paused"; untilTs: number; minutes: number } & DewLive)
  | ({ kind: "following" } & DewLive);

/** One tick of the dew loop, as the panels need it.
 *
 *  `canViewWeather` is passed in rather than read from a store so this stays
 *  pure: the same node renders differently for two principals, and that is a
 *  property of the caller, not of the node. */
export function dewView(
  node: DewStatus | null | undefined,
  canViewWeather: boolean,
  nowMs: number = Date.now(),
): DewView {
  if (node === undefined) return { kind: "absent" };
  if (node === null) return { kind: "idle" };
  const live: DewLive = {
    reason: node.reason ?? "",
    // `?? null`, never `?? 0`: see the module header.
    power: node.power_pct ?? null,
    margin: node.margin_c ?? null,
    readingsHidden: !canViewWeather,
    ports: node.ports?.length ?? 0,
  };
  if (!node.enabled) return { kind: "off", ...live };
  const until = node.override_until_ts;
  if (!node.following && until != null && until * 1000 > nowMs) {
    const minutes = Math.max(0, Math.round((until * 1000 - nowMs) / 60000));
    return { kind: "paused", untilTs: until, minutes, ...live };
  }
  if (!node.following) return { kind: "waiting", ...live };
  return { kind: "following", ...live };
}

/** A wall clock, because "for another 118 min" is a duration and "back at
 *  23:14" is a time you can compare with the rest of the night. */
export function dewClockAt(unixSeconds: number): string {
  return new Date(unixSeconds * 1000)
    .toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** The heater level, or why there is not one. NEVER a number when `power` is
 *  null - that null is a redaction or an uncommanded tick, and both would print
 *  a heater at 0%. */
function levelClause(view: DewLive): string {
  if (view.power != null) return `${view.power}%`;
  return view.readingsHidden ? dewHiddenNote() : "no level commanded yet";
}

function marginClause(view: DewLive): string | null {
  return view.margin == null ? null : `margin ${view.margin.toFixed(1)}°C`;
}

/** What the loop is doing TO THE CAMERA WINDOW, for the camera sheet's line.
 *
 *  `null` means this engine carries no dew loop, and the caller keeps its own
 *  hand-set note - the UI must not describe a mechanism that is not there.
 *
 *  `cameraWindow` is `config.dew.camera_window`: a loop can be following the
 *  objective through a switch port while the window is not one of the surfaces
 *  it drives, and a line that said "following" beside a heater nothing is
 *  commanding would be the exact lie this task closes. */
export function dewCameraNote(view: DewView, cameraWindow: boolean | undefined): string | null {
  switch (view.kind) {
    case "absent":
      return null;
    case "idle":
      return DEW_NOT_TICKED;
    case "off":
    case "waiting":
      return view.reason;
    case "paused":
      return `${view.reason} · the loop takes it back at ${dewClockAt(view.untilTs)}`;
    case "following": {
      if (cameraWindow === false) {
        return `${view.reason} · the sensor window is not one of the surfaces it drives`;
      }
      const bits = [view.reason, marginClause(view), levelClause(view)];
      return bits.filter((s): s is string => !!s).join(" · ");
    }
  }
}

/** The surfaces the loop drives, named. Empty when the answer is "none of
 *  them"; `undefined` for `cameraWindow` means the config has not been read, in
 *  which case silence is the only honest answer about the window. */
function surfaceClause(cameraWindow: boolean | undefined, ports: number): string {
  const bits: string[] = [];
  if (cameraWindow) bits.push("the camera window");
  if (ports > 0) bits.push(ports === 1 ? "1 port" : `${ports} ports`);
  return bits.join(" and ");
}

/** The one line the Weather band adds under its DEW tile: what the HEATERS are
 *  doing about the margin the tile above already states.
 *
 *  `view.weather` gates the READINGS, not this line: `enabled`, `following`,
 *  `reason` and `ports` all survive `_strip_dew`, so a principal without it
 *  still learns whether anything is being done about the dew point. */
export function dewBandLine(view: DewView, cameraWindow?: boolean): string | null {
  switch (view.kind) {
    case "absent":
      return null;
    case "idle":
      return DEW_NOT_TICKED;
    case "off":
    case "waiting":
      return view.reason;
    case "paused":
      return `${view.reason} · back at ${dewClockAt(view.untilTs)}`;
    case "following": {
      const where = surfaceClause(cameraWindow, view.ports);
      // Known to drive nothing (the window is off and no port follows) against
      // simply not having read the config: only the first is worth saying, and
      // a loop following nothing is the one state a level would not explain.
      if (!where && cameraWindow !== undefined) {
        return `${view.reason} · nothing is set to follow it`;
      }
      if (view.power != null) {
        return where ? `${view.reason} · ${view.power}% on ${where}` : `${view.reason} · ${view.power}%`;
      }
      // No level to hang off the surfaces, so the surfaces stand on their own
      // clause: "... need operator or admin access on 2 ports" would parse as
      // the access being on the ports.
      const why = view.readingsHidden ? dewHiddenNote() : "no level commanded yet";
      return where ? `${view.reason} · ${where} · ${why}` : `${view.reason} · ${why}`;
    }
  }
}

/** The sentence beside the hand power control while the loop is following it.
 *
 *  It is a FACT, not a warning: `dew.py:444-502` stops following for
 *  `manual_override_s` after any hand write and resumes on its own afterwards,
 *  and `manual_override_s: 0` is an override that never expires rather than one
 *  that expires immediately (`:486-490`) - the opposite reading would make the
 *  safest-looking setting the one that ignores the operator on the next tick. */
export function dewFollowNote(manualOverrideS: number | undefined): string {
  const head = "The dew loop is setting this from the margin. Moving it by hand takes it back";
  if (!manualOverrideS || manualOverrideS <= 0) {
    return `${head} for the rest of the night - this rig's override is set never to expire on its own.`;
  }
  const minutes = Math.max(1, Math.round(manualOverrideS / 60));
  return `${head} for ${minutes} minutes.`;
}

/** The two relational rules pydantic cannot express per field
 *  (`config.py:1064-1077`), in the wire's own words so the sentence a form
 *  refuses with is the sentence the 422 would have carried.
 *
 *  Checked in the SERVER'S ORDER, because a block that breaks both should
 *  report the same one the rig would. */
export function dewRefusal(cfg: DewConfig): string | null {
  if (cfg.margin_off_c <= cfg.margin_full_c) return "dew.margin_off_c must be above margin_full_c";
  if (cfg.max_power < cfg.min_power) return "dew.max_power must be at least min_power";
  return null;
}
